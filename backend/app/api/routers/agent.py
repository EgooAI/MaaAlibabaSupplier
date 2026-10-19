from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from agent_pipeline.tools import ToolContext

from backend.app.api.envelope import AppError, api_error, ok
from backend.app.shared.agent.chat_history_content import (
    SCHEMA_VERSION,
    ChatHistoryContent,
    ChatHistoryDraft,
    ChatHistoryPin,
    ChatHistoryScope,
    ChatHistorySource,
    assistant_turn_payload,
    build_history_content,
    history_messages,
    load_history_content,
)
from backend.app.shared.agent.inputs import build_chat_dialog_input
from backend.app.shared.agent.preset_history import (
    MAX_REVISIONS_PER_PRESET,
    latest_revision,
    list_preset_revisions as load_preset_revisions,
    normalize_revision_source,
    record_preset_revision,
)
from backend.app.shared.agent.reply_tool import ReplyScope
from backend.app.shared.agent.runner import ChatAgentTurn, run_chat_agent
from backend.app.shared.crm.intent_store import (
    MODE_SIMULATED,
    STATUS_REQUESTED,
    IntentStore,
    summary_lines,
)
from backend.app.shared.utils.log_context import log_event
from backend.app.shared.agent.system_agents import SYSTEM_AGENT_APIDS, SYSTEM_AGENT_DEFINITIONS
from backend.app.shared.agent.system_presets import (
    apply_system_agent_default,
    find_system_agent_default,
    system_agent_presets,
)
from backend.app.shared.agent.tools import list_registered_tools, unknown_tool_names
from backend.app.shared.crm.sdk import (
    AgentPreset,
    AgentPresetManager,
    AgentPresetRevision,
    ChatHistory,
    ChatHistoryManager,
    LLMApiConfig,
    LLMApiConfigManager,
    LLM_MAX_LEVEL,
)
from backend.app.shared.crm.views import format_created_at

router = APIRouter()

MAX_FROZEN_INPUT_CHARS = 60_000


class LlmConfigInput(BaseModel):
    level: int = 0
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    system_prompt: str = ""
    context: int = 12000
    max_tool_rounds: int | None = None


class PresetInput(BaseModel):
    apid: str = ""
    name: str = ""
    description: str = ""
    prompt: str = ""
    intelevel: int = 0
    tools: list[str] = []
    enabled: bool = True
    requires_tool: bool = False
    updated_at: str = ""
    category: str | None = None
    revision_source: str = "manual"
    revision_note: str = ""


class DraftPresetInput(BaseModel):
    prompt: str = ""
    level: int = 0
    tools: list[str] = []
    requires_tool: bool | None = None


class AgentTestInput(BaseModel):
    agentId: str = ""
    content: str = ""
    sessionId: str | None = None
    draft: DraftPresetInput | None = None
    # ``buyer`` appends the content as a buyer message; ``agent`` runs the agent
    # on the current context without appending anything.
    inputMode: str = "buyer"


class ForkMessageInput(BaseModel):
    agentId: str = ""
    conversationId: int = Field(ge=1)
    messageIndex: int = Field(ge=0)
    # ``preset`` pins the saved preset (optionally a revision); ``draft`` pins an
    # unsaved editor snapshot.
    versionKind: str = "preset"
    revisionId: int | None = None
    draft: DraftPresetInput | None = None


class BranchInput(BaseModel):
    # Truncate the copy after this message; omitted copies the whole session.
    messageIndex: int | None = Field(default=None, ge=0)


def _refresh_llm_runtime() -> None:
    try:
        from agent_pipeline.llm_api import register_default_llms
    except ImportError:
        log_event("agent.registration_skipped", reason="llm_api_unavailable")
        return
    try:
        register_default_llms()
    except Exception:
        logger.exception("LLM runtime registration failed")


def _db_preset_to_dict(preset: AgentPreset, *, updated_at: str = "") -> dict:
    category = "system" if preset.apid in SYSTEM_AGENT_APIDS else "regular"
    return {
        "apid": preset.apid,
        "name": preset.name,
        "description": preset.description,
        "prompt": preset.prompt,
        "intelevel": preset.llm_level,
        "tools": list(preset.tools or []),
        "enabled": preset.enabled,
        "requires_tool": bool(getattr(preset, "requires_tool", False)),
        "updated_at": updated_at,
        "category": category,
    }


def _revision_to_dict(revision: AgentPresetRevision) -> dict:
    return {
        "id": revision.id,
        "apid": revision.apid,
        "name": revision.name,
        "description": revision.description,
        "prompt": revision.prompt,
        "intelevel": revision.llm_level,
        "tools": list(revision.tools or []),
        "requires_tool": bool(getattr(revision, "requires_tool", False)),
        "source": revision.source,
        "note": revision.note,
        "created_at": format_created_at(revision.created_time) or "",
    }


