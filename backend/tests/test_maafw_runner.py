from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.shared.backend import maafw_runner as runner
from backend.app.api.envelope import AppError
from backend.app.shared.backend import account_context, gui_session
from backend.app.shared.utils import settings
from tools.validate_schema import load_jsonc


ASSETS = Path(__file__).resolve().parents[1] / "assets"
WINDOW_TITLE = "\u63a5\u5f85\u4e2d\u5fc3"


def window(hwnd=1, class_name="Qt5152QWindowIcon", title=WINDOW_TITLE):
    return SimpleNamespace(hwnd=hwnd, class_name=class_name, window_name=title)


class FakeJob:
    done = True

    def __init__(self, succeeded=True, detail=None):
        self.succeeded = succeeded
        self.detail = detail

    def wait(self):
        return self

    def get(self):
        if isinstance(self.detail, Exception):
            raise self.detail
        return self.detail


@pytest.fixture
def sdk(monkeypatch):
    state = SimpleNamespace(
        windows=[window()], fail=None, controllers=[], taskers=[], bundles=[],
        options=[], calls=[], outcome=True,
        native_dirs=[],
        config={"self_ali_id": "seller-a", "alibaba_data_dir": "test-data"},
    )

    class FakeToolkit:
        @staticmethod
        def init_option(path):
            state.options.append(Path(path))
            return state.fail != "options"

        @staticmethod
        def find_desktop_windows():
            if state.fail == "discovery_exception":
                raise RuntimeError("discovery failed")
            return state.windows

    class FakeController:
        def __init__(self, hwnd, **kwargs):
            self.hwnd = hwnd
            self.methods = kwargs
            self.connected = False
            state.controllers.append(self)

        def set_screenshot_target_long_side(self, value):
            self.long_side = value
            return state.fail != "screenshot_size"

        def post_connection(self):
            self.connected = state.fail != "connection"
            return FakeJob(self.connected)

    class FakeResource:
        def post_bundle(self, path):
            state.bundles.append(path)
            return FakeJob(state.fail != "resource")

    class FakeTasker:
        def __init__(self):
            self.inited = state.fail != "inited"
            state.taskers.append(self)

        def bind(self, resource, controller):
            self.resource = resource
            self.controller = controller
            return state.fail != "bind"

        def post_task(self, entry, override):
            state.calls.append((entry, deepcopy(override)))
            result = state.outcome
            if isinstance(result, bool):
                result = SimpleNamespace(status=SimpleNamespace(succeeded=result))
            return FakeJob(detail=result)

    monkeypatch.setattr(runner, "Toolkit", FakeToolkit)
    monkeypatch.setattr(runner, "Win32Controller", FakeController)
    monkeypatch.setattr(runner, "Resource", FakeResource)
    monkeypatch.setattr(runner, "Tasker", FakeTasker)
    for name in ("_tasker", "_resource", "_window_hwnd"):
        monkeypatch.setattr(runner, name, None)
    monkeypatch.setattr(runner, "_window_generation", "")
    monkeypatch.setattr(runner, "_native_logs", None)
    monkeypatch.setattr(runner, "_native_jobs", {})
    monkeypatch.setattr(runner, "_native_post_uncertain", False)
    monkeypatch.setattr(runner, "_native_log_observation", {})
    monkeypatch.setattr(runner, "_set_native_log_dir", lambda path: state.native_dirs.append(path) or True)
    monkeypatch.setattr(account_context, "_context", None)
    monkeypatch.setattr(account_context, "read_app_config", lambda: dict(state.config))
    yield state
    if runner._native_logs is not None:
        runner._native_logs.release_after_shutdown(native_stopped=True)


def connected_token():
    gui_session.connect_client()
    epoch = account_context.get_account_context().epoch
    return gui_session.capture_gui_session(epoch)


def run_connected(fn):
    try:
        token = connected_token()
    except AppError as exc:
        return False, exc.message
    return gui_session.run_guarded(token, fn)


@pytest.mark.parametrize("failure", [
    "options", "discovery_exception", "screenshot_size", "connection",
    "resource", "bind", "inited",
])
def test_initialization_failure_can_recover(sdk, failure):
    sdk.fail = failure
    assert not run_connected(lambda: runner.chat_input("first"))[0]
    assert runner._tasker is None
    assert runner._resource is None
    assert not sdk.calls

    sdk.fail = None
    assert run_connected(lambda: runner.chat_input("second"))[0]
    assert len(sdk.calls) == 1


