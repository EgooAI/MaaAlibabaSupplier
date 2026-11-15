from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import re
from threading import Event

import pytest
from PIL import Image

from backend.app.shared.backend import account_context, client_preview, maafw_runner
from backend.tests.test_connection_api import client, connect, sdk


@pytest.fixture
def capture(client, monkeypatch):
    connect(client)

    def grab(*, window):
        assert window == maafw_runner._window_hwnd == 1
        return Image.new("RGB", (20, 12), (127, 64, 32))

    monkeypatch.setattr(client_preview.ImageGrab, "grab", grab)
    return grab


def test_live_frames_and_activity_do_not_wait_for_gui_lock(client, capture):
    entered, release = Event(), Event()

    def hold_gui():
        with account_context.account_lock, maafw_runner._run_lock:
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(hold_gui)
        try:
            assert entered.wait(5)
            first = client.get("/api/remote/frame")
            assert first.status_code == 200
            assert first.headers["content-type"] == "image/jpeg"
            assert first.headers["cache-control"] == "no-store"
            assert first.headers["X-Frame-Width"] == "20"
            with Image.open(BytesIO(first.content)) as image:
                assert image.size == (20, 12)
                assert image.quantization[0][0] == 8
            unchanged = client.get("/api/remote/frame", headers={"If-None-Match": first.headers["ETag"]})
            assert unchanged.status_code == 304
            assert unchanged.content == b""
            original = client.get("/api/remote/download")
            assert original.status_code == 200
            assert re.fullmatch(r'attachment; filename="client-\d{6}-\d{6}\.png"', original.headers["content-disposition"])
            with Image.open(BytesIO(original.content)) as image:
                assert image.format == "PNG"
                assert image.getpixel((0, 0)) == (127, 64, 32)
            assert client.get("/api/remote/activity").json()["data"] == {"sending": False}
            assert not release.is_set()
        finally:
            release.set()
            future.result(5)


@pytest.mark.parametrize("change", ["account", "window", "failure"])
def test_preview_discards_changed_or_failed_capture(client, capture, monkeypatch, change):
    def grab(**kwargs):
        image = capture(**kwargs)
        if change == "account":
            account_context.invalidate_account_context()
        elif change == "window":
            monkeypatch.setattr(maafw_runner, "_window_generation", "new-window")
        else:
            image.close()
            raise OSError("capture unavailable")
        return image

    monkeypatch.setattr(client_preview.ImageGrab, "grab", grab)
    response = client.get("/api/remote/frame")
    assert response.status_code == (500 if change == "failure" else 409)
    assert "image" not in response.headers["content-type"]


@pytest.mark.parametrize("action", ["click", "long_press", "drag", "type"])
def test_remote_view_rejects_client_input(client, action):
    response = client.post("/api/remote/action", json={
        "session_id": "a" * 32, "action": action, "x": 1, "y": 2,
        "x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration": 600, "text": "input",
    })
    assert response.status_code == 404
