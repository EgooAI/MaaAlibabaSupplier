import ctypes
import platform
import subprocess
import time
from pathlib import Path

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_recognition import CustomRecognition

from app.agent.nodes.auxi.read_clipboard import matches_expected_text, read_clipboard_text


VK_C = 0x43
VK_CONTROL = 0x11
KEYEVENTF_KEYUP = 0x0002


def _save_latest_copy_text(text: str) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    output_path = repo_root / "debug" / "copy_text.latest.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


def copy_selected_text(expect=None) -> dict:
    if not send_copy_shortcut():
        return {
            "text": "",
            "matched": False,
            "expect": expect,
            "error": "Copy shortcut is unavailable on this platform.",
        }

    time.sleep(0.1)
    clipboard_text = read_clipboard_text()
    if clipboard_text is None:
        return {
            "text": "",
            "matched": False,
            "expect": expect,
            "error": "Clipboard text is unavailable after copy.",
        }

    matched = matches_expected_text(clipboard_text, expect)
    _save_latest_copy_text(clipboard_text)
    return {
        "text": clipboard_text,
        "matched": matched,
        "expect": expect,
    }


def send_copy_shortcut() -> bool:
    system = platform.system()
    if system == "Windows":
        return _send_windows_copy_shortcut()
    if system == "Darwin":
        return _send_macos_copy_shortcut()
    return False


def _send_macos_copy_shortcut() -> bool:
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "c" using command down',
            ],
            capture_output=True,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def _send_windows_copy_shortcut() -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    keybd_event = user32.keybd_event
    keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_ulong]
    keybd_event.restype = None

    try:
        keybd_event(VK_CONTROL, 0, 0, 0)
        keybd_event(VK_C, 0, 0, 0)
        keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
        keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    except OSError:
        return False
    return True


@AgentServer.custom_recognition("CopyText")
class CopyTextRecognition(CustomRecognition):

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult:
        del context

        param = getattr(argv, "custom_recognition_param", None)
        if not isinstance(param, dict):
            param = {}

        detail = copy_selected_text(expect=param.get("expect", None))

        return CustomRecognition.AnalyzeResult(
            box=(0, 0, 1, 1) if detail.get("matched") else None,
            detail=detail,
        )