@pytest.mark.parametrize("windows", [
    [], [window(class_name="Other")], [window(title="Other")],
    [window(class_name=None, title=None)],
    [window(1), window(2)],
])
def test_rejects_missing_partial_or_ambiguous_windows_and_recovers(sdk, windows):
    sdk.windows = windows
    assert not run_connected(lambda: runner.chat_send("first"))[0]
    assert not sdk.controllers
    assert not sdk.calls

    sdk.windows = [window()]
    assert run_connected(lambda: runner.chat_input("second"))[0]


def test_resource_path_and_controller_match_interface(sdk):
    sdk.windows = [window(2, title="Other"), window(3, class_name="Other"), window(4)]
    assert run_connected(lambda: runner.goto_contact("buyer-test"))[0]
    assert sdk.bundles == [settings.resolve_backend_root() / "assets" / "resource"]
    assert sdk.options == [settings.resolve_backend_root() / "debug"]
    config = load_jsonc(ASSETS / "interface.json")["controller"][0]
    controller = sdk.controllers[0]
    assert controller.hwnd == 4
    assert controller.long_side == config["display_long_side"]
    assert runner._WIN_CLASS_RE.pattern == config["win32"]["class_regex"]
    assert runner._WIN_NAME_RE.pattern == config["win32"]["window_regex"]
    for field, method in (("screencap", "screencap_method"), ("mouse", "mouse_method"), ("keyboard", "keyboard_method")):
        assert controller.methods[method].name == config["win32"][field]
    assert sdk.calls[0][1]["ContactSearch_InputText"]["action"]["param"]["input_text"] == "buyer-test"


@pytest.mark.parametrize("change", ["new_hwnd", "disconnected", "not_inited"])
def test_rebind_requires_explicit_reconnection(sdk, change):
    token = connected_token()
    assert gui_session.run_guarded(token, lambda: runner.chat_input("first"))[0]
    if change == "new_hwnd":
        sdk.windows = [window(2)]
    elif change == "disconnected":
        sdk.controllers[0].connected = False
    else:
        sdk.taskers[0].inited = False

    assert not gui_session.run_guarded(token, lambda: runner.chat_input("stale"))[0]
    assert len(sdk.calls) == 1
    assert len(sdk.controllers) == 1
    assert run_connected(lambda: runner.chat_input("second"))[0]
    assert token.window_generation != runner._window_generation
    assert len(sdk.controllers) == 2
    assert runner._tasker.controller is sdk.controllers[-1]
    assert runner._window_hwnd == sdk.windows[0].hwnd
    assert len(sdk.calls) == 2


@pytest.mark.parametrize("windows", [[], [window(1), window(2)], [window(1, title="Other")]])
def test_cached_window_is_rechecked(sdk, windows):
    token = connected_token()
    assert gui_session.run_guarded(token, lambda: runner.chat_input("first"))[0]
    sdk.windows = windows
    assert not gui_session.run_guarded(token, lambda: runner.chat_send("second"))[0]
    assert len(sdk.calls) == 1
    assert runner._tasker is None

    sdk.windows = [window(3)]
    assert run_connected(lambda: runner.chat_input("third"))[0]
    assert sdk.controllers[-1].hwnd == 3


@pytest.mark.parametrize("outcome", [False, None, RuntimeError("result unavailable")])
def test_failed_task_is_not_replayed(sdk, outcome):
    sdk.outcome = outcome
    assert not run_connected(lambda: runner.chat_send("first"))[0]
    assert len(sdk.calls) == 1
    assert len(sdk.taskers) == 1

    sdk.outcome = True
    sdk.windows = [window(2)]
    assert run_connected(lambda: runner.chat_input("second"))[0]
    assert len(sdk.calls) == 2
    assert len(sdk.taskers) == 2


def assert_chat_calls(calls):
    assert len(calls) == 3
    for (entry, override), text, send in zip(calls, ("first", "test", "last"), (True, False, True)):
        assert entry == "ChatInput"
        node = override["ChatInput_InputText"]
        assert node["action"]["param"]["input_text"] == text
        assert node["next"] == (["ChatInput_SendMessage"] if send else [])
        assert override["ChatInput_SendMessage"]["enabled"] is send


def test_send_test_send_overrides_both_branches(sdk):
    token = connected_token()
    assert gui_session.run_guarded(token, lambda: runner.chat_send("first"))[0]
    assert gui_session.run_guarded(token, lambda: runner.chat_input("test"))[0]
    assert gui_session.run_guarded(token, lambda: runner.chat_send("last"))[0]
    assert_chat_calls(sdk.calls)
    assert len(sdk.taskers) == 1


