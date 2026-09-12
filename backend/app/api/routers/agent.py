from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.envelope import err, ok
from backend.app.shared.agent.system_agents import SYSTEM_AGENT_DEFINITIONS

router = APIRouter()

_TODO = "agent console not implemented yet (needs LLMApiConfig/AgentPreset/ChatHistory wiring)"


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
    return err(_TODO)


@router.put("/api/agent/llm-config")
def save_llm_config(body: dict) -> dict:
    return err(_TODO)


@router.post("/api/agent/presets")
def save_preset(body: dict) -> dict:
    return err(_TODO)


@router.delete("/api/agent/presets/{preset_id}")
def delete_preset(preset_id: str) -> dict:
    return err(_TODO)


@router.post("/api/agent/system/{apid}/restore")
def restore_system_agent(apid: str) -> dict:
    return err(_TODO)


@router.post("/api/agent/test")
def run_agent_test(body: dict) -> dict:
    return err(_TODO)


@router.get("/api/agent/test-history")
def list_history() -> dict:
    return err(_TODO)


@router.post("/api/agent/test-history/{session_id}/undo")
def undo_session(session_id: str) -> dict:
    return err(_TODO)


@router.post("/api/agent/test-history/{session_id}/regenerate")
def regenerate_session(session_id: str) -> dict:
    return err(_TODO)


@router.delete("/api/agent/test-history/{session_id}")
def delete_session(session_id: str) -> dict:
    return err(_TODO)


@router.post("/api/agent/test-history/{session_id}/branch")
def branch_session(session_id: str) -> dict:
    return err(_TODO)
