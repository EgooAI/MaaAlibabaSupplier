"""Captures a bound window without entering the GUI action queue."""

from hashlib import sha256
from io import BytesIO

from PIL import ImageGrab

from backend.app.api.envelope import AppError
from backend.app.shared.backend import maafw_runner as runner
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.backend.gui_evidence import ClientFrame


def capture_preview() -> ClientFrame:
    """Rejects frames whose account or window binding changed during capture."""
    context = get_account_context()
    hwnd, generation = runner._window_hwnd, runner._window_generation

    def validate():
        if (not context.self_ali_id or not hwnd or not generation
                or get_account_context() != context
                or (runner._window_hwnd, runner._window_generation) != (hwnd, generation)):
            raise AppError("客户端连接或账号已变化，请重新查看。", status_code=409)
        # Observation never resets or reconnects the runner's binding.
        window = runner._find_window(runner.Toolkit.find_desktop_windows())
        if window.hwnd != hwnd:
            raise AppError("客户端窗口已变化，请重新连接。", status_code=409)

    validate()
    # Pillow uses PrintWindow for an explicit HWND; no input or activation is sent.
    with ImageGrab.grab(window=hwnd) as image:
        output = BytesIO()
        image.convert("RGB").save(output, format="PNG")
        width, height = image.size
    validate()
    png = output.getvalue()
    return ClientFrame(png, sha256(png).hexdigest(), width, height)
