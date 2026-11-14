"""Exercises Win32 clipboard/key boundaries without accessing a real desktop."""

import ctypes
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.shared.backend import clipboard_input as clip, paste_text_action


@pytest.fixture
def desktop(monkeypatch):
    state = SimpleNamespace(
        editor="Previous unsent draft", selection=False, pressed=set(), events=[],
        buffers={}, transferred=[], freed=[], destroyed=[], clipboard=0, owner=0, sequence=1,
        foreground=42, focus=43, fault=None, clock=0.0, pending_copy=None, selects=0,
    )

    def alloc(flags, size):
        buffer = ctypes.create_string_buffer(size)
        handle = ctypes.addressof(buffer)
        state.buffers[handle] = buffer
        return handle

    def free(handle):
        state.freed.append(handle)
        del state.buffers[handle]

    def window_thread(hwnd, pid):
        ctypes.cast(pid, ctypes.POINTER(clip.wintypes.DWORD)).contents.value = 200 if hwnd == 999 else 100
        return 1

    def gui_info(thread, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(clip._GuiThreadInfo)).contents
        info.active, info.focus = 42, state.focus
        return True

    def empty():
        state.clipboard = 0
        state.owner = 77
        state.sequence += 1
        return True

    def set_data(format, handle):
        assert format == clip.CF_UNICODETEXT
        if state.fault == "write":
            return 0
        state.clipboard = handle
        state.transferred.append(handle)
        state.sequence += 1
        return handle

    def clipboard_text():
        return state.buffers[state.clipboard].raw.decode("utf-16-le").split("\0", 1)[0]

    def copy():
        text = state.editor.replace("\r\n", "\n") if state.fault == "copy_lf" else state.editor
        data = (text + "\0").encode("utf-16-le") + b"\x00\xd8"  # Allocator padding may not be valid UTF-16.
        handle = alloc(2, len(data))
        ctypes.memmove(handle, data, len(data))
        state.clipboard = handle
        state.owner = 999 if state.fault == "external_copy" else 42
        state.sequence += 1

    def process(key, up):
        if up:
            state.pressed.discard(key)
            return
        state.pressed.add(key)
        if key == clip.VK_A and clip.VK_CONTROL in state.pressed:
            state.selects += 1
            state.selection = not (state.fault == "select_ignored" and state.selects == 1)
        elif key == clip.VK_BACK:
            state.editor = "" if state.selection else state.editor[:-1]
            state.selection = False
        elif key == clip.VK_V and clip.VK_CONTROL in state.pressed:
            value = clipboard_text()
            if state.fault == "lost_newlines":
                value = value.replace("\r", "").replace("\n", "")
            state.editor = value if state.selection else state.editor + value
            state.selection = False
            if state.fault == "focus_changed":
                state.focus = 44
        elif key == clip.VK_C and clip.VK_CONTROL in state.pressed and state.selection:
            if state.fault != "copy_missing":
                state.pending_copy = copy
        elif key == clip.VK_RIGHT:
            state.selection = False

    def send(count, inputs, size):
        assert size == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        events = [(item.value.keyboard.vk, bool(item.value.keyboard.flags & clip.KEYEVENTF_KEYUP))
                  for item in inputs]
        assert all(item.type == 1 for item in inputs)
        written = 2 if state.fault == "partial" and not state.events else count
        state.events.append(events)
        for key, up in events[:written]:
            process(key, up)
        return written

    def sleep(duration):
        state.clock += duration
        if state.pending_copy:
            callback, state.pending_copy = state.pending_copy, None
            callback()

    user = SimpleNamespace(**{name: Mock(side_effect=fn) for name, fn in {
        "IsWindow": lambda hwnd: hwnd == 42,
        "GetForegroundWindow": lambda: state.foreground,
        "GetWindowThreadProcessId": window_thread,
        "GetGUIThreadInfo": gui_info,
        "GetAsyncKeyState": lambda key: 0x8000 if state.fault == "modifier_held" else 0,
        "SendInput": send,
        "CreateWindowExW": lambda *args: 77,
        "DestroyWindow": lambda hwnd: state.destroyed.append(hwnd) or True,
        "OpenClipboard": lambda owner: state.fault != "busy",
        "CloseClipboard": lambda: True,
        "EmptyClipboard": empty,
        "SetClipboardData": set_data,
        "GetClipboardData": lambda format: state.clipboard,
        "GetClipboardOwner": lambda: state.owner,
        "GetClipboardSequenceNumber": lambda: state.sequence,
    }.items()})
    kernel = SimpleNamespace(**{name: Mock(side_effect=fn) for name, fn in {
        "GlobalAlloc": alloc, "GlobalLock": lambda handle: handle,
        "GlobalUnlock": lambda handle: True, "GlobalFree": free,
        "GlobalSize": lambda handle: ctypes.sizeof(state.buffers[handle]),
    }.items()})
    state.user, state.kernel = user, kernel
    monkeypatch.setattr(clip, "_load_win32", lambda: (user, kernel))
    monkeypatch.setattr(clip, "time", SimpleNamespace(monotonic=lambda: state.clock, sleep=sleep))
    return state


