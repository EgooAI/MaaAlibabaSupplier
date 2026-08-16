"""System agent preset data and startup seeding.

系统 Agent 预设属于主仓库业务层：数据预制在本模块中，每次启动调用
:func:`ensure_system_agents_seeded` 覆盖写入 CRM SDK 数据库。SDK 仅提供
通用的 AgentPreset 存储与解析能力，不承载任何业务数据。
"""

from __future__ import annotations

from loguru import logger

from app.shared.agent.chat_tools import (
    CHAT_CUSTOMER_INTENT_AGENT_APID,
    CHAT_CUSTOMER_STAGE_AGENT_APID,
    CHAT_REPLY_SUGGESTION_AGENT_APID,
    CHAT_TRANSLATION_AGENT_APID,
    SYSTEM_AGENT_DEFINITIONS,
)
from app.shared.crm.sdk import load_sdk

_SYSTEM_AGENT_PROMPTS: dict[str, str] = {
    CHAT_TRANSLATION_AGENT_APID: (
        "你是聊天消息翻译助手。\n\n"
        "请把用户输入 JSON 中每个 items[].text 翻译成简体中文。\n\n"
        "输出要求：\n"
        "1) 只输出 JSON，不要输出解释性文字。\n"
        "2) 输出格式必须是：{\"translations\": {\"<text_hash>\": \"<translation or null>\"}}。\n"
        "3) 如果某条文本已经是简体中文，对应 text_hash 返回 null。\n"
        "4) 不要遗漏任何 text_hash。\n"
        "5) 不要编造原文不存在的信息。"
    ),
    CHAT_REPLY_SUGGESTION_AGENT_APID: (
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
        "Return JSON only with this exact top-level shape:\n"
        '{"buyer_language": "English", "items": [{"zh": "中文建议", "reply": "buyer language reply"}]}\n'
        "Do not use top-level keys such as suggestions, replies, or reply_suggestions."
    ),
    CHAT_CUSTOMER_INTENT_AGENT_APID: (
        "你是客户意图分析助手。\n\n"
        "请基于用户提供的【任务】和【聊天记录】分析客户采购意图、关注点和下一步动作。"
        "结论必须来自聊天内容，不要编造未出现的信息。\n\n"
        "输出要求：\n"
        "1) 输出中文。\n"
        "2) 结构清晰，重点给出可执行建议。\n"
        "3) 优先输出 JSON：{\"intent\": \"客户意图\", \"evidence\": [\"依据\"], "
        "\"concerns\": [\"顾虑\"], \"next_actions\": [\"下一步建议\"]}。\n"
        "4) 如果信息不足，请明确说明缺少哪些判断依据。"
    ),
    CHAT_CUSTOMER_STAGE_AGENT_APID: (
        "你是客户阶段分析助手。\n\n"
        "请基于用户提供的【任务】和【聊天记录】分析客户当前所处阶段。"
        "结论必须来自聊天内容，不要编造未出现的信息。\n\n"
        "输出要求：\n"
        "1) 输出中文。\n"
        "2) 结构清晰，重点给出可执行建议。\n"
        "3) 优先输出 JSON：{\"stage\": \"客户阶段\", \"evidence\": [\"依据\"], "
        "\"next_actions\": [\"下一步建议\"], \"confidence\": \"置信度\"}。\n"
        "4) 如果信息不足，请明确说明缺少哪些判断依据。"
    ),
}


def system_agent_presets() -> list[dict[str, object]]:
    """Build the system agent preset payloads from program-defined data."""
    definitions = {
        apid: (name, description)
        for name, apid, description in SYSTEM_AGENT_DEFINITIONS
    }
    presets: list[dict[str, object]] = []
    for apid, prompt in _SYSTEM_AGENT_PROMPTS.items():
        name, description = definitions[apid]
        presets.append(
            {
                "apid": apid,
                "name": name,
                "description": description,
                "prompt": prompt,
                "intelevel": 0,
                "tools": [],
            }
        )
    return presets


def ensure_system_agents_seeded() -> None:
    """Upsert all system agent presets, overwriting any drifted records.

    每次启动调用，幂等覆盖：预设数据始终与程序内置版本保持一致。
    """
    sdk = load_sdk()
    from core import AgentPresetManager

    manager = AgentPresetManager()
    try:
        for payload in system_agent_presets():
            manager.upsert_agent_preset(sdk["AgentPreset"](**payload))
        logger.info("Seeded {} system agent presets into {}", len(_SYSTEM_AGENT_PROMPTS), manager.database_path)
    finally:
        manager.engine.dispose()


__all__ = ["ensure_system_agents_seeded", "system_agent_presets"]
