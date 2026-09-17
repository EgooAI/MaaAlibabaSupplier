from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from threading import Event

import pytest

from backend.app.api.envelope import AppError
from backend.app.shared.backend import account_context, gui_session, maafw_runner as runner
from backend.tests.test_maafw_runner import confirmed_token, sdk, window


def test_status_polling_never_initializes_and_connect_has_zero_input(sdk):
    for _ in range(3):
        status = gui_session.get_client_status()
        assert not status["connected"] and not status["confirmed"]
        assert status["window_generation"] == ""
    assert not sdk.controllers and not sdk.options and not sdk.calls

    status = gui_session.connect_client()
    assert status["connected"] and not status["confirmed"]
    assert status["window_generation"]
    assert "not automatic login identity verification" in status["detail"]
    assert "same window cannot be detected" in status["detail"]
    for _ in range(3):
        assert gui_session.get_client_status() == status
    assert len(sdk.controllers) == 1 and len(sdk.options) == 1
    assert not sdk.calls


@pytest.mark.parametrize("operation", [
    lambda: runner.chat_send("text"),
    lambda: runner.chat_input("text"),
    lambda: runner.goto_contact("buyer"),
    lambda: runner.run_node("ChatInput_SendMessage"),
    lambda: runner.run_node("Diagnostics_Unknown"),
    lambda: runner.run_node("Diagnostics_ChatInput", {"Diagnostics_ChatInput": {"action": "Click"}}),
])
@pytest.mark.parametrize("confirmed", [False, True])
def test_direct_writes_cannot_bypass_guard(sdk, operation, confirmed):
    if confirmed:
        confirmed_token()
    assert not operation()[0]
    assert not sdk.calls
    assert len(sdk.controllers) == int(confirmed)


@pytest.mark.parametrize("entry", ["Diagnostics_ChatInput", "Diagnostics_ContactSearch"])
def test_diagnostics_work_without_account_or_confirmation(sdk, entry):
    sdk.config.clear()
    assert runner.run_node(entry)[0]
    assert sdk.calls == [(entry, {})]
    assert not gui_session.get_client_status()["confirmed"]


def test_confirmation_requires_selection_current_epoch_and_generation(sdk):
    status = gui_session.connect_client()
    context = account_context.get_account_context()
    for epoch, generation in (("old-epoch", status["window_generation"]), (context.epoch, "old-window")):
        with pytest.raises(AppError) as error:
            gui_session.confirm_client(epoch, generation)
        assert error.value.status_code == 409
    with pytest.raises(AppError) as error:
        gui_session.capture_gui_session(context.epoch)
    assert error.value.status_code == 409
    sdk.config["self_ali_id"] = ""
    context = account_context.get_account_context()
    with pytest.raises(AppError) as error:
        gui_session.confirm_client(context.epoch, status["window_generation"])
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
    with pytest.raises(AppError) as error:
        gui_session.confirm_client(epoch, "")
    assert error.value.status_code == 503
    assert not sdk.calls


@pytest.mark.parametrize("windows", [[window(2)], [], [window(1), window(2)]])
def test_poll_revokes_confirmation_without_rebinding_even_if_window_returns(sdk, windows):
    token = confirmed_token()
    sdk.windows = windows
    status = gui_session.get_client_status()
    assert not status["connected"] and not status["confirmed"]
    assert status["window_generation"] == ""
    sdk.windows = [window(1)]
    assert not gui_session.get_client_status()["confirmed"]
    assert not gui_session.run_guarded(token, lambda: runner.chat_send("stale"))[0]
    assert len(sdk.controllers) == 1 and not sdk.calls
    new_status = gui_session.connect_client()
    assert new_status["window_generation"] != token.window_generation
    assert not new_status["confirmed"]
    with pytest.raises(AppError) as error:
        gui_session.confirm_client(token.epoch, token.window_generation)
    assert error.value.status_code == 409


def test_window_changed_between_connect_and_confirm_is_rejected(sdk):
    status = gui_session.connect_client()
    sdk.windows = [window(2)]
    with pytest.raises(AppError) as error:
        gui_session.confirm_client(account_context.get_account_context().epoch, status["window_generation"])
    assert error.value.status_code == 409
    assert len(sdk.controllers) == 1 and not sdk.calls


def test_window_changed_during_confirmation_is_rejected(sdk, monkeypatch):
    status = gui_session.connect_client()
    observations = iter([[window(1)], [window(2)]])
    monkeypatch.setattr(runner.Toolkit, "find_desktop_windows", lambda: next(observations))
    with pytest.raises(AppError) as error:
        gui_session.confirm_client(account_context.get_account_context().epoch, status["window_generation"])
    assert error.value.status_code == 409
    assert gui_session._confirmation is None
    assert not sdk.calls


