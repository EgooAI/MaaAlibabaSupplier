"""Freeze a real conversation into the text an agent run would have received.

The playground fork and the experiment builder both need the same guarantee:
the first simulated turn must see byte-identical input to the live run, and
later edits to the source conversation must not rewrite it. The projection is
therefore materialized once, as a string, and stored with the session.
"""

from __future__ import annotations

from typing import Any

from backend.app.api.envelope import AppError

MAX_FROZEN_INPUT_CHARS = 60_000


def conversation_title(conv: Any) -> str:
    """The best human-readable label a conversation offers."""
    for attr in ("title", "contact_ali_id"):
        value = getattr(conv, attr, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(getattr(conv, "sid", "") or "")


def freeze_conversation_message(conversation_id: int, message_index: int) -> tuple[str, dict]:
    """Freeze the conversation up to and including one message.

    Returns the projected transcript and the provenance record to store beside
    it (``conversationId``, ``messageIndex``, ``conversationTitle``,
    ``frozenChars``). The projection is the live agent projection
    (``AGENT_OPTIONS``), so a run that starts from it is indistinguishable from
    the live one until the simulation departs.

    Raises :class:`AppError` when the conversation or message is not visible to
    the current account, or when the projection exceeds the frozen prefix cap.
    """
    from backend.app.api.routers.conversations import _inbox_store, _scoped_conversation
    from backend.app.shared.crm.message_markdown import AGENT_OPTIONS, transcript_markdown
    from backend.app.shared.crm.sync import CRMAdapter
    from backend.app.shared.crm.views import CrmResolver

    store, context = _inbox_store()
    conv = _scoped_conversation(store, context, conversation_id, CRMAdapter())
    if conv is None:
        raise AppError("会话不存在", status_code=404)
    if message_index >= len(conv.messages):
        raise AppError("消息不存在", status_code=404)
    frozen = transcript_markdown(
        conv.messages[: message_index + 1],
        resolver=CrmResolver(context.self_ali_id),
        options=AGENT_OPTIONS,
        limit=None,
    )
    if len(frozen) > MAX_FROZEN_INPUT_CHARS:
        raise AppError(
            f"从该消息开始需要冻结 {len(frozen)} 字的上下文，超过 {MAX_FROZEN_INPUT_CHARS} 字上限；请选择更早的消息。",
            status_code=422,
        )
    return frozen, {
        "conversationId": conversation_id,
        "messageIndex": message_index,
        "conversationTitle": conversation_title(conv),
        "frozenChars": len(frozen),
    }


__all__ = [
    "MAX_FROZEN_INPUT_CHARS",
    "conversation_title",
    "freeze_conversation_message",
]
