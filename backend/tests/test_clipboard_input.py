"""Exercises Win32 clipboard/key boundaries without accessing a real desktop."""

import ctypes
import json
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.shared.backend import clipboard_input as clip, paste_text_action
from backend.app.shared.backend import clipboard_transport as transport
from backend.app.shared.clipboard_payload import CF_UNICODETEXT, clipboard_html, read_clipboard_html, read_unicode


@pytest.fixture
def desktop(monkeypatch):
    state = SimpleNamespace(
        editor="Previous unsent draft", selection=False, pressed=set(), events=[],
        buffers={}, formats={}, transferred=[], freed=[], destroyed=[], clipboard=0, owner=0, sequence=1,
        editor_html=None, published={},
        foreground=42, focus=43, fault=None, clock=0.0, pending_copy=None, selects=0,
        clipboard_dirty=False, handoff_replied=False, owner_thread=None,
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
        ctypes.cast(pid, ctypes.POINTER(transport.wintypes.DWORD)).contents.value = 200 if hwnd == 999 else 100
        return 1

    def gui_info(thread, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(transport._GuiThreadInfo)).contents
        info.active, info.focus = 42, state.focus
        return True

    def empty():
        state.clipboard = 0
        state.formats.clear()
        state.owner = 77
        state.sequence += 1
        state.clipboard_dirty = True
        return True

    def set_data(format, handle):
        if state.fault == "write" or (state.fault == "second_format_write" and format == CF_UNICODETEXT):
            return 0
        state.formats[format] = handle
        if format == CF_UNICODETEXT:
            state.clipboard = handle
        state.transferred.append(handle)
        state.sequence += 1
        return handle

    def close():
        modified, state.clipboard_dirty = state.clipboard_dirty, False
        if modified:
            # Windows publishes synthesized text formats when the writer closes.
            state.sequence += 3
        if ((modified and state.fault == "external_before_baseline")
                or (not modified and state.owner == 77 and state.fault == "external_after_baseline")):
            state.owner = 999
            state.sequence += 1
        if modified and state.fault == "focus_before_paste":
            state.focus = 44
        return True

    def clipboard_text():
        return state.buffers[state.clipboard].raw.decode("utf-16-le").split("\0", 1)[0]

    def copy():
        text = state.editor.replace("\r\n", "\n") if state.fault == "copy_lf" else state.editor
        data = (text + "\0").encode("utf-16-le") + b"\x00\xd8"  # Allocator padding may not be valid UTF-16.
        handle = alloc(2, len(data))
        ctypes.memmove(handle, data, len(data))
        state.clipboard = handle
        state.formats = {CF_UNICODETEXT: handle}
        if state.editor_html is not None and state.fault != "html_missing":
            copied = state.editor_html.replace("<b>", "<strong>").replace("</b>", "</strong>") if state.fault == "html_changed" else state.editor_html
            html = clipboard_html(copied)
            handle = alloc(2, len(html))
            ctypes.memmove(handle, html, len(html))
            state.formats[49152] = handle
        state.owner = 999 if state.fault == "external_copy" else 42
        state.sequence += 1

    def create_window(*args):
        state.owner_thread = get_ident()
        return 77

    def peek_message(pointer, hwnd, minimum, maximum, flags):
        assert hwnd == 77 and get_ident() == state.owner_thread
        # PeekMessage dispatches sent WM_DESTROYCLIPBOARD even when no queued
        # message is returned. The other process cannot publish until it replies.
        if state.pending_copy:
            state.handoff_replied = True
            if state.fault == "focus_changed_in_handoff":
                state.focus = 44
        return False

    def process(key, up):
        if up:
            state.pressed.discard(key)
            return
        state.pressed.add(key)
        if key == clip.VK_A and transport.VK_CONTROL in state.pressed:
            state.selects += 1
            state.selection = not (state.fault == "select_ignored" and state.selects == 1)
        elif key == clip.VK_BACK:
            state.editor = "" if state.selection else state.editor[:-1]
            state.editor_html = None
            state.selection = False
        elif key == clip.VK_V and transport.VK_CONTROL in state.pressed:
            state.published = {format: state.buffers[handle].raw for format, handle in state.formats.items()}
            if 49152 in state.formats:
                state.editor_html = read_clipboard_html(state.published[49152])
            value = clipboard_text()
            if state.fault == "lost_newlines":
                value = value.replace("\r", "").replace("\n", "")
            state.editor = value if state.selection else state.editor + value
            state.selection = False
            if state.fault == "focus_changed":
                state.focus = 44
        elif key == clip.VK_C and transport.VK_CONTROL in state.pressed and state.selection:
            if state.fault != "copy_missing":
                state.pending_copy = copy
                state.handoff_replied = False
        elif key == clip.VK_RIGHT:
            state.selection = False

    def send(count, inputs, size):
        assert size == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        events = [(item.value.keyboard.vk, bool(item.value.keyboard.flags & transport.KEYEVENTF_KEYUP))
                  for item in inputs]
        assert all(item.type == 1 for item in inputs)
        written = 2 if state.fault == "partial" and not state.events else count
        state.events.append(events)
        for key, up in events[:written]:
            process(key, up)
        return written

    def sleep(duration):
        state.clock += duration
        if state.pending_copy and state.handoff_replied:
            callback, state.pending_copy = state.pending_copy, None
            callback()

    user = SimpleNamespace(**{name: Mock(side_effect=fn) for name, fn in {
        "IsWindow": lambda hwnd: hwnd == 42,
        "GetForegroundWindow": lambda: state.foreground,
        "GetWindowThreadProcessId": window_thread,
        "GetGUIThreadInfo": gui_info,
        "GetAsyncKeyState": lambda key: 0x8000 if state.fault == "modifier_held" else 0,
        "SendInput": send,
        "CreateWindowExW": create_window,
        "DestroyWindow": lambda hwnd: state.destroyed.append(hwnd) or True,
        "OpenClipboard": lambda owner: state.fault != "busy" and state.pending_copy is None,
        "CloseClipboard": close,
        "EmptyClipboard": empty,
        "SetClipboardData": set_data,
        "GetClipboardData": lambda format: state.formats.get(format, 0),
        "RegisterClipboardFormatW": lambda name: 49152,
        "GetClipboardOwner": lambda: state.owner,
        "GetClipboardSequenceNumber": lambda: state.sequence,
        "PeekMessageW": peek_message,
        "DispatchMessageW": lambda pointer: 0,
    }.items()})
    kernel = SimpleNamespace(**{name: Mock(side_effect=fn) for name, fn in {
        "GlobalAlloc": alloc, "GlobalLock": lambda handle: handle,
        "GlobalUnlock": lambda handle: True, "GlobalFree": free,
        "GlobalSize": lambda handle: ctypes.sizeof(state.buffers[handle]),
    }.items()})
    state.user, state.kernel = user, kernel
    monkeypatch.setattr(transport, "_load_win32", lambda: (user, kernel))
    clock = SimpleNamespace(monotonic=lambda: state.clock, sleep=sleep)
    monkeypatch.setattr(clip, "time", clock)
    monkeypatch.setattr(transport, "time", clock)
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
    ("copy_missing", "copied_text_not_published"), ("external_copy", "clipboard_owner_mismatch"),
    ("lost_newlines", "pasted_text_mismatch"), ("select_ignored", "pasted_text_mismatch"),
    ("focus_changed", "focus_changed_during_paste"),
    ("focus_changed_in_handoff", "focus_changed_during_paste"),
])
def test_failed_paste_or_copy_cannot_report_success(desktop, fault, reason):
    desktop.fault = fault
    with pytest.raises(RuntimeError, match=reason):
        clip.replace_text(42, "First\n\n第二段")
    assert len(desktop.events) == 1  # No retry or caret movement after failure.
    assert desktop.destroyed == [77]
    assert not desktop.pressed


