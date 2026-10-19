"""Session content for agent test/playground sessions.

Schema v3 turns each assistant message into a *turn record*: the text plus the
tool calls and reply intents that produced it. Older sessions simply carry no
tool fields, so they load with empty lists and keep working.

A session also records its owner (seller + data directory), the pinned version
it runs (saved revision snapshot or draft snapshot), and, for forked sessions,
where its frozen prefix came from.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = 3
SESSION_TYPES = frozenset({"test", "playground", "fork"})
PINNED_KINDS = frozenset({"preset", "draft"})


class ChatHistoryToolCall(BaseModel):
    callId: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    content: Any = None
    error: str | None = None
    durationMs: float = 0.0
    round: int = 0


class ChatHistoryIntent(BaseModel):
    intentId: str | None = None
    kind: str = "reply_customer"
    status: str = "requested"
    callId: str | None = None
    batchId: str = ""
    sequence: int = 0
    reply: str = ""
    zh: str = ""
    tone: str = "formal"
    conversationId: int | None = None


class ChatHistoryUsage(BaseModel):
    promptTokens: int | None = None
    completionTokens: int | None = None
    totalTokens: int | None = None


class ChatHistoryMessage(BaseModel):
    role: str
    content: str = ""
    toolCalls: list[ChatHistoryToolCall] = Field(default_factory=list)
    intents: list[ChatHistoryIntent] = Field(default_factory=list)
    usage: ChatHistoryUsage | None = None
    iterations: int = 0
    durationMs: float = 0.0
    restricted: bool = False
    mode: str = "live"
    batchId: str = ""


class ChatHistoryDraft(BaseModel):
    """未保存的草稿提示词快照。

    会话一旦带草稿快照，后续轮次按快照运行，保证历史可复核；想换提示词
    就开新会话。
    """

    prompt: str = ""
    llm_level: int = 0
    tools: list[str] = Field(default_factory=list)
    requires_tool: bool = False


class ChatHistoryPin(BaseModel):
    """The version a session is pinned to.

    ``revision`` is the saved preset snapshot taken when the session started;
    ``draft`` is the unsaved editor snapshot. Exactly one normally applies;
    ``kind`` says which one the session runs.
    """

    kind: Literal["preset", "draft"] = "preset"
    revision_id: int | None = None
    name: str = ""
    prompt: str = ""
    llm_level: int = 0
    tools: list[str] = Field(default_factory=list)
    requires_tool: bool = False


class ChatHistorySource(BaseModel):
    """Where a forked session's frozen prefix came from.

    ``frozen_input`` stores the fully projected input string, not a reference:
    later changes to the source messages must not change this simulation.
    """

    session_id: str | None = None
    message_index: int | None = None
    conversation_id: int | None = None
    conversation_title: str = ""
    frozen_input: str = ""
    created_at: float = 0.0


class ChatHistoryScope(BaseModel):
    seller: str = ""
    data_dir: str = ""


class ChatHistoryContent(BaseModel):
    schema_version: int = SCHEMA_VERSION
    apid: str
    agent_name: str = ""
    session_type: str = "test"
    scope: ChatHistoryScope | None = None
    pinned: ChatHistoryPin | None = None
    source: ChatHistorySource | None = None
    parent_id: str | None = None
    requires_tool: bool = False
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    draft: ChatHistoryDraft | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


def build_history_content(
    *,
    apid: str,
    agent_name: str,
    messages: list[dict[str, Any]],
    existing: dict[str, Any] | None = None,
    draft: ChatHistoryDraft | dict[str, Any] | None = None,
    session_type: str | None = None,
    scope: ChatHistoryScope | dict[str, Any] | None = None,
    pinned: ChatHistoryPin | dict[str, Any] | None = None,
    source: ChatHistorySource | dict[str, Any] | None = None,
    parent_id: str | None = None,
    requires_tool: bool | None = None,
) -> dict[str, Any]:
    now = time.time()
    existing = existing or {}
    existing_created = existing.get("created_at")
    try:
        created_at = float(existing_created) if existing_created else now
    except (TypeError, ValueError):
        created_at = now
    carried_draft = _load_draft(existing.get("draft"))
    resolved_draft = _load_draft(draft) if draft is not None else carried_draft
    content = ChatHistoryContent(
        apid=apid,
        agent_name=agent_name,
        session_type=session_type or str(existing.get("session_type") or "test"),
        scope=_load_model(ChatHistoryScope, scope) if scope is not None else _load_model(
            ChatHistoryScope, existing.get("scope")
        ),
        pinned=_load_model(ChatHistoryPin, pinned) if pinned is not None else _load_model(
            ChatHistoryPin, existing.get("pinned")
        ),
        source=_load_model(ChatHistorySource, source) if source is not None else _load_model(
            ChatHistorySource, existing.get("source")
        ),
        parent_id=parent_id if parent_id is not None else existing.get("parent_id"),
        requires_tool=bool(
            requires_tool if requires_tool is not None else existing.get("requires_tool", False)
        ),
        messages=[_message_payload(message) for message in messages],
        draft=resolved_draft,
        created_at=created_at,
        updated_at=now,
    )
    return content.model_dump()


def _message_payload(message: dict[str, Any]) -> dict[str, Any]:
    """Keep every turn field, so a save never drops tool records."""
    payload: dict[str, Any] = {
        "role": message.get("role", ""),
        "content": message.get("content", ""),
    }
    for key in ("toolCalls", "intents"):
        value = message.get(key)
        payload[key] = value if isinstance(value, list) else []
    usage = message.get("usage")
    payload["usage"] = usage if isinstance(usage, dict) else None
    payload["iterations"] = int(message.get("iterations") or 0)
    payload["durationMs"] = float(message.get("durationMs") or 0.0)
    payload["restricted"] = bool(message.get("restricted"))
    payload["mode"] = "simulated" if message.get("mode") == "simulated" or message.get("intentMode") == "simulated" else "live"
    payload["batchId"] = str(message.get("batchId") or "")
    return payload


def load_history_content(raw: Any) -> ChatHistoryContent:
    if not isinstance(raw, dict):
        return ChatHistoryContent(apid="")
    try:
        return ChatHistoryContent.model_validate(raw)
    except ValidationError:
        return ChatHistoryContent(apid="")


def history_messages(content: ChatHistoryContent) -> list[dict[str, Any]]:
    return [message.model_dump() for message in content.messages]


def assistant_turn_payload(turn: dict[str, Any]) -> dict[str, Any]:
    """Message payload for one finished agent turn."""
    payload = dict(turn)
    payload["role"] = "assistant"
    payload["content"] = str(payload.pop("text", "") or "")
    return _message_payload(payload)


def _load_draft(value: Any) -> ChatHistoryDraft | None:
    return _load_model(ChatHistoryDraft, value)


def _load_model(model, value: Any):
    if value is None:
        return None
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError:
        return None


__all__ = [
    "PINNED_KINDS",
    "SCHEMA_VERSION",
    "SESSION_TYPES",
    "ChatHistoryContent",
    "ChatHistoryDraft",
    "ChatHistoryIntent",
    "ChatHistoryMessage",
    "ChatHistoryPin",
    "ChatHistoryScope",
    "ChatHistorySource",
    "ChatHistoryToolCall",
    "ChatHistoryUsage",
    "assistant_turn_payload",
    "build_history_content",
    "history_messages",
    "load_history_content",
]
