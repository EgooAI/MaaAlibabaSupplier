"""Orchestrates chat-editor replacement independently of clipboard format encoding."""

from threading import Lock
import time

from backend.app.shared.backend.clipboard_transport import (
    ClipboardInputError, Desktop, VK_A, VK_BACK, VK_C, VK_RIGHT, VK_V, _chord,
)
from backend.app.shared.clipboard_payload import text_payload


_clipboard_lock = Lock()


def replace_text(hwnd: int, text: str, content_format: str = "text") -> None:
    """Selects, clears, pastes and verifies editor content without sending."""
    if type(hwnd) is not int or hwnd <= 0:
        raise ValueError("Invalid clipboard target")
    payload = text_payload(text, content_format)
    if not _clipboard_lock.acquire(blocking=False):
        raise ClipboardInputError("clipboard_operation_active")
    owner = None
    try:
        desktop = Desktop()
        target = desktop.focus(hwnd)
        if any(desktop.key_state(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise ClipboardInputError("modifier_key_held")
        owner = desktop.create_window(0, "STATIC", "MaaClipboardInput", 0, 0, 0, 0, 0, -3, None, None, None)
        if not owner:
            raise ClipboardInputError("clipboard_owner_window_unavailable")
        sequence = desktop.write(owner, payload.formats)
        if desktop.focus(hwnd) != target or desktop.sequence() != sequence:
            raise ClipboardInputError("focus_or_clipboard_changed_before_paste")
        # Copy follows paste in the same input queue and must publish fresh data.
        desktop.keys(_chord(VK_A) + [(VK_BACK, False), (VK_BACK, True)] + _chord(VK_V)
                     + _chord(VK_A) + _chord(VK_C))
        deadline = time.monotonic() + 2.0
        while True:
            desktop.pump_messages(owner)
            if desktop.focus(hwnd) != target:
                raise ClipboardInputError("focus_changed_during_paste")
            if desktop.sequence() != sequence:
                index, data = desktop.copied_data(owner, target[0], tuple(
                    (readback.format, readback.max_bytes) for readback in payload.readbacks
                ))
                try:
                    matches = payload.readbacks[index].matches(data)
                except ValueError as exc:
                    raise ClipboardInputError("copied_content_invalid") from exc
                if not matches:
                    raise ClipboardInputError("pasted_text_mismatch")
                if desktop.focus(hwnd) != target:
                    raise ClipboardInputError("focus_changed_after_paste")
                desktop.keys([(VK_RIGHT, False), (VK_RIGHT, True)])
                return
            if time.monotonic() >= deadline:
                raise ClipboardInputError("copied_text_not_published")
            time.sleep(0.01)
    finally:
        try:
            if owner:
                desktop.destroy_window(owner)
        finally:
            _clipboard_lock.release()