def test_clipboard_read_waits_for_owner_notification_before_reopening(desktop):
    api = transport.Desktop()
    owner = api.create_window()
    api.write(owner, ((CF_UNICODETEXT, "original\0".encode("utf-16-le")),))
    desktop.editor = "copied text"
    desktop.selection = True
    api.keys(transport._chord(clip.VK_C))

    assert read_unicode(api.copied_data(owner, 100, CF_UNICODETEXT, 4096)) == "copied text"
    assert desktop.owner == 42
    assert desktop.pending_copy is None
    assert 0 < desktop.clock < 1.0


def test_partial_key_injection_releases_keys_and_does_not_replay(desktop):
    desktop.fault = "partial"
    with pytest.raises(RuntimeError, match="incomplete"):
        clip.replace_text(42, "new draft")
    assert desktop.editor == "Previous unsent draft"
    assert len(desktop.events) == 2
    assert all(up for _, up in desktop.events[1])
    assert not desktop.pressed
    assert desktop.destroyed == [77]


@pytest.mark.parametrize("fault", ["external_before_baseline", "external_after_baseline", "focus_before_paste"])
def test_changes_around_closed_clipboard_baseline_prevent_input(desktop, fault):
    desktop.fault = fault
    with pytest.raises(clip.ClipboardInputError, match="focus_or_clipboard_changed_before_paste"):
        clip.replace_text(42, "new draft")
    assert desktop.editor == "Previous unsent draft"
    assert desktop.events == []
    assert desktop.destroyed == [77]
    assert not desktop.freed  # The clipboard owns the successfully transferred allocation.


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
    monkeypatch.setattr(transport, "_load_win32", load)
    with pytest.raises(ValueError):
        clip.replace_text(hwnd, text)
    load.assert_not_called()


