from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from threading import Event

import pytest

from backend.app.api.envelope import AppError
from backend.app.shared.backend import account_context, gui_session, maafw_runner as runner
from backend.tests.test_maafw_runner import connected_token, sdk, window


def test_status_polling_never_initializes_and_connect_has_zero_input(sdk):
    for _ in range(3):
        status = gui_session.get_client_status()
        assert not status["connected"]
        assert status["window_generation"] == ""
    assert not sdk.controllers and not sdk.options and not sdk.calls

    status = gui_session.connect_client()
    assert status["connected"]
    assert status["window_generation"]
    assert "confirmed" not in status
    for _ in range(3):
        assert gui_session.get_client_status() == status
    assert len(sdk.controllers) == 1 and len(sdk.options) == 1
    assert not sdk.calls


@pytest.mark.parametrize("operation", [
    lambda: runner.chat_input("text"),
    lambda: runner.goto_contact("buyer"),
    lambda: runner.run_node("ChatInput_SendMessage"),
    lambda: runner.run_node("Diagnostics_Unknown"),
    lambda: runner.run_node("Diagnostics_ChatInput", {"Diagnostics_ChatInput": {"action": "Click"}}),
])
@pytest.mark.parametrize("connected", [False, True])
def test_direct_writes_cannot_bypass_guard(sdk, operation, connected):
    if connected:
        connected_token()
    assert not operation()[0]
    assert not sdk.calls
    assert len(sdk.controllers) == int(connected)


@pytest.mark.parametrize("entry", ["Diagnostics_ChatInput", "Diagnostics_ContactSearch"])
def test_diagnostics_work_without_account_or_connection(sdk, entry):
    sdk.config.clear()
    assert runner.run_node(entry)[0]
    assert sdk.calls == [(entry, {})]
    assert gui_session.get_client_status()["connected"]


def test_capture_requires_selection_and_current_epoch(sdk):
    gui_session.connect_client()
    context = account_context.get_account_context()
    with pytest.raises(AppError) as error:
        gui_session.capture_gui_session("old-epoch")
    assert error.value.status_code == 409
    assert gui_session.capture_gui_session(context.epoch).self_ali_id == context.self_ali_id
    sdk.config["self_ali_id"] = ""
    context = account_context.get_account_context()
    with pytest.raises(AppError) as error:
        gui_session.capture_gui_session(context.epoch)
    assert error.value.status_code == 409
    assert not sdk.calls


def test_unavailable_connection_raises_service_error(sdk):
    sdk.windows = []
    with pytest.raises(AppError) as error:
        gui_session.connect_client()
    assert error.value.status_code == 503
    epoch = account_context.get_account_context().epoch
    with pytest.raises(AppError) as error:
        gui_session.capture_gui_session(epoch)
    assert error.value.status_code == 503
    assert not sdk.calls


@pytest.mark.parametrize("windows", [[window(2)], [], [window(1), window(2)]])
def test_poll_invalidates_session_without_rebinding_even_if_window_returns(sdk, windows):
    token = connected_token()
    sdk.windows = windows
    status = gui_session.get_client_status()
    assert not status["connected"]
    assert status["window_generation"] == ""
    sdk.windows = [window(1)]
    assert not gui_session.get_client_status()["connected"]
    assert not gui_session.run_guarded(token, lambda: runner.chat_input("stale"))[0]
    assert len(sdk.controllers) == 1 and not sdk.calls
    new_status = gui_session.connect_client()
    assert new_status["window_generation"] != token.window_generation
    assert new_status["connected"]
    assert not gui_session.run_guarded(token, lambda: runner.chat_input("stale"))[0]
    assert not sdk.calls


def test_window_changed_between_connect_and_capture_is_rejected(sdk):
    gui_session.connect_client()
    sdk.windows = [window(2)]
    with pytest.raises(AppError) as error:
        gui_session.capture_gui_session(account_context.get_account_context().epoch)
    assert error.value.status_code == 503
    assert len(sdk.controllers) == 1 and not sdk.calls


def test_window_changed_after_capture_is_rejected_at_execution(sdk, monkeypatch):
    gui_session.connect_client()
    observations = iter([[window(1)], [window(2)]])
    monkeypatch.setattr(runner.Toolkit, "find_desktop_windows", lambda: next(observations))
    token = gui_session.capture_gui_session(account_context.get_account_context().epoch)
    assert not gui_session.run_guarded(token, lambda: runner.chat_input("stale"))[0]
    assert not sdk.calls


def test_repeated_capture_is_equal_until_same_window_reconnects(sdk):
    token = connected_token()
    for _ in range(3):
        captured = gui_session.capture_gui_session(token.epoch)
        assert captured == token
        assert gui_session.run_guarded(captured, lambda: True) is True
    after = gui_session.connect_client()
    assert token.window_generation != after["window_generation"]
    assert len(sdk.controllers) == 1
    assert not gui_session.run_guarded(token, lambda: runner.chat_input("stale"))[0]
    current = gui_session.capture_gui_session(token.epoch)
    assert current.window_generation == after["window_generation"]
    assert gui_session.run_guarded(current, lambda: True) is True
    assert not sdk.calls