@pytest.mark.parametrize("text", ["hello", " first\n\n第二段 🙂\t ", "first\r\n\r\nlast", "first\rlast"])
@pytest.mark.parametrize("copy_lf", [False, True])
def test_replaces_old_draft_preserving_text_and_blank_lines(desktop, text, copy_lf):
    desktop.fault = "copy_lf" if copy_lf else None
    clip.replace_text(42, text)
    expected = text.replace("\r\n", "\n").replace("\r", "\n")
    assert desktop.editor == expected.replace("\n", "\r\n")
    assert desktop.events == [
        [(17, False), (65, False), (65, True), (17, True), (8, False), (8, True),
         (17, False), (86, False), (86, True), (17, True),
         (17, False), (65, False), (65, True), (17, True),
         (17, False), (67, False), (67, True), (17, True)],
        [(39, False), (39, True)],
    ]
    assert not desktop.pressed and not desktop.selection
    assert desktop.clock > 0  # Waits for a fresh copy, not the original clipboard payload.
    assert desktop.destroyed == [77]
    assert not desktop.freed  # Successful SetClipboardData transfers ownership to Windows.


@pytest.mark.parametrize("fault,reason", [
    ("copy_missing", "did not publish"), ("external_copy", "outside the bound client"),
    ("lost_newlines", "does not match"), ("select_ignored", "does not match"),
    ("focus_changed", "focus changed"),
])
def test_failed_paste_or_copy_cannot_report_success(desktop, fault, reason):
    desktop.fault = fault
    with pytest.raises(RuntimeError, match=reason):
        clip.replace_text(42, "First\n\n第二段")
    assert len(desktop.events) == 1  # No retry or caret movement after failure.
    assert desktop.destroyed == [77]
    assert not desktop.pressed


def test_partial_key_injection_releases_keys_and_does_not_replay(desktop):
    desktop.fault = "partial"
    with pytest.raises(RuntimeError, match="incomplete"):
        clip.replace_text(42, "new draft")
    assert desktop.editor == "Previous unsent draft"
    assert len(desktop.events) == 2
    assert all(up for _, up in desktop.events[1])
    assert not desktop.pressed
    assert desktop.destroyed == [77]


@pytest.mark.parametrize("fault", ["write", "busy", "modifier_held", "background"])
def test_unavailable_clipboard_or_window_prevents_keyboard_input(desktop, fault):
    desktop.fault = fault
    if fault == "background":
        desktop.foreground = 99
    with pytest.raises(RuntimeError):
        clip.replace_text(42, "new draft")
    assert desktop.editor == "Previous unsent draft"
    assert desktop.events == []
    if fault in ("write", "busy"):
        assert len(desktop.freed) == 1
        assert desktop.destroyed == [77]
    else:
        assert desktop.kernel.GlobalAlloc.call_count == 0


@pytest.mark.parametrize("hwnd,text", [(0, "text"), (True, "text"), (42, ""), (42, "a\0b"), (42, 1)])
def test_invalid_payload_is_rejected_before_native_access(monkeypatch, hwnd, text):
    load = Mock(side_effect=AssertionError("Unexpected native access"))
    monkeypatch.setattr(clip, "_load_win32", load)
    with pytest.raises(ValueError):
        clip.replace_text(hwnd, text)
    load.assert_not_called()


def test_concurrent_clipboard_operation_is_rejected_before_destructive_input(desktop):
    with clip._clipboard_lock:
        with pytest.raises(RuntimeError, match="Another clipboard"):
            clip.replace_text(42, "new draft")
    assert desktop.events == []
    assert desktop.user.EmptyClipboard.call_count == 0


def test_action_uses_its_controller_and_reports_failed_verification(desktop):
    context = SimpleNamespace(tasker=SimpleNamespace(controller=SimpleNamespace(info={"type": "win32", "hwnd": 42})))
    argv = SimpleNamespace(custom_action_param=json.dumps({"text": "one\n\ntwo"}))
    action = paste_text_action.PasteChatText()
    assert action.run(context, argv)
    desktop.fault = "lost_newlines"
    assert not action.run(context, argv)
    assert desktop.editor == "onetwo"
