from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MessageRow:
    cid: str
    mid: str
    sender_id: str | None
    created_at: Any
    user_content_type: int | None
    content_label: str | None
    content: bytes | None = None
    is_system: bool = False
    is_auto_reply: bool = False


@dataclass(frozen=True)
class ContactConv:
    contact_ali_id: str
    messages: list[MessageRow]
    last_created_at: Any
    last_content_label: str | None


_SYSTEM_MSG_RE = re.compile(r'\\?"systemMessage\\?"\s*:\s*true')
_AUTO_REPLY_RE = re.compile(r'\\?"autoReply\\?"\s*:\s*true')


def _basic_message_info(extension: str | None) -> dict[str, Any]:
    if not extension:
        return {}

    try:
        outer = json.loads(extension)
    except (TypeError, ValueError):
        return {}

    basic = outer.get("basicMessageInfo")
    if isinstance(basic, dict):
        return basic
    if not isinstance(basic, str):
        return {}

    try:
        parsed = json.loads(basic)
    except ValueError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _is_system_message(extension: str | None) -> bool:
    basic = _basic_message_info(extension)
    if "systemMessage" in basic:
        return basic["systemMessage"] is True
    return bool(extension and _SYSTEM_MSG_RE.search(extension))


def _is_auto_reply(extension: str | None) -> bool:
    basic = _basic_message_info(extension)
    if "autoReply" in basic:
        return basic["autoReply"] is True
    return bool(extension and _AUTO_REPLY_RE.search(extension))


def open_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite db not found: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_msg_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        r"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'msg\_%' ESCAPE '\' ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def coerce_epoch(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 10**12 else float(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            try:
                number = int(value)
            except ValueError:
                return 0.0
            return float(number) / 1000.0 if number > 10**12 else float(number)
    return 0.0


def format_created_at(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            return datetime.fromisoformat(stripped).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return stripped
    epoch = coerce_epoch(value)
    if epoch <= 0:
        return str(value)
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _contact_ali_id_from_cid(cid: str, self_ali_id: str) -> str:
    main = cid.split("#")[0]
    parts = main.split("-")
    if len(parts) != 2:
        return ""
    first, second = parts
    return second if first == self_ali_id else first


def build_conversations(conn: sqlite3.Connection, self_ali_id: str) -> list[ContactConv]:
    from collections import defaultdict

    groups: dict[str, list[MessageRow]] = defaultdict(list)
    for table in list_msg_tables(conn):
        for row in conn.execute(
            f"SELECT cid, mid, sender_id, created_at, user_content_type, content_label, extension, content "
            f"FROM {table} "
            f"WHERE user_content_type IN (0, 10010) "
            f"ORDER BY created_at ASC"
        ):
            contact = _contact_ali_id_from_cid(str(row["cid"]), self_ali_id)
            if not contact:
                continue
            groups[contact].append(MessageRow(
                cid=str(row["cid"]),
                mid=str(row["mid"]),
                sender_id=row["sender_id"],
                created_at=row["created_at"],
                user_content_type=row["user_content_type"],
                content_label=row["content_label"],
                content=row["content"],
                is_system=_is_system_message(row["extension"]),
                is_auto_reply=_is_auto_reply(row["extension"]),
            ))

    conversations = [
        ContactConv(
            contact_ali_id=contact_ali_id,
            messages=messages,
            last_created_at=messages[-1].created_at,
            last_content_label=messages[-1].content_label,
        )
        for contact_ali_id, messages in groups.items()
        if messages
    ]
    conversations.sort(key=lambda conv: coerce_epoch(conv.last_created_at), reverse=True)
    return conversations


class MsgTableResolver:
    def __init__(self, self_ali_id: str = ""):
        self._self_sender_id = f"{self_ali_id}@icbu" if self_ali_id else ""

    def is_self(self, sender_id: str | None) -> bool:
        return bool(self._self_sender_id and sender_id == self._self_sender_id)
