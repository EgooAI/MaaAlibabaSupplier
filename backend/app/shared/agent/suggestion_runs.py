"""Runs recoverable suggestion previews against pinned prompts and conversations."""

from __future__ import annotations

import time
from threading import Lock

from loguru import logger

from backend.app.api.envelope import AppError
from backend.app.background import get_background_runtime
from backend.app.shared.agent.chat_history_content import ChatHistoryPin
from backend.app.shared.agent.freezing import MAX_FROZEN_INPUT_CHARS
from backend.app.shared.agent.reply_tool import ReplyScope, TOOL_NAME
from backend.app.shared.agent.runner import AgentRunError
from backend.app.shared.agent.suggestions import NoReplyIntentError, generate_reply_suggestions
from backend.app.shared.agent.system_agents import SYSTEM_AGENT_APIDS
from backend.app.shared.agent.versions import (
    draft_matches_preset, parse_draft, pinned_from_draft, pinned_from_preset,
    pinned_to_preset, require_runnable_preset,
)
from backend.app.shared.backend.account_context import account_lock, bind_account_context, get_account_context
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.crm.intent_store import IntentStore
from backend.app.shared.crm.message_markdown import AGENT_OPTIONS, transcript_markdown
from backend.app.shared.crm.sdk import AgentPresetManager
from backend.app.shared.crm.suggestion_store import SuggestionStore
from backend.app.shared.crm.views import CrmResolver, format_created_at


def freeze_latest(conversation_id, run_id):
    from backend.app.api.routers.conversations import _inbox_store, _scoped_conversation
    from backend.app.shared.crm.sync import CRMAdapter

    store, context = _inbox_store()
    conv = _scoped_conversation(store, context, conversation_id, CRMAdapter())
    if conv is None or not conv.messages:
        raise AppError("会话不存在或尚无已同步消息", status_code=404)
    transcript = transcript_markdown(conv.messages, resolver=CrmResolver(context.self_ali_id), options=AGENT_OPTIONS, limit=None)
    if len(transcript) > MAX_FROZEN_INPUT_CHARS:
        raise AppError("会话内容超过建议上下文上限，请使用模拟会话选择测试起点。", status_code=422)
    last = conv.messages[-1]
    return transcript, {
        "id": run_id, "messageCount": len(conv.messages), "capturedAt": time.time(),
        "lastMessageId": message_external_id(context.self_ali_id, last.table_name, last.mid),
        "lastMessageAt": format_created_at(last.created_at),
    }


def public_run(record):
    snapshot = record["snapshot"]
    return {
        "id": record["id"], "conversationId": str(record["conversation_id"]),
        "agentId": snapshot["agentId"], "agentName": snapshot["pinned"]["name"],
        "status": record["status"], "createdAt": record["created_at"], "error": record["error"],
        "pinned": snapshot["pinned"], "context": snapshot["context"],
        "result": record["result"],
        "request": record["request"],
    }


