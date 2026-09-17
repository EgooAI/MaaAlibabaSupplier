"""Application-owned snapshot state and batched message writes (no SDK schema changes)."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, select
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session

from backend.app.shared.crm.sdk import Message


sync_state = Table(
    "app_crm_sync_state", MetaData(),
    Column("self_ali_id", String, primary_key=True),
    Column("data_dir", String, primary_key=True),
    Column("view_revision", Integer, nullable=False),
    Column("source_revision", Integer, nullable=False),
    Column("last_success", Float, nullable=False),
    Column("inserted", Integer, nullable=False),
    Column("updated", Integer, nullable=False),
    Column("unchanged", Integer, nullable=False),
)


def canonical_source_dir(data_dir: str) -> str:
    """Empty is reserved for legacy internal callers, never the current directory."""
    return os.path.normcase(str(Path(data_dir).expanduser().resolve())) if data_dir else ""


def read_sync_state(
    self_ali_id: str, data_dir: str, database_path: Path | str | None = None,
) -> dict | None:
    """Read committed state without creating a file, schema, engine or migration.

    Return None for an absent database/table/key. Other database errors propagate.
    Revision aliases match the result of sync_im_database().result().
    """
    if database_path is None:
        from backend.app.shared.crm.sync import _default_database_path

        database_path = _default_database_path()
    path = Path(database_path).resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (sync_state.name,),
        ).fetchone():
            return None
        row = conn.execute(
            "SELECT * FROM app_crm_sync_state WHERE self_ali_id=? AND data_dir=?",
            (self_ali_id, canonical_source_dir(data_dir)),
        ).fetchone()
        return _result(dict(row)) if row is not None else None


def _result(values: dict) -> dict:
    return {**values, "revision": values["view_revision"], "applied_source_revision": values["source_revision"]}


def write_sync_state(
    session: Session, self_ali_id: str, data_dir: str, source_revision: int, counts: dict,
) -> dict:
    """Caller owns BEGIN IMMEDIATE and commit; state rolls back with all CRM writes."""
    sync_state.create(session.connection(), checkfirst=True)
    key = {"self_ali_id": self_ali_id, "data_dir": canonical_source_dir(data_dir)}
    previous = session.execute(select(sync_state.c.view_revision).where(
        sync_state.c.self_ali_id == key["self_ali_id"], sync_state.c.data_dir == key["data_dir"],
    )).scalar_one_or_none()
    values = {
        **key, "view_revision": (previous or 0) + 1, "source_revision": source_revision,
        "last_success": time.time(), **counts,
    }
    statement = insert(sync_state).values(**values)
    session.execute(statement.on_conflict_do_update(
        index_elements=[sync_state.c.self_ali_id, sync_state.c.data_dir],
        set_={name: statement.excluded[name] for name in values if name not in key},
    ))
    return _result(values)


def upsert_messages(session: Session, messages: list[Message]) -> dict[str, int]:
    """Compare bounded batches by source ID, preserving locally managed read state."""
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    table = Message.__table__
    fields = ("sid", "sender", "content", "type", "created_at")
    # Source IDs, not text or timestamps, are the identity. Never delete history.
    payloads = {message.external_mid: message.model_dump() for message in messages}
    keys = list(payloads)
    changed = []
    for offset in range(0, len(keys), 500):
        batch = keys[offset:offset + 500]
        existing = {
            row["external_mid"]: row for row in session.execute(
                select(table).where(table.c.external_mid.in_(batch)),
            ).mappings()
        }
        for key in batch:
            payload = payloads[key]
            current = existing.get(key)
            if current is None:
                counts["inserted"] += 1
            elif all(current[field] == payload[field] for field in fields):
                counts["unchanged"] += 1
                continue
            else:
                counts["updated"] += 1
            changed.append(payload)
        # Coalesce sparse changes across comparison batches without exceeding the
        # existing 500-row write bound (inbox projection shares this transaction).
        while len(changed) >= 500 or (changed and offset + 500 >= len(keys)):
            statement = insert(table)
            session.execute(statement.on_conflict_do_update(
                index_elements=[table.c.external_mid],
                set_={field: statement.excluded[field] for field in fields},
            ), changed[:500])
            del changed[:500]
    return counts