def _updated_at_text(apid: str) -> str:
    revision = latest_revision(apid)
    return format_created_at(revision.created_time) if revision is not None else ""


# ---------------------------------------------------------------------------
# Session ownership


def _current_scope() -> ChatHistoryScope:
    from backend.app.shared.backend.account_context import get_account_context

    context = get_account_context()
    return ChatHistoryScope(seller=context.self_ali_id, data_dir=context.data_dir)


def _scope_allows(content: ChatHistoryContent) -> bool:
    """Sessions without a scope belong to the current account (legacy behaviour).

    A scoped session is only visible to the seller + data directory that owns it.
    """
    scope = content.scope
    if scope is None or (not scope.seller and not scope.data_dir):
        return True
    current = _current_scope()
    if not current.seller or not current.data_dir:
        return False
    if scope.seller and scope.seller != current.seller:
        return False
    if scope.data_dir and _normalize_dir(scope.data_dir) != _normalize_dir(current.data_dir):
        return False
    return True


def _normalize_dir(value: str) -> str:
    from pathlib import Path

    try:
        return str(Path(value).expanduser().resolve()).casefold()
    except (OSError, RuntimeError):
        return value.strip().casefold()


def _adopt_scope(content: ChatHistoryContent) -> bool:
    """Attach the current scope to an unscoped legacy session; returns changed."""
    if content.scope is not None and (content.scope.seller or content.scope.data_dir):
        return False
    content.scope = _current_scope()
    return True


# ---------------------------------------------------------------------------
# Session projection


def _session_to_dict(hist: ChatHistory) -> dict | None:
    content = load_history_content(hist.content)
    if not content.apid or hist.id is None:
        return None
    if not _scope_allows(content):
        return None
    created = format_created_at(content.created_at) or ""
    messages = [
        {
            "id": f"{hist.id}-{index}",
            **_message_to_payload(message, created),
        }
        for index, message in enumerate(content.messages)
    ]
    return {
        "id": str(hist.id),
        "title": hist.name,
        "agentId": content.apid,
        "draft": content.draft is not None or (
            content.pinned is not None and content.pinned.kind == "draft"
        ),
        "createdAt": created,
        "type": content.session_type,
        "parentId": content.parent_id,
        "requiresTool": content.requires_tool,
        "pinned": content.pinned.model_dump() if content.pinned is not None else None,
        "source": _public_source(content.source),
        "messages": messages,
    }


def _public_source(source: ChatHistorySource | None) -> dict | None:
    if source is None:
        return None
    return {
        "sessionId": source.session_id,
        "messageIndex": source.message_index,
        "conversationId": source.conversation_id,
        "conversationTitle": source.conversation_title,
        "createdAt": source.created_at,
        "frozenChars": len(source.frozen_input),
    }


def _message_to_payload(message: Any, created: str) -> dict[str, Any]:
    data = message if isinstance(message, dict) else message.model_dump()
    role = data.get("role", "user")
    payload = {
        "role": role if role in ("user", "assistant") else "user",
        "content": data.get("content") or "",
        "createdAt": created,
        "toolCalls": list(data.get("toolCalls") or []),
        "intents": list(data.get("intents") or []),
        "iterations": int(data.get("iterations") or 0),
        "durationMs": float(data.get("durationMs") or 0.0),
        "restricted": bool(data.get("restricted")),
        # Older turn payloads warned the policy as ``intentMode``.
        "mode": "simulated" if data.get("mode") == "simulated" or data.get("intentMode") == "simulated" else "live",
    }
    usage = data.get("usage")
    if isinstance(usage, dict):
        payload["usage"] = usage
    return payload


def _load_session(manager: ChatHistoryManager, session_id: str) -> ChatHistory:
    try:
        raw_id = int(session_id)
    except (TypeError, ValueError):
        raise AppError("会话不存在", status_code=404)
    hist = manager.get_chat_history(raw_id)
    if hist is None:
        raise AppError("会话不存在", status_code=404)
    if _session_to_dict(hist) is None:
        raise AppError("会话不存在", status_code=404)
    return hist


def _draft_override(preset: AgentPreset, draft: ChatHistoryDraft) -> AgentPreset:
    """Build the unsaved draft preset used to run one draft test session."""
    return AgentPreset(
        apid=preset.apid,
        name=preset.name,
        description=preset.description,
        prompt=draft.prompt,
        llm_level=draft.llm_level,
        tools=list(draft.tools),
        enabled=True,
        requires_tool=bool(draft.requires_tool),
    )


def _draft_matches_preset(draft: ChatHistoryDraft, preset: AgentPreset) -> bool:
    return (
        draft.prompt == preset.prompt
        and draft.llm_level == preset.llm_level
        and list(draft.tools or []) == list(preset.tools or [])
        and bool(draft.requires_tool) == bool(getattr(preset, "requires_tool", False))
    )


