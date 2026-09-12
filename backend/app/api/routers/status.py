from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.api.envelope import ok
from backend.app.shared.backend import status as status_mod
from backend.app.shared.backend.maafw_runner import run_node
from backend.app.task_queue import get_task_queue

router = APIRouter()

# Last manual node-test outcome shown in the aggregate snapshot.
# None means "not tested yet" (polls must not trigger real MaaFW runs).
_last_node_result: dict | None = None


class NodeTestInput(BaseModel):
    entry: str = "ChatInput"


class CreateTestTaskInput(BaseModel):
    type: str = "test"
    target: str = ""


def _format_time(epoch: float | None) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _format_duration(started_at: float | None, completed_at: float | None) -> str:
    if not started_at:
        return "0s"
    seconds = max(0, int((completed_at or time.time()) - started_at))
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}m {rest}s" if minutes else f"{rest}s"


def _network_health(status: dict) -> str:
    if not status.get("reachable"):
        return "offline"
    latency = status.get("latency_ms")
    if latency is not None and latency > 180:
        return "warning"
    return "healthy"


def _snapshot_to_task_item(s) -> dict:
    result = list(s.result) if s.result else None
    ui_status = "queued" if str(s.status) == "pending" else str(s.status)
    return {
        "id": s.task_id,
        "type": s.description,
        "status": ui_status,
        "createdAt": _format_time(s.created_at),
        "duration": _format_duration(s.started_at, s.completed_at),
        "target": None,
        "message": s.message,
        "result": result[1] if result else None,
        "resultSuccess": result[0] if result else None,
    }


def _snapshot_to_task_snapshot(s) -> dict:
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


def _build_system_snapshot() -> dict:
    user = asdict(status_mod.check_user_status())
    proxy = asdict(status_mod.check_mitm_proxy())
    receiver = asdict(status_mod.check_mitm_receiver())
    snaps = get_task_queue().all_snapshots()
    node = _last_node_result
    return {
        "updatedAt": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "modules": [
            {
                "id": "health-identity",
                "name": "身份服务",
                "status": "healthy" if (user.get("has_key") and user.get("db_exists")) else "warning",
                "latency": None,
                "description": f"{user.get('source')} · {user.get('ali_id') or '未识别'}",
            },
            {
                "id": "health-proxy",
                "name": "代理服务",
                "status": _network_health(proxy),
                "latency": proxy.get("latency_ms"),
                "description": f"{proxy.get('host')}:{proxy.get('port')}"
                + (f" · {proxy.get('error')}" if proxy.get("error") else ""),
            },
            {
                "id": "health-receiver",
                "name": "Receiver",
                "status": _network_health(receiver),
                "latency": receiver.get("latency_ms"),
                "description": f"{receiver.get('host')}:{receiver.get('port')}"
                + (f" · {receiver.get('error')}" if receiver.get("error") else ""),
            },
            {
                "id": "health-node",
                "name": "MaaFW 节点",
                "status": "healthy" if node and node.get("success") else ("offline" if node else "warning"),
                "latency": None,
                "description": node.get("message") if node else "尚未测试，请点击下方节点测试按钮",
            },
        ],
        "tasks": [_snapshot_to_task_item(s) for s in snaps],
        "userStatus": user,
        "proxyStatus": proxy,
        "receiverStatus": receiver,
        "nodeResult": node,
        "taskSnapshots": [_snapshot_to_task_snapshot(s) for s in snaps],
    }


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
    return ok([_snapshot_to_task_snapshot(s) for s in get_task_queue().all_snapshots()])


@router.get("/api/status")
def system_status() -> dict:
    return ok(_build_system_snapshot())


@router.post("/api/status/refresh")
def status_refresh() -> dict:
    return ok(_build_system_snapshot())


@router.post("/api/status/node-test")
def node_test(body: NodeTestInput) -> dict:
    global _last_node_result
    success, message = run_node(body.entry or "ChatInput")
    _last_node_result = {"success": success, "message": message}
    return ok(_last_node_result)


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
