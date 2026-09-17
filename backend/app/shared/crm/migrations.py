"""Application-owned migrations, run before the SDK can write to the database."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.shared.crm.identities import PLATFORM_PID, message_external_id

MESSAGE_SCOPE_MIGRATION = "seller_scoped_message_ids_v1"
_MARKER_TABLE = "app_crm_migrations"


class CRMMigrationError(RuntimeError):
    """The original database is retained; imports must not proceed."""


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_applied(conn: sqlite3.Connection) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_MARKER_TABLE,)
    ).fetchone()
    return bool(exists and conn.execute(
        f"SELECT 1 FROM {_MARKER_TABLE} WHERE name=?", (MESSAGE_SCOPE_MIGRATION,)
    ).fetchone())


def _backup_database(path: Path) -> Path:
    directory = path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"{path.name}.{MESSAGE_SCOPE_MIGRATION}.{stamp}.{uuid4().hex}.sqlite"
    partial = target.with_suffix(".partial")
    # A separate reader can back up while our BEGIN IMMEDIATE excludes writers.
    # Backing up from the connection holding the write transaction would hang.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)) as source:
        with closing(sqlite3.connect(partial, timeout=30)) as destination:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode=DELETE")
            if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise CRMMigrationError("CRM backup failed integrity validation")
    with partial.open("r+b") as stream:
        os.fsync(stream.fileno())
    # Interrupted backups retain a unique .partial file and are never reused.
    partial.replace(target)
    return target


def _migrate_rows(conn: sqlite3.Connection, tables: list[str]) -> None:
    if "message" not in tables:
        return
    conn.execute("CREATE TEMP TABLE message_id_map (old_id TEXT PRIMARY KEY, new_id TEXT UNIQUE NOT NULL)")
    if "session_meta" not in tables:
        if conn.execute("SELECT 1 FROM message LIMIT 1").fetchone():
            raise CRMMigrationError("Messages have no session ownership; refusing to guess seller")
        return
    rows = conn.execute(
        "SELECT m.external_mid, m.content, s.key FROM message AS m "
        "LEFT JOIN session_meta AS s ON s.sid=m.sid"
    )
    for old_id, raw_content, key in rows:
        parts = str(key or "").split(":")
        if len(parts) != 3 or parts[0] != PLATFORM_PID or not parts[1] or not parts[2]:
            raise CRMMigrationError(f"Message {old_id!r} has no unambiguous seller session")
        _, seller, contact = parts
        content = json.loads(raw_content)
        if not isinstance(content, dict):
            raise CRMMigrationError(f"Message {old_id!r} has invalid source content")
        cid = content.get("cid")
        if cid and str(cid).split("#", 1)[0] not in (f"{seller}-{contact}", f"{contact}-{seller}"):
            raise CRMMigrationError(f"Message {old_id!r} source contradicts session ownership")
        scoped_prefix = f"{PLATFORM_PID}:{seller}:"
        source_id = old_id.removeprefix(scoped_prefix)
        table, separator, mid = source_id.partition(":")
        if not separator or table == PLATFORM_PID:
            raise CRMMigrationError(f"Message {old_id!r} has an unsupported source ID")
        if (content.get("table_name") is not None and content["table_name"] != table) or (
            content.get("mid") is not None and str(content["mid"]) != mid
        ):
            raise CRMMigrationError(f"Message {old_id!r} source ID contradicts content")
        new_id = message_external_id(seller, table, mid)
        if new_id != old_id:
            conn.execute("INSERT INTO message_id_map VALUES (?, ?)", (old_id, new_id))

    # Inspect the actual database, including any legacy tables, rather than only
    # today's SDK metadata. Translate currently uses text_hash, not message IDs.
    columns = {
        table: conn.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
        for table in tables
    }
    column_names = {
        (table.lower(), column[1].lower()): (table, column[1])
        for table, info in columns.items() for column in info
    }
    primary_keys = {
        table.lower(): [column[1] for column in sorted(info, key=lambda column: column[5]) if column[5]]
        for table, info in columns.items()
    }
    references = {value for key, value in column_names.items() if key[1] == "external_mid"}
    children: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for table in tables:
        for fk in conn.execute(f"PRAGMA foreign_key_list({_quoted(table)})"):
            parent_table, parent_column = fk[2].lower(), fk[4]
            if parent_column is None:
                # An omitted target uses the parent's PK order, which can differ
                # from its column order. Each composite FK component is separate.
                parent_pk = primary_keys.get(parent_table, [])
                if fk[1] >= len(parent_pk):
                    raise CRMMigrationError(f"Invalid implicit foreign key in {table!r}")
                parent_column = parent_pk[fk[1]]
            parent = column_names.get((parent_table, parent_column.lower()))
            if parent is None:
                raise CRMMigrationError(f"Unknown foreign key target in {table!r}")
            child = column_names[(table.lower(), fk[3].lower())]
            children.setdefault(parent, set()).add(child)
    pending = list(references)
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in references:
                references.add(child)
                pending.append(child)
    # The visited set above also terminates cyclic and self-referential FKs.
    for table, column in sorted(references - {("message", "external_mid")}) + [("message", "external_mid")]:
        quoted_column = _quoted(column)
        conn.execute(
            f"UPDATE {_quoted(table)} AS target SET {quoted_column}="
            f"(SELECT new_id FROM message_id_map WHERE old_id=target.{quoted_column}) "
            f"WHERE target.{quoted_column} IN (SELECT old_id FROM message_id_map)"
        )


def migrate_message_ids(database_path: Path | str) -> None:
    """Back up and atomically scope old IDs once; concurrent callers serialize.

    Only the persisted session key establishes ownership. Missing or conflicting
    ownership, ID collisions, backup failures and broken references block access.
    The completion marker commits with the changes, so interruption is retryable.
    """
    path = Path(database_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30, isolation_level=None)) as conn:
        if _is_applied(conn):
            return
        try:
            # This connection alone disables FK actions; explicitly updating all
            # references avoids ON UPDATE cascades changing unrelated row data.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN IMMEDIATE")
            if _is_applied(conn):
                conn.rollback()
                return
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            backup = _backup_database(path) if tables else None
            _migrate_rows(conn, tables)
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise CRMMigrationError("CRM message migration found broken foreign key references")
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {_MARKER_TABLE} "
                "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL, backup_path TEXT)"
            )
            conn.execute(
                f"INSERT INTO {_MARKER_TABLE} VALUES (?, ?, ?)",
                (MESSAGE_SCOPE_MIGRATION, datetime.now(timezone.utc).isoformat(), str(backup) if backup else None),
            )
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            if not isinstance(exc, Exception):
                raise
            raise CRMMigrationError(f"CRM migration blocked for {path}: {exc}") from exc
