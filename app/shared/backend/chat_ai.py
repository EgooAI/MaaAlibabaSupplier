from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from app.shared.crm.sdk import load_sdk


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
    lines: list[str] = []
    for timestamp, speaker, text in conversation:
        safe_text = strip_html(text)
        if safe_text:
            lines.append(f"[{timestamp}] {speaker}: {safe_text}")

    transcript = "\n".join(lines).strip() or "(empty)"
    return (
        "你是一名阿里巴巴国际站供应商客服，正在处理买家的询盘对话。\n"
        "请根据【对话记录】生成可直接发送给买家的回复建议。\n\n"
        "输出要求：\n"
        "1) 只输出 JSON，字段见 schema。\n"
        "2) 先判断买家主要语言 buyer_language，然后为每条建议同时给出中文 zh 和买家语言 reply。\n"
        "3) reply 必须使用买家在对话中使用的语言，不要默认翻译为英文。\n"
        "4) 最多给出 3 条建议，按推荐顺序排列。\n"
        "5) 语气专业、友好、简洁，优先推进成交。\n"
        "6) 不要编造任何无法从对话中确定的信息；信息不足时用提问补齐。\n"
        "7) 不要提及你是 AI，也不要输出解释性文字。\n\n"
        "对话记录：\n"
        "```\n"
        f"{transcript}\n"
        "```\n"
        "\nReturn JSON only with this exact top-level shape:\n"
        "{\"buyer_language\": \"English\", \"items\": [{\"zh\": \"中文建议\", \"reply\": \"buyer language reply\"}]}\n"
        "Do not use top-level keys such as suggestions, replies, or reply_suggestions.\n"
    )


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
    sdk = load_sdk()
    from agent_pipeline import AgentPipeline, AgentPipelineInput
    from agent_pipeline.llm import OpenAICompatibleLLMClient
    from agent_pipeline.llm_api import register_default_llms
    from core import llm_registry

    register_default_llms()
    client = OpenAICompatibleLLMClient(llm_registry.require(0), timeout_seconds=60.0)
    preset = sdk["AgentPreset"](
        apid="builtin-reply-suggestion-agent",
        name="Built-in Reply Suggestion Agent",
        description="Generate reply suggestions for Alibaba supplier chat.",
        prompt=(
            "You are a reply suggestion agent for Alibaba supplier chat. "
            "Generate concise, professional reply suggestions. "
            "You must output JSON only. "
            "The top-level JSON keys must be exactly buyer_language and items; do not use suggestions."
        ),
        intelevel=0,
        tools=[],
    )
    result = AgentPipeline(llm_client=client).run(
        AgentPipelineInput(
            user_input=build_inquiry_prompt(conversation),
            agent_preset=preset,
        )
    )
    return _load_reply_suggestions(result.output_text)
