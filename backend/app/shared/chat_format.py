"""Shared chat formatting helpers for the HTTP API (no UI dependencies)."""

from __future__ import annotations

from typing import Iterable

from backend.app.shared.crm.views import (
    CrmMessage,
    CrmResolver,
    format_created_at,
    message_display_text,
)


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
