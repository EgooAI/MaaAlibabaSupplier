from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field
from pydantic import AliasChoices

from backend.app.api.envelope import AppError, api_error, ok
from backend.app.shared.agent.chat_history_content import (
    ChatHistoryDraft,
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
from backend.app.shared.agent.runner import run_chat_tool_agent
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
    updated_at: str = ""
    category: str | None = None
    revision_source: str = "manual"
    revision_note: str = ""


class DraftPresetInput(BaseModel):
    prompt: str = ""
    level: int = 0
    tools: list[str] = []


class AgentTestInput(BaseModel):
    agentId: str = ""
    content: str = ""
    sessionId: str | None = None
    draft: DraftPresetInput | None = None


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
        "source": revision.source,
        "note": revision.note,
        "created_at": format_created_at(revision.created_time) or "",
    }


def _updated_at_text(apid: str) -> str:
    revision = latest_revision(apid)
    return format_created_at(revision.created_time) if revision is not None else ""


def _session_to_dict(hist: ChatHistory) -> dict | None:
    content = load_history_content(hist.content)
    if not content.apid or hist.id is None:
        return None
    created = format_created_at(content.created_at) or ""
    messages = [
        {
            "id": f"{hist.id}-{index}",
            "role": message["role"] if message["role"] in ("user", "assistant") else "user",
            "content": message["content"],
            "createdAt": created,
        }
        for index, message in enumerate(history_messages(content))
    ]
    return {
        "id": str(hist.id),
        "title": hist.name,
        "agentId": content.apid,
        "draft": content.draft is not None,
        "createdAt": created,
        "messages": messages,
    }


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
    )


def _draft_matches_preset(draft: ChatHistoryDraft, preset: AgentPreset) -> bool:
    return (
        draft.prompt == preset.prompt
        and draft.llm_level == preset.llm_level
        and list(draft.tools or []) == list(preset.tools or [])
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
        "tools": list_registered_tools(),
        "history": history,
    })


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


@router.post("/api/agent/test")
def run_agent_test(body: AgentTestInput) -> dict:
    agent_id = (body.agentId or "").strip()
    content = (body.content or "").strip()
    if not agent_id:
        raise AppError("agentId is required", status_code=422)
    manager = AgentPresetManager()
    try:
        preset = manager.get_agent_preset(agent_id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    if preset is None:
        raise AppError("Agent 不存在", status_code=404)
    _require_runnable_preset(preset)
    history_manager = ChatHistoryManager()
    hist: ChatHistory | None = None
    raw: dict[str, Any] = {}
    if body.sessionId:
        hist = _load_session(history_manager, body.sessionId)
        raw = hist.content if isinstance(hist.content, dict) else {}
        if load_history_content(raw).apid != agent_id:
            raise AppError("会话不属于该 Agent", status_code=400)
    if not content:
        if hist is None:
            raise AppError("content is required", status_code=422)
        session = _session_to_dict(hist)
        if session is None:
            raise AppError("会话不存在", status_code=404)
        return ok({"session": session, "reply": None})

    # 会话一旦带草稿快照，后续轮次沿用快照（历史可复核）；换提示词请开新会话。
    existing_draft = load_history_content(raw).draft if hist is not None else None
    draft_snapshot = existing_draft
    override: AgentPreset | None = None
    if existing_draft is not None:
        override = _draft_override(preset, existing_draft)
    elif hist is None and body.draft is not None:
        try:
            draft_level = max(0, min(LLM_MAX_LEVEL, int(body.draft.level)))
        except (TypeError, ValueError):
            raise AppError(f"draft.level 必须是 0 到 {LLM_MAX_LEVEL} 的整数", status_code=422)
        candidate = ChatHistoryDraft(
            prompt=body.draft.prompt,
            llm_level=draft_level,
            tools=[tool for tool in (body.draft.tools or []) if isinstance(tool, str) and tool.strip()],
        )
        if _draft_matches_preset(candidate, preset):
            # 草稿与已保存配置一致：按已保存运行记录，不标记草稿。
            draft_snapshot = None
        else:
            draft_snapshot = candidate
            override = _draft_override(preset, candidate)
    if override is not None:
        # 草稿里也可能挂着未注册的工具，同样在运行前拦截。
        _require_runnable_preset(override)

    messages = history_messages(load_history_content(raw)) if hist else []
    messages.append({"role": "user", "content": content})
    try:
        reply_text = run_chat_tool_agent(
            agent_id, build_chat_dialog_input(messages), preset_override=override,
        )
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    messages.append({"role": "assistant", "content": reply_text})
    title = hist.name if hist else (content[:40] if len(content) > 40 else content) or preset.name
    try:
        if hist is None:
            fresh = ChatHistory(name=title, content=build_history_content(
                apid=agent_id, agent_name=preset.name, messages=messages, draft=draft_snapshot))
            history_manager.add_chat_history(fresh)
            hist = fresh
        else:
            history_manager.edit_chat_history(hist.id, ChatHistory(
                name=title,
                content=build_history_content(
                    apid=agent_id, agent_name=preset.name, messages=messages,
                    existing=raw, draft=draft_snapshot),
            ))
            hist = history_manager.get_chat_history(hist.id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    session = _session_to_dict(hist) if hist else None
    if session is None:
        raise AppError("会话保存失败", status_code=500)
    reply = session["messages"][-1] if session["messages"] else None
    return ok({"session": session, "reply": reply})


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
    if not base or base[-1]["role"] != "user":
        raise AppError("没有可重新生成的回复", status_code=400)
    preset = AgentPresetManager().get_agent_preset(content.apid)
    if preset is None:
        raise AppError("Agent 不存在", status_code=404)
    _require_runnable_preset(preset)
    override: AgentPreset | None = None
    if content.draft is not None:
        override = _draft_override(preset, content.draft)
        _require_runnable_preset(override)
    try:
        reply_text = run_chat_tool_agent(
            content.apid, build_chat_dialog_input(base), preset_override=override,
        )
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    try:
        manager.edit_chat_history(hist.id, ChatHistory(
            name=hist.name,
            content=build_history_content(
                apid=content.apid, agent_name=content.agent_name,
                messages=[*base, {"role": "assistant", "content": reply_text}], existing=raw),
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
    try:
        raw_id = int(session_id)
    except (TypeError, ValueError):
        raise AppError("会话不存在", status_code=404)
    try:
        ChatHistoryManager().delete_chat_history(raw_id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(None)


@router.post("/api/agent/test-history/{session_id}/branch")
def branch_session(session_id: str) -> dict:
    manager = ChatHistoryManager()
    hist = _load_session(manager, session_id)
    raw = hist.content if isinstance(hist.content, dict) else {}
    content = load_history_content(raw)
    try:
        branched = ChatHistory(
            name=f"{hist.name}-分支",
            content=build_history_content(
                apid=content.apid, agent_name=content.agent_name,
                messages=history_messages(content)),
        )
        manager.add_chat_history(branched)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    session = _session_to_dict(branched)
    if session is None:
        raise AppError("会话创建失败", status_code=500)
    return ok(session)
