"""Instance-level remote Python execution for diagnostics.

These routes are intentionally not account-scoped: the switch and the recorded
runs belong to this installation, and diagnostics must work while chat data or
the GUI binding is unavailable. Authentication is still the standard Bearer
session issued by /api/auth/login.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.envelope import AppError, ok
from backend.app.shared.backend.execution_service import (
    DEFAULT_TIMEOUT_S,
    MAX_RUNS,
    MAX_SCRIPT_BYTES,
    MAX_TIMEOUT_S,
    MIN_TIMEOUT_S,
    ExecutionRejected,
    get_execution_service,
)

router = APIRouter()

_REASONS = {
    "execution_disabled": "远程执行尚未开启。",
    "execution_generation_changed": "执行开关已变化，请刷新后重试。",
    "execution_start_token_changed": "程序已重启，请刷新页面后重试。",
    "execution_busy": "已有脚本正在执行，请等待结束或先停止。",
    "execution_duplicate_conflict": "该任务标识已用于其他脚本或超时设置。",
    "execution_script_invalid": "脚本为空或超过大小限制。",
    "execution_timeout_invalid": "执行超时不在允许范围内。",
    "execution_interpreter_unavailable": "未找到可用的 Python 解释器。",
    "execution_storage_unavailable": "无法写入执行记录目录。",
    "execution_run_not_found": "执行记录不存在。",
    "execution_start_failed": "执行任务启动失败。",
    "execution_stopping": "程序正在停止，无法执行脚本。",
}


def _reject(exc: ExecutionRejected) -> AppError:
    return AppError(_REASONS.get(exc.reason, "远程执行失败。"), status_code=exc.status_code)


class SwitchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool
    generation: int = Field(ge=1)


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    start_token: str = Field(pattern=r"^[0-9a-f]{32}$")
    generation: int = Field(ge=1)
    script: str = Field(min_length=1, max_length=MAX_SCRIPT_BYTES)
    timeout_s: int = Field(default=DEFAULT_TIMEOUT_S, ge=MIN_TIMEOUT_S, le=MAX_TIMEOUT_S)


@router.get("/api/app/diagnostics/execution")
def get_execution() -> dict:
    return ok(get_execution_service().state())


@router.put("/api/app/diagnostics/execution")
def switch_execution(body: SwitchInput) -> dict:
    try:
        return ok(get_execution_service().set_enabled(body.enabled, generation=body.generation))
    except ExecutionRejected as exc:
        raise _reject(exc) from None


@router.get("/api/app/diagnostics/execution/runs")
def list_execution_runs(limit: int = Query(default=MAX_RUNS, ge=1, le=MAX_RUNS)) -> dict:
    return ok({"runs": get_execution_service().list_runs(limit)})


@router.post("/api/app/diagnostics/execution/runs")
def submit_execution_run(body: RunInput) -> dict:
    try:
        return ok(get_execution_service().submit(
            run_id=body.id, start_token=body.start_token, generation=body.generation,
            script=body.script, timeout_s=body.timeout_s,
        ))
    except ExecutionRejected as exc:
        raise _reject(exc) from None


@router.get("/api/app/diagnostics/execution/runs/{run_id}")
def get_execution_run(
    run_id: str,
    stdout_offset: int = Query(default=0, ge=0),
    stderr_offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return ok(get_execution_service().get(
            run_id, stdout_offset=stdout_offset, stderr_offset=stderr_offset,
        ))
    except ExecutionRejected as exc:
        raise _reject(exc) from None


@router.post("/api/app/diagnostics/execution/runs/{run_id}/cancel")
def cancel_execution_run(run_id: str) -> dict:
    try:
        return ok(get_execution_service().cancel(run_id))
    except ExecutionRejected as exc:
        raise _reject(exc) from None