def test_reconnect_same_window_invalidates_an_open_confirmation_dialog(sdk):
    before = gui_session.connect_client()
    epoch = account_context.get_account_context().epoch
    after = gui_session.connect_client()
    assert before["window_generation"] != after["window_generation"]
    with pytest.raises(AppError) as error:
        gui_session.confirm_client(epoch, before["window_generation"])
    assert error.value.status_code == 409
    assert gui_session.confirm_client(epoch, after["window_generation"])["confirmed"]
    assert not sdk.calls


def test_window_lost_during_connect_raises_service_error(sdk, monkeypatch):
    observations = iter([[window(1)], []])
    monkeypatch.setattr(runner.Toolkit, "find_desktop_windows", lambda: next(observations))
    with pytest.raises(AppError) as error:
        gui_session.connect_client()
    assert error.value.status_code == 503
    assert gui_session._confirmation is None
    assert not sdk.calls


def test_discovery_error_revokes_confirmation_without_reconnecting(sdk):
    token = confirmed_token()
    sdk.fail = "discovery_exception"
    status = gui_session.get_client_status()
    assert not status["connected"] and not status["confirmed"]
    assert "discovery failed" in status["detail"]
    sdk.fail = None
    assert not gui_session.run_guarded(token, lambda: runner.chat_send("stale"))[0]
    assert len(sdk.controllers) == 1 and not sdk.calls


def test_diagnostic_rebind_also_invalidates_old_confirmation(sdk):
    token = confirmed_token()
    sdk.windows = [window(2)]
    assert runner.run_node("Diagnostics_ChatInput")[0]
    status = gui_session.get_client_status()
    assert status["connected"] and not status["confirmed"]
    assert status["window_generation"] != token.window_generation
    assert not gui_session.run_guarded(token, lambda: runner.chat_send("stale"))[0]
    assert sdk.calls == [("Diagnostics_ChatInput", {})]


def test_window_change_after_navigation_stops_before_send_without_rebinding(sdk):
    token = confirmed_token()

    def navigate_and_send():
        assert runner.goto_contact("buyer")[0]
        sdk.windows = [window(2)]
        return runner.chat_send("must not send")

    success, reason = gui_session.run_guarded(token, navigate_and_send)
    assert not success and reason
    assert [entry for entry, _ in sdk.calls] == ["ContactSearch"]
    assert len(sdk.controllers) == 1
    assert not gui_session.get_client_status()["confirmed"]


@pytest.mark.parametrize("change", ["seller", "data_dir", "a_b_a", "restart", "reconfirm"])
def test_queued_stale_session_fails_before_invoking_closure(sdk, monkeypatch, change):
    token = confirmed_token()
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
        monkeypatch.setattr(gui_session, "_confirmation", None)
        monkeypatch.setattr(account_context, "_context", None)
        assert not gui_session.get_client_status()["confirmed"]
        assert account_context.get_account_context().epoch != token.epoch
    else:
        gui_session.connect_client()
        assert not gui_session.get_client_status()["confirmed"]

    # Even a new confirmation cannot revive a previously queued task.
    new_token = confirmed_token()
    assert new_token != token

    def queued():
        invoked.append(True)
        return runner.chat_send("stale")

    success, reason = gui_session.run_guarded(token, queued)
    assert not success and reason
    assert not invoked and not sdk.calls
    assert gui_session.run_guarded(new_token, lambda: runner.chat_input("current"))[0]


def test_token_is_immutable_and_all_binding_fields_are_checked(sdk):
    token = confirmed_token()
    with pytest.raises(FrozenInstanceError):
        token.epoch = "changed"
    for field in ("self_ali_id", "data_dir", "epoch", "window_generation", "confirmation_id"):
        altered = replace(token, **{field: "changed"})
        assert not gui_session.run_guarded(altered, lambda: runner.chat_send("forged"))[0]
    assert not sdk.calls


def test_account_switch_is_rejected_for_the_entire_guarded_task(sdk):
    token = confirmed_token()
    navigating, release = Event(), Event()

    def task():
        assert runner.goto_contact("buyer")[0]
        navigating.set()
        if not release.wait(5):
            raise TimeoutError("test gate not released")
        return runner.chat_send("current")

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
    assert not gui_session.get_client_status()["confirmed"]


def test_guard_does_not_leak_after_failure_or_to_other_threads(sdk):
    token = confirmed_token()

    def fail():
        raise RuntimeError("task failed")

    assert gui_session.run_guarded(token, fail) == (False, "task failed")
    assert not runner.chat_send("unguarded")[0]
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert not executor.submit(runner.chat_send, "unguarded worker").result(timeout=2)[0]
    assert not sdk.calls
    assert gui_session.run_guarded(token, lambda: runner.chat_send("confirmed"))[0]
