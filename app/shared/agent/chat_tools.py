from __future__ import annotations

import json
import re

from app.shared.crm.sdk import load_sdk

CHAT_TRANSLATION_AGENT_APID = "agent-1bad27aabaac439da678f31d53855b5d"
CHAT_REPLY_SUGGESTION_AGENT_APID = "agent-5a43bda9e1304108a1a78a3575a44e27"
CHAT_CUSTOMER_STAGE_AGENT_APID = "agent-f6fb1e0ddff44d27bb3e19e243a70584"
CHAT_CUSTOMER_INTENT_AGENT_APID = "agent-c9b80fdfad234392b55d84de93a186ae"


def strip_html(text: str) -> str:
    import html

    if not text:
        return ""
    value = html.unescape(text)
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def run_chat_tool_agent(apid: str, user_input: str) -> str:
    load_sdk()
    from agent_pipeline import AgentPipeline, AgentPipelineInput
    from agent_pipeline.llm import OpenAICompatibleLLMClient
    from agent_pipeline.llm_api import register_default_llms
    from agent_tools import register_builtin_tools
    from core import AgentPresetManager
    from utils import register_chat_result_tools

    register_default_llms()
    register_builtin_tools()
    register_chat_result_tools()

    manager = AgentPresetManager()
    preset = manager.get_agent_preset(apid)
    if preset is None:
        raise ValueError(f"AgentPreset {apid} not found")

    llm_levels = register_default_llms()
    llm_config = llm_levels.get(int(preset.intelevel))
    if llm_config is None:
        raise ValueError(f"LLM level {preset.intelevel} is not configured")

    result = AgentPipeline(
        llm_client=OpenAICompatibleLLMClient(llm_config, timeout_seconds=60.0),
        manager=manager,
    ).run(AgentPipelineInput(user_input=user_input, apid=apid))
    return result.output_text


def build_translation_input(items: list[dict[str, str]]) -> str:
    return json.dumps({"items": items}, ensure_ascii=False)


def build_reply_suggestion_input(conversation: list[tuple[str, str, str]]) -> str:
    lines: list[str] = []
    for timestamp, speaker, text in conversation:
        safe_text = strip_html(text)
        if safe_text:
            lines.append(f"[{timestamp}] {speaker}: {safe_text}")

    transcript = "\n".join(lines).strip() or "(empty)"
    return f"对话记录：\n```\n{transcript}\n```"


def build_analysis_input(
    *,
    task: str,
    conversation: list[tuple[str, str, str]],
) -> str:
    lines: list[str] = []
    for timestamp, speaker, text in conversation:
        safe_text = strip_html(text)
        if safe_text:
            lines.append(f"[{timestamp}] {speaker}: {safe_text}")
    transcript = "\n".join(lines).strip() or "(empty)"
    return f"任务：{task}\n\n" "聊天记录：\n" "```\n" f"{transcript}\n" "```"


__all__ = [
    "CHAT_CUSTOMER_INTENT_AGENT_APID",
    "CHAT_CUSTOMER_STAGE_AGENT_APID",
    "CHAT_REPLY_SUGGESTION_AGENT_APID",
    "CHAT_TRANSLATION_AGENT_APID",
    "build_analysis_input",
    "build_reply_suggestion_input",
    "build_translation_input",
    "run_chat_tool_agent",
]
