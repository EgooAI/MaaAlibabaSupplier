"""Workspace unread and reply state, independent of platform read receipts.

Explicit initialize() and upgrade sync adopt the existing CRM archive as history
before processing new source messages. A genuinely new scope baselines its first
source snapshot. Neither operation repeats the baseline. Reads never initialize
or migrate; app-owned tables contain projections, never message bodies.
Without an adoptable archive or a verified legacy sync scope, initialize() leaves
baseline_complete=False and creates no scope row. Only an explicit source
snapshot (including an empty one) can complete that new scope's baseline.

Archive adoption requires an existing app_crm_sync_state entry for this scope,
and excludes messages already claimed by another directory's ledger. If the
seller has no sync or inbox scope evidence at all, only the first user-selected
scope may adopt the unscoped legacy archive. Legacy CRM has no per-message source
directory, so this is an explicit adoption policy, not inferred provenance.

Only human messages observed in local IM count as replies, not outbox/GUI events
or platform delivery acknowledgements. A reply must be strictly later than the
buyer's in source time order (first_seen_at when source time is invalid). Ties
cannot clear pending work; fallback ordering is explicitly uncertain.
An entirely historical unanswered segment is history_pending with no deadline;
once it contains new buyers, its clock starts at the first NEW unanswered buyer.
Invalid source times, including times over 300 seconds beyond the fixed
first_seen_at, use first_seen_at for the clock and set uncertain=True.

API integration: explicitly initialize old archives before listing; call
snapshot(sid, ...) BEFORE loading detail messages, then pass its opaque token to
mark_read(). Concurrent arrivals after that boundary stay unread. Tokens are
process-local and invalid after restart. The API must pass the current account
epoch to both snapshot() and mark_read(), as well as guard request epochs. A token
is not a platform read receipt or proof that a human read text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import time
from datetime import datetime
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table, create_engine, func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import URL
from sqlmodel import Session

from backend.app.shared.crm.identities import self_sender_id, session_key_prefix
from backend.app.shared.crm.paths import default_crm_database_path
from backend.app.shared.crm.sdk import Message, SessionMeta
from backend.app.shared.crm.sync_store import canonical_source_dir, sync_state
from backend.app.shared.crm.views import coerce_epoch


DEFAULT_REPLY_TIMEOUT_S = 86400

_TOKEN_KEY = secrets.token_bytes(32)
_SCHEMA = MetaData()
inbox_scope = Table(
    "app_inbox_scope", _SCHEMA,
    Column("seller", String, primary_key=True),
    Column("data_dir", String, primary_key=True),
    Column("baseline_complete", Integer, nullable=False),
    Column("last_seq", Integer, nullable=False),
    Column("inbox_revision", Integer, nullable=False),
    Column("timeout_seconds", Integer, nullable=False, server_default=str(DEFAULT_REPLY_TIMEOUT_S)),
)
inbox_message = Table(
    "app_inbox_message", _SCHEMA,
    Column("seller", String, primary_key=True),
    Column("data_dir", String, primary_key=True),
    Column("external_mid", String, primary_key=True),
    Column("sid", Integer, nullable=False),
    Column("ingest_seq", Integer, nullable=False),
    Column("first_seen_at", Float, nullable=False),
    Column("direction", String, nullable=False),
    Column("occurred_at", Float),
    Column("is_system", Integer, nullable=False),
    Column("is_auto_reply", Integer, nullable=False),
    Column("is_history", Integer, nullable=False),
)
Index("app_inbox_message_session", inbox_message.c.seller, inbox_message.c.data_dir, inbox_message.c.sid)
inbox_state = Table(
    "app_inbox_state", _SCHEMA,
    Column("seller", String, primary_key=True),
    Column("data_dir", String, primary_key=True),
    Column("sid", Integer, primary_key=True),
    Column("read_seq", Integer, nullable=False),
)


def _scope(seller: str, data_dir: str | Path) -> dict:
    if not isinstance(seller, str) or not seller.strip() or ":" in seller:
        raise ValueError("A selected seller is required")
    if not isinstance(data_dir, (str, Path)):
        raise ValueError("data_dir must be a path")
    # Empty is the existing internal sync caller's legacy scope, not cwd.
    return {"seller": seller, "data_dir": canonical_source_dir(str(data_dir))}


def _epoch(value: object) -> float | None:
    try:
        epoch = coerce_epoch(value)
        if not isinstance(value, bool) and math.isfinite(epoch) and epoch > 0:
            datetime.fromtimestamp(epoch)
            return epoch
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return None


def write_inbox(
    session: Session, seller: str, data_dir: str | Path, messages: list[Message] | None = None,
) -> None:
    """Join the caller's BEGIN IMMEDIATE; never commit. None seeds an old archive.

    Call BEFORE upsert_messages/write_sync_state so archive and scope evidence
    still describe the previous commit. Sync passes the complete source snapshot:
    a message already present in another directory is still new to this ledger.
    Missing source records are deliberately retained.
    """
    key = _scope(seller, data_dir)
    _SCHEMA.create_all(session.connection())
    condition = (inbox_scope.c.seller == key["seller"], inbox_scope.c.data_dir == key["data_dir"])
    previous = session.execute(select(inbox_scope).where(*condition)).mappings().first()
    if previous is not None and messages is None:
        return
    history = previous is None
    prefix = session_key_prefix(seller)
    # SQLite LIKE is case-insensitive even with escaped wildcards. Identity is not.
    belongs_to_seller = func.substr(SessionMeta.key, 1, len(prefix)) == prefix
    contacts = dict(session.execute(select(SessionMeta.sid, SessionMeta.key).where(
        belongs_to_seller,
    )).all())
    payloads = {}
    legacy_scope = False
    if history:
        sync_dirs = set()
        if session.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_crm_sync_state'",
        )).first():
            sync_dirs = set(session.execute(select(sync_state.c.data_dir).where(
                sync_state.c.self_ali_id == seller,
            )).scalars())
        known_scope = session.execute(select(inbox_scope.c.data_dir).where(
            inbox_scope.c.seller == seller,
        ).limit(1)).first()
        legacy_scope = key["data_dir"] in sync_dirs
        adopt_archive = legacy_scope or (not sync_dirs and known_scope is None)
    else:
        adopt_archive = False
    if adopt_archive:
        # Project JSON inside SQLite; archive bootstrap does not load message bodies.
        fields = ("sender_id", "created_at", "is_system", "is_auto_reply")
        records = session.execute(select(
            Message.external_mid, Message.sid,
            *(func.json_extract(Message.content, f"$.{name}").label(name) for name in fields),
        ).join(SessionMeta, SessionMeta.sid == Message.sid).where(
            belongs_to_seller,
            Message.external_mid.not_in(select(inbox_message.c.external_mid).where(
                inbox_message.c.seller == seller, inbox_message.c.data_dir != key["data_dir"],
            )),
        ).order_by(Message.external_mid)).mappings()
        payloads = {row["external_mid"]: dict(row) for row in records}
    archived_ids = set(payloads)
    if messages is None and not archived_ids and not legacy_scope:
        # A visit without source evidence must not consume the first-sync baseline.
        return
    source_history = history and not (legacy_scope or archived_ids)
    if messages is not None:
        payloads.update({
            message.external_mid: {
                **(message.content if isinstance(message.content, dict) else {}),
                "external_mid": message.external_mid, "sid": message.sid,
            } for message in messages
        })
    seq = previous["last_seq"] if previous else 0
    now = time.time()
    changed_any = False
    ids = list(payloads)
    projection_fields = ("sid", "direction", "occurred_at", "is_system", "is_auto_reply")
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        existing = {row["external_mid"]: row for row in session.execute(select(inbox_message).where(
            inbox_message.c.seller == seller, inbox_message.c.data_dir == key["data_dir"],
            inbox_message.c.external_mid.in_(batch),
        )).mappings()}
        changed = []
        for mid in batch:
            record = payloads[mid]
            sid = record["sid"]
            if sid not in contacts:
                raise ValueError("Inbox message is outside the seller's sessions")
            contact = contacts[sid][len(prefix):]
            sender = record.get("sender_id")
            direction = "unknown"
            if sender == self_sender_id(seller):
                direction = "outbound"
            elif contact and sender == self_sender_id(contact):
                direction = "inbound"
            current = existing.get(mid)
            first_seen_at = current["first_seen_at"] if current is not None else now
            occurred_at = _epoch(record.get("created_at"))
            # Use the original observation even for corrections and repeat syncs:
            # advancing the wall clock must not rehabilitate a bad future time.
            if occurred_at is not None and occurred_at > first_seen_at + 300:
                occurred_at = None
            projection = {
                "sid": sid, "direction": direction, "occurred_at": occurred_at,
                "is_system": int(bool(record.get("is_system"))),
                "is_auto_reply": int(bool(record.get("is_auto_reply"))),
            }
            if current is not None:
                if all(current[field] == projection[field] for field in projection_fields):
                    continue
                values = {**dict(current), **projection}
            else:
                seq += 1
                values = {
                    **key, "external_mid": mid, **projection, "ingest_seq": seq,
                    "first_seen_at": first_seen_at, "is_history": int(mid in archived_ids or source_history),
                }
            changed.append(values)
        if changed:
            statement = insert(inbox_message)
            session.execute(statement.on_conflict_do_update(
                index_elements=[inbox_message.c.seller, inbox_message.c.data_dir, inbox_message.c.external_mid],
                set_={field: statement.excluded[field] for field in projection_fields},
            ), changed)
            changed_any = True
    if history or changed_any:
        values = {
            **key, "baseline_complete": 1, "last_seq": seq,
            "inbox_revision": (previous["inbox_revision"] if previous else 0) + 1,
            "timeout_seconds": previous["timeout_seconds"] if previous else DEFAULT_REPLY_TIMEOUT_S,
        }
        statement = insert(inbox_scope).values(**values)
        session.execute(statement.on_conflict_do_update(
            index_elements=[inbox_scope.c.seller, inbox_scope.c.data_dir],
            set_={field: statement.excluded[field] for field in values if field not in key},
        ))


class InboxStore:
    def __init__(self, database_path: Path | str | None = None) -> None:
        if database_path is None:
            database_path = default_crm_database_path()
        self.database_path = Path(database_path).expanduser().resolve()

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(
            self.database_path.as_uri() + f"?mode={mode}", uri=True, timeout=30, isolation_level=None,
        )) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
                if write:
                    conn.commit()
            finally:
                conn.rollback()

    def initialize(self, seller: str, data_dir: str | Path) -> dict:
        """Add a baseline to an existing CRM, preserving all SDK/app rows.

        This requires an existing CRM schema; it does not instantiate CRMAdapter,
        run SDK migrations, or create a missing archive. DDL and seed roll back
        together on failure, so no backup/rewriting of existing tables is needed.
        Return baseline_complete=False when the scope still needs its first sync.
        """
        _scope(seller, data_dir)
        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)
        engine = create_engine(URL.create("sqlite", database=str(self.database_path)), connect_args={"timeout": 30})
        try:
            with Session(engine) as session:
                session.execute(text("BEGIN IMMEDIATE"))
                write_inbox(session, seller, data_dir)
                session.commit()
        finally:
            engine.dispose()
        return self.metadata(seller, data_dir)

    def _metadata(self, conn: sqlite3.Connection, key: dict) -> dict:
        row = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_inbox_scope' AND type='table'").fetchone():
            row = conn.execute(
                "SELECT * FROM app_inbox_scope WHERE seller=:seller AND data_dir=:data_dir", key,
            ).fetchone()
        return {**dict(row), "baseline_complete": bool(row["baseline_complete"])} if row else {
            **key, "baseline_complete": False, "last_seq": 0,
            "inbox_revision": 0, "timeout_seconds": DEFAULT_REPLY_TIMEOUT_S,
        }

    def metadata(self, seller: str, data_dir: str | Path) -> dict:
        key = _scope(seller, data_dir)
        if not self.database_path.is_file():
            return {**key, "baseline_complete": False, "last_seq": 0, "inbox_revision": 0, "timeout_seconds": DEFAULT_REPLY_TIMEOUT_S}
        with self._connection() as conn:
            return self._metadata(conn, key)

    def states(
        self, seller: str, data_dir: str | Path, sids: Iterable[int] | None = None, now: float | None = None,
    ) -> dict[int, dict]:
        key = _scope(seller, data_dir)
        if not self.database_path.is_file():
            return {}
        with self._connection() as conn:
            return self._states(conn, key, sids, now)

    def _states(self, conn: sqlite3.Connection, key: dict, sids: Iterable[int] | None, now: float | None) -> dict:
        metadata = self._metadata(conn, key)
        if not metadata["baseline_complete"]:
            return {}
        now = time.time() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError("now must be finite epoch seconds")
        grouped = defaultdict(list)
        # JSON binds a large requested SID set without SQLite's variable limit.
        parameters = {**key, "sids": json.dumps(list(sids)) if sids is not None else None}
        rows = conn.execute("""
            SELECT m.*, coalesce(s.read_seq, 0) AS read_seq
            FROM app_inbox_message m LEFT JOIN app_inbox_state s
              ON s.seller=m.seller AND s.data_dir=m.data_dir AND s.sid=m.sid
            WHERE m.seller=:seller AND m.data_dir=:data_dir
              AND (:sids IS NULL OR m.sid IN (SELECT value FROM json_each(:sids)))
        """, parameters)
        for row in rows:
            grouped[row["sid"]].append(row)
        result = {}
        for sid, messages in grouped.items():
            read_seq = messages[0]["read_seq"]
            human = [m for m in messages if not m["is_system"] and not m["is_auto_reply"]]
            buyers = [m for m in human if m["direction"] == "inbound"]
            sellers = [m for m in human if m["direction"] == "outbound"]
            unknown = [m for m in human if m["direction"] == "unknown"]
            effective_time = lambda m: m["occurred_at"] if m["occurred_at"] is not None else m["first_seen_at"]
            last_reply = max(map(effective_time, sellers), default=0)
            pending = [m for m in buyers if effective_time(m) >= last_reply]
            new_pending = [m for m in pending if not m["is_history"]]
            uncertain = any(m["occurred_at"] is None for m in human) or any(
                effective_time(m) == last_reply for m in pending
            )
            history_pending = bool(pending) and not new_pending
            reply_state = "history_pending" if history_pending else "needs_reply" if pending else "waiting_customer" if sellers else "none"
            latest_known = max((effective_time(m) for m in buyers + sellers), default=0)
            if unknown and any(effective_time(m) >= latest_known for m in unknown):
                reply_state = "unknown"
                uncertain = True
            pending_since = min(map(effective_time, new_pending)) if new_pending else None
            due_at = pending_since + metadata["timeout_seconds"] if pending_since is not None else None
            result[sid] = {
                "unread_count": sum(not m["is_history"] and m["ingest_seq"] > read_seq for m in buyers),
                "reply_state": reply_state, "pending_since": pending_since, "due_at": due_at,
                "is_overdue": reply_state == "needs_reply" and due_at is not None and now >= due_at,
                "history_pending": history_pending, "read_seq": read_seq,
                "snapshot_seq": max(m["ingest_seq"] for m in messages), "uncertain": uncertain,
            }
        return result

    def snapshot(self, sid: int, *, seller: str, data_dir: str | Path, epoch: str | None = None) -> str:
        """Capture BEFORE detail loads; API callers must supply the current epoch."""
        if type(sid) is not int or sid < 1:
            raise ValueError("sid must be a positive integer")
        if epoch is not None and not isinstance(epoch, str):
            raise ValueError("epoch must be a string or None")
        key = _scope(seller, data_dir)
        state = self.states(seller, data_dir, [sid]).get(sid)
        if state is None:
            raise ValueError("Conversation is not in this initialized inbox scope")
        payload = json.dumps(
            [str(self.database_path), key["seller"], key["data_dir"], sid, epoch, state["snapshot_seq"]],
            separators=(",", ":"),
        ).encode()
        signature = hmac.digest(_TOKEN_KEY, payload, hashlib.sha256)
        return base64.urlsafe_b64encode(signature + payload).decode()

    def mark_read(
        self, sid: int, read_snapshot: str, *, seller: str, data_dir: str | Path, epoch: str | None = None,
    ) -> dict:
        if type(sid) is not int or sid < 1:
            raise ValueError("sid must be a positive integer")
        if epoch is not None and not isinstance(epoch, str):
            raise ValueError("epoch must be a string or None")
        key = _scope(seller, data_dir)
        try:
            raw = base64.b64decode(read_snapshot, altchars=b"-_", validate=True)
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(_TOKEN_KEY, payload, hashlib.sha256)):
                raise ValueError
            database, token_seller, directory, token_sid, token_epoch, seq = json.loads(payload)
            if [database, token_seller, directory, token_sid, token_epoch] != [
                str(self.database_path), seller, key["data_dir"], sid, epoch,
            ]:
                raise ValueError
            if type(seq) is not int or seq < 0:
                raise ValueError
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("Invalid or expired inbox read snapshot") from exc
        with self._connection(write=True) as conn:
            current = self._states(conn, key, [sid], None).get(sid)
            if current is None or seq > current["snapshot_seq"]:
                raise ValueError("Read snapshot is outside the current inbox ledger")
            if seq > current["read_seq"]:
                parameters = {**key, "sid": sid, "seq": seq}
                conn.execute("""
                    INSERT INTO app_inbox_state (seller, data_dir, sid, read_seq)
                    VALUES (:seller, :data_dir, :sid, :seq)
                    ON CONFLICT(seller, data_dir, sid) DO UPDATE SET read_seq=excluded.read_seq
                """, parameters)
                conn.execute("""UPDATE app_inbox_scope SET inbox_revision=inbox_revision+1
                    WHERE seller=:seller AND data_dir=:data_dir""", key)
            return self._states(conn, key, [sid], None)[sid]

    def set_timeout(self, value: int, *, seller: str, data_dir: str | Path) -> dict:
        if type(value) is not int or not 0 < value <= 2147483647:
            raise ValueError("timeout_seconds must be a positive 32-bit integer")
        key = _scope(seller, data_dir)
        with self._connection(write=True) as conn:
            metadata = self._metadata(conn, key)
            if not metadata["baseline_complete"]:
                raise ValueError("Initialize the inbox scope before setting its timeout")
            conn.execute("""UPDATE app_inbox_scope SET timeout_seconds=:value, inbox_revision=inbox_revision+1
                WHERE seller=:seller AND data_dir=:data_dir AND timeout_seconds != :value""", {**key, "value": value})
            return self._metadata(conn, key)
