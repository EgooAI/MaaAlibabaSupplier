from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = 2


class ChatHistoryMessage(BaseModel):
    role: str
    content: str


class ChatHistoryDraft(BaseModel):
    """未保存的草稿提示词快照。

    会话一旦带草稿快照，后续轮次按快照运行，保证历史可复核；想换提示词
    就开新会话。
    """

    prompt: str = ""
    llm_level: int = 0
    tools: list[str] = Field(default_factory=list)


class ChatHistoryContent(BaseModel):
    schema_version: int = SCHEMA_VERSION
    apid: str
    agent_name: str = ""
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    draft: ChatHistoryDraft | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


def build_history_content(
    *,
    apid: str,
    agent_name: str,
    messages: list[dict[str, str]],
    existing: dict[str, Any] | None = None,
    draft: ChatHistoryDraft | dict[str, Any] | None = None,
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
        messages=[{"role": m.get("role", ""), "content": m.get("content", "")} for m in messages],
        draft=resolved_draft,
        created_at=created_at,
        updated_at=now,
    )
    return content.model_dump()


def load_history_content(raw: Any) -> ChatHistoryContent:
    if not isinstance(raw, dict):
        return ChatHistoryContent(apid="")
    try:
        return ChatHistoryContent.model_validate(raw)
    except ValidationError:
        return ChatHistoryContent(apid="")


def history_messages(content: ChatHistoryContent) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in content.messages]


def _load_draft(value: Any) -> ChatHistoryDraft | None:
    if value is None:
        return None
    try:
        return ChatHistoryDraft.model_validate(value)
    except ValidationError:
        return None


__all__ = [
    "SCHEMA_VERSION",
    "ChatHistoryContent",
    "ChatHistoryDraft",
    "ChatHistoryMessage",
    "build_history_content",
    "history_messages",
    "load_history_content",
]
