from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.shared.crm.identities import self_sender_id
from backend.app.shared.utils.crm_time import coerce_epoch, format_created_at


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
    sid: int = 0
    key: str = ""
    participants: tuple[int, ...] = ()


CARD_CONTENT_TYPE = 10010


class CrmResolver:
    def __init__(self, self_ali_id: str = "") -> None:
        self._self_sender_id = self_sender_id(self_ali_id) if self_ali_id else ""

    def is_self(self, sender_id: str | None) -> bool:
        return bool(self._self_sender_id and sender_id == self._self_sender_id)


def is_card_message(message: CrmMessage) -> bool:
    return message.user_content_type == CARD_CONTENT_TYPE


def resolve_role(message: CrmMessage, resolver: CrmResolver) -> str:
    if message.is_system or message.is_auto_reply:
        return "system"
    if is_card_message(message):
        return "card"
    return "seller" if resolver.is_self(message.sender_id) else "buyer"


def normalize_message_type(message: CrmMessage) -> str:
    if message.is_system or message.is_auto_reply:
        return "system"
    if is_card_message(message):
        return "card"
    return "text"


def message_display_text(message: CrmMessage) -> str:
    if is_card_message(message):
        return message.content_label or "[卡片]"
    return message.content_label or ""


__all__ = [
    "CARD_CONTENT_TYPE",
    "CrmConversation",
    "CrmMessage",
    "CrmResolver",
    "coerce_epoch",
    "format_created_at",
    "is_card_message",
    "message_display_text",
    "normalize_message_type",
    "resolve_role",
]