def _require_runnable_preset(preset: AgentPreset) -> None:
    """System agents are integral and always runnable; regular agents honor 停用."""
    if preset.apid not in SYSTEM_AGENT_APIDS and not preset.enabled:
        raise AppError("Agent 已停用，请先启用后再运行", status_code=409)
    unknown = unknown_tool_names(preset.tools or [])
    if unknown:
        raise AppError(
            f"该 Agent 挂载了未注册的工具：{'、'.join(unknown)}。请先在编辑器中移除后再运行。",
            status_code=409,
        )
    if bool(getattr(preset, "requires_tool", False)) and not (preset.tools or []):
        raise AppError(
            "该 Agent 要求必须调用工具，但没有挂载任何工具。请添加工具或关闭「必须调用工具」。",
            status_code=409,
        )


# ---------------------------------------------------------------------------
# Console


@router.get("/api/agent/system")
def list_system_agents() -> dict:
    return ok(_system_agent_definition_payloads())


def _system_agent_definition_payloads() -> list[dict]:
    """System agent identity plus its current built-in default payload。

    ``prompt`` 等字段是内置默认（不是用户当前值），供编辑器做“查看默认/对比”；
    用户当前值来自 ``/api/agent/console``。
    """
    defaults = {str(item["apid"]): item for item in system_agent_presets()}
    payloads: list[dict] = []
    for name, apid, desc in SYSTEM_AGENT_DEFINITIONS:
        default = defaults.get(apid, {})
        payloads.append({
            "display_name": name,
            "apid": apid,
            "description": desc,
            "prompt": str(default.get("prompt") or ""),
            "intelevel": int(default.get("llm_level") or 0),
            "tools": list(default.get("tools") or []),
            "requires_tool": bool(default.get("requires_tool") or False),
        })
    return payloads


def _llm_level_payloads() -> list[dict]:
    """The complete LLM level grid, including levels without a saved row.

    只回传数据库已有的行会让“只配了一个模型”的实例看起来只有一个等级，而
    LLM 页没有新增入口，其余等级便无从配置。这里为缺失的等级合成空白模板行
    （沿用列默认值），用户保存时才真正落库。
    """
    configs = {config.level: config for config in LLMApiConfigManager().list_configs()}
    payloads: list[dict] = []
    for level in range(LLM_MAX_LEVEL + 1):
        config = configs.get(level) or LLMApiConfig(level=level, base_url="", api_key="", model_name="")
        payloads.append({
            "level": config.level,
            "base_url": config.base_url,
            "api_key": config.api_key,
            "model_name": config.model_name,
            "system_prompt": config.system_prompt,
            "context": config.context,
            "max_tool_rounds": config.max_tool_rounds,
            # 兼容旧前端 camel 键。
            "baseUrl": config.base_url,
            "apiKey": config.api_key,
            "modelName": config.model_name,
            "systemPrompt": config.system_prompt,
            "maxToolRounds": config.max_tool_rounds,
        })
    return payloads


@router.get("/api/agent/console")
def agent_console() -> dict:
    try:
        levels = _llm_level_payloads()
        presets = AgentPresetManager().list_agent_preset()
        updated_at_by_apid = {preset.apid: _updated_at_text(preset.apid) for preset in presets}
        db_presets = [
            _db_preset_to_dict(preset, updated_at=updated_at_by_apid[preset.apid])
            for preset in presets
        ]
        agents = [
            {
                "id": preset.apid,
                "name": preset.name,
                "category": "system" if preset.apid in SYSTEM_AGENT_APIDS else "regular",
                "enabled": preset.enabled,
                "capabilities": list(preset.tools or []),
                "description": preset.description,
                "updatedAt": updated_at_by_apid[preset.apid],
                "prompt": preset.prompt,
                "level": preset.llm_level,
                "apid": preset.apid,
                "requiresTool": bool(getattr(preset, "requires_tool", False)),
            }
            for preset in presets
        ]
        history_manager = ChatHistoryManager()
        history = []
        for hist in history_manager.list_chat_history():
            session = _session_to_dict(hist)
            if session is not None:
                history.append(session)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok({
        "llmLevels": levels,
        "agents": agents,
        "agentPresets": db_presets,
        "systemAgents": _system_agent_definition_payloads(),
        "history": history,
    })


@router.get("/api/agent/tools")
def agent_tools() -> dict:
    """Tool catalog for the editor: name, 中文名, category, description, parameters."""
    try:
        return ok(list_registered_tools())
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)