def test_submit_send_validates_and_posts_without_waiting(sdk, monkeypatch):
    token = connected_token()
    calls = []
    validate = gui_session._validate_token

    def checked_token(value):
        validate(value)
        calls.append("validate")

    class Job(FakeJob):
        def wait(self):
            calls.append("wait")
            return super().wait()

    job = Job(detail=SimpleNamespace(status=SimpleNamespace(succeeded=True)))

    def post(entry, override):
        assert entry == "ChatInput_SendOnly"
        assert override == {"ChatInput_SendMessage": {"enabled": True}}
        calls.append("post")
        return job

    monkeypatch.setattr(gui_session, "_validate_token", checked_token)
    monkeypatch.setattr(runner._tasker, "post_task", post)

    def send():
        calls.clear()
        assert runner.submit_send() is job
        assert calls == ["validate", "post"]
        return runner.wait_send(job)

    assert gui_session.run_guarded(token, send)[0]
    assert calls == ["validate", "post", "wait"]


@pytest.mark.parametrize("fault", ["job_id", "sink", None])
@pytest.mark.parametrize("split_send", [False, True])
def test_native_correlation_failure_never_skips_wait_or_replays(sdk, monkeypatch, fault, split_send):
    from backend.app.shared.utils.log_context import bind_log_context, capture_log_context

    token = connected_token()
    calls, events = [], []

    class Job(FakeJob):
        @property
        def job_id(self):
            if fault == "job_id":
                raise RuntimeError("telemetry unavailable")
            return 42

        def wait(self):
            calls.append("wait")
            return self

    def post(*args):
        calls.append("post")
        return Job(detail=SimpleNamespace(status=SimpleNamespace(succeeded=True)))

    def emit(event, **fields):
        if fault == "sink":
            raise OSError("sink unavailable")
        events.append((event, capture_log_context(), fields))

    monkeypatch.setattr(runner._tasker, "post_task", post)
    monkeypatch.setattr(runner, "log_event", emit)
    with bind_log_context(request_id="origin", queue_task_id="queue", outbox_id="outbox", attempt=2):
        result = gui_session.run_guarded(token, lambda: runner.wait_send(runner.submit_send()) if split_send
                                         else runner.chat_input("PRIVATE_TEXT"))
    assert result[0] and calls == ["post", "wait"]
    if fault is None:
        assert [event for event, _, _ in events] == ["maa.submitted", "maa.succeeded"]
        assert all(context["outbox_id"] == "outbox" and context["attempt"] == 2 for _, context, _ in events)
        assert all(fields["native_job_id"] == 42 and fields["native_runtime_id"] == token.window_generation
                   for _, _, fields in events)


@pytest.mark.parametrize("connected", [False, True])
def test_submit_send_requires_guard_and_never_initializes(sdk, connected):
    if connected:
        connected_token()
    with pytest.raises(AppError, match="run_guarded"):
        runner.submit_send()
    assert sdk.calls == []
    assert len(sdk.controllers) == int(connected)


def test_submit_send_revalidates_session_inside_guard_before_native_post(sdk, monkeypatch):
    token = connected_token()

    def send():
        monkeypatch.setattr(runner, "_window_generation", "reconnected")
        return runner.submit_send()

    ok, reason = gui_session.run_guarded(token, send)
    assert not ok and "expired" in reason
    assert sdk.calls == []


@pytest.mark.parametrize("outcome", [True, False, None, RuntimeError("wait failed")])
def test_wait_send_only_waits_for_original_job_without_reposting(sdk, outcome):
    token = connected_token()
    sdk.outcome = outcome

    def send():
        job = runner.submit_send()
        return runner.wait_send(job)

    assert gui_session.run_guarded(token, send)[0] is (outcome is True)
    assert sdk.calls == [("ChatInput_SendOnly", {"ChatInput_SendMessage": {"enabled": True}})]


