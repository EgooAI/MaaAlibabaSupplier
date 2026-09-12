from __future__ import annotations

import json

from pydantic import BaseModel, Field

from backend.app.shared.agent.inputs import build_reply_suggestion_input
from backend.app.shared.agent.runner import run_chat_tool_agent
from backend.app.shared.agent.system_agents import CHAT_REPLY_SUGGESTION_AGENT_APID


class SuggestionItem(BaseModel):
    zh: str = Field(description="中文版本的回复建议。")
    reply: str = Field(description="买家语言版本的回复建议，可直接发送给买家。")


class ReplySuggestions(BaseModel):
    buyer_language: str = Field(
        description=(
            "买家使用的主要语言，例如 English, Español, Русский, العربية 等。"
            "如果买家同时使用多种语言或无法确定，填写 mixed。"
        )
    )
    items: list[SuggestionItem] = Field(description="最多 3 条回复建议，按推荐顺序排列。")


def generate_reply_suggestions(conversation: list[tuple[str, str, str]]) -> ReplySuggestions:
    raw_text = run_chat_tool_agent(
        CHAT_REPLY_SUGGESTION_AGENT_APID,
        build_reply_suggestion_input(conversation),
    )
    return ReplySuggestions.model_validate(json.loads(raw_text))
