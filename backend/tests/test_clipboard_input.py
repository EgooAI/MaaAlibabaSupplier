"""Exercises Win32 clipboard/key boundaries without accessing a real desktop."""

import ctypes
import json
import struct
from io import BytesIO
from PIL import Image
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.shared.backend import clipboard_input as clip, paste_text_action
from backend.app.shared.backend import clipboard_transport as transport
from backend.app.shared.clipboard_payload import CF_UNICODETEXT, read_unicode
from backend.app.shared.image_clipboard import CF_HDROP, CF_DIBV5, image_payload, single_image_selection


def file_drop(*names):
    return struct.pack("<IiiII", 20, 0, 0, 0, 1) + ("\0".join(names) + "\0\0").encode("utf-16-le")


@pytest.fixture
def desktop(monkeypatch):
    state = SimpleNamespace(
        editor="Previous unsent draft", selection=False, pressed=set(), events=[],
        buffers={}, formats={}, transferred=[], freed=[], destroyed=[], clipboard=0, owner=0, sequence=1,
        published={}, editor_image=None, image_copy_delay=0, copies=0,
        foreground=42, focus=43, fault=None, clock=0.0, pending_copy=None, selects=0,
        clipboard_dirty=False, handoff_replied=False, owner_thread=None,
        image_readback="PNG", image_structure="marker", registered={"PNG": 49152},
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
        state.copies += 1
        text = state.editor.replace("\r\n", "\n") if state.fault == "copy_lf" else state.editor
        if state.editor_image and state.fault == "multiple_images":
            text = "\ufffc\ufffc"
        if state.editor_image and state.fault == "image_empty_text":
            text = ""
        data = (text + "\0").encode("utf-16-le") + b"\x00\xd8"  # Allocator padding may not be valid UTF-16.
        if state.fault == "copy_invalid":
            data = b"a"
        if state.fault == "copy_oversized":
            data += b" " * 65537
        handle = alloc(2, len(data))
        ctypes.memmove(handle, data, len(data))
        state.clipboard = handle
        state.formats = {CF_UNICODETEXT: handle}
        if state.editor_image is not None and state.copies > state.image_copy_delay:
            png = state.editor_image
            if state.fault == "image_changed":
                stream = BytesIO()
                with Image.open(BytesIO(png)) as image:
                    image.putpixel((0, 0), (1, 2, 3, 4))
                    image.save(stream, format="PNG")
                png = stream.getvalue()
            image_handle = alloc(2, len(png))
            ctypes.memmove(image_handle, png, len(png))
            if state.image_readback == CF_DIBV5:
                dib = dict(image_payload(png).formats)[CF_DIBV5]
                image_handle = alloc(2, len(dib))
                ctypes.memmove(image_handle, dib, len(dib))
            image_format = register(state.image_readback) if isinstance(state.image_readback, str) else state.image_readback
            state.formats[image_format] = image_handle
            if state.image_structure != "marker":
                drop = file_drop(r"C:\client\image.png", r"C:\client\other.png") if state.fault == "multiple_images" else file_drop(r"C:\client\image.png")
                drop_handle = alloc(2, len(drop))
                ctypes.memmove(drop_handle, drop, len(drop))
                state.formats[CF_HDROP] = drop_handle
                if state.image_structure == "files" and text and not text.strip("\ufffc"):
                    state.formats.pop(CF_UNICODETEXT)
            if state.fault == "image_no_text":
                state.formats.pop(CF_UNICODETEXT)
        if state.fault == "copy_formats_missing":
            state.formats.clear()
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
            if state.selection:
                state.editor_image = None
            state.editor = "" if state.selection else state.editor[:-1]
            state.selection = False
        elif key == clip.VK_V and transport.VK_CONTROL in state.pressed:
            state.published = {format: state.buffers[handle].raw for format, handle in state.formats.items()}
            if 49152 in state.published:
                state.editor_image = state.published[49152]
                value = "\ufffc"
            else:
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

    def register(name):
        return state.registered.setdefault(name, 49152 + len(state.registered))

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
        "IsClipboardFormatAvailable": lambda format: format in state.formats,
        "GetClipboardData": lambda format: 0 if state.fault == "copy_unreadable" or (state.fault == "files_unreadable" and format == CF_HDROP) else state.formats.get(format, 0),
        "RegisterClipboardFormatW": register,
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
    ("copy_formats_missing", "copied_format_missing"),
    ("copy_unreadable", "copied_format_unavailable"), ("copy_invalid", "copied_content_invalid"),
    ("copy_oversized", "copied_text_invalid_size"),
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

    _, data = api.copied_data(owner, 100, ((CF_UNICODETEXT, 4096),))
    assert read_unicode(data) == "copied text"
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


@pytest.mark.parametrize("hwnd,text,format", [
    (0, "text", "text"), (True, "text", "text"), (42, "", "text"),
    (42, "a\0b", "text"), (42, 1, "text"), (42, "<b>text</b>", "html"),
])
def test_invalid_payload_is_rejected_before_native_access(monkeypatch, hwnd, text, format):
    load = Mock(side_effect=AssertionError("Unexpected native access"))
    monkeypatch.setattr(transport, "_load_win32", load)
    with pytest.raises(ValueError):
        clip.replace_text(hwnd, text, format)
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


@pytest.mark.parametrize("content", [
    "<b>Bold test</b>", "<b>Bold test</b>\n<li>List test</li>",
    "<b>中文 🙂</b><br>Next &amp; last", '<img src="example.png">',
])
def test_html_looking_text_is_pasted_literally_and_only_as_unicode(desktop, content):
    argv = SimpleNamespace(custom_action_param=json.dumps({"text": content, "content_format": "text"}),
                           node_name="ChatInput_PasteText", task_detail=SimpleNamespace(task_id=42))
    context = SimpleNamespace(tasker=SimpleNamespace(controller=SimpleNamespace(info={"type": "win32", "hwnd": 42})))
    assert paste_text_action.PasteChatText().run(context, argv)
    assert desktop.editor == content.replace("\n", "\r\n")
    assert set(desktop.published) == {CF_UNICODETEXT}
    assert not desktop.selection and not desktop.pressed
    assert not desktop.freed and desktop.destroyed == [77]


def test_partial_multiformat_publication_frees_only_untransferred_memory(desktop):
    desktop.fault = "second_format_write"
    api = transport.Desktop()
    with pytest.raises(clip.ClipboardInputError, match="clipboard_write_failed"):
        api.write(77, (("Test Format", b"opaque data"), (CF_UNICODETEXT, "body\0".encode("utf-16-le"))))
    assert len(desktop.transferred) == len(desktop.freed) == 1
    assert set(desktop.transferred).isdisjoint(desktop.freed)
    assert desktop.events == []


@pytest.mark.parametrize("fault", [None, "image_changed", "select_ignored", "external_copy", "focus_changed", "copy_missing", "multiple_images", "image_empty_text", "image_no_text"])
def test_image_paste_verifies_pixels_and_excludes_residual_text(desktop, fault):
    stream = BytesIO()
    with Image.new("RGBA", (9, 5), (33, 44, 55, 128)) as image:
        image.save(stream, format="PNG")
    desktop.fault = fault
    if fault is None:
        desktop.image_copy_delay = 2
        clip.replace_image(42, stream.getvalue())
        assert desktop.editor == "\ufffc" and not desktop.selection
        assert desktop.copies >= 3
    else:
        with pytest.raises(clip.ClipboardInputError):
            clip.replace_image(42, stream.getvalue())
    # Copy retries must not become paste retries or submit a send key.
    assert sum(key == clip.VK_V and not up for batch in desktop.events for key, up in batch) == 1
    assert not any(key == 13 for batch in desktop.events for key, _ in batch)
    assert desktop.destroyed == [77] and not desktop.pressed


@pytest.mark.parametrize("readback", ["image/png", CF_DIBV5])
@pytest.mark.parametrize("fault", [None, "multiple_images", "select_ignored", "image_changed", "files_unreadable", "external_copy"])
def test_qt_file_drop_readback_requires_exact_single_image(desktop, readback, fault):
    stream = BytesIO()
    with Image.new("RGBA", (9, 5), (33, 44, 55, 128)) as image:
        image.save(stream, format="PNG")
    desktop.image_readback, desktop.image_structure, desktop.fault = readback, "files", fault
    if fault is None:
        clip.replace_image(42, stream.getvalue())
        assert not desktop.selection and CF_UNICODETEXT not in desktop.formats
    else:
        with pytest.raises(clip.ClipboardInputError):
            clip.replace_image(42, stream.getvalue())
        assert desktop.selection
    assert sum(key == clip.VK_V and not up for batch in desktop.events for key, up in batch) == 1
    assert not any(key == 13 for batch in desktop.events for key, _ in batch)
    assert desktop.destroyed == [77] and not desktop.pressed


@pytest.mark.parametrize("text,files,expected", [
    (None, file_drop(r"C:\图片\one.png"), True),
    ("\ufffc", file_drop(r"C:\one.png"), True),
    ("\ufffc", None, True),
    (None, None, False),
    ("", file_drop(r"C:\one.png"), False),
    ("caption", file_drop(r"C:\one.png"), False),
    ("\ufffc", file_drop(r"C:\one.png", r"C:\two.png"), False),
    (None, file_drop(), False),
])
def test_image_selection_rejects_missing_or_conflicting_evidence(text, files, expected):
    raw = (text + "\0").encode("utf-16-le") if text is not None else None
    assert single_image_selection(raw, files) is expected


@pytest.mark.parametrize("files", [
    b"", file_drop("x")[:-2], file_drop("x")[:20] + b"\x00\xd8\0\0\0\0",
    struct.pack("<IiiII", 0, 0, 0, 0, 1) + b"\0" * 4,
    struct.pack("<IiiII", 21, 0, 0, 0, 1) + b"\0" * 8,
    struct.pack("<IiiII", 400, 0, 0, 0, 1) + b"\0" * 4,
    struct.pack("<IiiII", 20, 0, 0, 0, 0) + b"x\0\0\0",
])
def test_malformed_file_drop_cannot_supply_structure_evidence(files):
    with pytest.raises(ValueError):
        single_image_selection(None, files)