@router.put("/api/agent/llm-config")
def save_llm_config(body: LlmConfigInput) -> dict:
    base_url = (body.base_url or "").strip()
    api_key = (body.api_key or "").strip()
    model_name = (body.model_name or "").strip()
    if not base_url:
        raise AppError("服务地址不能为空", status_code=422)
    if not api_key:
        raise AppError("API Key 不能为空", status_code=422)
    if not model_name:
        raise AppError("模型名称不能为空", status_code=422)
    try:
        config = LLMApiConfig(
            level=int(body.level),
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            system_prompt=body.system_prompt,
            context=body.context,
            max_tool_rounds=body.max_tool_rounds,
        )
        LLMApiConfigManager().upsert_config(config)
        saved = LLMApiConfigManager().get_config(config.level)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    _refresh_llm_runtime()
    if saved is None:
        raise AppError("LLM 配置保存失败", status_code=500)
    return ok({
        "level": saved.level,
        "base_url": saved.base_url,
        "api_key": saved.api_key,
        "model_name": saved.model_name,
        "system_prompt": saved.system_prompt,
        "context": saved.context,
        "max_tool_rounds": saved.max_tool_rounds,
    })


@router.post("/api/agent/presets")
def save_preset(body: PresetInput) -> dict:
    apid = (body.apid or "").strip() or f"agent-{uuid.uuid4().hex}"
    try:
        level = max(0, min(LLM_MAX_LEVEL, int(body.intelevel)))
    except (TypeError, ValueError):
        raise AppError(f"intelevel must be 0-{LLM_MAX_LEVEL}", status_code=422)
    tools = [tool for tool in (body.tools or []) if isinstance(tool, str) and tool.strip()]
    unknown = unknown_tool_names(tools)
    if unknown:
        raise AppError(
            f"未注册的工具：{'、'.join(unknown)}。请只使用工具清单中的已实现工具。",
            status_code=422,
        )
    if body.requires_tool and not tools:
        raise AppError("要求必须调用工具时，至少需要挂载一个工具。", status_code=422)
    # 系统 Agent 恒启用：其开关已从界面移除，运行路径也不检查启用状态。
    enabled = True if apid in SYSTEM_AGENT_APIDS else bool(body.enabled)
    preset = AgentPreset(
        apid=apid,
        name=body.name,
        description=body.description,
        prompt=body.prompt,
        llm_level=level,
        tools=tools,
        enabled=enabled,
        requires_tool=bool(body.requires_tool),
    )
    try:
        AgentPresetManager().upsert_agent_preset(preset)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    record_preset_revision(
        preset,
        source=normalize_revision_source(body.revision_source),
        note=body.revision_note,
    )
    return ok(_db_preset_to_dict(preset, updated_at=_updated_at_text(apid)))


@router.get("/api/agent/presets/{preset_id}/revisions")
def list_preset_revisions(preset_id: str, limit: int = MAX_REVISIONS_PER_PRESET) -> dict:
    if not 1 <= limit <= MAX_REVISIONS_PER_PRESET:
        raise AppError(f"limit must be 1-{MAX_REVISIONS_PER_PRESET}", status_code=422)
    try:
        revisions = load_preset_revisions(preset_id, limit=limit)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok([_revision_to_dict(revision) for revision in revisions])


