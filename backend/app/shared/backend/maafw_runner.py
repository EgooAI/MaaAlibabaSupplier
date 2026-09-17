"""Run MaaFW pipeline nodes directly from the web process."""

from __future__ import annotations

import os
import re
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

# Must match backend/assets/interface.json controller config.
_WIN_CLASS_RE = re.compile(r"Qt")
_WIN_NAME_RE = re.compile(r"接待中心")

_resource: Resource | None = None
_tasker: Tasker | None = None
_window_hwnd: int | None = None
_window_generation = ""
_run_lock = RLock()
_READ_ONLY_ENTRIES = frozenset({"Diagnostics_ChatInput", "Diagnostics_ContactSearch"})


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
            return False, "Client is not connected; connect and manually confirm the selected account."
        if _window_hwnd != window.hwnd:
            raise RuntimeError("Client window changed; reconnect and manually confirm again.")
        if not _tasker.inited or not _tasker.controller.connected:
            raise RuntimeError("Client connection lost; reconnect and manually confirm again.")
        return True, "Client window is connected."
    except Exception as exc:
        _reset_binding()
        return False, str(exc)


def _ensure_init() -> tuple[Tasker | None, str | None]:
    """Connect without input. Caller holds account_lock then _run_lock."""
    global _resource, _tasker, _window_hwnd, _window_generation

    try:
        from backend.app.shared.utils.settings import resolve_backend_root

        backend_root = resolve_backend_root()
        if _tasker is None:
            user_path = str(backend_root / "debug")
            os.makedirs(user_path, exist_ok=True)
            if not Toolkit.init_option(user_path):
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
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("MaaFW 窗口连接失败")

        resource = Resource()
        if not resource.post_bundle(backend_root / "assets" / "resource").wait().succeeded:
            raise RuntimeError("MaaFW 资源加载失败")

        tasker = Tasker()
        if not tasker.bind(resource, controller) or not tasker.inited:
            raise RuntimeError("MaaFW Tasker 初始化失败")

        _resource = resource
        _tasker = tasker
        _window_hwnd = window.hwnd
        _window_generation = uuid4().hex

        logger.info("MaaFW runner initialized (hwnd={}, window='{}')", window.hwnd, window.window_name)
        return _tasker, None
    except Exception as exc:
        _reset_binding()
        logger.error("MaaFW runner init failed: {}", exc)
        return None, str(exc)


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
            return False, "GUI writes require a manually confirmed session and run_guarded."

        # Never replay a failed task: its send click may already have happened.
        try:
            detail = tasker.post_task(entry, pipeline_override or {}).wait().get()
            if detail and detail.status.succeeded:
                logger.info("MaaFW node '{}' succeeded", entry)
                return True, "GUI 操作完成（不代表平台已确认发送）"
            msg = f"状态: {detail.status}" if detail else "无返回"
            logger.warning("MaaFW node '{}' failed: {}", entry, msg)
            return False, msg
        except Exception as exc:
            logger.error("MaaFW node '{}' exception: {}", entry, exc)
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
        job = _tasker.controller.post_screencap().wait()
        # MaaFW's result getter reads cached_image even after a failed capture.
        if not job.succeeded:
            raise RuntimeError("Fresh client screenshot failed.")
        frame = frame_from_image(job.get())
        gui_session._validate_token(token)
        return frame


def goto_contact(login_id: str) -> tuple[bool, str]:
    """Navigate to a contact's chat window via MaaFW ContactSearch pipeline."""
    override = {
        "ContactSearch_InputText": {
            "action": {
                "param": {
                    "input_text": login_id
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
            raise AppError("GUI writes require a manually confirmed session and run_guarded.", status_code=409)
        gui_session._validate_token(token)
        return _tasker.post_task("ChatInput_SendOnly", {"ChatInput_SendMessage": {"enabled": True}})


def wait_send(job: TaskJob) -> tuple[bool, str]:
    """Wait for an already posted job; never submit or replay any GUI action."""
    try:
        detail = job.wait().get()
        if detail and detail.status.succeeded:
            return True, "GUI send completed (not a delivery confirmation)."
        return False, f"Send task status: {detail.status}" if detail else "No send task result"
    except Exception as exc:
        logger.error("MaaFW send wait failed: {}", exc)
        return False, str(exc)


def click_send() -> tuple[bool, str]:
    """Synchronous guarded send for callers without a separate admission lock."""
    try:
        return wait_send(submit_send())
    except Exception as exc:
        logger.error("MaaFW send submission failed: {}", exc)
        return False, str(exc)


def chat_send(text: str) -> tuple[bool, str]:
    """Type text and click send; success does not confirm platform delivery."""
    return run_node("ChatInput", chat_input_override(text, send=True))
