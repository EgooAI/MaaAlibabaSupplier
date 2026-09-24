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
from backend.app.shared.backend.im_db_middleware import IMDBMiddleware
from backend.app.shared.backend.im_layout import encrypted_db_path
from backend.app.shared.crm.account_keys import get_key_source
from backend.app.shared.backend.gui_session import get_client_status
from backend.app.shared.backend import status as status_mod
from backend.app.shared.backend.maafw_runner import run_node
from backend.app.shared.mitm.pool import observe_pools
from backend.app.task_queue import DEFAULT_QUEUE_NAME, TaskQueue, get_task_queue, observe_task_queues

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
    return "uncertain"


def _observed_tasks() -> list:
    # Status reads must not create a worker just to observe an empty queue.
    with TaskQueue._instance_lock:
        queue = TaskQueue._instances.get(DEFAULT_QUEUE_NAME)
    return [s for s in queue.all_snapshots() if not s.description.startswith(("Outbox ", "Translation "))] if queue else []


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
    middleware = IMDBMiddleware._instance
    if middleware is None:
        source = get_key_source(context.self_ali_id) if context.self_ali_id else None
        has_key, source = bool(source), source or "none"
        sync = {}
    else:
        has_key, source = middleware.key_status()
        sync = middleware.sync_status()
    current = sync.get("epoch") == context.epoch and sync.get("self_ali_id") == context.self_ali_id
    # resolve_encrypted_db_path still takes the GUI account lock. Observing
    # existence only needs the captured directory and selected seller.
    db_exists = bool(context.data_dir and context.self_ali_id and (
        encrypted_db_path(Path(context.data_dir), context.self_ali_id).is_file()
    ))
    if get_account_context() != context:
        raise AppError("读取身份状态期间账号已切换，请刷新后重试。", status_code=409)
    return {
        "has_key": has_key, "source": source, "ali_id": context.self_ali_id, "db_exists": db_exists,
        "key_validation": sync.get("key_validation", "unverified") if current else "unverified",
        "last_observed_at": sync.get("last_observed_at") if current else None,
        "observation_stale": sync.get("observation_stale", True) if current else True,
    }


def _build_system_snapshot() -> dict:
    context = get_account_context()
    user = _user_status()
    user_observed_at = time.time()
    proxy = asdict(status_mod.check_mitm_proxy())
    receiver = asdict(status_mod.check_mitm_receiver())
    data_dir = status_mod.check_data_dir_status()
    data_dir_observed_at = time.time()
    snaps = _observed_tasks()
    node = _last_node_result
    client = get_client_status() if node else None
    node_current = bool(node and node.get("context") == context.to_dict()
                        and node.get("window_generation") == client["window_generation"] and client["connected"])
    workers = status_mod.observe_workers()
    queues = observe_task_queues()
    queues_observed_at = time.time()
    for queue in queues.values():
        queue["observed_at"] = queues_observed_at
        queue["current_age_s"] = max(0, queues_observed_at - queue["current_started"]) if queue["current_started"] is not None else None
    source = IMDBMiddleware._instance.sync_status() if IMDBMiddleware._instance is not None else None
    if source is not None and (source["epoch"] != context.epoch or source["self_ali_id"] != context.self_ali_id):
        raise AppError("读取源库状态期间账号已切换，请刷新后重试。", status_code=409)
    if source is not None:
        source["last_error"] = user_message(source["last_error"]) if source["last_error"] else ""
    key_healthy = user["key_validation"] == "valid" and not user["observation_stale"] and user["has_key"] and user["db_exists"]
    if get_account_context() != context:
        raise AppError("读取系统状态期间账号已切换，请刷新后重试。", status_code=409)
    # Each health label is limited to its evidence; diagnostics are historical.
    return {
        "updatedAt": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "observedAt": time.time(),
        "context": context.to_dict(),
        "lastDiagnostic": {**node, "currentContext": node_current} if node else None,
        "workers": workers,
        "queues": queues,
        "pools": observe_pools(),
        "source": source,
        "modules": [
            {
                "id": "health-identity",
                "name": "身份服务",
                "status": "healthy" if key_healthy else ("warning" if user["key_validation"] in ("invalid", "unavailable") or not user["has_key"] or not user["db_exists"] else "uncertain"),
                "latency": None,
                "description": f"{user.get('source')} · {user.get('ali_id') or '未选择'} · "
                + ("密钥已验证且近期成功观察源库；不代表客户端登录身份或 CRM 提交完成" if key_healthy else "密钥尚未验证、不可用或源库观察已过期；已保存密钥不等于验证通过"),
                "observedAt": user["last_observed_at"] if key_healthy else user_observed_at,
                "evidence": "validated_source" if key_healthy else "key_presence_or_stale_validation",
            },
            {
                "id": "health-proxy",
                "name": "MITM 代理",
                "status": _network_health(proxy),
                "latency": proxy.get("latency_ms"),
                "description": f"{proxy.get('host')}:{proxy.get('port')}"
                + " · 仅 TCP 端口探测，不代表业务就绪"
                + (f" · {proxy.get('error')}" if proxy.get("error") else ""),
                "observedAt": proxy["observed_at"],
                "evidence": proxy["evidence"],
            },
            {
                "id": "health-receiver",
                "name": "MITM Receiver",
                "status": _network_health(receiver),
                "latency": receiver.get("latency_ms"),
                "description": f"{receiver.get('host')}:{receiver.get('port')}"
                + " · 仅 TCP 端口探测，不代表业务就绪"
                + (f" · {receiver.get('error')}" if receiver.get("error") else ""),
                "observedAt": receiver["observed_at"],
                "evidence": receiver["evidence"],
            },
            {
                "id": "health-node",
                "name": "MaaFW 上次手动检查",
                "status": ("healthy" if node.get("success") else "warning") if node_current else "uncertain",
                "latency": None,
                "description": (f"上次手动检查：{node.get('message')} · 仅代表当时界面" if node_current else "上次检查不属于当前账号或窗口，请重新检查") if node else "尚未测试，请点击下方节点测试按钮",
                "observedAt": node.get("completed_at") if node else None,
                "evidence": "manual_diagnostic",
            },
            {
                "id": "health-datadir",
                "name": "数据源目录",
                "status": "healthy" if data_dir["state"] == "ok" else "warning",
                "latency": None,
                "description": (data_dir["path"] + " · 仅目录检查，不代表数据同步完成") if data_dir["path"] else "尚未配置阿里客户端数据目录，请前往设置页配置",
                "observedAt": data_dir_observed_at,
                "evidence": "directory_check",
            },
        ],
        "tasks": [_snapshot_to_task_item(s) for s in snaps],
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
    return ok([_snapshot_to_task_snapshot(s) for s in _observed_tasks()])


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
    context = get_account_context()
    require_epoch(expected)
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
        _last_node_result = {
            "success": success, "message": message, "entry": entry,
            "context": context.to_dict(), "window_generation": client["window_generation"],
            "completed_at": time.time(),
        }
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
