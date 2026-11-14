"""The ``reply_customer`` effect tool: propose one customer reply.

The tool never sends anything and never chooses a recipient: the conversation
comes from the host run scope, which the model cannot see or supply. Every
successful call emits one ``reply_customer`` intent; the host dispatcher decides
whether it is persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any

from agent_pipeline.tools import CATEGORY_EFFECT, ToolContext, ToolSpec

TOOL_NAME = "reply_customer"
INTENT_KIND = "reply_customer"
TONE_VALUES = ("formal", "friendly", "urgent")
MAX_REPLY_LENGTH = 4000
MAX_REASONING_LENGTH = 500

_PLACEHOLDER_PATTERNS = (
    re.compile(r"\{\{[^{}]{1,80}\}\}"),
    re.compile(r"\[(?:name|product|price|company|买家名|产品名|价格|公司名)\]", re.IGNORECASE),
    re.compile(r"<(?:name|product|price|company)>", re.IGNORECASE),
)


@dataclass(frozen=True)
class ReplyScope:
    """Business context the dispatcher needs; the SDK never reads it."""

    seller: str
    data_dir: str
    apid: str
    agent_name: str
    conversation_id: int | None
    revision_id: int | None = None
    prior_replies: tuple[str, ...] = ()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def validate_reply(reply: str, *, prior_replies: tuple[str, ...]) -> str | None:
    """Return a rejection reason, or ``None`` when the reply may be recorded.

    Duplicates inside the current run are checked by :func:`reply_customer`
    against the context's own recordings.
    """
    text = (reply or "").strip()
    if not text:
        return "回复内容不能为空，请重新给出完整回复。"
    if len(text) > MAX_REPLY_LENGTH:
        return f"回复过长（超过 {MAX_REPLY_LENGTH} 字），请精简后重新调用。"
    for pattern in _PLACEHOLDER_PATTERNS:
        match = pattern.search(text)
        if match:
            return f"回复残留占位符 {match.group(0)}，请替换为真实内容后重新调用。"
    if _normalize(text) in {_normalize(item) for item in prior_replies}:
        return "这条回复与之前的回复重复，请给出不同措辞或补充新信息。"
    return None


def reply_customer(
    ctx: ToolContext,
    reply: Annotated[str, "用买家语言撰写的、可直接发送给买家的回复正文。"],
    zh: Annotated[str, "这条回复的简体中文参考译文，供商家核对。"],
    reasoning: Annotated[str, "简短说明买家关注点及回复依据，最多 500 字。"],
    tone: Annotated[str, "语气，只能是 formal（正式）、friendly（友好）或 urgent（催办）。"] = "formal",
) -> dict[str, Any]:
    """向当前会话的买家提出一条回复建议；只记录意图，不会自动发送。"""
    scope = ctx.scope if isinstance(ctx.scope, ReplyScope) else None
    if scope is None:
        raise ValueError("缺少会话上下文，无法记录回复。")

    normalized_tone = (tone or "formal").strip().lower()
    if normalized_tone not in TONE_VALUES:
        raise ValueError(f"tone 只能是 {'、'.join(TONE_VALUES)} 之一。")

    if ctx.recorded:
        raise ValueError("本次运行已经提出一条回复，请停止调用。")

    reason = validate_reply(reply, prior_replies=scope.prior_replies)
    if reason is not None:
        raise ValueError(reason)

    reference = (zh or "").strip() or (reply or "").strip()
    rationale = (reasoning or "").strip()
    if not rationale or len(rationale) > MAX_REASONING_LENGTH:
        raise ValueError(f"reasoning 必须是 1 至 {MAX_REASONING_LENGTH} 字的简短回复理由，请修正后重新调用。")

    ctx.emit(
        INTENT_KIND,
        {
            "reply": (reply or "").strip(),
            "zh": reference,
            "tone": normalized_tone,
            "reasoning": rationale,
            "scope": scope,
        },
        # Sequence numbers count the run, not the call or the round.
        sequence=ctx.next_sequence(),
    )
    ctx.recorded.append(dict(ctx.intents[-1].content))
    return {
        "recorded": True,
        "reasoning": rationale,
    }


TOOL_SPEC = ToolSpec(
    name=TOOL_NAME,
    title="回复客户",
    description=(
        "向当前会话的买家提出一条回复建议，并附简短 reasoning。每次运行最多成功一次，"
        "校验失败后可修正重试。回复只会等待商家确认，不会自动发送。"
    ),
    category=CATEGORY_EFFECT,
    repeatable=True,
)


__all__ = [
    "INTENT_KIND",
    "MAX_REASONING_LENGTH",
    "MAX_REPLY_LENGTH",
    "ReplyScope",
    "TOOL_NAME",
    "TOOL_SPEC",
    "TONE_VALUES",
    "reply_customer",
    "validate_reply",
]
