from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.api.envelope import err, ok
from backend.app.shared.agent.chat_history_content import (
    build_history_content,
    history_messages,
    load_history_content,
)
from backend.app.shared.agent.inputs import build_chat_dialog_input
from backend.app.shared.agent.runner import run_chat_tool_agent
from backend.app.shared.agent.system_agents import SYSTEM_AGENT_APIDS, SYSTEM_AGENT_DEFINITIONS
from backend.app.shared.agent.system_presets import system_agent_presets
from backend.app.shared.crm.sdk import (
    AgentPreset,
    AgentPresetManager,
    ChatHistory,
    ChatHistoryManager,
    LLMApiConfig,
    LLMApiConfigManager,
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


class AgentTestInput(BaseModel):
    agentId: str = ""
    content: str = ""
    sessionId: str | None = None


def _refresh_llm_runtime() -> None:
    try:
        from agent_pipeline.llm_api import register_default_llms
    except ImportError:
        return
    try:
        register_default_llms()
    except Exception:
        pass


def _db_preset_to_dict(preset: AgentPreset, *, enabled: bool = True, updated_at: str = "") -> dict:
    category = "system" if preset.apid in SYSTEM_AGENT_APIDS else "regular"
    return {
        "apid": preset.apid,
        "name": preset.name,
        "description": preset.description,
        "prompt": preset.prompt,
        "intelevel": preset.llm_level,
        "tools": list(preset.tools or []),
        "enabled": enabled,
        "updated_at": updated_at,
        "category": category,
    }


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
        "createdAt": created,
        "messages": messages,
    }


def _load_session(manager: ChatHistoryManager, session_id: str) -> tuple[ChatHistory | None, dict]:
    try:
        raw_id = int(session_id)
    except (TypeError, ValueError):
        return None, err("会话不存在")
    hist = manager.get_chat_history(raw_id)
    if hist is None:
        return None, err("会话不存在")
    session = _session_to_dict(hist)
    if session is None:
        return None, err("会话不存在")
    return hist, ok(session)


@router.get("/api/agent/system")
def list_system_agents() -> dict:
    return ok(
        [
            {"display_name": name, "apid": apid, "description": desc}
            for name, apid, desc in SYSTEM_AGENT_DEFINITIONS
        ]
    )


