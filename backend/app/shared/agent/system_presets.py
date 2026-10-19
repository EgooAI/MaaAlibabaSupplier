"""System agent preset data and startup seeding.

系统 Agent 预设属于主仓库业务层：数据预制在本模块中，启动时调用
:func:`ensure_system_agents_seeded` 写入 CRM SDK 数据库。SDK 仅提供通用的
AgentPreset 存储与解析能力，不承载任何业务数据。

种子策略（不覆盖用户编辑，同时保留默认升级通道）：

- 记录缺失：写入内置默认并记录默认指纹；
- 记录内容仍等于上一次内置默认（指纹匹配）：视为未修改，更新为最新默认；
- 其余情况视为用户已修改：启动保持不动，只有显式「恢复默认」才重置。

指纹记录在 SDK ``meta`` 表的 ``system_agent_seed:<apid>`` 键中，属于宿主业务
状态，不污染 AgentPreset 本身。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from backend.app.shared.agent.preset_history import record_preset_revision
from backend.app.shared.agent.inputs import TRANSLATION_PROTOCOL_RULES
from backend.app.shared.agent.system_agents import (
    CHAT_CUSTOMER_INTENT_AGENT_APID,
    CHAT_CUSTOMER_STAGE_AGENT_APID,
    CHAT_REPLY_SUGGESTION_AGENT_APID,
    CHAT_TRANSLATION_AGENT_APID,
    SYSTEM_AGENT_DEFINITIONS,
)
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager, Meta, MetaManager
from backend.app.shared.utils.log_context import log_event

SEED_META_PREFIX = "system_agent_seed"

_SYSTEM_AGENT_PROMPTS: dict[str, str] = {
    CHAT_TRANSLATION_AGENT_APID: (
        "你是跨境电商询盘对话的聊天消息翻译引擎，把外文消息翻译成简体中文。\n\n"
        "用户输入会依次提供【对话记录】、【翻译规则】和【待翻译条目】。对话记录是完整历史"
        "（任意一方：买家、卖家、自动接待系统），其中带 text_hash= 标记的行处于待翻译状态；"
        "没有标记的行仅作上下文（例如已翻译过），不要翻译它们。\n\n"
        "输出要求：\n"
        "1) 只输出 JSON，不要输出解释性文字。\n"
        + "".join(
            f"{index}) {rule}\n"
            for index, rule in enumerate(TRANSLATION_PROTOCOL_RULES, start=2)
        )
        + f"{len(TRANSLATION_PROTOCOL_RULES) + 2}) 结合对话上下文理解指代与行业术语，但不要编造原文不存在的信息。"
    ),
    CHAT_REPLY_SUGGESTION_AGENT_APID: (
        "你是一名阿里巴巴国际站供应商客服，正在处理买家的询盘对话。\n"
        "请根据【对话记录】判断买家语言与当前沟通阶段，并通过调用 reply_customer 工具提出回复建议。\n\n"
        "输出要求：\n"
        "1) 每条建议都必须单独调用一次 reply_customer，参数包含买家语言回复 reply、中文参考 zh 和语气 tone。\n"
        "2) reply 必须使用买家在对话中使用的语言，不要默认翻译为英文；zh 是该回复的简体中文参考。\n"
        "3) tone 只能是 formal（正式）、friendly（友好）或 urgent（催办）之一。\n"
        "4) 最多提出 3 条建议，按推荐顺序排列；超过 3 条或与已有建议重复会被拒绝。\n"
        "5) 语气专业、友好、简洁，优先推进成交。\n"
        "6) 不要编造任何无法从对话中确定的信息；信息不足时用提问补齐。\n"
        "7) 不要在工具参数里填写收件人、会话或账号，这些由系统提供。\n"
        "8) 工具调用完成后，最终文本只写明判断理由（例如买家关注点、为什么这样回复），不要重复回复正文。"
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


_SYSTEM_AGENT_TOOLS: dict[str, list[str]] = {
    CHAT_REPLY_SUGGESTION_AGENT_APID: ["reply_customer"],
}
# Agents that must call a tool to produce their result (seeded into the preset).
_SYSTEM_AGENT_REQUIRES_TOOL: dict[str, bool] = {
    CHAT_REPLY_SUGGESTION_AGENT_APID: True,
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
                "llm_level": 0,
                "tools": list(_SYSTEM_AGENT_TOOLS.get(apid, [])),
                "requires_tool": _SYSTEM_AGENT_REQUIRES_TOOL.get(apid, False),
            }
        )
    return presets


def seed_meta_key(apid: str) -> str:
    return f"{SEED_META_PREFIX}:{apid}"


def preset_seed_fingerprint(payload: Mapping[str, object]) -> str:
    """Fingerprint the fields a seed may overwrite.

    Password-independent content hash: a stored fingerprint marks which preset
    state was last written by a built-in seed.
    """
    canonical = json.dumps(
        {
            "name": str(payload.get("name") or ""),
            "description": str(payload.get("description") or ""),
            "prompt": str(payload.get("prompt") or ""),
            "llm_level": int(payload.get("llm_level") or 0),
            "tools": list(payload.get("tools") or []),
            "requires_tool": bool(payload.get("requires_tool") or False),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def preset_fingerprint_of(preset: AgentPreset) -> str:
    return preset_seed_fingerprint(
        {
            "name": preset.name,
            "description": preset.description,
            "prompt": preset.prompt,
            "llm_level": preset.llm_level,
            "tools": list(preset.tools or []),
            "requires_tool": bool(getattr(preset, "requires_tool", False)),
        }
    )


def find_system_agent_default(apid: str) -> dict[str, object] | None:
    return next(
        (payload for payload in system_agent_presets() if payload.get("apid") == apid),
        None,
    )


def apply_system_agent_default(apid: str, *, source: str = "restore_default") -> AgentPreset | None:
    """Write one built-in default and refresh its seed fingerprint."""
    payload = find_system_agent_default(apid)
    if payload is None:
        return None
    return _write_default(payload, AgentPresetManager(), MetaManager(), source=source)


def ensure_system_agents_seeded() -> None:
    """Seed system agent presets without overwriting user edits.

    幂等：只有记录缺失、记录仍是上一次内置默认（指纹匹配）时才写入；
    用户改过的记录保持不动。Failures propagate and abort startup.
    """
    manager = AgentPresetManager()
    meta = MetaManager()
    written = 0
    for payload in system_agent_presets():
        apid = str(payload["apid"])
        default_fingerprint = preset_seed_fingerprint(payload)
        current = manager.get_agent_preset(apid)
        if current is None:
            _write_default(payload, manager, meta, source="seed")
            written += 1
            continue
        recorded = meta.get_meta(seed_meta_key(apid))
        recorded_value = recorded.value if recorded else ""
        row_fingerprint = preset_fingerprint_of(current)
        if row_fingerprint == default_fingerprint:
            # Pristine row (legacy upgrade or already current): only refresh fingerprint.
            if recorded_value != default_fingerprint:
                meta.upsert_meta(Meta(key=seed_meta_key(apid), value=default_fingerprint))
            continue
        if recorded_value and recorded_value == row_fingerprint:
            # Untouched older built-in default: deliver the updated default.
            _write_default(payload, manager, meta, source="seed")
            written += 1
    if written:
        log_event("config.seeded", count=written, entry="system_agent")


def _write_default(
    payload: Mapping[str, object],
    manager: AgentPresetManager,
    meta: MetaManager,
    *,
    source: str,
) -> AgentPreset:
    preset = AgentPreset(
        apid=str(payload["apid"]),
        name=str(payload["name"]),
        description=str(payload["description"]),
        prompt=str(payload["prompt"]),
        llm_level=int(payload["llm_level"]),
        tools=list(payload["tools"] or []),
        enabled=True,
        requires_tool=bool(payload.get("requires_tool") or False),
    )
    manager.upsert_agent_preset(preset)
    meta.upsert_meta(Meta(
        key=seed_meta_key(preset.apid),
        value=preset_seed_fingerprint(payload),
    ))
    record_preset_revision(preset, source=source)
    return preset


__all__ = [
    "SEED_META_PREFIX",
    "apply_system_agent_default",
    "ensure_system_agents_seeded",
    "find_system_agent_default",
    "preset_fingerprint_of",
    "preset_seed_fingerprint",
    "seed_meta_key",
    "system_agent_presets",
]
