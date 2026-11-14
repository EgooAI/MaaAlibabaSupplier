"""Replaces focused Win32 text using Unicode clipboard data and real key events."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from threading import Lock
import time


CF_UNICODETEXT = 13
VK_CONTROL, VK_A, VK_C, VK_V = 0x11, 0x41, 0x43, 0x56
VK_BACK, VK_RIGHT = 0x08, 0x27
KEYEVENTF_KEYUP = 0x0002
_clipboard_lock = Lock()


class ClipboardInputError(RuntimeError):
    """Reports a fixed input failure without including clipboard or message text."""


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


def _newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _chord(key: int) -> list[tuple[int, bool]]:
    return [(VK_CONTROL, False), (key, False), (key, True), (VK_CONTROL, True)]


class _Desktop:
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
        self.open_clipboard = _bind(user, "OpenClipboard", wintypes.BOOL, wintypes.HWND)
        self.close_clipboard = _bind(user, "CloseClipboard", wintypes.BOOL)
        self.empty_clipboard = _bind(user, "EmptyClipboard", wintypes.BOOL)
        self.set_clipboard = _bind(user, "SetClipboardData", wintypes.HANDLE, wintypes.UINT, wintypes.HANDLE)
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
            raise ClipboardInputError("The bound client must remain in the foreground")
        pid = wintypes.DWORD()
        thread = self.thread(hwnd, ctypes.byref(pid))
        info = _GuiThreadInfo(size=ctypes.sizeof(_GuiThreadInfo))
        if not thread or not self.gui_info(thread, ctypes.byref(info)) or info.active != hwnd or not info.focus:
            raise ClipboardInputError("The bound client has no focused control")
        return pid.value, info.focus

    @contextmanager
    def clipboard(self, owner):
        deadline = time.monotonic() + 1.0
        while not self.open_clipboard(owner):
            if time.monotonic() >= deadline:
                raise ClipboardInputError("Clipboard is busy")
            time.sleep(0.01)
        try:
            yield
        finally:
            self.close_clipboard()

    def write(self, owner, data: bytes) -> int:
        handle = self.alloc(0x0002, len(data))  # GMEM_MOVEABLE; ownership transfers on success.
        if not handle:
            raise ClipboardInputError("Clipboard allocation failed")
        try:
            pointer = self.lock(handle)
            if not pointer:
                raise ClipboardInputError("Clipboard memory unavailable")
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                self.unlock(handle)
            with self.clipboard(owner):
                if not self.empty_clipboard() or not self.set_clipboard(CF_UNICODETEXT, handle):
                    raise ClipboardInputError("Clipboard write failed")
                handle = None
                return self.sequence()
        finally:
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
            raise ClipboardInputError("Keyboard input was incomplete")

    def copied_text(self, owner, pid: int, max_bytes: int) -> str:
        with self.clipboard(owner):
            source_pid = wintypes.DWORD()
            source = self.clipboard_owner()
            if not source or not self.thread(source, ctypes.byref(source_pid)) or source_pid.value != pid:
                raise ClipboardInputError("Clipboard changed outside the bound client")
            handle = self.get_clipboard(CF_UNICODETEXT)
            size = self.size(handle) if handle else 0
            if not size or size > max_bytes or size % 2:
                raise ClipboardInputError("Copied text is unavailable or exceeds the expected size")
            pointer = self.lock(handle)
            if not pointer:
                raise ClipboardInputError("Copied text memory unavailable")
            try:
                data = ctypes.string_at(pointer, size)
            finally:
                self.unlock(handle)
            end = next((index for index in range(0, len(data), 2) if data[index:index + 2] == b"\0\0"), None)
            if end is None:
                raise ClipboardInputError("Copied text is not terminated")
            return data[:end].decode("utf-16-le")


def replace_text(hwnd: int, text: str) -> None:
    """Selects, clears, pastes and verifies text without pressing Enter or sending."""
    if type(hwnd) is not int or hwnd <= 0 or not isinstance(text, str) or not text or "\0" in text:
        raise ValueError("Invalid clipboard input")
    expected = _newlines(text)
    data = (expected.replace("\n", "\r\n") + "\0").encode("utf-16-le")
    if not _clipboard_lock.acquire(blocking=False):
        raise ClipboardInputError("Another clipboard input operation is active")
    owner = None
    try:
        desktop = _Desktop()
        target = desktop.focus(hwnd)
        if any(desktop.key_state(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise ClipboardInputError("Release modifier keys before clipboard input")
        # OpenClipboard(NULL) cannot safely own EmptyClipboard/SetClipboardData.
        owner = desktop.create_window(0, "STATIC", "MaaClipboardInput", 0, 0, 0, 0, 0, -3, None, None, None)
        if not owner:
            raise ClipboardInputError("Clipboard owner window unavailable")
        sequence = desktop.write(owner, data)
        if desktop.focus(hwnd) != target or desktop.sequence() != sequence:
            raise ClipboardInputError("Input focus or clipboard changed before paste")
        # One ordered input batch prevents injected keys from interleaving. Copy
        # follows paste in the same queue; it must publish a NEW clipboard value.
        desktop.keys(_chord(VK_A) + [(VK_BACK, False), (VK_BACK, True)] + _chord(VK_V)
                     + _chord(VK_A) + _chord(VK_C))
        deadline = time.monotonic() + 2.0
        while True:
            if desktop.focus(hwnd) != target:
                raise ClipboardInputError("Input focus changed during paste")
            if desktop.sequence() != sequence:
                # Allows allocator padding, not arbitrary preexisting clipboard data.
                actual = desktop.copied_text(owner, target[0], len(data) + 4096)
                if _newlines(actual) != expected:
                    raise ClipboardInputError("Pasted text does not match the requested text")
                if desktop.focus(hwnd) != target:
                    raise ClipboardInputError("Input focus changed after paste")
                desktop.keys([(VK_RIGHT, False), (VK_RIGHT, True)])
                return
            if time.monotonic() >= deadline:
                raise ClipboardInputError("The client did not publish copied text after paste")
            time.sleep(0.01)
    finally:
        try:
            if owner:
                desktop.destroy_window(owner)
        finally:
            _clipboard_lock.release()
