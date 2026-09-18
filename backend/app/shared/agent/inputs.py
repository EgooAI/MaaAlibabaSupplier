"""Input builders for the system chat agents and the agent chat dialog."""

from __future__ import annotations

import html
import json
import re


def strip_html(text: str) -> str:
    if not text:
        return ""
    value = html.unescape(text)
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


# 输出协议正文（system_presets 的提示词与这里的用户输入规则共享同一份，避免两处漂移）。
TRANSLATION_PROTOCOL_RULES = (
    "只翻译【待翻译条目】中列出的 text_hash；"
    "对话记录中没有 text_hash 标记的行仅作上下文，不要翻译。",
    '输出 JSON：{"translations": {"<text_hash>": "<value>"}}，逐条对应，不要遗漏、不要新增。',
    "value 只能是以下三种之一：翻译后的简体中文文本；"
    '"NO_NEED_TO_TRANSLATE"：原文已经是简体中文，无需翻译；'
    '"ABNORMAL_MESSAGE"：原文是非常规消息（如纯占位符、乱码、无实义内容），无法翻译。',
    "译文必须保留原文中的 HTML 标签（如 <br>、<b>）、换行与空格格式，只翻译文本部分。",
)

# 稳定的输出协议说明（放在用户输入的稳定前缀区，配合 LLM 前缀缓存）。
_TRANSLATION_RULES = "翻译规则：\n" + "\n".join(f"- {line}" for line in TRANSLATION_PROTOCOL_RULES)


def build_translation_input(
    items: list[dict[str, str]],
    *,
    conversation: list[tuple[str, str, str]] | None = None,
    annotate: dict[str, str] | None = None,
) -> str:
    """Build translation user input ordered for LLM prompt prefix caching.

    Stable blocks come first (full transcript, output rules) and the volatile
    per-request target list goes last, so chunked calls of one job — and later
    jobs for the same conversation — share a long common prefix.

    *items* entries are ``{"text_hash", "text"}`` for the texts this call must
    translate. *conversation* is the ``(timestamp, speaker, text)`` transcript
    (raw text, HTML kept); lines whose text appears in *annotate* get a
    ``text_hash=`` marker (pending translation, any speaker), lines without the
    marker are context only — notably messages already translated.
    """
    if not items:
        return ""

    text_to_hash = dict(annotate or {})
    parts: list[str] = []

    if conversation:
        lines: list[str] = []
        for timestamp, speaker, text in conversation:
            line = (text or "").strip()
            if not line:
                continue
            short = text_to_hash.get(line)
            if short is not None:
                lines.append(f"[{timestamp}] {speaker}: text_hash={short}: {line}")
            else:
                lines.append(f"[{timestamp}] {speaker}: {line}")
        parts.append("对话记录：")
        parts.append("```")
        parts.append("\n".join(lines) if lines else "(empty)")
        parts.append("```")
        parts.append("")

    parts.append(_TRANSLATION_RULES)
    parts.append("")
    parts.append("待翻译条目：")
    parts.append("```json")
    parts.append(json.dumps({"items": items}, ensure_ascii=False))
    parts.append("```")
    parts.append("请只翻译以上列出的 text_hash。")
    return "\n".join(parts)


def format_conversation_transcript(conversation: list[tuple[str, str, str]]) -> str:
    lines = [
        f"[{timestamp}] {speaker}: {safe}"
        for timestamp, speaker, text in conversation
        if (safe := strip_html(text))
    ]
    return "\n".join(lines) or "(empty)"


def build_reply_suggestion_input(conversation: list[tuple[str, str, str]]) -> str:
    return f"对话记录：\n```\n{format_conversation_transcript(conversation)}\n```"


def build_analysis_input(*, task: str, conversation: list[tuple[str, str, str]]) -> str:
    return f"任务：{task}\n\n聊天记录：\n```\n{format_conversation_transcript(conversation)}\n```"


def build_chat_dialog_input(messages: list[dict[str, str]]) -> str:
    history = "\n".join(
        f"{'User' if message['role'] == 'user' else 'Agent'}: {message['content']}"
        for message in messages
    )
    return (
        "You are chatting with the user in a multi-turn dialog.\n"
        "Use the conversation history below as context, and answer the latest User message.\n\n"
        f"{history}"
    )


__all__ = [
    "TRANSLATION_PROTOCOL_RULES",
    "build_analysis_input",
    "build_chat_dialog_input",
    "build_reply_suggestion_input",
    "build_translation_input",
    "format_conversation_transcript",
    "strip_html",
]
