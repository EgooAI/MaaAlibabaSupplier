from __future__ import annotations

import html
import json
import re

from app.shared.crm.sdk import load_sdk

CHAT_TRANSLATION_AGENT_APID = "agent-1bad27aabaac439da678f31d53855b5d"
CHAT_REPLY_SUGGESTION_AGENT_APID = "agent-5a43bda9e1304108a1a78a3575a44e27"
CHAT_CUSTOMER_STAGE_AGENT_APID = "agent-f6fb1e0ddff44d27bb3e19e243a70584"
CHAT_CUSTOMER_INTENT_AGENT_APID = "agent-c9b80fdfad234392b55d84de93a186ae"

SYSTEM_AGENT_APIDS = frozenset(
    {
        CHAT_TRANSLATION_AGENT_APID,
        CHAT_REPLY_SUGGESTION_AGENT_APID,
        CHAT_CUSTOMER_STAGE_AGENT_APID,
        CHAT_CUSTOMER_INTENT_AGENT_APID,
    }
)

SYSTEM_AGENT_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("翻译", CHAT_TRANSLATION_AGENT_APID, "Chat 页面买家消息翻译工具绑定的系统 Agent。"),
    ("建议", CHAT_REPLY_SUGGESTION_AGENT_APID, "Chat 页面 AI 回复建议工具绑定的系统 Agent。"),
    ("客户意图分析", CHAT_CUSTOMER_INTENT_AGENT_APID, "Chat 页面客户意图分析工具绑定的系统 Agent。"),
    ("客户所处阶段分析", CHAT_CUSTOMER_STAGE_AGENT_APID, "Chat 页面客户阶段分析工具绑定的系统 Agent。"),
)

_TRANSLATION_RULES = (
    "你是专业的阿里巴巴国际站供应商客服翻译。\n"
    "请将标记了 text_hash 的买家消息翻译为简体中文。\n"
    "商家与系统消息仅供理解上下文，不要翻译。\n"
    "如果某条买家消息已经是简体中文，translations 中对应 value 必须为 null。\n"
    "只输出 JSON 对象，格式："
    '{"translations":{"<text_hash>":"简体中文译文或null"}}。\n'
    "translations 必须覆盖下方全部待翻译 text_hash；不要输出解释性文字。"
)


def strip_html(text: str) -> str:
    if not text:
        return ""
    value = html.unescape(text)
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def format_conversation_transcript(conversation: list[tuple[str, str, str]]) -> str:
    lines = [
        f"[{timestamp}] {speaker}: {safe}"
        for timestamp, speaker, text in conversation
        if (safe := strip_html(text))
    ]
    return "\n".join(lines) or "(empty)"


def run_chat_tool_agent(apid: str, user_input: str, *, timeout_seconds: float = 60.0) -> str:
    load_sdk()
    from agent_pipeline import (
        AgentPipeline,
        AgentPipelineInput,
        AgentPipelineResultStatus,
    )
    from agent_pipeline.llm import OpenAICompatibleLLMClient
    from agent_pipeline.resolver import require_agent_preset_by_apid
    from core import AgentPresetManager

    manager = AgentPresetManager()
    runtime = require_agent_preset_by_apid(manager, apid)
    result = AgentPipeline(
        llm_client=OpenAICompatibleLLMClient(runtime.llm, timeout_seconds=timeout_seconds),
        manager=manager,
    ).run(AgentPipelineInput(user_input=user_input, apid=apid))
    if result.status is AgentPipelineResultStatus.CONTEXT_LIMITED:
        return "上下文超过限制"
    if result.status is AgentPipelineResultStatus.TOOL_ROUNDS_LIMITED:
        return "调用超过次数限制"
    return result.output_text


def build_translation_input(
    items: list[dict[str, str]],
    *,
    conversation: list[tuple[str, str, str]] | None = None,
) -> str:
    """Build translation user input with main-parity rules + optional dialog context.

    *items* entries are ``{"text_hash", "text"}`` for buyer lines that must be translated.
    *conversation* is ``(timestamp, speaker, text)`` rows; buyer lines present in *items*
    are annotated with ``text_hash=...`` so the model can use seller/system context.
    """
    if not items:
        return ""

    text_to_hash = {item["text"]: item["text_hash"] for item in items}
    parts: list[str] = [_TRANSLATION_RULES, ""]

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


def build_reply_suggestion_input(conversation: list[tuple[str, str, str]]) -> str:
    return f"对话记录：\n```\n{format_conversation_transcript(conversation)}\n```"


def build_analysis_input(*, task: str, conversation: list[tuple[str, str, str]]) -> str:
    return f"任务：{task}\n\n聊天记录：\n```\n{format_conversation_transcript(conversation)}\n```"


__all__ = [
    "CHAT_CUSTOMER_INTENT_AGENT_APID",
    "CHAT_CUSTOMER_STAGE_AGENT_APID",
    "CHAT_REPLY_SUGGESTION_AGENT_APID",
    "CHAT_TRANSLATION_AGENT_APID",
    "SYSTEM_AGENT_APIDS",
    "SYSTEM_AGENT_DEFINITIONS",
    "build_analysis_input",
    "build_reply_suggestion_input",
    "build_translation_input",
    "format_conversation_transcript",
    "run_chat_tool_agent",
    "strip_html",
]
