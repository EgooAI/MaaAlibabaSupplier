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

    register_default_llms()
    register_builtin_tools()

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
    return json.dumps(
        {
            "task": "translate_buyer_messages_to_simplified_chinese",
            "instructions": [
                "Translate each item.text into Simplified Chinese.",
                "Return JSON only: {\"translations\": {\"<text_hash>\": \"<translation or null>\"}}.",
                "If an item is already Simplified Chinese, return null for that text_hash.",
                "Do not omit any text_hash.",
            ],
            "items": items,
        },
        ensure_ascii=False,
    )


def build_reply_suggestion_input(conversation: list[tuple[str, str, str]]) -> str:
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
    return (
        f"任务：{task}\n\n"
        "请基于下面的聊天记录进行分析，结论必须来自聊天内容，不要编造未出现的信息。\n"
        "输出中文，结构清晰，重点给出可执行建议。\n\n"
        "聊天记录：\n"
        "```\n"
        f"{transcript}\n"
        "```"
    )


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
