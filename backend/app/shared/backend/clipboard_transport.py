"""Provides format-independent Win32 clipboard ownership, memory and input I/O."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import time


VK_CONTROL, VK_A, VK_C, VK_V = 0x11, 0x41, 0x43, 0x56
VK_BACK, VK_RIGHT = 0x08, 0x27
KEYEVENTF_KEYUP = 0x0002


class ClipboardInputError(RuntimeError):
    """Reports a fixed reason code without including clipboard or message text."""


class _KeyboardInput(ctypes.Structure):
    _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    _fields_ = [("keyboard", _KeyboardInput), ("mouse", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", _InputUnion)]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("active", wintypes.HWND), ("focus", wintypes.HWND),
                ("capture", wintypes.HWND), ("menu", wintypes.HWND),
                ("move", wintypes.HWND), ("caret", wintypes.HWND), ("rect", wintypes.RECT)]


def _load_win32():
    """Loads native libraries only when an authorized operation reaches input."""
    return ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)


def _bind(library, name, result, *args):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(args)
    return function


def _chord(key: int) -> list[tuple[int, bool]]:
    return [(VK_CONTROL, False), (key, False), (key, True), (VK_CONTROL, True)]


class Desktop:
    def __init__(self):
        user, kernel = _load_win32()
        self.is_window = _bind(user, "IsWindow", wintypes.BOOL, wintypes.HWND)
        self.foreground = _bind(user, "GetForegroundWindow", wintypes.HWND)
        self.thread = _bind(user, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        self.gui_info = _bind(user, "GetGUIThreadInfo", wintypes.BOOL, wintypes.DWORD, ctypes.POINTER(_GuiThreadInfo))
        self.key_state = _bind(user, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
        self.send = _bind(user, "SendInput", wintypes.UINT, wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
        self.create_window = _bind(
            user, "CreateWindowExW", wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        )
        self.destroy_window = _bind(user, "DestroyWindow", wintypes.BOOL, wintypes.HWND)
        self.peek_message = _bind(user, "PeekMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG),
                                  wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
        self.dispatch_message = _bind(user, "DispatchMessageW", ctypes.c_ssize_t, ctypes.POINTER(wintypes.MSG))
        self.open_clipboard = _bind(user, "OpenClipboard", wintypes.BOOL, wintypes.HWND)
        self.close_clipboard = _bind(user, "CloseClipboard", wintypes.BOOL)
        self.empty_clipboard = _bind(user, "EmptyClipboard", wintypes.BOOL)
        self.set_clipboard = _bind(user, "SetClipboardData", wintypes.HANDLE, wintypes.UINT, wintypes.HANDLE)
        self.register_format = _bind(user, "RegisterClipboardFormatW", wintypes.UINT, wintypes.LPCWSTR)
        self.format_available = _bind(user, "IsClipboardFormatAvailable", wintypes.BOOL, wintypes.UINT)
        self.get_clipboard = _bind(user, "GetClipboardData", wintypes.HANDLE, wintypes.UINT)
        self.clipboard_owner = _bind(user, "GetClipboardOwner", wintypes.HWND)
        self.sequence = _bind(user, "GetClipboardSequenceNumber", wintypes.DWORD)
        self.alloc = _bind(kernel, "GlobalAlloc", wintypes.HGLOBAL, wintypes.UINT, ctypes.c_size_t)
        self.lock = _bind(kernel, "GlobalLock", wintypes.LPVOID, wintypes.HGLOBAL)
        self.unlock = _bind(kernel, "GlobalUnlock", wintypes.BOOL, wintypes.HGLOBAL)
        self.free = _bind(kernel, "GlobalFree", wintypes.HGLOBAL, wintypes.HGLOBAL)
        self.size = _bind(kernel, "GlobalSize", ctypes.c_size_t, wintypes.HGLOBAL)

    def focus(self, hwnd: int) -> tuple[int, int]:
        if not self.is_window(hwnd) or self.foreground() != hwnd:
            raise ClipboardInputError("client_not_foreground")
        pid = wintypes.DWORD()
        thread = self.thread(hwnd, ctypes.byref(pid))
        info = _GuiThreadInfo(size=ctypes.sizeof(_GuiThreadInfo))
        if not thread or not self.gui_info(thread, ctypes.byref(info)) or info.active != hwnd or not info.focus:
            raise ClipboardInputError("client_focus_unavailable")
        return pid.value, info.focus

    def pump_messages(self, owner) -> None:
        """Services the clipboard owner on its creating thread without waiting."""
        message = wintypes.MSG()
        # PeekMessage also dispatches sent WM_DESTROYCLIPBOARD notifications,
        # allowing the client to finish copying before its sequence can advance.
        for _ in range(32):  # Bounds queued work between focus/deadline checks.
            if not self.peek_message(ctypes.byref(message), owner, 0, 0, 0x0001):  # PM_REMOVE
                break
            self.dispatch_message(ctypes.byref(message))

    @contextmanager
    def clipboard(self, owner):
        deadline = time.monotonic() + 1.0
        while not self.open_clipboard(owner):
            if time.monotonic() >= deadline:
                raise ClipboardInputError("clipboard_busy")
            self.pump_messages(owner)
            time.sleep(0.01)
        try:
            yield
        finally:
            self.close_clipboard()

    def _allocate(self, data: bytes):
        handle = self.alloc(0x0002, len(data))  # GMEM_MOVEABLE; ownership transfers on success.
        if not handle:
            raise ClipboardInputError("clipboard_allocation_failed")
        try:
            pointer = self.lock(handle)
            if not pointer:
                raise ClipboardInputError("clipboard_memory_unavailable")
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                self.unlock(handle)
            return handle
        except BaseException:
            self.free(handle)
            raise

    def format_id(self, format: int | str) -> int:
        value = self.register_format(format) if isinstance(format, str) else format
        if not value:
            raise ClipboardInputError("clipboard_format_unavailable")
        return value

    def write(self, owner, formats: tuple[tuple[int | str, bytes], ...]) -> int:
        handles = {}
        try:
            for format, data in formats:
                identifier = self.format_id(format)
                if identifier in handles:
                    raise ValueError("Duplicate clipboard format")
                handles[identifier] = self._allocate(data)
            with self.clipboard(owner):
                if not self.empty_clipboard():
                    raise ClipboardInputError("clipboard_write_failed")
                for format, handle in handles.items():
                    if not self.set_clipboard(format, handle):
                        raise ClipboardInputError("clipboard_write_failed")
                    handles[format] = None
            # Closing the writer can synthesize formats and advance the sequence.
            # Reopens to capture the published baseline while ownership is stable.
            with self.clipboard(owner):
                if self.clipboard_owner() != owner:
                    raise ClipboardInputError("focus_or_clipboard_changed_before_paste")
                return self.sequence()
        finally:
            for handle in handles.values():
                if handle:
                    self.free(handle)

    def keys(self, events: list[tuple[int, bool]]) -> None:
        inputs = (_Input * len(events))()
        for item, (key, up) in zip(inputs, events):
            item.type = 1  # INPUT_KEYBOARD
            item.value.keyboard = _KeyboardInput(vk=key, flags=KEYEVENTF_KEYUP if up else 0)
        if self.send(len(inputs), inputs, ctypes.sizeof(_Input)) != len(inputs):
            # Releases keys from a partially injected batch without replaying text.
            release = (_Input * len(dict.fromkeys(key for key, _ in events)))()
            for item, key in zip(release, dict.fromkeys(key for key, _ in reversed(events))):
                item.type = 1
                item.value.keyboard = _KeyboardInput(vk=key, flags=KEYEVENTF_KEYUP)
            self.send(len(release), release, ctypes.sizeof(_Input))
            raise ClipboardInputError("keyboard_input_incomplete")

    def copied_data(self, owner, pid: int, formats: tuple[tuple[int | str, int], ...]) -> tuple[int, bytes]:
        """Reads the first advertised format and its index under one clipboard lock."""
        index, data, _ = self.copied_bundle(owner, pid, formats)
        return index, data

    def copied_bundle(self, owner, pid, formats, companions=()):
        """Reads a preferred format and required companions from one clipboard version."""
        with self.clipboard(owner):
            source_pid = wintypes.DWORD()
            source = self.clipboard_owner()
            if not source or not self.thread(source, ctypes.byref(source_pid)) or source_pid.value != pid:
                raise ClipboardInputError("clipboard_owner_mismatch")
            for index, (format, max_bytes) in enumerate(formats):
                identifier = self.format_id(format)
                if not self.format_available(identifier):
                    continue
                # An advertised format that fails to render is an I/O failure,
                # not permission to accept a weaker representation.
                data = self._read_bytes(identifier, max_bytes)
                extra = []
                for companion, limit in companions:
                    companion_id = self.format_id(companion)
                    if not self.format_available(companion_id):
                        raise ClipboardInputError("copied_companion_missing")
                    extra.append(self._read_bytes(companion_id, limit))
                return index, data, tuple(extra)
            raise ClipboardInputError("copied_format_missing")

    def _read_bytes(self, identifier, max_bytes):
        handle = self.get_clipboard(identifier)
        if not handle:
            raise ClipboardInputError("copied_format_unavailable")
        size = self.size(handle)
        if not size or size > max_bytes:
            raise ClipboardInputError("copied_text_invalid_size")
        pointer = self.lock(handle)
        if not pointer:
            raise ClipboardInputError("copied_text_memory_unavailable")
        try:
            return ctypes.string_at(pointer, size)
        finally:
            self.unlock(handle)
