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
    zh: str = Field(description="中文版回复建议。")
    reply: str = Field(description="买家语言版回复建议，直接可发送给买家。")


class ReplySuggestions(BaseModel):
    buyer_language: str = Field(description="买家使用的主要语言，如 English, Español, Русский, العربية 等。如果买家同时使用多种语言或无法确定，填写 \"mixed\"。")
    items: list[SuggestionItem] = Field(description="回复建议列表，按推荐顺序，最多3条。每条包含中文版和买家语言版。")


class TranslationResult(BaseModel):
    translations: dict[str, str | None] = Field(
        description='翻译结果。key 为消息编号（如 "msg1"），value 为简体中文翻译。'
        "如果消息本身就是简体中文，value 为 null。"
    )


def build_inquiry_prompt(conversation: list[tuple[str, str, str]]) -> str:
    lines: list[str] = []
    for timestamp, speaker, text in conversation:
        safe_text = strip_html(text)
        if safe_text:
            lines.append(f"[{timestamp}] {speaker}: {safe_text}")

    transcript = "\n".join(lines).strip() or "(empty)"
    return (
        "你是一名阿里巴巴国际站的供应商销售（商家）客服，正在处理买家的询盘对话。\n"
        "请根据【对话记录】生成可直接发送给买家的回复建议。\n\n"
        "输出要求：\n"
        "1) 只输出 JSON，字段见下方 Fields。\n"
        "2) 先判断买家使用的主要语言（buyer_language），然后为每条建议同时给出中文(zh)版本和买家语言(reply)版本。\n"
        "3) reply 字段必须使用买家在对话中使用的语言，不要默认翻译为英文。如果买家使用西班牙语就写西班牙语，俄语就写俄语，以此类推。\n"
        "4) 最多给出 3 条建议，按推荐顺序排列。\n"
        "5) 语气专业、友好、简洁；优先推进成交（确认需求、报价条件、交期、物流、付款等）。\n"
        "6) 不要编造任何你无法从对话中确定的信息（如具体价格/库存/认证等）。若信息不足，请用提问补齐。\n"
        "7) 不要提及你是 AI，也不要输出任何解释性文字。\n\n"
        "对话记录：\n"
        "```\n"
        f"{transcript}\n"
        "```\n"
    )


def build_translation_prompt(conversation: list[tuple[str, str, str | None]]) -> str:
    context_lines: list[str] = []
    to_translate: list[str] = []
    counter = 0

    for timestamp, speaker, text in conversation:
        if text is None:
            continue
        if speaker == "买家":
            counter += 1
            msg_id = f"msg{counter}"
            context_lines.append(f"[{timestamp}] 买家: {msg_id}: {text}")
            to_translate.append(msg_id)
        else:
            context_lines.append(f"[{timestamp}] 商家(我): {text}")

    if not to_translate:
        return ""

    transcript = "\n".join(context_lines)
    msg_list = ", ".join(to_translate)
    return (
        "你是专业的阿里巴巴国际站供应商客服翻译。\n"
        "请将以下对话中编号的买家消息翻译为简体中文。\n"
        "如果某条消息已经是简体中文，返回 null。\n"
        "对话中的商家消息仅供参考，不需要翻译。\n\n"
        "对话记录：\n"
        "```\n"
        f"{transcript}\n"
        "```\n\n"
        f"请翻译: {msg_list}\n"
    )


def generate_reply_suggestions(conversation: list[tuple[str, str, str]]) -> ReplySuggestions:
    llm = StructuredLLM(timeout=60.0)
    return llm.process(build_inquiry_prompt(conversation), ReplySuggestions)


def translate_buyer_messages(conversation: list[tuple[str, str, str | None]]) -> TranslationResult | None:
    prompt = build_translation_prompt(conversation)
    if not prompt:
        return None
    llm = StructuredLLM(timeout=60.0)
    return llm.process(prompt, TranslationResult)
