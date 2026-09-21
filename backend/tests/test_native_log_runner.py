from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.shared.backend import gui_session, maafw_runner as runner
from backend.app.shared.utils import native_logs
from backend.tests.test_maafw_runner import FakeJob, connected_token, native_pipeline, sdk


def test_native_setter_passes_utf8_byte_length_without_vendor_binding(monkeypatch, tmp_path):
    from maa.library import Library

    setter = Mock(return_value=True)
    monkeypatch.setattr(Library, "framework", lambda: SimpleNamespace(MaaGlobalSetOption=setter))
    path = tmp_path / "\u65e5\u5fd7" / "session"
    assert runner._set_native_log_dir(path)
    _, payload, size = setter.call_args.args
    assert payload == str(path).encode("utf-8")
    assert size == len(payload) and size > len(str(path))


def test_unicode_native_log_path_with_synthetic_task(native_pipeline, tmp_path):
    path = tmp_path / "\u65e5\u5fd7" / "session"
    assert runner._set_native_log_dir(path)
    job = native_pipeline.tasker.post_task("Diagnostics_ChatInput", {"Diagnostics_ChatInput": {"timeout": 0}}).wait()
    assert job.done
    assert any(path.rglob("*.log"))


def test_init_attaches_before_native_operations_and_reasserts_after_toolkit(sdk, monkeypatch):
    order = []
    init = runner.Toolkit.init_option
    controller = runner.Win32Controller

    def options(path):
        order.append("toolkit")
        return init(path)

    def set_dir(path):
        order.append("log_dir")
        assert path.parent.name == "native" and path.parent.parent.name == "logs"
        return True

    def create(*args, **kwargs):
        order.append("controller")
        return controller(*args, **kwargs)

    monkeypatch.setattr(runner.Toolkit, "init_option", options)
    monkeypatch.setattr(runner, "_set_native_log_dir", set_dir)
    monkeypatch.setattr(runner, "Win32Controller", create)
    connected_token()
    assert order == ["log_dir", "toolkit", "log_dir", "controller"]
    assert runner.get_native_log_status()["initialized"]


@pytest.mark.parametrize("phase", ["controller", "resource"])
def test_uncertain_initialization_job_prevents_global_reconfiguration(sdk, monkeypatch, phase):
    class Job(FakeJob):
        def wait(self):
            raise RuntimeError("initialization completion unknown")

    if phase == "controller":
        monkeypatch.setattr(runner.Win32Controller, "post_connection", lambda self: Job())
    else:
        monkeypatch.setattr(runner.Resource, "post_bundle", lambda self, path: Job())
    assert runner._ensure_init()[0] is None
    options, directories = len(sdk.options), len(sdk.native_dirs)
    assert runner._ensure_init()[0] is None
    assert len(sdk.options) == options and len(sdk.native_dirs) == directories
    assert runner.get_native_log_status()["outstanding_jobs"] == 1


def test_only_outer_guard_rotates_and_events_retain_one_native_session(sdk, monkeypatch):
    token = connected_token()
    initial = runner.get_native_log_status()["active_session"]
    events = []
    monkeypatch.setattr(runner, "log_event", lambda event, **fields: events.append((event, fields)))
    monkeypatch.setattr(native_logs, "LOG_MAX_BYTES", 1)
    calls = []

    class Job(FakeJob):
        job_id = 42

        def wait(self):
            calls.append("wait")
            return self

    def post(*args):
        calls.append("post")
        return Job(detail=SimpleNamespace(status=SimpleNamespace(succeeded=True)))

    monkeypatch.setattr(runner._tasker, "post_task", post)

    def action():
        active = runner.get_native_log_status()["active_session"]
        assert active != initial
        assert runner.goto_contact("private-contact")[0]
        assert gui_session.run_guarded(token, lambda: runner.chat_input("private-body"))[0]
        job = runner.submit_send()
        assert runner.get_native_log_status()["outstanding_jobs"] == 1
        assert runner.get_native_log_status()["active_session"] == active
        assert runner.wait_send(job)[0]
        assert runner.get_native_log_status()["active_session"] == active
        return active

    active = gui_session.run_guarded(token, action)
    assert calls == ["post", "wait"] * 3
    assert all(fields["native_log_session_id"] == active for _, fields in events)
    assert gui_session.run_guarded(token, lambda: True) is True
    assert runner.get_native_log_status()["active_session"] != active


@pytest.mark.parametrize("failure", ["post", "wait", "get"])
def test_uncertain_native_completion_defers_rotation_but_result_error_does_not(sdk, monkeypatch, failure):
    token = connected_token()
    initial = runner.get_native_log_status()["active_session"]
    calls = []

    class Job(FakeJob):
        def wait(self):
            calls.append("wait")
            if failure == "wait":
                raise RuntimeError("completion unknown")
            return self

        def get(self):
            raise RuntimeError("result unavailable")

    def post(*args):
        calls.append("post")
        if failure == "post":
            raise RuntimeError("submission unknown")
        return Job()

    monkeypatch.setattr(runner._tasker, "post_task", post)
    assert not gui_session.run_guarded(token, lambda: runner.wait_send(runner.submit_send()))[0]
    monkeypatch.setattr(native_logs, "LOG_MAX_BYTES", 1)
    assert gui_session.run_guarded(token, lambda: True) is True
    observed = runner.get_native_log_status()
    assert (observed["active_session"] == initial) is (failure != "get")
    assert ("native_rotation_deferred_outstanding_jobs" in observed["warnings"]) is (failure != "get")
    assert calls == (["post"] if failure == "post" else ["post", "wait"])


