"""Run MaaFW pipeline nodes directly from the web process."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from threading import RLock
from uuid import uuid4

from loguru import logger
from maa.controller import (
    MaaWin32InputMethodEnum,
    MaaWin32ScreencapMethodEnum,
    Win32Controller,
)
from maa.job import TaskJob
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

from backend.app.shared.backend.account_context import account_lock
from backend.app.shared.backend.gui_evidence import ClientFrame, frame_from_image
from backend.app.shared.utils.log_context import log_event
from backend.app.shared.utils.native_logs import NativeLogSessions, NATIVE_MAX_BYTES

# Must match backend/assets/interface.json controller config.
_WIN_CLASS_RE = re.compile(r"Qt")
_WIN_NAME_RE = re.compile(r"接待中心")

_resource: Resource | None = None
_tasker: Tasker | None = None
_window_hwnd: int | None = None
_window_generation = ""
_run_lock = RLock()
_READ_ONLY_ENTRIES = frozenset({"Diagnostics_ChatInput", "Diagnostics_ContactSearch"})
_native_logs: NativeLogSessions | None = None
_native_jobs: dict[int, object] = {}
_native_post_uncertain = False
_native_log_observation: dict = {}


def _set_native_log_dir(path: Path) -> bool:
    # The installed Python binding passes len(str), which truncates UTF-8 paths.
    from maa.define import MaaBool, MaaGlobalOption, MaaGlobalOptionEnum, MaaOptionValue, MaaOptionValueSize
    from maa.library import Library

    encoded = str(path).encode("utf-8")
    setter = Library.framework().MaaGlobalSetOption
    setter.restype = MaaBool
    setter.argtypes = [MaaGlobalOption, MaaOptionValue, MaaOptionValueSize]
    return bool(setter(MaaGlobalOption(MaaGlobalOptionEnum.LogDir), encoded, len(encoded)))


def get_native_log_status() -> dict:
    """Noninitializing, constant-size cached observation; no native or disk calls."""
    snapshot = _native_log_observation
    warnings = list(snapshot.get("warnings", ()))
    if _native_jobs or _native_post_uncertain:
        warnings.append("native_rotation_deferred_outstanding_jobs")
    return {
        "initialized": snapshot.get("active_session") is not None,
        "active_session": snapshot.get("active_session"),
        "bytes": snapshot.get("bytes"), "max_bytes": NATIVE_MAX_BYTES,
        "max_age_days": 7, "observed_at": snapshot.get("observed_at"),
        "outstanding_jobs": len(_native_jobs), "post_uncertain": _native_post_uncertain,
        "warnings": warnings,
    }


def _prepare_native_logs(*, force: bool = False) -> None:
    """Best effort at an outer operation boundary while holding _run_lock."""
    global _native_logs, _native_log_observation
    if _native_jobs or _native_post_uncertain:
        return
    try:
        if _native_logs is None:
            _native_logs = NativeLogSessions()
        _native_logs.prepare_session(_set_native_log_dir, quiescent=True, force=force)
        observed = _native_logs.status()
        _native_log_observation = {
            "active_session": observed["active_session"], "bytes": observed["bytes"],
            "warnings": tuple(observed["warnings"]), "observed_at": time.time(),
        }
    except Exception:
        # Logging policy failure must not interfere with an existing GUI job.
        previous = _native_log_observation
        active = _native_logs.active if _native_logs is not None else None
        warnings = (*previous.get("warnings", ()), "native_log_policy_unavailable")
        if _native_logs is not None and _native_logs._switch_uncertain:
            active = None
            warnings += ("native_log_switch_uncertain", "native_active_writer_unbounded")
        _native_log_observation = {
            **previous, "active_session": active.name if active else None,
            "warnings": tuple(dict.fromkeys(warnings)),
        }


def _post_native(post, *args):
    global _native_post_uncertain
    try:
        job = post(*args)
    except BaseException:
        # A post exception may occur after native submission. Never infer idle.
        _native_post_uncertain = True
        raise
    _native_jobs[id(job)] = job
    return job


def _wait_native(job):
    result = job.wait()
    # A failed wait retains the job. A failed result getter does not undo a
    # completed wait. If terminal-state observation fails, still return the wait
    # result to the caller and conservatively defer rotation.
    try:
        complete = job.done is True
    except Exception:
        complete = False
    if complete:
        with _run_lock:
            _native_jobs.pop(id(job), None)
    return result


def _find_window(windows):
    matches = [
        window for window in windows
        if _WIN_CLASS_RE.search(window.class_name or "")
        and _WIN_NAME_RE.search(window.window_name or "")
    ]
    if not matches:
        raise RuntimeError("未找到匹配的接待中心窗口")
    if len(matches) != 1:
        raise RuntimeError("找到多个匹配的接待中心窗口，无法确定操作目标")
    return matches[0]


def _reset_binding() -> None:
    global _resource, _tasker, _window_hwnd, _window_generation
    _resource = None
    _tasker = None
    _window_hwnd = None
    _window_generation = ""


def _probe_binding() -> tuple[bool, str]:
    """Observe without reconnecting. Caller holds account_lock then _run_lock."""
    try:
        window = _find_window(Toolkit.find_desktop_windows())
        if _tasker is None:
            return False, "Client is not connected; connect first."
        if _window_hwnd != window.hwnd:
            raise RuntimeError("Client window changed; reconnect and retry.")
        if not _tasker.inited or not _tasker.controller.connected:
            raise RuntimeError("Client connection lost; reconnect and retry.")
        return True, "Client window is connected."
    except Exception as exc:
        _reset_binding()
        return False, str(exc)


def _ensure_init() -> tuple[Tasker | None, str | None]:
    """Connect without input. Caller holds account_lock then _run_lock."""
    global _resource, _tasker, _window_hwnd, _window_generation

    try:
        from backend.app.shared.utils.settings import resolve_backend_root
        from backend.app.shared.backend.gui_session import _active_session

        backend_root = resolve_backend_root()
        outer_boundary = getattr(_active_session, "token", None) is None
        if outer_boundary:
            _prepare_native_logs()
        if _tasker is None:
            if not outer_boundary or _native_jobs or _native_post_uncertain:
                raise RuntimeError("Native initialization deferred until outstanding operations are resolved")
            user_path = str(backend_root / "debug")
            os.makedirs(user_path, exist_ok=True)
            try:
                initialized = Toolkit.init_option(user_path)
            finally:
                _prepare_native_logs(force=True)
            if not initialized:
                raise RuntimeError("MaaFW 配置初始化失败")

        window = _find_window(Toolkit.find_desktop_windows())
        if (
            _tasker is not None
            and _window_hwnd == window.hwnd
            and _tasker.inited
            and _tasker.controller.connected
        ):
            return _tasker, None

        _reset_binding()

        controller = Win32Controller(
            window.hwnd,
            screencap_method=MaaWin32ScreencapMethodEnum.Background,
            mouse_method=MaaWin32InputMethodEnum.Seize,
            keyboard_method=MaaWin32InputMethodEnum.SendMessage,
        )
        if not controller.set_screenshot_target_long_side(1280):
            raise RuntimeError("MaaFW 截图尺寸设置失败")
        if not _wait_native(_post_native(controller.post_connection)).succeeded:
            raise RuntimeError("MaaFW 窗口连接失败")

        resource = Resource()
        if not _wait_native(_post_native(resource.post_bundle, backend_root / "assets" / "resource")).succeeded:
            raise RuntimeError("MaaFW 资源加载失败")

        tasker = Tasker()
        if not tasker.bind(resource, controller) or not tasker.inited:
            raise RuntimeError("MaaFW Tasker 初始化失败")

        _resource = resource
        _tasker = tasker
        _window_hwnd = window.hwnd
        _window_generation = uuid4().hex

        try:
            log_event("maa.initialized", native_runtime_id=_window_generation,
                      native_log_session_id=_native_log_observation.get("active_session"))
        except Exception:
            pass
        return _tasker, None
    except Exception as exc:
        _reset_binding()
        logger.opt(exception=True).error("MaaFW runner init failed")
        return None, str(exc)


def _log_native_job(event: str, job: TaskJob, entry: str) -> None:
    # Even reading a native telemetry property may fail. Never skip wait or replay.
    try:
        log_event(event, native_job_id=job.job_id, native_runtime_id=_window_generation, entry=entry,
                  native_log_session_id=_native_log_observation.get("active_session"))
    except Exception:
        pass


def run_node(entry: str, pipeline_override: dict | None = None) -> tuple[bool, str]:
    """Run writes only inside run_guarded; bare diagnostics must have no overrides."""
    from backend.app.api.envelope import AppError
    from backend.app.shared.backend import gui_session

    with account_lock, _run_lock:
        token = getattr(gui_session._active_session, "token", None)
        if token is not None:
            try:
                gui_session._validate_token(token)
            except AppError as exc:
                return False, exc.message
            tasker = _tasker
        elif entry in _READ_ONLY_ENTRIES and not pipeline_override:
            tasker, err = _ensure_init()
            if tasker is None:
                return False, f"初始化失败: {err}"
        else:
            return False, "GUI writes require a valid session and run_guarded."

        # Never replay a failed task: its send click may already have happened.
        try:
            job = _post_native(tasker.post_task, entry, pipeline_override or {})
            _log_native_job("maa.submitted", job, entry)
            detail = _wait_native(job).get()
            _log_native_job("maa.succeeded" if detail and detail.status.succeeded else "maa.failed", job, entry)
            if detail and detail.status.succeeded:
                return True, "GUI 操作完成（不代表平台已确认发送）"
            msg = f"状态: {detail.status}" if detail else "无返回"
            return False, msg
        except Exception as exc:
            logger.opt(exception=True).error("MaaFW node execution failed")
            return False, str(exc)


def capture_client_frame() -> ClientFrame:
    """Capture with zero input inside run_guarded; failures never return old evidence.

    The service owns timestamps, expiry and task-specific storage paths. This
    function raises on failure; run_guarded reports it as (False, reason).
    """
    from backend.app.api.envelope import AppError
    from backend.app.shared.backend import gui_session

    with account_lock, _run_lock:
        token = getattr(gui_session._active_session, "token", None)
        if token is None:
            raise AppError("Screenshot capture requires run_guarded.", status_code=409)
        gui_session._validate_token(token)
        job = _wait_native(_post_native(_tasker.controller.post_screencap))
        # MaaFW's result getter reads cached_image even after a failed capture.
        if not job.succeeded:
            raise RuntimeError("Fresh client screenshot failed.")
        frame = frame_from_image(job.get())
        gui_session._validate_token(token)
        return frame


def goto_contact(contact_ref: str) -> tuple[bool, str]:
    """Navigate to a contact's chat window via MaaFW ContactSearch pipeline."""
    override = {
        "ContactSearch_InputText": {
            "action": {
                "param": {
                    "input_text": contact_ref
                }
            }
        }
    }
    return run_node("ContactSearch", override)


