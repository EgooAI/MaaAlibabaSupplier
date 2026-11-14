"""Reply suggestions through the ``reply_customer`` effect tool.

The reply and its short rationale come from the successful effect tool call.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_pipeline.tools import ToolContext

from backend.app.shared.agent.inputs import build_reply_suggestion_input
from backend.app.shared.agent.reply_tool import ReplyScope
from backend.app.shared.agent.runner import ChatAgentTurn, run_chat_agent
from backend.app.shared.crm.sdk import AgentPreset

REPLY_OUTPUT_RULES = (
    "请通过 reply_customer 工具提出一条回复，并在 reasoning 中简短说明回复理由。"
    "reply 使用买家语言，zh 提供中文参考，tone 为 formal、friendly 或 urgent。"
    "不要填写收件人或账号。校验失败后可修正重试，成功后本次运行立即结束。"
)


class NoReplyIntentError(RuntimeError):
    """The agent finished without asking for a single reply."""


@dataclass(frozen=True)
class ReplySuggestion:
    reply: str
    zh: str
    tone: str

    def to_payload(self) -> dict:
        return {
            "content": self.reply,
            "tone": self.tone,
            "zh": self.zh,
        }


@dataclass(frozen=True)
class ReplySuggestionResult:
    reply: ReplySuggestion
    reasoning: str = ""
    turn: ChatAgentTurn | None = None

def generate_reply_suggestions(
    transcript: str,
    *,
    scope: ReplyScope,
    mode: str = "simulated",
    agent_id: str,
    preset_override: AgentPreset | None = None,
) -> ReplySuggestionResult:
    """Runs the suggestion agent and returns its single recorded reply."""
    turn = run_chat_agent(
        agent_id,
        build_reply_suggestion_input(transcript) + f"\n\n## 回复输出要求\n\n{REPLY_OUTPUT_RULES}",
        preset_override=preset_override,
        # The scope is host data: the model never sees it and cannot supply it.
        tool_context=ToolContext(mode=mode, scope=scope),
        tool_choice="required",
        mode=mode,
        conversation_id=scope.conversation_id,
        revision_id=scope.revision_id,
        purpose="reply_suggestions",
    )
    replies = [
        item
        for item in turn.intents
        if item.get("kind") == "reply_customer" and str(item.get("reply") or "").strip()
    ]
    if not replies:
        raise NoReplyIntentError(
            "Agent 没有调用回复客户工具，无法生成建议。请检查工具配置或恢复默认提示词。"
        )
    if len(replies) != 1:
        raise NoReplyIntentError("Agent 返回了多个回复，违反单条建议协议。")
    item, = replies
    return ReplySuggestionResult(
        reply=ReplySuggestion(
            reply=str(item["reply"]).strip(),
            zh=str(item.get("zh") or "").strip(),
            tone=str(item.get("tone") or "formal"),
        ),
        reasoning=str(item.get("reasoning") or "").strip(),
        turn=turn,
    )


__all__ = [
    "NoReplyIntentError",
    "ReplySuggestion",
    "ReplySuggestionResult",
    "generate_reply_suggestions",
]
