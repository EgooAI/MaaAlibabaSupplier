from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from backend.app.api.envelope import AppError
from backend.app.shared.backend import account_context, gui_session, maafw_runner as runner
from backend.app.shared.backend.gui_evidence import ClientFrame, compare_frame, frame_from_image
from backend.tests.test_maafw_runner import confirmed_token, native_pipeline, sdk, window


@pytest.fixture
def capture(sdk, monkeypatch):
    token = confirmed_token()
    state = SimpleNamespace(
        token=token, image=np.zeros((4, 6, 3), dtype=np.uint8),
        failure=None, events=[],
    )

    class CaptureJob:
        def wait(self):
            state.events.append("wait")
            if state.failure == "wait":
                raise RuntimeError("capture wait failed")
            return self

        @property
        def succeeded(self):
            state.events.append("status")
            assert state.events[-2] == "wait"
            return state.failure != "status"

        def get(self):
            state.events.append("get")
            assert state.events[-2] == "status"
            if state.failure == "get":
                raise RuntimeError("capture result unavailable")
            return state.image

    def post_screencap():
        state.events.append("post")
        if state.failure == "post":
            raise RuntimeError("capture post failed")
        return CaptureJob()

    monkeypatch.setattr(runner._tasker.controller, "post_screencap", post_screencap, raising=False)
    return state


def test_capture_requires_active_guard_even_with_confirmed_session(capture, sdk):
    with pytest.raises(AppError, match="run_guarded"):
        runner.capture_client_frame()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = gui_session.run_guarded(
            capture.token, lambda: executor.submit(runner.capture_client_frame)
        )
        with pytest.raises(AppError, match="run_guarded"):
            future.result(timeout=2)
    assert capture.events == []
    assert sdk.calls == []


def test_capture_waits_for_fresh_full_frame_and_preserves_colors(capture, sdk):
    capture.image[0, 0] = [10, 20, 30]
    first = gui_session.run_guarded(capture.token, runner.capture_client_frame)
    assert isinstance(first, ClientFrame)
    assert (first.width, first.height) == (6, 4)
    assert first.sha256 == sha256(first.png_bytes).hexdigest()
    with Image.open(BytesIO(first.png_bytes)) as image:
        assert image.format == "PNG"
        assert image.getpixel((0, 0)) == (30, 20, 10)
        np.testing.assert_array_equal(np.array(image), capture.image[:, :, ::-1])
    capture.image[-1, -1, 0] = 1
    second = gui_session.run_guarded(capture.token, runner.capture_client_frame)
    assert second.sha256 != first.sha256
    assert capture.events == ["post", "wait", "status", "get"] * 2
    assert sdk.calls == []


@pytest.mark.parametrize("failure", ["post", "wait", "status", "get"])
def test_failed_capture_never_returns_previous_image(capture, sdk, failure):
    assert isinstance(gui_session.run_guarded(capture.token, runner.capture_client_frame), ClientFrame)
    capture.events.clear()
    capture.failure = failure
    # A valid old frame remains available to get(), just as in the SDK cache.
    result = gui_session.run_guarded(capture.token, runner.capture_client_frame)
    assert isinstance(result, tuple) and result[0] is False and result[1]
    if failure != "get":
        assert "get" not in capture.events
    assert sdk.calls == []


@pytest.mark.parametrize("image", [
    None, np.zeros((0, 4, 3), dtype=np.uint8), np.zeros((4, 0, 3), dtype=np.uint8),
    np.zeros((4, 6), dtype=np.uint8), np.zeros((4, 6, 4), dtype=np.uint8),
    np.zeros((4, 6, 3), dtype=np.float32),
])
def test_successful_job_with_invalid_image_fails_closed(capture, image):
    capture.image = image
    result = gui_session.run_guarded(capture.token, runner.capture_client_frame)
    assert result[0] is False
    assert "BGR frame" in result[1]


@pytest.mark.parametrize("change", ["window", "epoch", "reconnect"])
def test_capture_rechecks_binding_inside_guard(capture, sdk, change):
    def changed():
        if change == "window":
            sdk.windows = [window(2)]
        elif change == "epoch":
            sdk.config["self_ali_id"] = "seller-b"
        else:
            gui_session.connect_client()
        return runner.capture_client_frame()

    assert gui_session.run_guarded(capture.token, changed)[0] is False
    assert capture.events == []
    assert sdk.calls == []


def test_window_changed_during_capture_rejects_evidence(capture, sdk, monkeypatch):
    encode = runner.frame_from_image

    def change_window(image):
        frame = encode(image)
        sdk.windows = [window(2)]
        return frame

    monkeypatch.setattr(runner, "frame_from_image", change_window)
    assert gui_session.run_guarded(capture.token, runner.capture_client_frame)[0] is False
    assert capture.events == ["post", "wait", "status", "get"]
    assert sdk.calls == []