def chat_input_override(text: str, *, send: bool) -> dict:
    """Specify both branches on every call, including nested Agent tasks."""
    return {
        "ChatInput_InputText": {
            "action": {
                "param": {
                    "input_text": text
                }
            },
            "next": ["ChatInput_SendMessage"] if send else [],
        },
        "ChatInput_SendMessage": {"enabled": send},
    }


def chat_input(text: str) -> tuple[bool, str]:
    """Type text into the chat input box via MaaFW ChatInput pipeline (no send)."""
    return run_node("ChatInput", chat_input_override(text, send=False))


def submit_send() -> TaskJob:
    """Post once without waiting, inside run_guarded and the service admission lock.

    Persist send uncertainty first. A post exception cannot prove no action was
    submitted. Keep the outer GUI/account guard until wait_send has completed.
    """
    from backend.app.api.envelope import AppError
    from backend.app.shared.backend import gui_session

    with account_lock, _run_lock:
        token = getattr(gui_session._active_session, "token", None)
        if token is None:
            raise AppError("GUI writes require a valid session and run_guarded.", status_code=409)
        gui_session._validate_token(token)
        job = _post_native(_tasker.post_task, "ChatInput_SendOnly", {"ChatInput_SendMessage": {"enabled": True}})
        _log_native_job("maa.submitted", job, "ChatInput_SendOnly")
        return job


def wait_send(job: TaskJob) -> tuple[bool, str]:
    """Wait for an already posted job; never submit or replay any GUI action."""
    with _run_lock:
        try:
            detail = _wait_native(job).get()
            _log_native_job("maa.succeeded" if detail and detail.status.succeeded else "maa.failed", job, "ChatInput_SendOnly")
            if detail and detail.status.succeeded:
                return True, "GUI send completed (not a delivery confirmation)."
            return False, f"Send task status: {detail.status}" if detail else "No send task result"
        except Exception as exc:
            logger.opt(exception=True).error("MaaFW send wait failed")
            return False, str(exc)
