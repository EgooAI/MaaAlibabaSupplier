from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class CrmMessage:
    table_name: str
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
class CrmConversation:
    contact_ali_id: str
    messages: list[CrmMessage]
    last_created_at: Any
    last_content_label: str | None


class CrmResolver:
    def __init__(self, self_ali_id: str = "") -> None:
        self._self_sender_id = f"{self_ali_id}@icbu" if self_ali_id else ""

    def is_self(self, sender_id: str | None) -> bool:
        return bool(self._self_sender_id and sender_id == self._self_sender_id)


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


__all__ = ["CrmConversation", "CrmMessage", "CrmResolver", "coerce_epoch", "format_created_at"]