@pytest.mark.parametrize("change", ["same", "corner_pixel", "dimensions"])
def test_compare_entire_frame_including_dimensions(change):
    image = np.zeros((4, 6, 3), dtype=np.uint8)
    expected = frame_from_image(image)
    if change == "corner_pixel":
        image[-1, -1, -1] = 1
    elif change == "dimensions":
        image = image.reshape((6, 4, 3))
    fresh = frame_from_image(image)
    comparison = compare_frame(expected.sha256, fresh)
    assert comparison.needs_reconfirm is (change != "same")
    assert comparison.frame is fresh


@pytest.mark.parametrize("changed", [False, True])
def test_confirmation_stage_contract_and_persistence_barrier(capture, sdk, changed):
    def search():
        assert runner.goto_contact("buyer")[0]
        return runner.capture_client_frame()

    evidence = gui_session.run_guarded(capture.token, search)
    # The confirmation wait has no account or runner lock held.
    with ThreadPoolExecutor(max_workers=1) as executor:
        def acquire_between_stages():
            with account_context.changing_account(), runner._run_lock:
                return True
        assert executor.submit(acquire_between_stages).result(timeout=2)
    if changed:
        capture.image[-1, -1, 0] = 1
    barrier = []

    def confirm():
        comparison = compare_frame(evidence.sha256, runner.capture_client_frame())
        if comparison.needs_reconfirm:
            return comparison
        assert runner.chat_input("approved text")[0]
        barrier.append(len(sdk.calls))  # Service persists may_have_sent before click_send.
        return runner.click_send()

    result = gui_session.run_guarded(capture.token, confirm)
    if changed:
        assert result.needs_reconfirm
        assert result.frame.sha256 != evidence.sha256
        assert [entry for entry, _ in sdk.calls] == ["ContactSearch"]
        assert barrier == []
    else:
        assert result[0]
        assert [entry for entry, _ in sdk.calls] == ["ContactSearch", "ChatInput", "ChatInput_SendOnly"]
        assert sdk.calls[1][1]["ChatInput_InputText"]["next"] == []
        assert barrier == [2]


@pytest.mark.parametrize("outcome", [False, None, RuntimeError("click outcome unavailable")])
def test_send_only_failure_is_not_replayed(sdk, outcome):
    token = confirmed_token()
    sdk.outcome = outcome
    assert gui_session.run_guarded(token, runner.click_send)[0] is False
    assert sdk.calls == [("ChatInput_SendOnly", {"ChatInput_SendMessage": {"enabled": True}})]


@pytest.fixture
def native_binding(sdk, native_pipeline, monkeypatch):
    token = confirmed_token()
    monkeypatch.setattr(runner, "_tasker", native_pipeline.tasker)
    monkeypatch.setattr(runner, "_resource", native_pipeline.resource)
    return token, native_pipeline


def test_native_capture_is_fresh_and_read_only_even_after_failure(native_binding):
    token, native = native_binding
    controller = native.controller
    controller.frame[0, 0] = [10, 20, 30]
    first = gui_session.run_guarded(token, runner.capture_client_frame)
    assert isinstance(first, ClientFrame)
    assert (first.width, first.height) == (1280, 800)
    controller.frame[-1, -1, -1] = 1
    second = gui_session.run_guarded(token, runner.capture_client_frame)
    assert compare_frame(first.sha256, second).needs_reconfirm
    with Image.open(BytesIO(second.png_bytes)) as image:
        np.testing.assert_array_equal(np.array(image), controller.frame[:, :, ::-1])
    controller.screencap_ok = False
    assert gui_session.run_guarded(token, runner.capture_client_frame)[0] is False
    assert controller.screencaps >= 3
    assert controller.clicks == controller.texts == controller.keys == []


@pytest.mark.parametrize("button", ["enabled", "disabled", "missing", "click_failed"])
def test_native_send_only_recognizes_button_without_typing(native_binding, button):
    token, native = native_binding
    controller = native.controller
    if button != "missing":
        pixels = native.images[0 if button == "disabled" else 1]
        height, width = pixels.shape[:2]
        controller.frame[620:620 + height, 650:650 + width] = pixels
    controller.click_ok = button != "click_failed"
    # A preceding fill disables the send node; click_send must enable it explicitly.
    assert native.resource.override_pipeline(runner.chat_input_override("unused", send=False))
    assert native.resource.override_pipeline({"ChatInput_SendOnly": {"timeout": 0}})
    result = gui_session.run_guarded(token, runner.click_send)
    assert result[0] is (button == "enabled")
    assert len(controller.clicks) == (1 if button in ("enabled", "click_failed") else 0)
    assert controller.keys == controller.texts == []
    for x, y in controller.clicks:
        assert 650 <= x < 650 + width
        assert 620 <= y < 620 + height


def test_native_disabled_send_node_cannot_be_executed_as_entry_action(native_pipeline):
    native = native_pipeline
    pixels = native.images[1]
    height, width = pixels.shape[:2]
    native.controller.frame[620:620 + height, 650:650 + width] = pixels
    detail = native.tasker.post_task("ChatInput_SendOnly", {
        "ChatInput_SendOnly": {"timeout": 0},
        "ChatInput_SendMessage": {"enabled": False},
    }).wait().get()
    assert detail is not None and not detail.status.succeeded
    assert native.controller.clicks == native.controller.texts == native.controller.keys == []