def test_wait_send_keeps_outer_account_and_gui_guard(sdk, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    token = connected_token()

    def probe():
        for lock in (account_context.account_lock, runner._run_lock):
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
            assert not acquired

    class Job(FakeJob):
        def wait(self):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(probe).result(timeout=5)
            return super().wait()

    monkeypatch.setattr(runner._tasker, "post_task", lambda *args: Job(
        detail=SimpleNamespace(status=SimpleNamespace(succeeded=True)),
    ))
    assert gui_session.run_guarded(token, lambda: runner.wait_send(runner.submit_send()))[0]


@pytest.fixture
def actions(monkeypatch):
    from maa.library import Library

    # Importing maa.agent otherwise switches the process to the AgentServer DLL.
    monkeypatch.setattr(Library, "open", lambda *args, **kwargs: None)
    from maa.agent.agent_server import AgentServer

    monkeypatch.setattr(AgentServer, "custom_action", lambda name: lambda cls: cls)
    return [
        importlib.import_module(f"backend.app.agent.nodes.auxi.{name}")
        for name in ("chat_send", "send_message")
    ]


@pytest.mark.parametrize("kind", ["chat", "message"])
def test_agent_send_test_send(actions, kind):
    calls = []

    def run_task(entry, override):
        calls.append((entry, deepcopy(override)))
        return SimpleNamespace(status=SimpleNamespace(succeeded=True))

    context = SimpleNamespace(run_task=run_task)
    for text, send in (("first", True), ("test", False), ("last", True)):
        if kind == "chat":
            action = actions[0].ChatSend()
            params = {"text": text, "confirm": send}
        else:
            action = actions[1].SendMessage()
            params = {"text": text, "login id": "buyer-test", "dry run": not send}
        assert action.run(context, SimpleNamespace(custom_action_param=params))

    assert_chat_calls([call for call in calls if call[0] == "ChatInput"])
    searches = [call for call in calls if call[0] == "ContactSearch"]
    assert len(searches) == (3 if kind == "message" else 0)
    for _, override in searches:
        assert override["ContactSearch_InputText"]["action"]["param"]["input_text"] == "buyer-test"


@pytest.mark.parametrize("failed_entry", ["ContactSearch", "ChatInput"])
def test_agent_failure_stops_without_replay(actions, failed_entry):
    calls = []

    def run_task(entry, override):
        calls.append(entry)
        return SimpleNamespace(status=SimpleNamespace(succeeded=entry != failed_entry))

    context = SimpleNamespace(run_task=run_task)
    params = {"text": "first", "login id": "buyer-test"}
    assert not actions[1].SendMessage().run(context, SimpleNamespace(custom_action_param=params))
    assert calls == (["ContactSearch"] if failed_entry == "ContactSearch" else ["ContactSearch", "ChatInput"])


@pytest.fixture
def native_pipeline(tmp_path):
    """Use only repository button templates and in-memory synthetic frames."""
    import numpy as np
    from PIL import Image
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker

    class SyntheticController(CustomController):
        def __init__(self):
            self.frame = np.zeros((800, 1280, 3), dtype=np.uint8)
            self.clicks = []
            self.texts = []
            self.keys = []
            self.click_ok = True
            self.screencap_ok = True
            self.screencaps = 0
            super().__init__()

        def connect(self):
            return True

        def request_uuid(self):
            return "synthetic-chat-test"

        def get_features(self):
            return 0

        def screencap(self):
            self.screencaps += 1
            return self.frame.copy() if self.screencap_ok else np.empty((0, 0, 3), dtype=np.uint8)

        def click(self, x, y):
            self.clicks.append((x, y))
            return self.click_ok

        def input_text(self, text):
            self.texts.append(text)
            return True

        def click_key(self, keycode):
            self.keys.append(("click", keycode))
            return True

        def key_down(self, keycode):
            self.keys.append(("down", keycode))
            return True

        def key_up(self, keycode):
            self.keys.append(("up", keycode))
            return True

        def start_app(self, intent):
            raise AssertionError("No application access allowed")

        stop_app = start_app

        def swipe(self, *args):
            raise AssertionError("Unexpected swipe")

        touch_down = swipe
        touch_move = swipe
        touch_up = swipe

    try:
        Tasker.set_log_dir(tmp_path / "logs")
    except (OSError, FileNotFoundError) as exc:
        pytest.skip(f"Native Maa library unavailable: {exc}")
    Tasker.set_save_on_error(False)
    Tasker.set_save_draw(False)
    controller = SyntheticController()
    assert controller.set_screenshot_use_raw_size(True)
    assert controller.post_connection().wait().succeeded
    resource = Resource()
    pipeline = {}
    for filename in ("ChatInput.json", "ContactSearch.json", "Diagnostics.json"):
        path = ASSETS / "resource" / "pipeline" / filename
        assert resource.post_pipeline(path).wait().succeeded
        pipeline.update(load_jsonc(path))
    templates = dict.fromkeys(
        template
        for name in ("ChatInput_GoToInput", "ContactSearch_ClearSearchBox", "ContactSearch_ClickSearchButton")
        for template in pipeline[name]["recognition"]["param"]["template"]
    )
    images = []
    for name in templates:
        with Image.open(ASSETS / "resource" / "image" / name) as image:
            pixels = np.array(image.convert("RGB"))[:, :, ::-1].copy()
        assert resource.override_image(name, pixels)
        images.append(pixels)
        templates[name] = pixels
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    assert tasker.inited
    yield SimpleNamespace(
        controller=controller, resource=resource, tasker=tasker,
        images=images, templates=templates, pipeline=pipeline,
    )
    tasker.post_stop().wait()


@pytest.mark.parametrize("button", ["enabled", "disabled", "missing", "click_failed"])
def test_native_send_requires_enabled_button(native_pipeline, button):
    native = native_pipeline
    controller = native.controller
    if button != "missing":
        pixels = native.images[0 if button == "disabled" else 1]
        height, width = pixels.shape[:2]
        controller.frame[620:620 + height, 650:650 + width] = pixels
    controller.click_ok = button != "click_failed"
    # Start just before recognition to isolate the send guard and its outcome.
    override = runner.chat_input_override("test", send=True)
    override["ChatInput_InputText"]["timeout"] = 0
    detail = native.tasker.post_task("ChatInput_InputText", override).wait().get()
    assert detail is not None
    assert detail.status.succeeded is (button == "enabled")
    assert len(controller.clicks) == (1 if button in ("enabled", "click_failed") else 0)
    for x, y in controller.clicks:
        assert 650 <= x < 650 + width
        assert 620 <= y < 620 + height


def test_native_send_test_send_with_persisted_overrides(native_pipeline):
    native = native_pipeline
    controller = native.controller
    pixels = native.images[1]
    height, width = pixels.shape[:2]
    controller.frame[620:620 + height, 650:650 + width] = pixels
    for text, send, count in (("first", True, 2), ("test", False, 3), ("last", True, 5)):
        # Persist each override to expose omissions even across shared contexts.
        assert native.resource.override_pipeline(runner.chat_input_override(text, send=send))
        detail = native.tasker.post_task("ChatInput").wait().get()
        assert detail is not None and detail.status.succeeded
        assert len(controller.clicks) == count
    assert controller.texts == ["first", "test", "last"]


@pytest.mark.parametrize("entry,node,index", [
    ("Diagnostics_ChatInput", "ChatInput_GoToInput", 0),
    ("Diagnostics_ChatInput", "ChatInput_GoToInput", 1),
    ("Diagnostics_ChatInput", None, 0),
    ("Diagnostics_ContactSearch", "ContactSearch_ClearSearchBox", 0),
    ("Diagnostics_ContactSearch", "ContactSearch_ClickSearchButton", 0),
    ("Diagnostics_ContactSearch", None, 0),
], ids=["chat-disabled", "chat-enabled", "chat-missing", "search-clear", "search-button", "search-missing"])
def test_native_diagnostics_are_read_only_and_preserve_sending(native_pipeline, entry, node, index):
    native = native_pipeline
    controller = native.controller
    if node is not None:
        params = native.pipeline[node]["recognition"]["param"]
        pixels = native.templates[params["template"][index]]
        x, y, _, _ = params["roi"]
        height, width = pixels.shape[:2]
        controller.frame[y:y + height, x:x + width] = pixels

    business_nodes = {
        name: native.resource.get_node_data(name)
        for name in native.pipeline
        if not name.startswith("Diagnostics_")
    }
    # Only the diagnostic guard's recognition timeout is shortened.
    detail = native.tasker.post_task(entry, {entry: {"timeout": 0}}).wait().get()
    assert detail is not None
    assert detail.status.succeeded is (node is not None)
    assert controller.clicks == []
    assert controller.texts == []
    assert controller.keys == []
    assert business_nodes == {
        name: native.resource.get_node_data(name) for name in business_nodes
    }

    controller.frame.fill(0)
    pixels = native.images[1]
    height, width = pixels.shape[:2]
    controller.frame[620:620 + height, 650:650 + width] = pixels
    detail = native.tasker.post_task(
        "ChatInput", runner.chat_input_override("after diagnostic", send=True)
    ).wait().get()
    assert detail is not None and detail.status.succeeded
    assert len(controller.clicks) == 2
    assert controller.texts == ["after diagnostic"]
    assert controller.keys == [("down", 17), ("click", 65), ("up", 17), ("click", 8)]