def test_window_lost_during_connect_raises_service_error(sdk, monkeypatch):
    observations = iter([[window(1)], []])
    monkeypatch.setattr(runner.Toolkit, "find_desktop_windows", lambda: next(observations))
    with pytest.raises(AppError) as error:
        gui_session.connect_client()
    assert error.value.status_code == 503
    assert runner._window_generation == ""
    assert not sdk.calls


def test_discovery_error_invalidates_session_without_reconnecting(sdk):
    token = connected_token()
    sdk.fail = "discovery_exception"
    status = gui_session.get_client_status()
    assert not status["connected"]
    assert "discovery failed" in status["detail"]
    sdk.fail = None
    assert not gui_session.run_guarded(token, lambda: runner.chat_input("stale"))[0]
    assert len(sdk.controllers) == 1 and not sdk.calls


def test_diagnostic_rebind_also_invalidates_old_session(sdk):
    token = connected_token()
    sdk.windows = [window(2)]
    assert runner.run_node("Diagnostics_ChatInput")[0]
    status = gui_session.get_client_status()
    assert status["connected"]
    assert status["window_generation"] != token.window_generation
    assert not gui_session.run_guarded(token, lambda: runner.chat_input("stale"))[0]
    assert sdk.calls == [("Diagnostics_ChatInput", {})]


def test_window_change_after_navigation_stops_before_send_without_rebinding(sdk):
    token = connected_token()

    def navigate_and_send():
        assert runner.goto_contact("buyer")[0]
        sdk.windows = [window(2)]
        return runner.chat_input("must not send")

    success, reason = gui_session.run_guarded(token, navigate_and_send)
    assert not success and reason
    assert [entry for entry, _ in sdk.calls] == ["ContactSearch"]
    assert len(sdk.controllers) == 1
    assert not gui_session.get_client_status()["connected"]


@pytest.mark.parametrize("change", ["seller", "data_dir", "a_b_a", "restart", "reconnect"])
def test_queued_stale_session_fails_before_invoking_closure(sdk, monkeypatch, change):
    token = connected_token()
    invoked = []
    if change == "seller":
        sdk.config["self_ali_id"] = "seller-b"
    elif change == "data_dir":
        sdk.config["alibaba_data_dir"] = "other-data"
    elif change == "a_b_a":
        for seller in ("seller-b", "seller-a"):
            with account_context.changing_account():
                sdk.config["self_ali_id"] = seller
                account_context.invalidate_account_context()
        assert account_context.get_account_context().self_ali_id == token.self_ali_id
        assert account_context.get_account_context().epoch != token.epoch
    elif change == "restart":
        # Recreate the process-local startup state without reloading modules in other tests.
        runner._reset_binding()
        monkeypatch.setattr(account_context, "_context", None)
        assert not gui_session.get_client_status()["connected"]
        assert account_context.get_account_context().epoch != token.epoch
    else:
        gui_session.connect_client()
        assert gui_session.get_client_status()["connected"]

    # Capturing the current session cannot revive a previously queued task.
    if change == "restart":
        gui_session.connect_client()
    new_token = gui_session.capture_gui_session(account_context.get_account_context().epoch)
    assert new_token != token

    def queued():
        invoked.append(True)
        return runner.chat_input("stale")

    success, reason = gui_session.run_guarded(token, queued)
    assert not success and reason
    assert not invoked and not sdk.calls
    assert gui_session.run_guarded(new_token, lambda: runner.chat_input("current"))[0]


def test_token_is_immutable_and_all_binding_fields_are_checked(sdk):
    token = connected_token()
    with pytest.raises(FrozenInstanceError):
        token.epoch = "changed"
    for field in ("self_ali_id", "data_dir", "epoch", "window_generation"):
        altered = replace(token, **{field: "changed"})
        assert not gui_session.run_guarded(altered, lambda: runner.chat_input("forged"))[0]
    assert not sdk.calls


def test_account_switch_is_rejected_for_the_entire_guarded_task(sdk):
    token = connected_token()
    navigating, release = Event(), Event()

    def task():
        assert runner.goto_contact("buyer")[0]
        navigating.set()
        if not release.wait(5):
            raise TimeoutError("test gate not released")
        return runner.chat_input("current")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(gui_session.run_guarded, token, task)
        try:
            assert navigating.wait(2)
            with pytest.raises(AppError) as error:
                with account_context.changing_account():
                    pytest.fail("Account switch must not enter while the task holds account_lock")
            assert error.value.status_code == 409
        finally:
            release.set()
        assert future.result(timeout=2)[0]
    assert [entry for entry, _ in sdk.calls] == ["ContactSearch", "ChatInput"]
    with account_context.changing_account():
        sdk.config["self_ali_id"] = "seller-b"
        account_context.invalidate_account_context()
    assert gui_session.get_client_status()["connected"]
    assert not gui_session.run_guarded(token, lambda: runner.chat_input("stale"))[0]


def test_guard_does_not_leak_after_failure_or_to_other_threads(sdk):
    token = connected_token()

    def fail():
        raise RuntimeError("task failed")

    assert gui_session.run_guarded(token, fail) == (False, "task failed")
    assert not runner.chat_input("unguarded")[0]
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert not executor.submit(runner.chat_input, "unguarded worker").result(timeout=2)[0]
    assert not sdk.calls
    assert gui_session.run_guarded(token, lambda: runner.chat_input("current"))[0]