def test_concurrent_clipboard_operation_is_rejected_before_destructive_input(desktop):
    with clip._clipboard_lock:
        with pytest.raises(RuntimeError, match="clipboard_operation_active"):
            clip.replace_text(42, "new draft")
    assert desktop.events == []
    assert desktop.user.EmptyClipboard.call_count == 0


def test_action_uses_its_controller_and_reports_failed_verification(desktop):
    context = SimpleNamespace(tasker=SimpleNamespace(controller=SimpleNamespace(info={"type": "win32", "hwnd": 42})))
    argv = SimpleNamespace(custom_action_param=json.dumps({"text": "one\n\ntwo"}),
                           node_name="ChatInput_PasteText", task_detail=SimpleNamespace(task_id=42))
    action = paste_text_action.PasteChatText()
    assert action.run(context, argv)
    desktop.fault = "lost_newlines"
    assert not action.run(context, argv)
    assert desktop.editor == "onetwo"


@pytest.mark.parametrize("content_format", ["text", "html"])
def test_html_literal_or_rendered_paste_uses_selected_format(desktop, content_format):
    content = "<b>中文 🙂</b><br>Next &amp; last"
    argv = SimpleNamespace(custom_action_param=json.dumps({"text": content, "content_format": content_format}),
                           node_name="ChatInput_PasteText", task_detail=SimpleNamespace(task_id=42))
    context = SimpleNamespace(tasker=SimpleNamespace(controller=SimpleNamespace(info={"type": "win32", "hwnd": 42})))
    assert paste_text_action.PasteChatText().run(context, argv)
    if content_format == "html":
        assert desktop.editor_html == content
        assert desktop.editor == "中文 🙂\r\nNext & last"
        assert set(desktop.published) == {CF_UNICODETEXT, 49152}
    else:
        assert desktop.editor_html is None and desktop.editor == content
        assert set(desktop.published) == {CF_UNICODETEXT}
    assert not desktop.freed and desktop.destroyed == [77]


@pytest.mark.parametrize("fault", ["html_missing", "html_changed", "external_copy", "focus_changed"])
def test_html_readback_requires_fresh_matching_html_not_just_plain_text(desktop, fault):
    desktop.fault = fault
    with pytest.raises(clip.ClipboardInputError):
        clip.replace_text(42, "<b>same words</b>", "html")
    assert len(desktop.events) == 1
    assert desktop.destroyed == [77]


def test_partial_multiformat_publication_frees_only_untransferred_memory(desktop):
    desktop.fault = "second_format_write"
    with pytest.raises(clip.ClipboardInputError, match="clipboard_write_failed"):
        clip.replace_text(42, "<b>body</b>", "html")
    assert len(desktop.transferred) == len(desktop.freed) == 1
    assert set(desktop.transferred).isdisjoint(desktop.freed)
    assert desktop.events == []
