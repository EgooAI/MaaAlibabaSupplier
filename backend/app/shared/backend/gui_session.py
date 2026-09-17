"""Process-local manual GUI confirmation, not automatic login identity verification.

The operator must confirm that the window belongs to the selected seller. Login
changes inside the same window cannot be detected; the operator must reconnect
and confirm again. The separate, manually invoked Maa CLI is outside this guard.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import local
from typing import TypedDict
from uuid import uuid4

from backend.app.api.envelope import AppError
from backend.app.shared.backend import maafw_runner as runner
from backend.app.shared.backend.account_context import AccountContext, account_lock, get_account_context


class ClientStatus(TypedDict):
    connected: bool
    window_generation: str
    confirmed: bool
    detail: str


@dataclass(frozen=True)
class GuiSessionToken:
    self_ali_id: str
    data_dir: str
    epoch: str
    window_generation: str
    confirmation_id: str


_confirmation: GuiSessionToken | None = None
_active_session = local()
_MANUAL_NOTICE = (
    "Manual confirmation only, not automatic login identity verification. "
    "Account changes inside the same window cannot be detected; reconnect and confirm again."
)


def _status_locked() -> ClientStatus:
    global _confirmation
    context = get_account_context()
    connected, detail = runner._probe_binding()
    if _confirmation is not None and (
        not connected
        or (_confirmation.self_ali_id, _confirmation.data_dir, _confirmation.epoch)
        != (context.self_ali_id, context.data_dir, context.epoch)
        or _confirmation.window_generation != runner._window_generation
    ):
        _confirmation = None
    confirmed = _confirmation is not None
    if connected:
        detail = "Selected account manually confirmed." if confirmed else "Manual account confirmation required."
    return {
        "connected": connected,
        "window_generation": runner._window_generation,
        "confirmed": confirmed,
        "detail": detail + " " + _MANUAL_NOTICE,
    }


def get_client_status() -> ClientStatus:
    """Probe the visible window; never initialize or reconnect while polling."""
    # A running task holds the runner lock. Observation must not delay its
    # acceptance response or the status poll used to track that same task.
    if not runner._run_lock.acquire(blocking=False):
        context = get_account_context()
        confirmation = _confirmation
        return {
            "connected": bool(runner._window_generation),
            "window_generation": runner._window_generation,
            "confirmed": bool(confirmation and confirmation.epoch == context.epoch
                              and confirmation.window_generation == runner._window_generation),
            "detail": "客户端操作正在执行；连接状态将在操作间隙重新检查。 " + _MANUAL_NOTICE,
        }
    try:
        return _status_locked()
    finally:
        runner._run_lock.release()


def connect_client() -> ClientStatus:
    """Initialize with zero GUI input and require a fresh manual confirmation."""
    global _confirmation
    with account_lock, runner._run_lock:
        _confirmation = None
        tasker, error = runner._ensure_init()
        if tasker is None:
            raise AppError(f"Client connection failed: {error}", status_code=503)
        # Even reconnecting the same HWND invalidates an already-open dialog.
        runner._window_generation = uuid4().hex
        status = _status_locked()
        if not status["connected"]:
            raise AppError(status["detail"], status_code=503)
        return status


def _require_context(expected_epoch: str) -> AccountContext:
    context = get_account_context()
    if context.epoch != expected_epoch:
        raise AppError("Account context changed; reload and manually confirm again.", status_code=409)
    if not context.self_ali_id:
        raise AppError("Select a seller before confirming the client.", status_code=409)
    return context


def confirm_client(expected_epoch: str, expected_generation: str) -> ClientStatus:
    """Record the operator's assertion; this does not discover the logged-in user."""
    global _confirmation
    with account_lock, runner._run_lock:
        status = _status_locked()
        context = _require_context(expected_epoch)
        if expected_generation != status["window_generation"]:
            raise AppError("Client window changed; reconnect and manually confirm again.", status_code=409)
        if not status["connected"]:
            raise AppError(status["detail"], status_code=503)
        _confirmation = GuiSessionToken(
            context.self_ali_id, context.data_dir, context.epoch,
            status["window_generation"], uuid4().hex,
        )
        status = _status_locked()
        if not status["confirmed"]:
            raise AppError("Client window changed during confirmation; reconnect and confirm again.", status_code=409)
        return status


def capture_gui_session(expected_epoch: str) -> GuiSessionToken:
    """Capture at enqueue time; the worker must still call run_guarded."""
    with account_lock, runner._run_lock:
        status = _status_locked()
        _require_context(expected_epoch)
        if not status["connected"]:
            raise AppError(status["detail"], status_code=503)
        if _confirmation is None:
            raise AppError("Manually confirm the selected account and client window first.", status_code=409)
        return _confirmation


def _validate_token(token: GuiSessionToken) -> None:
    """Caller holds both locks, including for each runner node inside a task."""
    status = _status_locked()
    _require_context(token.epoch)
    if token != _confirmation or token.window_generation != status["window_generation"]:
        raise AppError("GUI session expired; reconnect and manually confirm again.", status_code=409)
    if not status["connected"]:
        raise AppError(status["detail"], status_code=503)


def run_guarded(token: GuiSessionToken, fn: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    """Hold the account and runner locks for the complete synchronous GUI task.

    Account switching in another thread must use changing_account's nonblocking
    lock. Thread-local authorization does not propagate to spawned work.
    """
    with account_lock, runner._run_lock:
        previous = getattr(_active_session, "token", None)
        try:
            _validate_token(token)
            _active_session.token = token
            return fn()
        except Exception as exc:
            return False, str(exc)
        finally:
            _active_session.token = previous
