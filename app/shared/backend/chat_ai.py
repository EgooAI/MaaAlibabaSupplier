from __future__ import annotations

from pydantic import BaseModel, Field

from app.shared.llm.structured_llm import StructuredLLM


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
    )


def generate_reply_suggestions(conversation: list[tuple[str, str, str]]) -> ReplySuggestions:
    llm = StructuredLLM(timeout=60.0)
    return llm.process(build_inquiry_prompt(conversation), ReplySuggestions)
