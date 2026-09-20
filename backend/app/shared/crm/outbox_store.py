"""Application-owned outbox in the CRM database; never a delivery receipt.

The service owns GUI authorization, screenshot artifacts and local-message matching.
It must persist may_have_sent=True BEFORE invoking a send action. Only the service
may advance awaiting_confirmation after validating its live confirmation token.
Recovery is an explicit startup operation, before workers accept new tasks.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from backend.app.api.envelope import AppError


STATUSES = (
    "queued", "navigating", "awaiting_confirmation", "queued_send", "running",
    "verifying", "observed", "filled", "failed", "unknown", "cancelled",
)
PENDING_STATUSES = STATUSES[:6]
_TRANSITIONS = {
    "queued": {"navigating", "failed", "cancelled"},
    "navigating": {"awaiting_confirmation", "failed", "unknown"},
    "awaiting_confirmation": {"queued_send", "failed", "cancelled"},
    "queued_send": {"awaiting_confirmation", "running", "failed", "cancelled"},
    "running": {"verifying", "filled", "failed", "unknown"},
    "verifying": {"observed", "failed", "unknown"},
    "unknown": {"observed"},
}
_FIELDS = {
    "phase", "may_have_sent", "reason", "screenshot_id", "screenshot_at",
    "screenshot_digest", "baseline", "evidence",
}
_PAYLOAD = ("conversation_id", "contact_ali_id", "login_id", "content", "action", "draft_version")


class OutboxConflict(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


def _scope(seller: str, data_dir: str | Path) -> tuple[str, str]:
    if (not isinstance(seller, str) or not seller.strip()
            or not isinstance(data_dir, (str, Path)) or not str(data_dir).strip()):
        raise ValueError("seller and data_dir are required")
    return seller, os.path.normcase(str(Path(data_dir).expanduser().resolve()))


def _states(states: str | Iterable[str]) -> tuple[str, ...]:
    result = (states,) if isinstance(states, str) else tuple(states)
    if not result or any(state not in STATUSES for state in result):
        raise ValueError("Expected at least one valid outbox status")
    return result


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _record(row: sqlite3.Row) -> dict:
    result = dict(row)
    result.setdefault("origin_request_id", None)
    result.setdefault("origin_account_epoch", None)
    result["may_have_sent"] = bool(result["may_have_sent"])
    for name in ("baseline", "evidence"):
        result[name] = json.loads(result[name]) if result[name] is not None else None
    return result


class OutboxStore:
    def __init__(self, database_path: Path | str | None = None) -> None:
        if database_path is None:
            from backend.app.shared.crm.sync import _default_database_path

            database_path = _default_database_path()
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
            conn.execute("PRAGMA foreign_keys=ON")
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
        """Create only app-owned tables; construction and reads never initialize."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        statuses = ",".join(f"'{status}'" for status in STATUSES)
        with self._connection(write=True, create=True) as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS app_outbox (
                    id TEXT PRIMARY KEY,
                    seller TEXT NOT NULL,
                    data_dir TEXT NOT NULL,
                    conversation_id INTEGER NOT NULL,
                    contact_ali_id TEXT NOT NULL,
                    login_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('send','test')),
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ({statuses})),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    attempt INTEGER NOT NULL CHECK(attempt >= 1),
                    phase TEXT NOT NULL,
                    may_have_sent INTEGER NOT NULL CHECK(may_have_sent IN (0,1)),
                    reason TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    screenshot_id TEXT,
                    screenshot_at REAL,
                    screenshot_digest TEXT,
                    baseline TEXT,
                    evidence TEXT,
                    matched_message_id TEXT,
                    draft_version INTEGER,
                    origin_request_id TEXT,
                    origin_account_epoch TEXT,
                    UNIQUE(seller, data_dir, idempotency_key),
                    UNIQUE(seller, data_dir, matched_message_id)
                )
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(app_outbox)")}
            for name in ("origin_request_id", "origin_account_epoch"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE app_outbox ADD COLUMN {name} TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_outbox_events (
                    id INTEGER PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES app_outbox(id),
                    version INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    previous TEXT,
                    current TEXT NOT NULL,
                    UNIQUE(task_id, version)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS app_outbox_conversation ON app_outbox "
                         "(seller, data_dir, conversation_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS app_outbox_pending ON app_outbox (status, created_at)")

    def _read(self, sql: str, parameters: tuple) -> list[sqlite3.Row]:
        if not self.database_path.is_file():
            return []
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_outbox'",
            ).fetchone():
                return []
            return conn.execute(sql, parameters).fetchall()

    def create(
        self, *, seller: str, data_dir: str | Path, conversation_id: int,
        contact_ali_id: str, login_id: str, content: str, action: str,
        idempotency_key: str, draft_version: int | None = None,
        origin_request_id: str | None = None, origin_account_epoch: str | None = None,
    ) -> tuple[dict, bool]:
        seller, directory = _scope(seller, data_dir)
        if action not in ("send", "test"):
            raise ValueError("action must be send or test")
        if type(conversation_id) is not int or conversation_id < 1:
            raise ValueError("conversation_id must be a positive integer")
        if draft_version is not None and (type(draft_version) is not int or draft_version < 0):
            raise ValueError("draft_version must be a nonnegative integer or None")
        for value in (contact_ali_id, login_id, content, idempotency_key):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Recipient, content and idempotency key must be nonempty strings")
        payload = dict(zip(_PAYLOAD, (conversation_id, contact_ali_id, login_id, content, action, draft_version)))
        self.initialize()
        with self._connection(write=True) as conn:
            existing = conn.execute(
                "SELECT * FROM app_outbox WHERE seller=? AND data_dir=? AND idempotency_key=?",
                (seller, directory, idempotency_key),
            ).fetchone()
            if existing is not None:
                record = _record(existing)
                if any(record[name] != payload[name] for name in _PAYLOAD):
                    raise OutboxConflict("Idempotency key already belongs to a different payload")
                return record, False
            now = time.time()
            record = {
                "id": uuid4().hex, "seller": seller, "data_dir": directory, **payload,
                "idempotency_key": idempotency_key, "status": "queued", "version": 1,
                "attempt": 1, "phase": "queued", "may_have_sent": False, "reason": None,
                "created_at": now, "updated_at": now, "screenshot_id": None,
                "screenshot_at": None, "screenshot_digest": None, "baseline": None,
                "evidence": None, "matched_message_id": None,
                "origin_request_id": origin_request_id, "origin_account_epoch": origin_account_epoch,
            }
            conn.execute(
                f"INSERT INTO app_outbox ({','.join(record)}) VALUES ({','.join('?' for _ in record)})",
                tuple(record.values()),
            )
            self._event(conn, None, record, "create")
            return record, True

    def get(self, id: str, *, seller: str, data_dir: str | Path) -> dict | None:
        rows = self._read("SELECT * FROM app_outbox WHERE id=? AND seller=? AND data_dir=?",
                          (id, *_scope(seller, data_dir)))
        return _record(rows[0]) if rows else None

    def list(
        self, conversation_id: int, *, seller: str, data_dir: str | Path, limit: int = 100,
    ) -> list[dict]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        return [_record(row) for row in self._read(
            "SELECT * FROM app_outbox WHERE conversation_id=? AND seller=? AND data_dir=? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (conversation_id, *_scope(seller, data_dir), limit),
        )]

    def list_pending(
        self, states: str | Iterable[str] = PENDING_STATUSES, *, seller: str, data_dir: str | Path,
    ) -> list[dict]:
        """Scoped observer read; callers may include unknown for late reconciliation."""
        states = _states(states)
        return [_record(row) for row in self._read(
            f"SELECT * FROM app_outbox WHERE seller=? AND data_dir=? AND status IN ({','.join('?' for _ in states)}) "
            "ORDER BY created_at, id", (*_scope(seller, data_dir), *states),
        )]

    def events(self, id: str, *, seller: str, data_dir: str | Path) -> list[dict]:
        rows = self._read(
            "SELECT e.* FROM app_outbox_events e JOIN app_outbox o ON o.id=e.task_id "
            "WHERE o.id=? AND o.seller=? AND o.data_dir=? ORDER BY e.version",
            (id, *_scope(seller, data_dir)),
        )
        return [{**dict(row), "previous": json.loads(row["previous"]) if row["previous"] else None,
                 "current": json.loads(row["current"])} for row in rows]

    @staticmethod
    def _event(conn: sqlite3.Connection, previous: dict | None, current: dict, kind: str) -> None:
        conn.execute(
            "INSERT INTO app_outbox_events (task_id,version,kind,created_at,previous,current) VALUES (?,?,?,?,?,?)",
            (current["id"], current["version"], kind, current["updated_at"],
             _json(previous) if previous is not None else None, _json(current)),
        )

    @staticmethod
    def _expected(
        conn: sqlite3.Connection, id: str, seller: str, data_dir: str | Path,
        expected_status: str | Iterable[str], expected_version: int | None,
    ) -> dict:
        states = _states(expected_status)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise ValueError("expected_version must be a positive integer or None")
        row = conn.execute("SELECT * FROM app_outbox WHERE id=? AND seller=? AND data_dir=?",
                           (id, *_scope(seller, data_dir))).fetchone()
        if row is None:
            raise OutboxConflict("Outbox task not found in this scope")
        record = _record(row)
        if record["status"] not in states or (expected_version is not None and record["version"] != expected_version):
            raise OutboxConflict("Outbox status or version changed")
        return record

    def _save(self, conn: sqlite3.Connection, previous: dict, changes: dict, kind: str) -> dict:
        current = {**previous, **changes, "version": previous["version"] + 1, "updated_at": time.time()}
        values = {name: current[name] for name in (*changes, "version", "updated_at")}
        for name in ("baseline", "evidence"):
            if name in values:
                values[name] = _json(values[name]) if values[name] is not None else None
        try:
            cursor = conn.execute(
                f"UPDATE app_outbox SET {','.join(name + '=?' for name in values)} "
                "WHERE id=? AND seller=? AND data_dir=? AND status=? AND version=?",
                (*values.values(), previous["id"], previous["seller"], previous["data_dir"],
                 previous["status"], previous["version"]),
            )
        except sqlite3.IntegrityError as exc:
            raise OutboxConflict("Local message is already claimed by another outbox task") from exc
        if cursor.rowcount != 1:
            raise OutboxConflict("Outbox status or version changed")
        self._event(conn, previous, current, kind)
        return current

    @staticmethod
    def _validate_changes(previous: dict, changes: dict, *, transition: bool) -> None:
        allowed = _FIELDS | {"status", "matched_message_id"} if transition else _FIELDS
        if changes.keys() - allowed:
            raise ValueError(f"Fields are not editable: {sorted(changes.keys() - allowed)}")
        current = {**previous, **changes}
        status = current["status"]
        if transition and (not isinstance(status, str) or status not in _TRANSITIONS.get(previous["status"], set())):
            raise OutboxConflict(f"Invalid outbox transition: {previous['status']} -> {status}")
        if transition and previous["status"] == "queued_send" and status == "awaiting_confirmation":
            if previous["phase"] != "queued_send" or previous["may_have_sent"]:
                raise OutboxConflict("Only a queued task before input can request fresh confirmation")
        if not transition and previous["status"] not in PENDING_STATUSES:
            raise OutboxConflict("Only pending tasks accept field updates")
        for name in ("baseline", "evidence"):
            if current[name] is not None and not isinstance(current[name], dict):
                raise ValueError(f"{name} must be a JSON object or None")
            _json(current[name])
        for name in ("phase", "reason", "screenshot_id", "screenshot_digest", "matched_message_id"):
            value = current[name]
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a nonempty string or None")
        if current["phase"] is None:
            raise ValueError("phase is required")
        stamp = current["screenshot_at"]
        if stamp is not None and (type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0):
            raise ValueError("screenshot_at must be a nonnegative Unix timestamp")
        if (current["screenshot_id"] is None) != (stamp is None):
            raise ValueError("screenshot_id and screenshot_at must be supplied together")
        if status in ("awaiting_confirmation", "queued_send") and current["screenshot_id"] is None:
            raise OutboxConflict("A confirmation screenshot is required")
        if type(current["may_have_sent"]) is not bool:
            raise ValueError("may_have_sent must be boolean")
        if previous["may_have_sent"] and not current["may_have_sent"]:
            raise OutboxConflict("Send uncertainty cannot be cleared")
        if current["may_have_sent"] and (current["action"] != "send" or status in (
            "queued", "navigating", "awaiting_confirmation", "queued_send", "filled", "cancelled",
        )):
            raise OutboxConflict("Send risk is incompatible with this action or status")
        if status == "verifying" and (current["action"] != "send" or not current["may_have_sent"]):
            raise OutboxConflict("Only a possible send can be verified")
        if status == "filled" and current["action"] != "test":
            raise OutboxConflict("Only a test action can finish as filled")
        if status == "observed":
            if (current["action"] != "send" or not current["may_have_sent"]
                    or not current["matched_message_id"] or not current["evidence"]):
                raise OutboxConflict("Observed requires a possible send and local-message evidence")
        elif current["matched_message_id"] is not None:
            raise OutboxConflict("Only observed tasks can claim a local message")

    def transition(
        self, id: str, expected_status: str | Iterable[str], expected_version: int | None = None,
        *, seller: str, data_dir: str | Path, **changes: object,
    ) -> dict:
        """Pass status=...; claiming matched_message_id is atomic with observed.

        Use expected_version from the screenshot snapshot for user confirmation.
        Payload and ownership are immutable. Failed -> queued is only via retry().
        """
        if "status" not in changes:
            raise ValueError("transition requires status")
        with self._connection(write=True) as conn:
            previous = self._expected(conn, id, seller, data_dir, expected_status, expected_version)
            changes.setdefault("phase", changes["status"])
            self._validate_changes(previous, changes, transition=True)
            return self._save(conn, previous, changes, "transition")

    def update_fields(
        self, id: str, expected_status: str | Iterable[str], expected_version: int | None = None,
        *, seller: str, data_dir: str | Path, **changes: object,
    ) -> dict:
        with self._connection(write=True) as conn:
            previous = self._expected(conn, id, seller, data_dir, expected_status, expected_version)
            self._validate_changes(previous, changes, transition=False)
            return self._save(conn, previous, changes, "update_fields")

    def retry(
        self, id: str, expected_version: int | None = None, *, seller: str, data_dir: str | Path,
    ) -> dict:
        with self._connection(write=True) as conn:
            previous = self._expected(conn, id, seller, data_dir, "failed", expected_version)
            if previous["may_have_sent"]:
                raise OutboxConflict("A possibly sent task cannot be retried")
            return self._save(conn, previous, {
                "status": "queued", "phase": "queued", "attempt": previous["attempt"] + 1,
                "reason": None, "screenshot_id": None, "screenshot_at": None, "screenshot_digest": None,
                "baseline": None, "evidence": None, "matched_message_id": None,
            }, "retry")

    def cancel(
        self, id: str, expected_version: int | None = None, *, seller: str, data_dir: str | Path,
        reason: str = "cancelled_by_user",
    ) -> dict:
        return self.transition(
            id, ("queued", "awaiting_confirmation", "queued_send"), expected_version,
            seller=seller, data_dir=data_dir, status="cancelled", reason=reason,
        )

    def recover(self) -> list[dict]:
        """Invalidate all unfinished tasks in this CRM file at startup; never replay.

        This is deliberately database-wide. Run before any worker is started.
        Navigation/execution interruptions are unknown, even for test actions.
        """
        if not self.database_path.is_file():
            return []
        with self._connection(write=True) as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_outbox'",
            ).fetchone():
                return []
            rows = conn.execute(
                f"SELECT * FROM app_outbox WHERE status IN ({','.join('?' for _ in PENDING_STATUSES)})",
                PENDING_STATUSES,
            ).fetchall()
            recovered = []
            for row in rows:
                previous = _record(row)
                risky = previous["may_have_sent"] or previous["status"] in ("navigating", "running", "verifying")
                status = "unknown" if risky else "failed"
                reason = "restart_execution_uncertain" if risky else "restart_before_execution"
                if previous["status"] == "awaiting_confirmation":
                    status, reason = "failed", "restart_confirmation_lost"
                recovered.append(self._save(conn, previous, {
                    "status": status, "phase": "recovery", "reason": reason,
                    "screenshot_id": None, "screenshot_at": None, "screenshot_digest": None,
                }, "recover"))
            return recovered