class SuggestionRuns:
    def __init__(self, store=None, *, runtime=None, pool=None):
        self.store = store or SuggestionStore()
        self.runtime = runtime or get_background_runtime()
        self.pool = pool or self.runtime.pool("suggestion-runs", max_workers=2)
        self.lock = Lock()

    def require(self, context, conversation_id, run_id):
        record = self.store.get(context, conversation_id, run_id)
        if record is None:
            raise AppError("建议运行不存在", status_code=404)
        return record

    def submit(self, context, conversation_id, request):
        with self.lock:
            run_id = request["runId"]
            existing = self.store.get(context, conversation_id, run_id)
            if existing is not None:
                if existing["request"] != request:
                    raise AppError("同一次生成请求的内容不能改变", status_code=409)
                return existing
            preset = AgentPresetManager().get_agent_preset(request["agentId"])
            if preset is None:
                raise AppError("Agent 不存在", status_code=404)
            if preset.apid in SYSTEM_AGENT_APIDS or not preset.enabled or TOOL_NAME not in (preset.tools or []):
                raise AppError("请选择已启用且具备回复能力的普通 Agent", status_code=409)
            draft = parse_draft(**request["draft"]) if request.get("draft") is not None else None
            pinned = pinned_from_draft(draft) if draft and not draft_matches_preset(draft, preset) else pinned_from_preset(preset)
            pinned.name = preset.name
            effective = pinned_to_preset(preset, pinned)
            effective.enabled = preset.enabled
            require_runnable_preset(effective)
            if TOOL_NAME not in (effective.tools or []):
                raise AppError("请先为该 Agent 添加回复能力", status_code=409)
            previous = request.get("contextRunId")
            if previous:
                source = self.require(context, conversation_id, previous)["snapshot"]
                transcript, frozen_context = source["transcript"], source["context"]
            else:
                transcript, frozen_context = freeze_latest(conversation_id, run_id)
            snapshot = dict(agentId=preset.apid, pinned=pinned.model_dump(), transcript=transcript, context=frozen_context)
            record = self.store.create(context, conversation_id, run_id, request, snapshot)
            try:
                self.pool.submit(self.execute, context, record, effective)
            except Exception:
                logger.exception("Suggestion worker admission failed")
                self.store.finish(context, conversation_id, run_id, "failed", error="生成任务未能启动，请重新测试。")
                return self.require(context, conversation_id, run_id)
            return record

    def execute(self, context, record, preset):
        cid, run_id = record["conversation_id"], record["id"]
        try:
            with account_lock:
                if self.runtime.closing or get_account_context() != context:
                    raise AppError("账号已切换或程序正在停止，本次生成未执行。", status_code=409)
            self.store.finish(context, cid, run_id, "running")
            # Running work stays in its original workspace after an account switch.
            with bind_account_context(context):
                result = generate_reply_suggestions(
                    record["snapshot"]["transcript"], agent_id=preset.apid, preset_override=preset,
                    scope=ReplyScope(context.self_ali_id, context.data_dir, preset.apid, preset.name, cid),
                    mode="simulated",
                )
            payload = dict(items=[item.to_payload(index) for index, item in enumerate(result.suggestions)],
                           reasoning=result.reasoning, restricted=result.restricted,
                           toolCalls=result.turn.tool_calls if result.turn else [])
            self.store.finish(context, cid, run_id, "completed", result=payload)
        except Exception as exc:
            logger.exception("Suggestion preview failed")
            message = (exc.message if isinstance(exc, (AppError, AgentRunError)) else str(exc)
                       if isinstance(exc, NoReplyIntentError) else "建议生成失败，请检查模型配置后重试。")
            self.store.finish(context, cid, run_id, "failed", error=message)

    def adopt(self, context, conversation_id, run_id, index):
        record = self.require(context, conversation_id, run_id)
        if record["status"] != "completed":
            raise AppError("建议尚未生成完成", status_code=409)
        items = record["result"]["items"]
        if not 0 <= index < len(items):
            raise AppError("建议不存在", status_code=404)
        pinned = ChatHistoryPin.model_validate(record["snapshot"]["pinned"])
        records = IntentStore(self.store.database_path).adopt_suggestions(
            seller=context.self_ali_id, data_dir=context.data_dir, batch_id=run_id,
            apid=record["snapshot"]["agentId"], agent_name=pinned.name, revision_id=pinned.revision_id,
            conversation_id=conversation_id, items=items,
        )
        intent = records[index]
        if intent["status"] != "requested":
            raise AppError("这批建议已被采纳，请重新生成。", status_code=409)
        return {**items[index], "id": intent["id"], "intentId": intent["id"], "status": intent["status"]}


_service = None
_service_lock = Lock()


def get_suggestion_runs():
    global _service
    runtime = get_background_runtime()
    with _service_lock:
        if _service is None or _service.runtime is not runtime:
            _service = SuggestionRuns(runtime=runtime)
        return _service
