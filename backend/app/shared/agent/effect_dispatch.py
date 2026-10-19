"""Turn SDK effect intents into host state, or refuse to.

Only the execution policy differs between live and simulated runs: live runs
persist ``requested`` rows and simulated runs keep the intent inside the session
record. Nothing here ever queues a send.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_pipeline.types import EffectIntent

from backend.app.shared.agent.reply_tool import INTENT_KIND, ReplyScope
from backend.app.shared.crm.intent_store import (
    MODE_LIVE,
    MODE_SIMULATED,
    STATUS_REQUESTED,
    STATUS_SIMULATED,
    IntentStore,
)
from backend.app.shared.utils.log_context import log_event


@dataclass(frozen=True)
class DispatchedIntent:
    """One intent after the host interpreted it (live row or simulated record)."""

    kind: str
    status: str
    payload: dict[str, Any]
    intent_id: str | None = None
    call_id: str | None = None
    batch_id: str = ""
    sequence: int = 0

    def to_record(self) -> dict[str, Any]:
        return {
            "intentId": self.intent_id,
            "kind": self.kind,
            "status": self.status,
            "callId": self.call_id,
            "batchId": self.batch_id,
            "sequence": self.sequence,
            **self.payload,
        }


@dataclass
class DispatchResult:
    intents: list[DispatchedIntent] = field(default_factory=list)
    batch_id: str = ""


class IntentDispatcher:
    """Single place that decides "record / persist / deny" for effect intents."""

    def __init__(self, store: IntentStore | None = None) -> None:
        self.store = store

    def _require_scope(self, scope: Any) -> ReplyScope:
        if not isinstance(scope, ReplyScope):
            raise ValueError("效果工具缺少运行作用域，已拒绝落地。")
        if not scope.seller or not scope.data_dir:
            raise ValueError("效果工具缺少账号作用域，已拒绝落地。")
        return scope

    def dispatch(self, intents: list[EffectIntent], *, mode: str, batch_id: str) -> DispatchResult:
        result = DispatchResult(batch_id=batch_id)
        for intent in intents:
            if intent.kind != INTENT_KIND:
                # Unknown effect kinds are never executed silently.
                log_event("agent.intent_rejected", kind=str(intent.kind)[:40], reason="unsupported_kind")
                continue
            try:
                # The tool attaches its host scope to the intent payload; the
                # dispatcher validates it before persisting anything.
                scope = self._require_scope(intent.content.get("scope"))
                payload = self._reply_payload(intent, scope)
            except ValueError as exc:
                log_event("agent.intent_rejected", kind=intent.kind, reason=str(exc)[:120])
                continue

            if mode == MODE_SIMULATED:
                result.intents.append(DispatchedIntent(
                    kind=intent.kind, status=STATUS_SIMULATED, payload=payload,
                    call_id=intent.call_id, batch_id=batch_id, sequence=intent.sequence,
                ))
                log_event("agent.reply_simulated", batch_id=batch_id, sequence=intent.sequence)
                continue

            if mode != MODE_LIVE:
                log_event("agent.intent_rejected", kind=intent.kind, reason="unknown_mode")
                continue

            store = self.store or IntentStore()
            row = store.create(
                seller=scope.seller, data_dir=scope.data_dir, apid=scope.apid,
                agent_name=scope.agent_name, revision_id=scope.revision_id,
                conversation_id=scope.conversation_id, batch_id=batch_id,
                reply=payload["reply"], zh=payload["zh"], tone=payload["tone"],
                status=STATUS_REQUESTED, call_id=intent.call_id,
            )
            result.intents.append(DispatchedIntent(
                kind=intent.kind, status=STATUS_REQUESTED, payload=payload,
                intent_id=row["id"], call_id=intent.call_id, batch_id=batch_id,
                sequence=intent.sequence,
            ))
            log_event(
                "agent.reply_requested", batch_id=batch_id, intent_id=row["id"],
                sequence=intent.sequence, tone=payload["tone"],
            )
        return result

    @staticmethod
    def _scope_of(intent: EffectIntent) -> Any:
        return intent.content.get("scope")

    @staticmethod
    def _reply_payload(intent: EffectIntent, scope: ReplyScope) -> dict[str, Any]:
        reply = str(intent.content.get("reply") or "").strip()
        if not reply:
            raise ValueError("回复内容为空")
        return {
            "reply": reply,
            "zh": str(intent.content.get("zh") or "").strip() or reply,
            "tone": str(intent.content.get("tone") or "formal"),
            "conversationId": scope.conversation_id,
            "apid": scope.apid,
            "agentName": scope.agent_name,
        }


__all__ = ["DispatchResult", "DispatchedIntent", "IntentDispatcher"]
