"""System chat agent identifiers and preset definitions.

系统 Agent 的 apid 与展示定义。预设数据（prompt）在 ``system_presets`` 中，
输出归一化在 ``output_normalizers`` 中，本模块只维护身份常量。
"""

from __future__ import annotations

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

__all__ = [
    "CHAT_CUSTOMER_INTENT_AGENT_APID",
    "CHAT_CUSTOMER_STAGE_AGENT_APID",
    "CHAT_REPLY_SUGGESTION_AGENT_APID",
    "CHAT_TRANSLATION_AGENT_APID",
    "SYSTEM_AGENT_APIDS",
    "SYSTEM_AGENT_DEFINITIONS",
]
