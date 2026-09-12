from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.api.envelope import err, ok
from backend.app.shared.backend.maafw_runner import goto_contact
from backend.app.task_queue import TaskStatus, get_task_queue

router = APIRouter()

_TODO = "conversation aggregate not implemented yet (needs SessionMeta/sid mapping)"


class GotoContactInput(BaseModel):
    login_id: str = ""


def _snap_to_dict(s) -> dict:
    return {
        "task_id": s.task_id,
        "description": s.description,
        "status": str(s.status),
        "message": s.message,
        "result": list(s.result) if s.result else None,
        "created_at": s.created_at,
        "started_at": s.started_at,
        "completed_at": s.completed_at,
    }


@router.get("/api/conversations")
def list_conversations() -> dict:
    return err(_TODO)


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    return err(_TODO)


@router.post("/api/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, body: dict) -> dict:
    return err(_TODO)


@router.get("/api/conversations/{conversation_id}/suggestions")
def suggestions(conversation_id: str) -> dict:
    return err(_TODO)


@router.get("/api/conversations/{conversation_id}/analysis")
def analysis(conversation_id: str) -> dict:
    return err(_TODO)


@router.post("/api/conversations/export")
def export_conversations(body: dict) -> dict:
    return err("zip export not implemented yet (needs _build_export_zip port)")


@router.post("/api/conversations/{conversation_id}/goto-contact")
def goto_contact_api(conversation_id: str, body: GotoContactInput) -> dict:
    login_id = (body.login_id or "").strip()
    if not login_id:
        return err("login_id is required")
    snap = get_task_queue().enqueue(
        lambda: goto_contact(login_id), description=f"goto-contact {login_id}"
    )
    current = get_task_queue().get(snap.task_id)
    status = str(current.status) if current else str(TaskStatus.PENDING)
    return ok({"task_snapshot": _snap_to_dict(current) if current else None, "status": status})
