from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.api.envelope import ok
from backend.app.shared.backend import status as status_mod
from backend.app.shared.backend.maafw_runner import run_node
from backend.app.task_queue import get_task_queue

router = APIRouter()


class NodeTestInput(BaseModel):
    entry: str = "ChatInput"


class CreateTestTaskInput(BaseModel):
    type: str = "test"
    target: str = ""


@router.get("/api/status/user")
def status_user() -> dict:
    return ok(asdict(status_mod.check_user_status()))


@router.get("/api/status/mitm-proxy")
def status_proxy() -> dict:
    return ok(asdict(status_mod.check_mitm_proxy()))


@router.get("/api/status/mitm-receiver")
def status_receiver() -> dict:
    return ok(asdict(status_mod.check_mitm_receiver()))


@router.get("/api/status/tasks")
def status_tasks() -> dict:
    snaps = get_task_queue().all_snapshots()
    return ok(
        [
            {
                "task_id": s.task_id,
                "description": s.description,
                "status": str(s.status),
                "message": s.message,
                "result": list(s.result) if s.result else None,
                "created_at": s.created_at,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
            }
            for s in snaps
        ]
    )


@router.get("/api/status")
def system_status() -> dict:
    return ok(
        {
            "user": asdict(status_mod.check_user_status()),
            "proxy": asdict(status_mod.check_mitm_proxy()),
            "receiver": asdict(status_mod.check_mitm_receiver()),
            "tasks": [s.task_id for s in get_task_queue().all_snapshots()],
        }
    )


@router.post("/api/status/refresh")
def status_refresh() -> dict:
    return system_status()


@router.post("/api/status/node-test")
def node_test(body: NodeTestInput) -> dict:
    success, message = run_node(body.entry or "ChatInput")
    return ok({"success": success, "message": message})


@router.post("/api/status/test-tasks")
def create_test_task(body: CreateTestTaskInput) -> dict:
    snap = get_task_queue().enqueue(
        lambda: (True, "frontend connectivity ok"),
        description=body.type or "test",
    )
    return ok(
        {
            "id": snap.task_id,
            "type": body.type,
            "status": "queued" if str(snap.status) == "pending" else str(snap.status),
            "createdAt": snap.created_at,
            "duration": "—",
            "target": body.target or None,
            "message": snap.message,
        }
    )
