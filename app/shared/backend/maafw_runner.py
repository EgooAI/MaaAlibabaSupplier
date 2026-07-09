"""Run MaaFW pipeline nodes directly from the web process."""

from __future__ import annotations

import os
import re
from pathlib import Path

from loguru import logger
from maa.controller import (
    MaaWin32InputMethodEnum,
    MaaWin32ScreencapMethodEnum,
    Win32Controller,
)
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

# Must match assets/interface.json controller config
_WIN_CLASS_RE = re.compile(r"Qt")
_WIN_NAME_RE = re.compile(r"接待中心")

_resource: Resource | None = None
_tasker: Tasker | None = None
_init_error: str | None = None


def _find_best_window(windows):
    """Score windows by class/name regex match, return the best one."""
    def _score(w):
        s = 0
        if _WIN_CLASS_RE.search(w.class_name or ""):
            s += 1
        if _WIN_NAME_RE.search(w.window_name or ""):
            s += 2
        return s

    scored = [(w, _score(w)) for w in windows]
    best_w, best_s = max(scored, key=lambda x: x[1])
    if best_s == 0:
        return None
    return best_w


def _ensure_init() -> tuple[Tasker | None, str | None]:
    global _resource, _tasker, _init_error
    if _tasker is not None:
        return _tasker, None
    if _init_error is not None:
        return None, _init_error

    try:
        repo_root = Path(__file__).resolve().parents[3]
        user_path = str(repo_root / "debug")
        os.makedirs(user_path, exist_ok=True)
        Toolkit.init_option(user_path)

        windows = Toolkit.find_desktop_windows()
        if not windows:
            _init_error = "未找到任何窗口"
            return None, _init_error

        window = _find_best_window(windows)
        if window is None:
            _init_error = "未找到匹配的接待中心窗口"
            return None, _init_error

        controller = Win32Controller(
            window.hwnd,
            screencap_method=MaaWin32ScreencapMethodEnum.Background,
            mouse_method=MaaWin32InputMethodEnum.PostMessageWithCursorPos,
            keyboard_method=MaaWin32InputMethodEnum.PostMessage,
        )
        controller.post_connection().wait()

        resource_path = repo_root / "assets" / "resource"
        _resource = Resource()
        _resource.post_bundle(resource_path).wait()

        _tasker = Tasker()
        _tasker.bind(_resource, controller)

        if not _tasker.inited:
            _init_error = "MaaFW Tasker 初始化失败"
            _tasker = None
            return None, _init_error

        logger.info("MaaFW runner initialized (hwnd={}, window='{}')", window.hwnd, window.window_name)
        return _tasker, None
    except Exception as exc:
        _init_error = str(exc)
        logger.error("MaaFW runner init failed: {}", exc)
        return None, _init_error


def run_node(entry: str, pipeline_override: dict | None = None) -> tuple[bool, str]:
    """Run a pipeline node by name. Returns (success, message)."""
    tasker, err = _ensure_init()
    if tasker is None:
        return False, f"初始化失败: {err}"

    try:
        detail = tasker.post_task(entry, pipeline_override).wait().get()
        if detail and detail.status.succeeded:
            logger.info("MaaFW node '{}' succeeded", entry)
            return True, "执行成功"
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


def chat_input(text: str) -> tuple[bool, str]:
    """Type text into the chat input box via MaaFW ChatInput pipeline (no send)."""
    override = {
        "ChatInput_InputText": {
            "action": {
                "param": {
                    "input_text": text
                }
            }
        }
    }
    return run_node("ChatInput", override)


def chat_send(text: str) -> tuple[bool, str]:
    """Type text and send via MaaFW ChatInput pipeline (with send)."""
    override = {
        "ChatInput_InputText": {
            "action": {
                "param": {
                    "input_text": text
                }
            }
        },
        "ChatInput_SendMessage": {
            "enabled": True
        }
    }
    return run_node("ChatInput", override)
