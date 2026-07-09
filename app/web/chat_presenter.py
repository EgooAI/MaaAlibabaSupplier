from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.shared.backend.im_chat_db import ContactConv, MessageRow, coerce_epoch, format_created_at
from app.shared.mitm.pool import get_generic_card_pool, get_product_card_pool, get_user_info_pool
from app.shared.mitm.pool import GenericCard, ProductCard
from app.web.components.card import card_type_name

_CARD_JSON_RE = re.compile(r'"cardType"\s*:\s*3.*?"id"\s*:\s*"(\d+)"', re.DOTALL)
_ANY_CARD_JSON_RE = re.compile(
    r'"cardType"\s*:\s*(\d+).*?"(?:id|ids|encryFeedbackId|orderId|quoteProductId|quoId)"\s*:\s*"([^"]+)"',
    re.DOTALL,
)


def _extract_card_info(message: MessageRow) -> tuple[int, str]:
    """Extract (card_type, card_id) from message content BLOB."""
    if not message.content:
        return 0, ""
    try:
        text = bytes(message.content).decode("latin1")
    except Exception:
        return 0, ""
    m = _ANY_CARD_JSON_RE.search(text)
    if not m:
        return 0, ""
    return int(m.group(1)), m.group(2)


def generic_card_from_message(message: MessageRow) -> GenericCard | None:
    """Look up GenericCard from pool using card type and ID from message content."""
    if message.user_content_type != 10010:
        return None
    card_type, card_id = _extract_card_info(message)
    if not card_type or not card_id:
        return None
    return get_generic_card_pool().get(card_type, card_id)


def product_card_from_message(message: MessageRow) -> ProductCard | None:
    """Look up ProductCard from pool using the product ID in message content."""
    if message.user_content_type != 10010 or not message.content:
        return None
    try:
        raw = bytes(message.content)
        text = raw.decode("latin1")
    except Exception:
        return None
    m = _CARD_JSON_RE.search(text)
    if not m:
        return None
    return get_product_card_pool().find_by_product_id(m.group(1))


@dataclass(frozen=True)
class ConversationGroup:
    label: str
    conversations: list[ContactConv]
    expanded: bool


def contact_display_name(contact_ali_id: str) -> str:
    pool = get_user_info_pool()
    info = pool.get(contact_ali_id) or pool.get_by_login_id(contact_ali_id)
    if info is None:
        return contact_ali_id
    name = f"{info.first_name} {info.last_name}".strip()
    return name or info.login_id or contact_ali_id


def message_text(message: MessageRow) -> str:
    if message.user_content_type == 10010:
        if message.content_label:
            return message.content_label
        card_type, card_id = _extract_card_info(message)
        if card_type:
            type_name = card_type_name(card_type)
            return f"[{type_name}:{card_id}]"
        return "[卡片]"
    return message.content_label or ""


def message_datetime(message: MessageRow) -> datetime:
    return datetime.fromtimestamp(coerce_epoch(message.created_at), tz=timezone.utc).astimezone()


def message_speaker(message: MessageRow, *, is_self: bool) -> str:
    if message.is_system:
        return "系统"
    return "商家(我)" if is_self else "买家"


def conversation_for_suggestions(messages: Iterable[MessageRow], resolver, limit: int = 30) -> list[tuple[str, str, str]]:
    rows = list(messages)[-limit:]
    return [
        (
            format_created_at(message.created_at),
            message_speaker(message, is_self=resolver.is_self(message.sender_id)),
            message_text(message),
        )
        for message in rows
    ]


def conversation_for_translation(messages: Iterable[MessageRow], resolver, cache, *, force: bool = False) -> list[tuple[str, str, str | None]]:
    conversation: list[tuple[str, str, str | None]] = []
    for message in messages:
        text = message_text(message)
        is_self = resolver.is_self(message.sender_id)
        speaker = "商家(我)" if is_self else "买家"
        if is_self or not text or message.is_system:
            conversation.append((format_created_at(message.created_at), speaker, text))
        elif not force and cache.is_cached(text):
            conversation.append((format_created_at(message.created_at), speaker, None))
        else:
            conversation.append((format_created_at(message.created_at), speaker, text))
    return conversation


def group_conversations(conversations: list[ContactConv], now: float | None = None) -> list[ConversationGroup]:
    current = time.time() if now is None else now
    cutoffs = [current - 86400, current - 7 * 86400, current - 30 * 86400]
    groups = [
        ConversationGroup("最近24小时", [], True),
        ConversationGroup("最近7天", [], True),
        ConversationGroup("最近30天", [], True),
        ConversationGroup("更早", [], False),
    ]

    for conv in conversations:
        timestamp = coerce_epoch(conv.last_created_at)
        if timestamp >= cutoffs[0]:
            groups[0].conversations.append(conv)
        elif timestamp >= cutoffs[1]:
            groups[1].conversations.append(conv)
        elif timestamp >= cutoffs[2]:
            groups[2].conversations.append(conv)
        else:
            groups[3].conversations.append(conv)
    return groups
