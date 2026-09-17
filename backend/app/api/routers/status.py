from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.api.envelope import AppError, ok, user_message
from backend.app.api.account_scope import AccountRoute, request_epoch, require_epoch
from backend.app.shared.backend.account_context import account_lock, get_account_context
from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.crm.identities import self_sender_id
from backend.app.shared.backend.gui_session import get_client_status
from backend.app.shared.backend import status as status_mod
from backend.app.shared.backend.maafw_runner import run_node
from backend.app.task_queue import get_task_queue

router = APIRouter(route_class=AccountRoute)

ALLOWED_NODE_ENTRIES = {
    "ChatInput_GoToInput": "Diagnostics_ChatInput",
    "ContactSearch_GoToSearch": "Diagnostics_ContactSearch",
}

# Last manual node-test outcome shown in the aggregate snapshot.
# None means "not tested yet" (polls must not trigger real MaaFW runs).
_last_node_result: dict | None = None


class NodeTestInput(BaseModel):
    entry: str = Field(default="ChatInput_GoToInput", max_length=64)


class CreateTestTaskInput(BaseModel):
    type: str = Field(default="test", max_length=32)
    target: str = Field(default="", max_length=256)


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
        "message": user_message(s.message),
        "result": user_message(result[1]) if result else None,
        "resultSuccess": result[0] if result else None,
    }


def _snapshot_to_task_snapshot(s) -> dict:
    return {
        "task_id": s.task_id,
        "description": s.description,
        "status": str(s.status),
        "message": user_message(s.message),
        "result": [s.result[0], user_message(s.result[1])] if s.result else None,
        "created_at": s.created_at,
        "started_at": s.started_at,
        "completed_at": s.completed_at,
    }


def _user_status() -> dict:
    context = get_account_context()
    has_key, source = get_im_db_middleware().key_status()
    # resolve_encrypted_db_path still takes the GUI account lock. Observing
    # existence only needs the captured directory and selected seller.
    db_exists = bool(context.data_dir and context.self_ali_id and (
        Path(context.data_dir) / "IMServiceDir" / "MessageSDK" / self_sender_id(context.self_ali_id) / "database" / "im.sqlite"
    ).is_file())
    if get_account_context() != context:
        raise AppError("读取身份状态期间账号已切换，请刷新后重试。", status_code=409)
    return {"has_key": has_key, "source": source, "ali_id": context.self_ali_id, "db_exists": db_exists}


def _build_system_snapshot() -> dict:
    context = get_account_context()
    user = _user_status()
    proxy = asdict(status_mod.check_mitm_proxy())
    receiver = asdict(status_mod.check_mitm_receiver())
    data_dir = status_mod.check_data_dir_status()
    im_sync = status_mod.check_im_sync_status()
    snaps = get_task_queue().all_snapshots()
    node = _last_node_result
    if get_account_context() != context:
        raise AppError("读取系统状态期间账号已切换，请刷新后重试。", status_code=409)
    return {
        "updatedAt": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "modules": [
            {
                "id": "health-datadir",
                "name": "数据源目录",
                "status": "healthy" if data_dir["state"] == "ok" else "warning",
                "latency": None,
                "description": data_dir["path"] or "尚未配置阿里客户端数据目录，请前往设置页配置",
            },
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
        "dataDirStatus": data_dir,
        "imSyncStatus": im_sync,
        "nodeResult": node,
        "taskSnapshots": [_snapshot_to_task_snapshot(s) for s in snaps],
    }


@router.get("/api/status/user")
def status_user() -> dict:
    return ok(_user_status())


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
    entry = body.entry.strip()
    if entry not in ALLOWED_NODE_ENTRIES:
        raise AppError(f"请选择允许的诊断节点：{sorted(ALLOWED_NODE_ENTRIES)}", status_code=422)
    expected = request_epoch.get()
    client = get_client_status()
    if not client["connected"]:
        raise AppError(client["detail"], status_code=503)

    def _run() -> tuple[bool, str]:
        global _last_node_result
        try:
            with account_lock:
                require_epoch(expected)
                current = get_client_status()
                if not current["connected"] or current["window_generation"] != client["window_generation"]:
                    raise AppError("客户端窗口已变化，请重新连接后重试诊断。", status_code=409)
                success, message = run_node(ALLOWED_NODE_ENTRIES[entry])
            if success:
                message = "界面识别通过（未点击、未输入）"
        except AppError as exc:
            success, message = False, user_message(str(exc))
        except Exception:
            logger.exception("node diagnostic failed")
            success, message = False, "界面识别失败"
        _last_node_result = {"success": success, "message": message}
        return success, message

    snap = get_task_queue().enqueue(_run, description=f"diagnostic {entry}")
    return ok({
        "success": None,
        "message": "诊断任务已提交，请在任务队列查看结果",
        "task_snapshot": _snapshot_to_task_snapshot(snap),
    })


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
