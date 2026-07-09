import ctypes
import platform
import re
import subprocess
import time
from ctypes import wintypes
from typing import Any, List, Optional

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_recognition import CustomRecognition


CF_UNICODETEXT = 13


def read_clipboard_text() -> Optional[str]:
    system = platform.system()
    if system == "Windows":
        return _normalize_newlines(_read_windows_clipboard_text())
    if system == "Darwin":
        return _normalize_newlines(_read_macos_clipboard_text())
    return None


def matches_expected_text(text: str, expect: Any) -> bool:
    patterns = _normalize_expect(expect)
    if patterns is None:
        return True
    if not patterns:
        return False

    for pattern in patterns:
        if re.fullmatch(pattern, text) is not None:
            return True
    return False


def _normalize_expect(expect: Any) -> Optional[List[str]]:
    if expect is None:
        return None
    if isinstance(expect, str):
        return [expect]
    if isinstance(expect, list):
        return [item for item in expect if isinstance(item, str)]
    return []


def _normalize_newlines(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_macos_clipboard_text() -> Optional[str]:
    try:
        result = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout


def _read_windows_clipboard_text() -> Optional[str]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    open_clipboard = user32.OpenClipboard
    open_clipboard.argtypes = [wintypes.HWND]
    open_clipboard.restype = wintypes.BOOL

    close_clipboard = user32.CloseClipboard
    close_clipboard.argtypes = []
    close_clipboard.restype = wintypes.BOOL

    is_format_available = user32.IsClipboardFormatAvailable
    is_format_available.argtypes = [wintypes.UINT]
    is_format_available.restype = wintypes.BOOL

    get_clipboard_data = user32.GetClipboardData
    get_clipboard_data.argtypes = [wintypes.UINT]
    get_clipboard_data.restype = wintypes.HANDLE

    global_lock = kernel32.GlobalLock
    global_lock.argtypes = [wintypes.HANDLE]
    global_lock.restype = wintypes.LPVOID

    global_unlock = kernel32.GlobalUnlock
    global_unlock.argtypes = [wintypes.HANDLE]
    global_unlock.restype = wintypes.BOOL

    for _ in range(5):
        if open_clipboard(None):
            break
        time.sleep(0.05)
    else:
        return None

    try:
        if not is_format_available(CF_UNICODETEXT):
            return None

        handle = get_clipboard_data(CF_UNICODETEXT)
        if not handle:
            return None

        locked = global_lock(handle)
        if not locked:
            return None

        try:
            return ctypes.wstring_at(locked)
        finally:
            global_unlock(handle)
    finally:
        close_clipboard()


@AgentServer.custom_recognition("ReadClipboard")
class ReadClipboardRecognition(CustomRecognition):

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        del context

        param = getattr(argv, "custom_recognition_param", None)
        if not isinstance(param, dict):
            param = {}

        expect = param.get("expect")
        clipboard_text = read_clipboard_text()

        if clipboard_text is None:
            return CustomRecognition.AnalyzeResult(
                box=None,
                detail={
                    "text": "",
                    "matched": False,
                    "expect": expect,
                    "error": "Clipboard text is unavailable.",
                },
            )

        matched = matches_expected_text(clipboard_text, expect)

        return CustomRecognition.AnalyzeResult(
            box=(0, 0, 1, 1) if matched else None,
            detail={
                "text": clipboard_text,
                "matched": matched,
                "expect": expect,
            },
        )
