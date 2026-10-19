"""Reply suggestions through the ``reply_customer`` effect tool.

Each suggestion is an intent the Agent asked for, not text parsed out of a JSON
blob. The Agent's final text is only the reasoning shown to the merchant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_pipeline.tools import ToolContext

from backend.app.shared.agent.inputs import build_reply_suggestion_input
from backend.app.shared.agent.reply_tool import ReplyScope
from backend.app.shared.agent.runner import ChatAgentTurn, run_chat_agent
from backend.app.shared.agent.system_agents import CHAT_REPLY_SUGGESTION_AGENT_APID


class NoReplyIntentError(RuntimeError):
    """The agent finished without asking for a single reply."""


@dataclass(frozen=True)
class ReplySuggestion:
    intent_id: str | None
    reply: str
    zh: str
    tone: str
    status: str

    def to_payload(self, index: int) -> dict:
        title = (self.zh or self.reply).strip()
        return {
            "id": self.intent_id or f"sug-{index}",
            "intentId": self.intent_id,
            "title": title[:16] if len(title) > 16 else title,
            "content": self.reply,
            "tone": self.tone,
            "zh": self.zh,
            "status": self.status,
        }


@dataclass(frozen=True)
class ReplySuggestionResult:
    suggestions: list[ReplySuggestion] = field(default_factory=list)
    turn: ChatAgentTurn | None = None
    batch_id: str = ""

    @property
    def restricted(self) -> bool:
        return bool(self.turn and self.turn.restricted)

    @property
    def reasoning(self) -> str:
        return self.turn.text if self.turn is not None else ""


def generate_reply_suggestions(
    transcript: str,
    *,
    scope: ReplyScope,
    prior_replies: tuple[str, ...] = (),
    mode: str = "live",
) -> ReplySuggestionResult:
    """Run the suggestion agent and return the intents it recorded."""
    run_scope = ReplyScope(
        seller=scope.seller,
        data_dir=scope.data_dir,
        apid=scope.apid,
        agent_name=scope.agent_name,
        conversation_id=scope.conversation_id,
        revision_id=scope.revision_id,
        prior_replies=tuple(prior_replies) or scope.prior_replies,
    )
    turn = run_chat_agent(
        CHAT_REPLY_SUGGESTION_AGENT_APID,
        build_reply_suggestion_input(transcript),
        # The scope is host data: the model never sees it and cannot supply it.
        tool_context=ToolContext(mode=mode, scope=run_scope),
        tool_choice="required",
        mode=mode,
        conversation_id=scope.conversation_id,
        revision_id=scope.revision_id,
    )
    suggestions = [
        ReplySuggestion(
            intent_id=item.get("intentId"),
            reply=str(item.get("reply") or "").strip(),
            zh=str(item.get("zh") or "").strip(),
            tone=str(item.get("tone") or "formal"),
            status=str(item.get("status") or "requested"),
        )
        for item in turn.intents
        if str(item.get("reply") or "").strip()
    ]
    if not suggestions:
        raise NoReplyIntentError(
            "Agent 没有调用回复客户工具，无法生成建议。请检查工具配置或恢复默认提示词。"
        )
    return ReplySuggestionResult(suggestions=suggestions, turn=turn, batch_id=turn.batch_id)


__all__ = [
    "NoReplyIntentError",
    "ReplySuggestion",
    "ReplySuggestionResult",
    "generate_reply_suggestions",
]
