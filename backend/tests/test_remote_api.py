from io import BytesIO
from types import SimpleNamespace

import numpy as np
from PIL import Image

from backend.app.shared.backend import gui_session, maafw_runner
from backend.app.shared.backend.gui_evidence import frame_from_image
from backend.tests.test_connection_api import client, connect, sdk


def test_remote_frame_uses_jpeg_and_avoids_retransmitting_unchanged_frame(client, monkeypatch):
    connect(client)
    frame = frame_from_image(np.full((12, 20, 3), 127, dtype=np.uint8))
    monkeypatch.setattr(gui_session, "capture_gui_session", lambda epoch: SimpleNamespace(epoch=epoch))
    monkeypatch.setattr(gui_session, "run_guarded", lambda token, action: action())
    monkeypatch.setattr(maafw_runner, "capture_client_frame", lambda: frame)

    created = client.post("/api/remote/session", json={})
    assert created.status_code == 200, created.text
    session_id = created.json()["data"]["session_id"]
    first = client.get(f"/api/remote/session/{session_id}/frame")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["X-Frame-Width"] == "20"
    with Image.open(BytesIO(first.content)) as image:
        assert image.format == "JPEG"
        assert image.size == (20, 12)
        assert image.quantization[0][0] == 8

    unchanged = client.get(
        f"/api/remote/session/{session_id}/frame", headers={"If-None-Match": first.headers["ETag"]}
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["ETag"] == first.headers["ETag"]


def test_remote_action_checks_screenshot_bounds_and_dispatches(client, monkeypatch):
    connect(client)
    frame = frame_from_image(np.zeros((12, 20, 3), dtype=np.uint8))
    monkeypatch.setattr(gui_session, "capture_gui_session", lambda epoch: SimpleNamespace(epoch=epoch))
    monkeypatch.setattr(gui_session, "run_guarded", lambda token, action: action())
    monkeypatch.setattr(maafw_runner, "capture_client_frame", lambda: frame)
    received = []
    monkeypatch.setattr(maafw_runner, "run_remote_action", lambda action, payload: received.append((action, payload)) or (True, "ok"))
    session_id = client.post("/api/remote/session", json={}).json()["data"]["session_id"]
    assert client.get(f"/api/remote/session/{session_id}/frame").status_code == 200

    invalid = client.post("/api/remote/action", json={"session_id": session_id, "action": "click", "x": 20, "y": 2})
    assert invalid.status_code == 422
    valid = client.post("/api/remote/action", json={"session_id": session_id, "action": "click", "x": 19, "y": 11})
    assert valid.status_code == 200
    assert received[0][0] == "click"
    assert received[0][1]["x"] == 19
