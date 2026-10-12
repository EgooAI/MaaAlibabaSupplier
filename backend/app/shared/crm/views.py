from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.shared.crm.identities import sender_matches
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


@dataclass(frozen=True)
class CrmConversationDigest:
    contact_ali_id: str
    sid: int
    key: str
    participants: tuple[int, ...] = ()
    latest_created_at: Any = None
    latest_content_label: str | None = None
    latest_is_card: bool = False
    dialogue_count: int = 0


CARD_CONTENT_TYPE = 10010


def _extra_of(account: Any) -> dict:
    extra = account.extra if hasattr(account, "extra") else account.get("extra") if isinstance(account, dict) else None
    return extra if isinstance(extra, dict) else {}


def _cid_of(account: Any) -> int | None:
    cid = account.cid if hasattr(account, "cid") else account.get("cid") if isinstance(account, dict) else None
    return cid if isinstance(cid, int) and cid > 0 else None


def is_self_account(account: Any) -> bool:
    return _extra_of(account).get("is_self") is True


def resolve_contact_cid(participants: list[int], accounts: dict[int, Any]) -> int | None:
    """Resolve the single contact customer for a conversation.

    Accounts may be SDK models or JSON dicts. A lone non-self participant wins
    only when it is the positional contact of a two-party conversation; several
    non-self participants win only when they share one ``cid``. Otherwise a
    two-party conversation falls back to its second participant so synced rows
    keep working when ``is_self`` is missing. Anything else is ambiguous and
    returns ``None`` so callers can disable customer maintenance.
    """
    resolved = {aid: accounts[aid] for aid in participants if aid in accounts}
    nonself = [account for account in resolved.values() if not is_self_account(account)]
    if len(nonself) == 1:
        if len(participants) != 2 or resolved.get(participants[1]) is nonself[0]:
            return _cid_of(nonself[0])
    elif len(nonself) > 1:
        cids = {cid for cid in (_cid_of(account) for account in nonself) if cid is not None}
        if len(cids) == 1:
            return next(iter(cids))
    if len(participants) == 2:
        candidate = resolved.get(participants[1])
        if candidate is not None and not is_self_account(candidate):
            return _cid_of(candidate)
    return None


class CrmResolver:
    def __init__(self, self_ali_id: str = "") -> None:
        self._self_ali_id = self_ali_id

    def is_self(self, sender_id: str | None) -> bool:
        return sender_matches(sender_id, self._self_ali_id)


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
    if message.user_content_type not in (0, CARD_CONTENT_TYPE):
        return f"[暂不支持的消息类型: {message.user_content_type if message.user_content_type is not None else 'unknown'}]"
    return message.content_label or ""


__all__ = [
    "CARD_CONTENT_TYPE",
    "CrmConversation",
    "CrmMessage",
    "CrmConversationDigest",
    "CrmResolver",
    "coerce_epoch",
    "format_created_at",
    "is_card_message",
    "is_self_account",
    "message_display_text",
    "normalize_message_type",
    "resolve_contact_cid",
    "resolve_role",
]
