from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

def strip_html(text: str) -> str:
    import html
    import re

    if not text:
        return ""
    value = html.unescape(text)
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


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


def build_inquiry_prompt(conversation: list[tuple[str, str, str]]) -> str:
    from app.shared.agent.chat_tools import build_reply_suggestion_input

    return build_reply_suggestion_input(conversation)


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def _load_reply_suggestions(raw_text: str) -> ReplySuggestions:
    payload = json.loads(_strip_json_fence(raw_text))
    if not isinstance(payload, dict):
        raise ValueError("reply suggestion agent output must be a JSON object")

    if "items" not in payload:
        for fallback_key in ("suggestions", "replies", "reply_suggestions"):
            fallback_value = payload.get(fallback_key)
            if isinstance(fallback_value, list):
                payload["items"] = fallback_value
                break

    raw_items = payload.get("items")
    if isinstance(raw_items, list):
        normalized_items: list[dict[str, Any]] = []
        for item in raw_items[:3]:
            if not isinstance(item, dict):
                continue
            normalized_items.append(
                {
                    "zh": str(item.get("zh") or item.get("cn") or item.get("chinese") or "").strip(),
                    "reply": str(
                        item.get("reply")
                        or item.get("buyer_reply")
                        or item.get("message")
                        or item.get("text")
                        or ""
                    ).strip(),
                }
            )
        payload["items"] = normalized_items

    return ReplySuggestions.model_validate(payload)


def generate_reply_suggestions(conversation: list[tuple[str, str, str]]) -> ReplySuggestions:
    from app.shared.agent.chat_tools import (
        CHAT_REPLY_SUGGESTION_AGENT_APID,
        build_reply_suggestion_input,
        run_chat_tool_agent,
    )

    raw_text = run_chat_tool_agent(
        CHAT_REPLY_SUGGESTION_AGENT_APID,
        build_reply_suggestion_input(conversation),
    )
    return _load_reply_suggestions(raw_text)
