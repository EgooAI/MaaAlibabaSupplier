"""Process-local GUI guards bound to the selected seller and connected window."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import local
from typing import TypeVar, TypedDict
from uuid import uuid4

from backend.app.api.envelope import AppError
from backend.app.shared.backend import maafw_runner as runner
from backend.app.shared.backend.account_context import AccountContext, account_lock, get_account_context


class ClientStatus(TypedDict):
    connected: bool
    window_generation: str
    detail: str


@dataclass(frozen=True)
class GuiSessionToken:
    self_ali_id: str
    data_dir: str
    epoch: str
    window_generation: str


_active_session = local()
_Result = TypeVar("_Result")


def _status_locked() -> ClientStatus:
    connected, detail = runner._probe_binding()
    return {
        "connected": connected,
        "window_generation": runner._window_generation,
        "detail": detail,
    }


def get_client_status() -> ClientStatus:
    """Probe the visible window; never initialize or reconnect while polling."""
    # A running task holds the runner lock. Observation must not delay its
    # acceptance response or the status poll used to track that same task.
    if not runner._run_lock.acquire(blocking=False):
        generation = runner._window_generation
        return {
            "connected": bool(generation),
            "window_generation": generation,
            "detail": "客户端操作正在执行；连接状态将在操作间隙重新检查。",
        }
    try:
        return _status_locked()
    finally:
        runner._run_lock.release()


def connect_client() -> ClientStatus:
    """Initialize with zero GUI input and refresh the window generation."""
    with account_lock, runner._run_lock:
        tasker, error = runner._ensure_init()
        if tasker is None:
            raise AppError(f"Client connection failed: {error}", status_code=503)
        # Even reconnecting the same HWND invalidates previously captured tasks.
        runner._window_generation = uuid4().hex
        status = _status_locked()
        if not status["connected"]:
            raise AppError(status["detail"], status_code=503)
        return status


def _require_context(expected_epoch: str) -> AccountContext:
    context = get_account_context()
    if context.epoch != expected_epoch:
        raise AppError("Account context changed; reload and retry.", status_code=409)
    if not context.self_ali_id:
        raise AppError("Select a seller before operating the client.", status_code=409)
    return context


def capture_gui_session(expected_epoch: str) -> GuiSessionToken:
    """Capture at enqueue time; the worker must still call run_guarded."""
    with account_lock, runner._run_lock:
        status = _status_locked()
        context = _require_context(expected_epoch)
        if not status["connected"]:
            raise AppError(status["detail"], status_code=503)
        return GuiSessionToken(
            context.self_ali_id, context.data_dir, context.epoch,
            status["window_generation"],
        )


def _validate_token(token: GuiSessionToken) -> None:
    """Caller holds both locks, including for each runner node inside a task."""
    status = _status_locked()
    context = _require_context(token.epoch)
    if (
        (token.self_ali_id, token.data_dir, token.epoch)
        != (context.self_ali_id, context.data_dir, context.epoch)
        or token.window_generation != status["window_generation"]
    ):
        raise AppError("GUI session expired; reconnect and retry.", status_code=409)
    if not status["connected"]:
        raise AppError(status["detail"], status_code=503)


def run_guarded(token: GuiSessionToken, fn: Callable[[], _Result]) -> _Result | tuple[bool, str]:
    """Hold the account and runner locks for the complete synchronous GUI task.

    Account switching in another thread must use changing_account's nonblocking
    lock. Thread-local authorization does not propagate to spawned work.
    Results pass through unchanged; exceptions return (False, reason). Release
    this guard before waiting for the user's screenshot confirmation, then reuse
    the original token for the fresh-frame comparison, input and send barrier.
    """
    with account_lock, runner._run_lock:
        previous = getattr(_active_session, "token", None)
        try:
            _validate_token(token)
            if previous is None:
                runner._prepare_native_logs()
            _active_session.token = token
            return fn()
        except Exception as exc:
            return False, str(exc)
        finally:
            _active_session.token = previous
