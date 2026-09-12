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


def build_translation_input(
    items: list[dict[str, str]],
    *,
    conversation: list[tuple[str, str, str]] | None = None,
) -> str:
    """Build translation user input with optional dialog context.

    *items* entries are ``{"text_hash", "text"}`` for buyer lines that must be translated.
    *conversation* is ``(timestamp, speaker, text)`` rows; buyer lines present in *items*
    are annotated with ``text_hash=...`` so the model can use seller/system context.
    """
    if not items:
        return ""

    text_to_hash = {item["text"]: item["text_hash"] for item in items}
    parts: list[str] = []

    if conversation:
        lines: list[str] = []
        for timestamp, speaker, text in conversation:
            safe = strip_html(text)
            if not safe:
                continue
            if speaker == "买家" and safe in text_to_hash:
                lines.append(f"[{timestamp}] 买家: text_hash={text_to_hash[safe]}: {safe}")
            else:
                lines.append(f"[{timestamp}] {speaker}: {safe}")
        parts.append("对话记录：")
        parts.append("```")
        parts.append("\n".join(lines) if lines else "(empty)")
        parts.append("```")
        parts.append("")

    hash_list = ", ".join(item["text_hash"] for item in items)
    parts.append(f"请翻译以下 text_hash：{hash_list}")
    parts.append("待翻译条目：")
    parts.append("```json")
    parts.append(json.dumps({"items": items}, ensure_ascii=False))
    parts.append("```")
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
    "build_analysis_input",
    "build_chat_dialog_input",
    "build_reply_suggestion_input",
    "build_translation_input",
    "format_conversation_transcript",
    "strip_html",
]
