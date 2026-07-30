from __future__ import annotations

import html
import json
import re

from app.shared.crm.sdk import load_sdk

# Keep in sync with app.crm_sdk.core.system_agents
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
    from agent_pipeline import AgentPipeline, AgentPipelineInput
    from agent_pipeline.llm import OpenAICompatibleLLMClient
    from agent_pipeline.llm_api import register_default_llms
    from agent_tools import register_builtin_tools
    from core import AgentPresetManager
    from utils import register_chat_result_tools

    llm_levels = register_default_llms()
    register_builtin_tools()
    register_chat_result_tools()

    manager = AgentPresetManager()
    preset = manager.get_agent_preset(apid)
    if preset is None:
        raise ValueError(f"AgentPreset {apid} not found")

    llm_config = llm_levels.get(int(preset.intelevel))
    if llm_config is None:
        raise ValueError(f"LLM level {preset.intelevel} is not configured")

    result = AgentPipeline(
        llm_client=OpenAICompatibleLLMClient(llm_config, timeout_seconds=timeout_seconds),
        manager=manager,
    ).run(AgentPipelineInput(user_input=user_input, apid=apid))
    return result.output_text


def build_translation_input(items: list[dict[str, str]]) -> str:
    return json.dumps({"items": items}, ensure_ascii=False)


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