@router.delete("/api/agent/presets/{preset_id}")
def delete_preset(preset_id: str) -> dict:
    if preset_id in SYSTEM_AGENT_APIDS:
        return ok(False)
    try:
        AgentPresetManager().delete_agent_preset(preset_id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(True)


@router.post("/api/agent/system/{apid}/restore")
def restore_system_agent(apid: str) -> dict:
    if find_system_agent_default(apid) is None:
        raise AppError("系统 Agent 默认配置不存在", status_code=404)
    try:
        preset = apply_system_agent_default(apid)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    if preset is None:
        raise AppError("系统 Agent 默认配置不存在", status_code=404)
    return ok(_db_preset_to_dict(preset, updated_at=_updated_at_text(apid)))


# ---------------------------------------------------------------------------
# Running sessions


def _pinned_from_preset(preset: AgentPreset) -> ChatHistoryPin:
    return ChatHistoryPin(
        kind="preset",
        revision_id=None,
        name=preset.name,
        prompt=preset.prompt,
        llm_level=preset.llm_level,
        tools=list(preset.tools or []),
        requires_tool=bool(getattr(preset, "requires_tool", False)),
    )


def _pinned_from_draft(draft: ChatHistoryDraft) -> ChatHistoryPin:
    return ChatHistoryPin(
        kind="draft",
        name="",
        prompt=draft.prompt,
        llm_level=draft.llm_level,
        tools=list(draft.tools),
        requires_tool=bool(draft.requires_tool),
    )


def _pin_to_draft(pin: ChatHistoryPin | None) -> ChatHistoryDraft | None:
    if pin is None or pin.kind != "draft":
        return None
    return ChatHistoryDraft(
        prompt=pin.prompt, llm_level=pin.llm_level, tools=list(pin.tools),
        requires_tool=bool(pin.requires_tool),
    )


def _run_turn(
    *,
    apid: str,
    preset: AgentPreset,
    override: AgentPreset | None,
    messages: list[dict[str, Any]],
    frozen: str,
    mode: str,
    conversation_id: int | None,
    revision_id: int | None,
    prior_replies: tuple[str, ...],
) -> ChatAgentTurn:
    """Run one turn; forked sessions send the frozen prefix as the input body."""
    from backend.app.shared.backend.account_context import get_account_context

    context = get_account_context()
    scope = ReplyScope(
        seller=context.self_ali_id,
        data_dir=context.data_dir,
        apid=apid,
        agent_name=preset.name,
        conversation_id=conversation_id,
        revision_id=revision_id,
        prior_replies=prior_replies,
    )
    if frozen:
        # Forked sessions keep the prefix byte-identical. The frozen string already
        # ends with the source message, so the first run sends exactly that input;
        # later runs append only the turns created inside this session.
        user_input = _frozen_input(
            apid, conversation_id, frozen, messages,
        )
    else:
        user_input = build_chat_dialog_input(messages)
    return run_chat_agent(
        apid,
        user_input,
        preset_override=override,
        tool_context=ToolContext(mode=mode, scope=scope),
        mode=mode,
        conversation_id=conversation_id,
        revision_id=revision_id,
    )


def _frozen_input(
    apid: str, conversation_id: int | None, frozen: str, messages: list[dict[str, Any]],
) -> str:
    """Input for a forked session: the frozen live projection, then new turns."""
    from backend.app.shared.agent.inputs import build_reply_suggestion_input
    from backend.app.shared.agent.system_agents import CHAT_REPLY_SUGGESTION_AGENT_APID

    body = frozen if not messages else f"{frozen}\n\n{_new_turns_input(messages)}"
    if apid == CHAT_REPLY_SUGGESTION_AGENT_APID:
        # Same wrapper as the live suggestion endpoint, so the first forked turn
        # receives a byte-identical string.
        return build_reply_suggestion_input(body)
    return body


def _new_turns_input(messages: list[dict[str, Any]]) -> str:
    """Dialog formatting for turns created inside a frozen session.

    The frozen prefix is the live projection; this only appends what happened
    afterwards, without repeating the multi-turn setup wording.
    """
    history = "\n\n".join(
        f"**{'User' if message['role'] == 'user' else 'Agent'}**\n\n{(message.get('content') or '').strip()}"
        for message in messages
    )
    return f"## 继续对话\n\n{history}"


def _history_summaries(
    conversation_id: int | None, *, seller: str, data_dir: str,
) -> tuple[str, ...]:
    if conversation_id is None or not seller or not data_dir:
        return ()
    intents = IntentStore().list_conversation(
        conversation_id, seller=seller, data_dir=data_dir, limit=6,
    )
    return tuple(line[2:] for line in summary_lines(intents, limit=5) if line.startswith("- "))


def _turn_message(turn: ChatAgentTurn) -> dict[str, Any]:
    return assistant_turn_payload(turn.to_dict())


@router.post("/api/agent/test")
def run_agent_test(body: AgentTestInput) -> dict:
    agent_id = (body.agentId or "").strip()
    content = (body.content or "").strip()
    if not agent_id:
        raise AppError("agentId is required", status_code=422)
    if body.inputMode not in ("buyer", "agent"):
        raise AppError("inputMode must be buyer or agent", status_code=422)
    manager = AgentPresetManager()
    try:
        preset = manager.get_agent_preset(agent_id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    if preset is None:
        raise AppError("Agent 不存在", status_code=404)
    if preset.apid not in SYSTEM_AGENT_APIDS:
        _require_runnable_preset(preset)

    history_manager = ChatHistoryManager()
    hist: ChatHistory | None = None
    raw: dict[str, Any] = {}
    content_model = ChatHistoryContent(apid="")
    if body.sessionId:
        hist = _load_session(history_manager, body.sessionId)
        raw = hist.content if isinstance(hist.content, dict) else {}
        content_model = load_history_content(raw)
        if content_model.apid != agent_id:
            raise AppError("会话不属于该 Agent", status_code=400)

    if not content and body.inputMode == "buyer":
        if hist is None:
            raise AppError("content is required", status_code=422)
        session = _session_to_dict(hist)
        if session is None:
            raise AppError("会话不存在", status_code=404)
        return ok({"session": session, "reply": None})

    # A session keeps the version it started with; changing versions means a new session.
    override: AgentPreset | None = None
    draft_snapshot: ChatHistoryDraft | None = None
    pinned = content_model.pinned
    if hist is None and body.draft is not None:
        try:
            draft_level = max(0, min(LLM_MAX_LEVEL, int(body.draft.level)))
        except (TypeError, ValueError):
            raise AppError(f"draft.level 必须是 0 到 {LLM_MAX_LEVEL} 的整数", status_code=422)
        candidate = ChatHistoryDraft(
            prompt=body.draft.prompt,
            llm_level=draft_level,
            tools=[tool for tool in (body.draft.tools or []) if isinstance(tool, str) and tool.strip()],
            requires_tool=bool(body.draft.requires_tool),
        )
        if _draft_matches_preset(candidate, preset):
            # 草稿与已保存配置一致：按已保存运行记录，不标记草稿。
            draft_snapshot = None
            pinned = _pinned_from_preset(preset)
        else:
            draft_snapshot = candidate
            pinned = _pinned_from_draft(candidate)
            override = _draft_override(preset, candidate)
    elif hist is None:
        pinned = _pinned_from_preset(preset)
    else:
        # A legacy session without an owner adopts the current account on its
        # first write; from then on it is scoped like any other session.
        _adopt_scope(content_model)
        draft_snapshot = _playground_draft(pinned)
        override = _pinned_override(preset, pinned)
        if draft_snapshot is not None:
            override = _draft_override(preset, draft_snapshot)
    if hist is not None:
        # Persist the adopted scope with this write.
        raw["scope"] = content_model.scope.model_dump() if content_model.scope is not None else None
    if override is not None:
        # A pinned or draft version may reference tools that no longer exist.
        _require_runnable_preset(override)

    messages = history_messages(content_model) if hist else []
    if body.inputMode == "buyer" and content:
        messages.append({"role": "user", "content": content})

    frozen = content_model.source.frozen_input if content_model.source is not None else ""
    mode = MODE_SIMULATED if content_model.session_type in ("playground", "fork") else "live"
    from backend.app.shared.backend.account_context import get_account_context

    account = get_account_context()
    prior = _history_summaries(
        content_model.source.conversation_id if content_model.source else None,
        seller=account.self_ali_id, data_dir=account.data_dir,
    )
    try:
        turn = _run_turn(
            apid=agent_id, preset=preset, override=override, messages=messages,
            frozen=frozen, mode=mode,
            conversation_id=content_model.source.conversation_id if content_model.source else None,
            revision_id=pinned.revision_id if pinned is not None else None,
            prior_replies=prior,
        )
    except AppError:
        raise
    except Exception as exc:
        return _agent_run_error(exc)

    messages.append(_turn_message(turn))
    title = hist.name if hist else (content[:40] if len(content) > 40 else content) or preset.name
    session_type = content_model.session_type if hist is not None else "test"
    scope = _current_scope()
    try:
        if hist is None:
            fresh = ChatHistory(name=title, content=build_history_content(
                apid=agent_id, agent_name=preset.name, messages=messages,
                draft=draft_snapshot, session_type=session_type, scope=scope,
                pinned=pinned, requires_tool=bool(getattr(preset, "requires_tool", False)),
            ))
            history_manager.add_chat_history(fresh)
            hist = fresh
        else:
            history_manager.edit_chat_history(hist.id, ChatHistory(
                name=title,
                content=build_history_content(
                    apid=agent_id, agent_name=preset.name, messages=messages,
                    existing=raw, draft=draft_snapshot,
                    requires_tool=bool(getattr(preset, "requires_tool", False)),
                ),
            ))
            hist = history_manager.get_chat_history(hist.id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    session = _session_to_dict(hist) if hist else None
    if session is None:
        raise AppError("会话保存失败", status_code=500)
    reply = session["messages"][-1] if session["messages"] else None
    return ok({
        "session": session,
        "reply": reply,
        "restricted": turn.restricted,
        "reasoning": turn.text,
    })


def _playground_draft(pinned: ChatHistoryPin | None) -> ChatHistoryDraft | None:
    return _pin_to_draft(pinned)


def _pinned_override(preset: AgentPreset, pinned: ChatHistoryPin | None) -> AgentPreset | None:
    """Run a session against the version it was pinned to, not the current one."""
    if pinned is None or not pinned.prompt:
        return None
    if (
        pinned.kind == "preset"
        and pinned.prompt == preset.prompt
        and pinned.llm_level == preset.llm_level
        and list(pinned.tools) == list(preset.tools or [])
        and bool(pinned.requires_tool) == bool(getattr(preset, "requires_tool", False))
    ):
        return None
    return AgentPreset(
        apid=preset.apid,
        name=pinned.name or preset.name,
        description=preset.description,
        prompt=pinned.prompt,
        llm_level=pinned.llm_level,
        tools=list(pinned.tools),
        enabled=True,
        requires_tool=bool(pinned.requires_tool),
    )


def _agent_run_error(exc: Exception):
    """Map SDK/runner failures to explicit status codes instead of a blanket 500."""
    from backend.app.shared.agent.runner import AgentRunError

    if isinstance(exc, AgentRunError):
        return api_error(exc.message, status_code=exc.status_code)
    logger.exception("request failed")
    return api_error("服务器内部错误", status_code=500)


@router.get("/api/agent/test-history")
def list_history() -> dict:
    try:
        sessions = []
        for hist in ChatHistoryManager().list_chat_history():
            session = _session_to_dict(hist)
            if session is not None:
                sessions.append(session)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(sessions)


@router.post("/api/agent/test-history/{session_id}/undo")
def undo_session(session_id: str) -> dict:
    manager = ChatHistoryManager()
    hist = _load_session(manager, session_id)
    raw = hist.content if isinstance(hist.content, dict) else {}
    content = load_history_content(raw)
    messages = history_messages(content)
    if len(messages) < 2 or messages[-2]["role"] != "user" or messages[-1]["role"] != "assistant":
        raise AppError("没有可撤销的轮次", status_code=400)
    # Dropping the pair also drops that turn's tool records and intents.
    try:
        manager.edit_chat_history(hist.id, ChatHistory(
            name=hist.name,
            content=build_history_content(
                apid=content.apid, agent_name=content.agent_name,
                messages=messages[:-2], existing=raw),
        ))
        updated = manager.get_chat_history(hist.id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    session = _session_to_dict(updated) if updated else None
    if session is None:
        raise AppError("会话不存在", status_code=404)
    return ok(session)


@router.post("/api/agent/test-history/{session_id}/regenerate")
def regenerate_session(session_id: str) -> dict:
    manager = ChatHistoryManager()
    hist = _load_session(manager, session_id)
    raw = hist.content if isinstance(hist.content, dict) else {}
    content = load_history_content(raw)
    messages = history_messages(content)
    if not messages or messages[-1]["role"] != "assistant":
        raise AppError("没有可重新生成的回复", status_code=400)
    base = messages[:-1]
    if base and base[-1]["role"] == "user":
        base = base[:-1]
    preset = AgentPresetManager().get_agent_preset(content.apid)
    if preset is None:
        raise AppError("Agent 不存在", status_code=404)
    _require_runnable_preset(preset)
    draft = _playground_draft(content.pinned) or content.draft
    override: AgentPreset | None = _pinned_override(preset, content.pinned)
    if draft is not None:
        override = _draft_override(preset, draft)
        _require_runnable_preset(override)
    frozen = content.source.frozen_input if content.source is not None else ""
    mode = MODE_SIMULATED if content.session_type in ("playground", "fork") else "live"
    try:
        turn = _run_turn(
            apid=content.apid, preset=preset, override=override, messages=base,
            frozen=frozen, mode=mode,
            conversation_id=content.source.conversation_id if content.source else None,
            revision_id=content.pinned.revision_id if content.pinned else None,
            prior_replies=(),
        )
    except Exception as exc:
        return _agent_run_error(exc)
    try:
        manager.edit_chat_history(hist.id, ChatHistory(
            name=hist.name,
            content=build_history_content(
                apid=content.apid, agent_name=content.agent_name,
                messages=[*base, _turn_message(turn)], existing=raw),
        ))
        updated = manager.get_chat_history(hist.id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    session = _session_to_dict(updated) if updated else None
    if session is None:
        raise AppError("会话不存在", status_code=404)
    return ok(session)


@router.delete("/api/agent/test-history/{session_id}")
def delete_session(session_id: str) -> dict:
    manager = ChatHistoryManager()
    hist = _load_session(manager, session_id)
    try:
        manager.delete_chat_history(hist.id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(None)


@router.post("/api/agent/test-history/{session_id}/branch")
def branch_session(session_id: str, body: BranchInput | None = None) -> dict:
    manager = ChatHistoryManager()
    hist = _load_session(manager, session_id)
    raw = hist.content if isinstance(hist.content, dict) else {}
    content = load_history_content(raw)
    messages = history_messages(content)
    index = body.messageIndex if body is not None else None
    if index is not None:
        if index >= len(messages):
            raise AppError("消息不存在", status_code=404)
        messages = messages[: index + 1]
    try:
        branched = ChatHistory(
            name=f"{hist.name}-分支",
            content=build_history_content(
                apid=content.apid, agent_name=content.agent_name,
                messages=messages, existing=raw,
                scope=_current_scope(), parent_id=str(hist.id)),
        )
        manager.add_chat_history(branched)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    session = _session_to_dict(branched)
    if session is None:
        raise AppError("会话创建失败", status_code=500)
    return ok(session)


# ---------------------------------------------------------------------------
# Playground: fork from a real conversation message


def _conversation_title(conv: Any) -> str:
    for attr in ("title", "contact_ali_id"):
        value = getattr(conv, attr, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(getattr(conv, "sid", "") or "")


@router.post("/api/agent/fork")
def fork_from_message(body: ForkMessageInput) -> dict:
    """Freeze the conversation up to one message and start a simulated session.

    The frozen text is the live projection, so the first simulated turn receives
    exactly what a live run would have received. The string is stored rather than
    a message reference: later changes to the source must not rewrite it.
    """
    from backend.app.api.routers.conversations import _scoped_conversation, _inbox_store
    from backend.app.shared.crm.message_markdown import AGENT_OPTIONS, transcript_markdown
    from backend.app.shared.crm.sync import CRMAdapter
    from backend.app.shared.crm.views import CrmResolver

    agent_id = (body.agentId or "").strip()
    if not agent_id:
        raise AppError("agentId is required", status_code=422)
    if body.versionKind not in ("preset", "draft"):
        raise AppError("versionKind must be preset or draft", status_code=422)
    preset = AgentPresetManager().get_agent_preset(agent_id)
    if preset is None:
        raise AppError("Agent 不存在", status_code=404)
    _require_runnable_preset(preset)

    store, context = _inbox_store()
    adapter = CRMAdapter()
    conv = _scoped_conversation(store, context, body.conversationId, adapter)
    if conv is None:
        raise AppError("会话不存在", status_code=404)
    if body.messageIndex >= len(conv.messages):
        raise AppError("消息不存在", status_code=404)
    frozen = transcript_markdown(
        conv.messages[: body.messageIndex + 1],
        resolver=CrmResolver(context.self_ali_id),
        options=AGENT_OPTIONS,
        limit=None,
    )
    if len(frozen) > MAX_FROZEN_INPUT_CHARS:
        raise AppError(
            f"从该消息开始需要冻结 {len(frozen)} 字的上下文，超过 {MAX_FROZEN_INPUT_CHARS} 字上限；请选择更早的消息。",
            status_code=422,
        )

    override: AgentPreset | None = None
    pinned: ChatHistoryPin
    draft_snapshot: ChatHistoryDraft | None = None
    revision_id: int | None = None
    if body.versionKind == "draft":
        if body.draft is None:
            raise AppError("draft is required when versionKind=draft", status_code=422)
        try:
            draft_level = max(0, min(LLM_MAX_LEVEL, int(body.draft.level)))
        except (TypeError, ValueError):
            raise AppError(f"draft.level 必须是 0 到 {LLM_MAX_LEVEL} 的整数", status_code=422)
        draft_snapshot = ChatHistoryDraft(
            prompt=body.draft.prompt, llm_level=draft_level,
            tools=[tool for tool in (body.draft.tools or []) if isinstance(tool, str) and tool.strip()],
            requires_tool=bool(body.draft.requires_tool),
        )
        pinned = _pinned_from_draft(draft_snapshot)
        override = _draft_override(preset, draft_snapshot)
        _require_runnable_preset(override)
    else:
        pinned = _pinned_from_preset(preset)
        if body.revisionId is not None:
            revision = next(
                (item for item in load_preset_revisions(agent_id, limit=MAX_REVISIONS_PER_PRESET)
                 if item.id == body.revisionId),
                None,
            )
            if revision is None:
                raise AppError("版本不存在", status_code=404)
            revision_id = revision.id
            pinned = ChatHistoryPin(
                kind="preset", revision_id=revision.id, name=revision.name,
                prompt=revision.prompt, llm_level=revision.llm_level,
                tools=list(revision.tools or []),
                requires_tool=bool(getattr(revision, "requires_tool", False)),
            )
            override = AgentPreset(
                apid=preset.apid, name=revision.name, description=preset.description,
                prompt=revision.prompt, llm_level=revision.llm_level,
                tools=list(revision.tools or []), enabled=True,
                requires_tool=bool(getattr(revision, "requires_tool", False)),
            )

    source = ChatHistorySource(
        message_index=body.messageIndex,
        conversation_id=body.conversationId,
        conversation_title=_conversation_title(conv),
        frozen_input=frozen,
    )
    title = f"{preset.name} · 从消息 #{body.messageIndex + 1} 模拟"
    try:
        hist = ChatHistory(
            name=title,
            content=build_history_content(
                apid=agent_id, agent_name=preset.name, messages=[],
                session_type="fork", scope=_current_scope(), pinned=pinned,
                source=source, requires_tool=bool(getattr(preset, "requires_tool", False)),
            ),
        )
        ChatHistoryManager().add_chat_history(hist)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    log_event("agent.fork_created", conversation_id=body.conversationId,
              message_index=body.messageIndex, frozen_chars=len(frozen),
              revision_id=revision_id, session_id=hist.id)
    session = _session_to_dict(hist)
    if session is None:
        raise AppError("会话创建失败", status_code=500)
    return ok(session)


@router.get("/api/agent/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    hist = _load_session(ChatHistoryManager(), session_id)
    session = _session_to_dict(hist)
    if session is None:
        raise AppError("会话不存在", status_code=404)
    raw = hist.content if isinstance(hist.content, dict) else {}
    content = load_history_content(raw)
    return ok({
        "session": session,
        "frozenInput": content.source.frozen_input if content.source is not None else "",
    })


__all__ = ["SCHEMA_VERSION", "agent_console", "router"]