def test_split_send_blocks_rotation_until_original_wait_completes(sdk, monkeypatch):
    token = connected_token()
    job = gui_session.run_guarded(token, runner.submit_send)
    active = runner.get_native_log_status()["active_session"]
    monkeypatch.setattr(native_logs, "LOG_MAX_BYTES", 1)
    # Even an incorrectly released outer guard cannot authorize rotating a live job.
    assert gui_session.run_guarded(token, lambda: True) is True
    assert runner.get_native_log_status()["active_session"] == active
    assert runner.wait_send(job)[0]
    assert gui_session.run_guarded(token, lambda: True) is True
    assert runner.get_native_log_status()["active_session"] != active
    assert len(sdk.calls) == 1


@pytest.mark.parametrize("terminal", [False, "unavailable"])
def test_uncertain_terminal_observation_does_not_skip_wait_and_defers_rotation(sdk, monkeypatch, terminal):
    token = connected_token()
    active = runner.get_native_log_status()["active_session"]
    calls = []

    class Job(FakeJob):
        @property
        def done(self):
            assert calls[-1] == "wait"
            if terminal == "unavailable":
                raise RuntimeError("terminal status unavailable")
            return False

        def wait(self):
            calls.append("wait")
            return self

    job = Job(detail=SimpleNamespace(status=SimpleNamespace(succeeded=True)))
    monkeypatch.setattr(runner._tasker, "post_task", lambda *args: job)
    assert gui_session.run_guarded(token, lambda: runner.wait_send(runner.submit_send()))[0]
    monkeypatch.setattr(native_logs, "LOG_MAX_BYTES", 1)
    assert gui_session.run_guarded(token, lambda: True) is True
    assert calls == ["wait"]
    assert runner.get_native_log_status()["active_session"] == active
    assert runner.get_native_log_status()["outstanding_jobs"] == 1


@pytest.mark.parametrize("failure", ["setter", "inventory", "event"])
def test_policy_and_event_failure_cannot_skip_native_wait_or_replay(sdk, monkeypatch, failure):
    token = connected_token()
    calls = []

    class Job(FakeJob):
        job_id = 42

        def wait(self):
            calls.append("wait")
            return self

    def post(*args):
        calls.append("post")
        return Job(detail=SimpleNamespace(status=SimpleNamespace(succeeded=True)))

    def fail(*args, **kwargs):
        raise OSError("private exception")

    monkeypatch.setattr(runner._tasker, "post_task", post)
    if failure == "setter":
        monkeypatch.setattr(native_logs, "LOG_MAX_BYTES", 1)
        monkeypatch.setattr(runner, "_set_native_log_dir", fail)
    elif failure == "inventory":
        monkeypatch.setattr(runner._native_logs, "status", fail)
    else:
        monkeypatch.setattr(runner, "log_event", fail)
    assert gui_session.run_guarded(token, lambda: runner.wait_send(runner.submit_send()))[0]
    assert calls == ["post", "wait"]
    if failure != "event":
        assert "native_log_policy_unavailable" in runner.get_native_log_status()["warnings"]


def test_status_is_noninitializing_and_never_waits_for_guard_or_scans(sdk, monkeypatch):
    for _ in range(3):
        assert not runner.get_native_log_status()["initialized"]
    assert runner._native_logs is None and not sdk.native_dirs
    token = connected_token()
    entered, release = Event(), Event()

    def hold():
        entered.set()
        assert release.wait(5)

    def unexpected(*args):
        raise AssertionError("Observation must not scan or call native code")

    with ThreadPoolExecutor(max_workers=2) as pool:
        guarded = pool.submit(gui_session.run_guarded, token, hold)
        try:
            assert entered.wait(2)
            monkeypatch.setattr(runner._native_logs, "status", unexpected)
            monkeypatch.setattr(runner, "_set_native_log_dir", unexpected)
            observed = pool.submit(runner.get_native_log_status).result(timeout=1)
            assert observed["initialized"] and observed["active_session"]
            observed["warnings"].clear()
            assert runner.get_native_log_status()["warnings"]
        finally:
            release.set()
        guarded.result(timeout=2)


def test_bare_diagnostics_rotate_at_entry_with_no_pending_jobs(sdk, monkeypatch):
    assert runner.run_node("Diagnostics_ChatInput")[0]
    first = runner.get_native_log_status()["active_session"]
    monkeypatch.setattr(native_logs, "LOG_MAX_BYTES", 1)
    assert runner.run_node("Diagnostics_ChatInput")[0]
    assert runner.get_native_log_status()["active_session"] != first
    assert runner.get_native_log_status()["outstanding_jobs"] == 0
