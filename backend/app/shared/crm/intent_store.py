"""Application-owned reply intents in the CRM database.

An intent records that the Agent *asked* for a customer reply. It is not a send:
only a merchant action (submitting to the outbox) moves ``requested`` to
``accepted``, and the outbox keeps its own evidence chain.

State machine (the only legal edges):

- live:    ``requested`` -> ``accepted`` | ``discarded``
- simulated: ``simulated`` (terminal, never persisted by the dispatcher)

Accepting one intent discards its batch siblings: the merchant picked a reply.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing, contextmanager
from collections.abc import Iterable, Iterator
from pathlib import Path
from threading import Lock
from uuid import uuid4

from backend.app.shared.crm.paths import default_crm_database_path

MODE_LIVE = "live"
MODE_SIMULATED = "simulated"
MODES = frozenset({MODE_LIVE, MODE_SIMULATED})

STATUS_REQUESTED = "requested"
STATUS_ACCEPTED = "accepted"
STATUS_DISCARDED = "discarded"
STATUS_SIMULATED = "simulated"
STATUSES = frozenset({STATUS_REQUESTED, STATUS_ACCEPTED, STATUS_DISCARDED, STATUS_SIMULATED})

TRANSITIONS = {
    STATUS_REQUESTED: frozenset({STATUS_ACCEPTED, STATUS_DISCARDED}),
    STATUS_ACCEPTED: frozenset(),
    STATUS_DISCARDED: frozenset(),
    STATUS_SIMULATED: frozenset(),
}

_INITIALIZATION_LOCK = Lock()
_INITIALIZED_DATABASES: set[Path] = set()


class IntentConflict(RuntimeError):
    """The intent is not in a state that allows the requested transition."""


def _scope(seller: str, data_dir: str | Path) -> tuple[str, str]:
    if (not isinstance(seller, str) or not seller.strip()
            or not isinstance(data_dir, (str, Path)) or not str(data_dir).strip()):
        raise ValueError("seller and data_dir are required")
    return seller, os.path.normcase(str(Path(data_dir).expanduser().resolve()))


def _record(row: sqlite3.Row) -> dict:
    return dict(row)


class IntentStore:
    def __init__(self, database_path: Path | str | None = None) -> None:
        if database_path is None:
            database_path = default_crm_database_path()
        self.database_path = Path(database_path).expanduser().resolve()

    @contextmanager
    def _connection(self, *, write: bool = False, create: bool = False) -> Iterator[sqlite3.Connection]:
        mode = "rwc" if create else "rw" if write else "ro"
        with closing(sqlite3.connect(
            self.database_path.as_uri() + f"?mode={mode}", uri=True,
            timeout=30, isolation_level=None,
        )) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            try:
                if write:
                    conn.execute("BEGIN IMMEDIATE")
                yield conn
                if write:
                    conn.commit()
            except BaseException:
                if write:
                    conn.rollback()
                raise

    def initialize(self) -> None:
        """Create only the app-owned intent table; reads never initialize."""
        with _INITIALIZATION_LOCK:
            if self.database_path in _INITIALIZED_DATABASES:
                return
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            statuses = ",".join(f"'{status}'" for status in sorted(STATUSES))
            modes = ",".join(f"'{mode}'" for mode in sorted(MODES))
            with self._connection(write=True, create=True) as conn:
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS app_agent_intent (
                        id TEXT PRIMARY KEY,
                        seller TEXT NOT NULL,
                        data_dir TEXT NOT NULL,
                        apid TEXT NOT NULL,
                        agent_name TEXT NOT NULL,
                        revision_id INTEGER,
                        conversation_id INTEGER,
                        batch_id TEXT NOT NULL,
                        mode TEXT NOT NULL CHECK(mode IN ({modes})),
                        reply TEXT NOT NULL,
                        zh TEXT NOT NULL,
                        tone TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ({statuses})),
                        call_id TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS app_agent_intent_batch ON app_agent_intent "
                    "(seller, data_dir, batch_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS app_agent_intent_conversation ON app_agent_intent "
                    "(seller, data_dir, conversation_id, created_at)"
                )
            _INITIALIZED_DATABASES.add(self.database_path)

    def _read(self, sql: str, parameters: tuple) -> list[sqlite3.Row]:
        if not self.database_path.is_file():
            return []
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_agent_intent'",
            ).fetchone():
                return []
            return conn.execute(sql, parameters).fetchall()

    def create(
        self, *, seller: str, data_dir: str | Path, apid: str, agent_name: str,
        revision_id: int | None, conversation_id: int | None, batch_id: str,
        reply: str, zh: str, tone: str, status: str = STATUS_REQUESTED,
        call_id: str | None = None,
    ) -> dict:
        seller, directory = _scope(seller, data_dir)
        if status not in (STATUS_REQUESTED, STATUS_SIMULATED):
            raise ValueError("new intents start as requested or simulated")
        now = time.time()
        record = {
            "id": uuid4().hex, "seller": seller, "data_dir": directory,
            "apid": apid, "agent_name": agent_name, "revision_id": revision_id,
            "conversation_id": conversation_id, "batch_id": batch_id,
            "mode": MODE_SIMULATED if status == STATUS_SIMULATED else MODE_LIVE,
            "reply": reply, "zh": zh, "tone": tone, "status": status,
            "call_id": call_id, "created_at": now, "updated_at": now,
        }
        self.initialize()
        with self._connection(write=True) as conn:
            conn.execute(
                f"INSERT INTO app_agent_intent ({','.join(record)}) "
                f"VALUES ({','.join('?' for _ in record)})",
                tuple(record.values()),
            )
        return record

    def get(self, intent_id: str, *, seller: str, data_dir: str | Path) -> dict | None:
        rows = self._read(
            "SELECT * FROM app_agent_intent WHERE id=? AND seller=? AND data_dir=?",
            (intent_id, *_scope(seller, data_dir)),
        )
        return _record(rows[0]) if rows else None

    def list_batch(self, batch_id: str, *, seller: str, data_dir: str | Path) -> list[dict]:
        return [_record(row) for row in self._read(
            "SELECT * FROM app_agent_intent WHERE batch_id=? AND seller=? AND data_dir=? "
            "ORDER BY created_at, id",
            (batch_id, *_scope(seller, data_dir)),
        )]

    def list_conversation(
        self, conversation_id: int, *, seller: str, data_dir: str | Path, limit: int = 20,
    ) -> list[dict]:
        return [_record(row) for row in self._read(
            "SELECT * FROM app_agent_intent WHERE conversation_id=? AND seller=? AND data_dir=? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (conversation_id, *_scope(seller, data_dir), limit),
        )]

    def transition(
        self, intent_id: str, status: str, *, seller: str, data_dir: str | Path,
    ) -> dict:
        """Apply one legal edge; accepting also discards the rest of its batch."""
        if status not in STATUSES:
            raise ValueError("unknown intent status")
        self.initialize()
        scope = _scope(seller, data_dir)
        with self._connection(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM app_agent_intent WHERE id=? AND seller=? AND data_dir=?",
                (intent_id, *scope),
            ).fetchone()
            if row is None:
                raise IntentConflict("意图不存在")
            current = _record(row)
            if status not in TRANSITIONS.get(current["status"], frozenset()):
                raise IntentConflict(f"意图状态不能从 {current['status']} 变为 {status}")
            now = time.time()
            conn.execute(
                "UPDATE app_agent_intent SET status=?, updated_at=? WHERE id=?",
                (status, now, intent_id),
            )
            if status == STATUS_ACCEPTED:
                conn.execute(
                    "UPDATE app_agent_intent SET status=?, updated_at=? "
                    "WHERE batch_id=? AND seller=? AND data_dir=? AND status=? AND id<>?",
                    (STATUS_DISCARDED, now, current["batch_id"], *scope, STATUS_REQUESTED, intent_id),
                )
            current.update(status=status, updated_at=now)
            return current


def summary_lines(intents: Iterable[dict], limit: int = 5) -> list[str]:
    """Compact prior-reply summaries for the prompt (avoids duplicates)."""
    lines: list[str] = []
    for intent in intents:
        text = str(intent.get("reply") or "").strip().replace("\n", " ")
        if not text:
            continue
        if len(text) > 80:
            text = text[:77] + "..."
        lines.append(f"- [{intent.get('status')}] {text}")
        if len(lines) >= limit:
            break
    return lines


__all__ = [
    "IntentConflict",
    "IntentStore",
    "MODE_LIVE",
    "MODE_SIMULATED",
    "MODES",
    "STATUSES",
    "STATUS_ACCEPTED",
    "STATUS_DISCARDED",
    "STATUS_REQUESTED",
    "STATUS_SIMULATED",
    "TRANSITIONS",
    "summary_lines",
]
