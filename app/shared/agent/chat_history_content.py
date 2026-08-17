from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = 1


class ChatHistoryMessage(BaseModel):
    role: str
    content: str


class ChatHistoryContent(BaseModel):
    schema_version: int = SCHEMA_VERSION
    apid: str
    agent_name: str = ""
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def build_history_content(
    *,
    apid: str,
    agent_name: str,
    messages: list[dict[str, str]],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    existing = existing or {}
    existing_created = existing.get("created_at")
    try:
        created_at = float(existing_created) if existing_created else now
    except (TypeError, ValueError):
        created_at = now
    content = ChatHistoryContent(
        apid=apid,
        agent_name=agent_name,
        messages=[{"role": m.get("role", ""), "content": m.get("content", "")} for m in messages],
        created_at=created_at,
        updated_at=now,
    )
    return content.to_dict()


def load_history_content(raw: Any) -> ChatHistoryContent:
    if not isinstance(raw, dict):
        return ChatHistoryContent(apid="")
    try:
        return ChatHistoryContent.model_validate(raw)
    except ValidationError:
        return ChatHistoryContent(apid="")


def history_messages(content: ChatHistoryContent) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in content.messages]


__all__ = [
    "SCHEMA_VERSION",
    "ChatHistoryContent",
    "ChatHistoryMessage",
    "build_history_content",
    "load_history_content",
    "history_messages",
]