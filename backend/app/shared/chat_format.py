"""Shared chat formatting helpers for the HTTP API (no UI dependencies)."""

from __future__ import annotations

import json
import re
from typing import Iterable

from backend.app.shared.crm.views import (
    CrmMessage,
    CrmResolver,
    format_created_at,
    message_display_text,
)
from backend.app.shared.mitm.pool import get_generic_card_pool, get_product_card_pool

_ANY_CARD_JSON_RE = re.compile(
    r'"cardType"\s*:\s*(\d+).*?"(?:id|ids|encryFeedbackId|orderId|quoteProductId|quoId)"\s*:\s*"([^"]+)"',
    re.DOTALL,
)
_PRODUCT_CARD_JSON_RE = re.compile(r'"cardType"\s*:\s*3.*?"id"\s*:\s*"(\d+)"', re.DOTALL)


def conversation_transcript(
    messages: Iterable[CrmMessage],
    resolver: CrmResolver,
    *,
    limit: int | None = 30,
) -> list[tuple[str, str, str]]:
    rows = list(messages)
    if limit is not None:
        rows = rows[-limit:]
    transcript: list[tuple[str, str, str]] = []
    for message in rows:
        if message.is_system:
            speaker = "系统"
        elif resolver.is_self(message.sender_id):
            speaker = "商家(我)"
        else:
            speaker = "买家"
        transcript.append((format_created_at(message.created_at), speaker, message_display_text(message)))
    return transcript


def extract_card_ref(message: CrmMessage) -> tuple[int, str]:
    """Extract (card_type, card_id) from a message content BLOB."""
    if not message.content:
        return 0, ""
    try:
        text = bytes(message.content).decode("latin1")
    except Exception:
        return 0, ""
    match = _ANY_CARD_JSON_RE.search(text)
    if not match:
        return 0, ""
    try:
        return int(match.group(1)), match.group(2)
    except ValueError:
        return 0, ""


def business_card_from_message(message: CrmMessage) -> dict | None:
    """Build a BusinessCard dict for a card message, or None if unresolvable."""
    if message.user_content_type != 10010:
        return None
    card_type, card_id = extract_card_ref(message)
    if not card_type or not card_id:
        return None
    if card_type == 3 and message.content:
        try:
            text = bytes(message.content).decode("latin1")
        except Exception:
            text = ""
        product_match = _PRODUCT_CARD_JSON_RE.search(text)
        if product_match:
            product = get_product_card_pool().find_by_product_id(product_match.group(1))
            if product is not None:
                price = product.display_price or product.price
                moq = f"{product.moq}{product.moq_unit}"
                return {
                    "id": product.card_id,
                    "title": product.title,
                    "type": "product",
                    "summary": f"{price} · MOQ {moq}",
                    "tags": [tag for tag in ("产品卡", product.product_id) if tag],
                    "coverTone": "#e6f4ff",
                    "details": [
                        {"label": "价格", "value": price},
                        {"label": "MOQ", "value": moq},
                        {"label": "商品 ID", "value": product.product_id},
                        {"label": "链接", "value": product.product_url},
                    ],
                }
    generic = get_generic_card_pool().get(card_type, card_id)
    if generic is None:
        return None
    payload = _parse_generic_payload(generic.raw_json)
    title = payload.get("title") or f"通用卡片 {generic.card_id}"
    return {
        "id": generic.card_id,
        "title": title,
        "type": "generic",
        "summary": payload.get("summary") or "通用运营卡片",
        "owner": payload.get("owner"),
        "tags": payload.get("tags") or ["通用卡", f"类型 {generic.card_type}"],
        "coverTone": "#f6ffed",
        "recommendedScenario": payload.get("scenario"),
        "details": [
            {"label": "卡片类型", "value": str(generic.card_type)},
            {"label": "来源", "value": generic.source_url},
        ],
    }


def _parse_generic_payload(raw_json: str) -> dict:
    try:
        value = json.loads(raw_json or "")
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    tags = value.get("tags")
    return {
        "title": value.get("title") or None,
        "summary": value.get("summary") or None,
        "owner": value.get("owner") or None,
        "scenario": value.get("scenario") or None,
        "tags": [tag for tag in tags if isinstance(tag, str) and tag.strip()] if isinstance(tags, list) else [],
    }