@router.get("/api/agent/console")
def agent_console() -> dict:
    try:
        levels = [
            {
                "level": config.level,
                "baseUrl": config.base_url,
                "apiKey": config.api_key,
                "modelName": config.model_name,
                "systemPrompt": config.system_prompt,
                "context": config.context,
                "maxToolRounds": config.max_tool_rounds,
            }
            for config in LLMApiConfigManager().list_configs()
        ]
        presets = AgentPresetManager().list_agent_preset()
        db_presets = [_db_preset_to_dict(preset) for preset in presets]
        agents = [
            {
                "id": preset.apid,
                "name": preset.name,
                "category": "system" if preset.apid in SYSTEM_AGENT_APIDS else "regular",
                "enabled": True,
                "capabilities": list(preset.tools or []),
                "description": preset.description,
                "updatedAt": "",
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
    except Exception as exc:
        return err(str(exc))
    return ok({"llmLevels": levels, "agents": agents, "agentPresets": db_presets, "history": history})


@router.put("/api/agent/llm-config")
def save_llm_config(body: LlmConfigInput) -> dict:
    try:
        config = LLMApiConfig(
            level=int(body.level),
            base_url=body.base_url,
            api_key=body.api_key,
            model_name=body.model_name,
            system_prompt=body.system_prompt,
            context=body.context,
            max_tool_rounds=body.max_tool_rounds,
        )
        LLMApiConfigManager().upsert_config(config)
        saved = LLMApiConfigManager().get_config(config.level)
    except Exception as exc:
        return err(str(exc))
    _refresh_llm_runtime()
    if saved is None:
        return err("LLM 配置保存失败")
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
        level = max(0, min(4, int(body.intelevel)))
    except (TypeError, ValueError):
        return err("intelevel must be 0-4")
    tools = [tool for tool in (body.tools or []) if isinstance(tool, str) and tool.strip()]
    try:
        AgentPresetManager().upsert_agent_preset(AgentPreset(
            apid=apid,
            name=body.name,
            description=body.description,
            prompt=body.prompt,
            llm_level=level,
            tools=tools,
        ))
    except Exception as exc:
        return err(str(exc))
    return ok({
        "apid": apid,
        "name": body.name,
        "description": body.description,
        "prompt": body.prompt,
        "intelevel": level,
        "tools": tools,
        "enabled": body.enabled,
        "updated_at": body.updated_at,
        "category": body.category or ("system" if apid in SYSTEM_AGENT_APIDS else "regular"),
    })


@router.delete("/api/agent/presets/{preset_id}")
def delete_preset(preset_id: str) -> dict:
    if preset_id in SYSTEM_AGENT_APIDS:
        return ok(False)
    try:
        AgentPresetManager().delete_agent_preset(preset_id)
    except Exception as exc:
        return err(str(exc))
    return ok(True)


@router.post("/api/agent/system/{apid}/restore")
def restore_system_agent(apid: str) -> dict:
    payload = next((item for item in system_agent_presets() if item.get("apid") == apid), None)
    if payload is None:
        return err("系统 Agent 默认配置不存在")
    try:
        AgentPresetManager().upsert_agent_preset(AgentPreset(
            apid=str(payload["apid"]),
            name=str(payload["name"]),
            description=str(payload["description"]),
            prompt=str(payload["prompt"]),
            llm_level=int(payload["llm_level"]),
            tools=list(payload["tools"] or []),
        ))
    except Exception as exc:
        return err(str(exc))
    return ok({
        "apid": payload["apid"],
        "name": payload["name"],
        "description": payload["description"],
        "prompt": payload["prompt"],
        "intelevel": payload["llm_level"],
        "tools": list(payload["tools"] or []),
        "enabled": True,
        "updated_at": "",
        "category": "system",
    })


@router.post("/api/agent/test")
def run_agent_test(body: AgentTestInput) -> dict:
    agent_id = (body.agentId or "").strip()
    content = (body.content or "").strip()
    if not agent_id:
        return err("agentId is required")
    manager = AgentPresetManager()
    try:
        preset = manager.get_agent_preset(agent_id)
    except Exception as exc:
        return err(str(exc))
    if preset is None:
        return err("Agent 不存在")
    history_manager = ChatHistoryManager()
    hist: ChatHistory | None = None
    raw: dict[str, Any] = {}
    if body.sessionId:
        hist, error = _load_session(history_manager, body.sessionId)
        if error["code"] != 0:
            return error
        assert hist is not None
        raw = hist.content if isinstance(hist.content, dict) else {}
        if load_history_content(raw).apid != agent_id:
            return err("会话不属于该 Agent")
    if not content:
        if hist is None:
            return err("content is required")
        session = _session_to_dict(hist)
        return ok({"session": session, "reply": None}) if session else err("会话不存在")
    messages = history_messages(load_history_content(raw)) if hist else []
    messages.append({"role": "user", "content": content})
    try:
        reply_text = run_chat_tool_agent(agent_id, build_chat_dialog_input(messages))
    except Exception as exc:
        return err(str(exc))
    messages.append({"role": "assistant", "content": reply_text})
    title = hist.name if hist else (content[:40] if len(content) > 40 else content) or preset.name
    try:
        if hist is None:
            fresh = ChatHistory(name=title, content=build_history_content(
                apid=agent_id, agent_name=preset.name, messages=messages))
            history_manager.add_chat_history(fresh)
            hist = fresh
        else:
            history_manager.edit_chat_history(hist.id, ChatHistory(
                name=title,
                content=build_history_content(
                    apid=agent_id, agent_name=preset.name, messages=messages, existing=raw),
            ))
            hist = history_manager.get_chat_history(hist.id)
    except Exception as exc:
        return err(str(exc))
    session = _session_to_dict(hist) if hist else None
    if session is None:
        return err("会话保存失败")
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
    except Exception as exc:
        return err(str(exc))
    return ok(sessions)


@router.post("/api/agent/test-history/{session_id}/undo")
def undo_session(session_id: str) -> dict:
    manager = ChatHistoryManager()
    hist, error = _load_session(manager, session_id)
    if error["code"] != 0:
        return error
    assert hist is not None
    raw = hist.content if isinstance(hist.content, dict) else {}
    content = load_history_content(raw)
    messages = history_messages(content)
    if len(messages) < 2 or messages[-2]["role"] != "user" or messages[-1]["role"] != "assistant":
        return err("没有可撤销的轮次")
    try:
        manager.edit_chat_history(hist.id, ChatHistory(
            name=hist.name,
            content=build_history_content(
                apid=content.apid, agent_name=content.agent_name,
                messages=messages[:-2], existing=raw),
        ))
        updated = manager.get_chat_history(hist.id)
    except Exception as exc:
        return err(str(exc))
    session = _session_to_dict(updated) if updated else None
    return ok(session) if session else err("会话不存在")


@router.post("/api/agent/test-history/{session_id}/regenerate")
def regenerate_session(session_id: str) -> dict:
    manager = ChatHistoryManager()
    hist, error = _load_session(manager, session_id)
    if error["code"] != 0:
        return error
    assert hist is not None
    raw = hist.content if isinstance(hist.content, dict) else {}
    content = load_history_content(raw)
    messages = history_messages(content)
    if not messages or messages[-1]["role"] != "assistant":
        return err("没有可重新生成的回复")
    base = messages[:-1]
    if not base or base[-1]["role"] != "user":
        return err("没有可重新生成的回复")
    try:
        reply_text = run_chat_tool_agent(content.apid, build_chat_dialog_input(base))
    except Exception as exc:
        return err(str(exc))
    try:
        manager.edit_chat_history(hist.id, ChatHistory(
            name=hist.name,
            content=build_history_content(
                apid=content.apid, agent_name=content.agent_name,
                messages=[*base, {"role": "assistant", "content": reply_text}], existing=raw),
        ))
        updated = manager.get_chat_history(hist.id)
    except Exception as exc:
        return err(str(exc))
    session = _session_to_dict(updated) if updated else None
    return ok(session) if session else err("会话不存在")


@router.delete("/api/agent/test-history/{session_id}")
def delete_session(session_id: str) -> dict:
    try:
        raw_id = int(session_id)
    except (TypeError, ValueError):
        return err("会话不存在")
    try:
        ChatHistoryManager().delete_chat_history(raw_id)
    except Exception as exc:
        return err(str(exc))
    return ok(None)


@router.post("/api/agent/test-history/{session_id}/branch")
def branch_session(session_id: str) -> dict:
    manager = ChatHistoryManager()
    hist, error = _load_session(manager, session_id)
    if error["code"] != 0:
        return error
    assert hist is not None
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
    except Exception as exc:
        return err(str(exc))
    session = _session_to_dict(branched)
    return ok(session) if session else err("会话创建失败")
