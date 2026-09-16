"""Run MaaFW pipeline nodes directly from the web process."""

from __future__ import annotations

import os
import re
from threading import Lock

from loguru import logger
from maa.controller import (
    MaaWin32InputMethodEnum,
    MaaWin32ScreencapMethodEnum,
    Win32Controller,
)
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

# Must match backend/assets/interface.json controller config.
_WIN_CLASS_RE = re.compile(r"Qt")
_WIN_NAME_RE = re.compile(r"接待中心")

_resource: Resource | None = None
_tasker: Tasker | None = None
_window_hwnd: int | None = None
_run_lock = Lock()


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


def _ensure_init() -> tuple[Tasker | None, str | None]:
    global _resource, _tasker, _window_hwnd

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

        _tasker = None
        _resource = None
        _window_hwnd = None

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

        logger.info("MaaFW runner initialized (hwnd={}, window='{}')", window.hwnd, window.window_name)
        return _tasker, None
    except Exception as exc:
        _tasker = None
        _resource = None
        _window_hwnd = None
        logger.error("MaaFW runner init failed: {}", exc)
        return None, str(exc)


def run_node(entry: str, pipeline_override: dict | None = None) -> tuple[bool, str]:
    """Run a pipeline node by name. Returns (success, message)."""
    with _run_lock:
        tasker, err = _ensure_init()
        if tasker is None:
            return False, f"初始化失败: {err}"

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


def chat_send(text: str) -> tuple[bool, str]:
    """Type text and click send; success does not confirm platform delivery."""
    return run_node("ChatInput", chat_input_override(text, send=True))
