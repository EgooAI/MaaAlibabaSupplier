"""Exercises update admission against real queues and controlled idle time."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from fastapi.testclient import TestClient

from backend.app import updater as updates
from backend.app.api.main import create_app
from backend.app.api.envelope import AppError
from backend.app.background import WorkBlocked
from backend.app.task_queue import TaskQueue
from backend.tests.auth_client import authenticate
from backend.tests.test_background import ControlledWake
from backend.tests.test_updater import manager, make_ready, remote, serve, wait_operation


@pytest.fixture
def clock(manager, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(manager, "_clock", lambda: now[0])
    monkeypatch.setattr(manager._background, "_clock", lambda: now[0])
    monkeypatch.setattr(manager._background, "_idle_since", 0.0)
    manager.state["auto"] = True
    return now


def test_auto_install_counts_from_download_ready_and_pauses_remote_checks(manager, clock, monkeypatch, tmp_path):
    github = serve(monkeypatch, tmp_path)
    clock[0] = 5000
    manager.check()
    wait_operation(manager)
    calls = github.json.call_count
    for elapsed in (0, 300, 600, 899):
        clock[0] = 5000 + elapsed
        manager._schedule()
        manager._auto_install()
        manager.runtime.start.assert_not_called()
    assert github.json.call_count == calls
    clock[0] = 5900
    manager._auto_install()
    manager.runtime.start.assert_called_once_with(*manager.prepared)
    manager.runtime.arm.assert_called_once()
    manager._auto_install()
    assert manager.runtime.start.call_count == 1
    with pytest.raises(WorkBlocked):
        manager._background.activity()


@pytest.mark.parametrize("kind", ["worker", "pool", "maafw", "translation", "cardsweep", "check"])
def test_even_short_work_between_polls_restarts_the_full_idle_period(manager, clock, kind, monkeypatch):
    make_ready(manager)
    runtime = manager._background
    clock[0] = 800
    if kind == "worker":
        assert runtime.worker("experiment", lambda stop: None).start().join(3)
    elif kind == "pool":
        pool = runtime.pool("crm-test")
        pool.submit(lambda: None).result(3)
        assert pool.join(3)
    elif kind in {"maafw", "translation"}:
        queue = TaskQueue(kind)
        queue.enqueue(lambda: (True, "done"), description="test")
        queue.shutdown(3)
    elif kind == "cardsweep":
        wake = ControlledWake()
        sweep = runtime.schedule("card-sweep", lambda: None, delay=None, wake=wake).start()
        assert wake.waits.get(timeout=3) == (None, False)
        sweep.trigger()
        assert wake.waits.get(timeout=3) == (None, False)
        sweep.stop(3)
    else:
        remote(monkeypatch, runs=[])
        manager.check()
        wait_operation(manager)
    clock[0] = 900
    manager._auto_install()
    manager.runtime.start.assert_not_called()
    clock[0] = 1699
    manager._auto_install()
    manager.runtime.start.assert_not_called()
    clock[0] = 1700
    manager._auto_install()
    manager.runtime.arm.assert_called_once()


def test_resident_lifetime_and_polling_do_not_reset_idle_but_active_poll_blocks_install(manager, clock):
    make_ready(manager)
    runtime = manager._background
    listener = runtime.worker("receiver", lambda stop: stop.wait(), resident=True).start()
    wake = ControlledWake()
    entered, release = Event(), Event()

    def poll():
        entered.set()
        assert release.wait(3)

    checker = runtime.schedule("resident-poll", poll, delay=None, wake=wake, resident=True).start()
    try:
        assert wake.waits.get(timeout=3) == (None, False)
        clock[0] = 900
        checker.trigger()
        assert entered.wait(3)
        manager._auto_install()
        manager.runtime.start.assert_not_called()
        release.set()
        assert wake.waits.get(timeout=3) == (None, False)
        manager._auto_install()
        manager.runtime.arm.assert_called_once()
        assert listener.is_alive()
    finally:
        release.set()
        checker.stop(3)
        listener.stop(3)


@pytest.mark.parametrize("kind", ["queue", "pool_callback", "pending_schedule"])
def test_pending_work_and_completion_callbacks_reject_both_install_modes(manager, clock, kind):
    make_ready(manager)
    runtime = manager._background
    entered, release = Event(), Event()
    cleanup = None
    calls = []

    def hold(*args):
        entered.set()
        assert release.wait(3)
        return True, "done"

    try:
        if kind == "queue":
            queue = TaskQueue("maafw")
            cleanup = lambda: queue.shutdown(3)
            queue.enqueue(hold, description="active")
            assert entered.wait(3)
            queue.enqueue(lambda: (calls.append("queued") is None, "done"), description="pending")
        elif kind == "pool_callback":
            pool = runtime.pool("crm-callback")
            cleanup = lambda: pool.join(3)
            finish = Event()
            future = pool.submit(lambda: finish.wait(3))
            future.add_done_callback(hold)
            finish.set()
            assert entered.wait(3)
            assert future.done()
        else:
            wake = ControlledWake()
            worker = runtime.schedule("sweep", lambda: calls.append("unexpected"), delay=None,
                                      ready=lambda: False, wake=wake).start()
            cleanup = lambda: worker.stop(3)
            assert wake.waits.get(timeout=3) == (None, False)
            worker.trigger()
            assert wake.waits.get(timeout=3) == (1, False)
        clock[0] = 5000
        manager._auto_install()
        with pytest.raises(AppError) as exc:
            manager.install_update("opaque")
        assert exc.value.status_code == 409
        manager.runtime.start.assert_not_called()
        assert not manager.handoff.is_set()
    finally:
        release.set()
        if cleanup:
            cleanup()
    if kind == "queue":
        assert calls == ["queued"]
    # Manual installation has no cooldown after the final work completes.
    manager.install_update("opaque")
    manager.runtime.start.assert_called_once()


def test_install_gate_closes_before_helper_start_and_reopens_on_failure(manager, clock):
    make_ready(manager)
    runtime = manager._background
    queue = TaskQueue("maafw")
    sweep = runtime.schedule("card-sweep", lambda: None, delay=None).start()
    pool = runtime.pool("crm-test")
    wake = ControlledWake()
    timer_calls = []
    timer = runtime.schedule("timer", lambda: timer_calls.append("ran"), delay=30, wake=wake).start()
    assert wake.waits.get(timeout=3) == (30, False)
    entered, release = Event(), Event()

    def prepare(*args):
        entered.set()
        assert release.wait(3)
        raise updates.UpdateError("Helper unavailable")

    manager.runtime.start.side_effect = prepare
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            installing = executor.submit(manager.install_update, "opaque")
            try:
                assert entered.wait(3)
                wake.elapse()
                assert wake.waits.get(timeout=3) == (1, False)
                assert timer_calls == []
                for submit in (
                    lambda: queue.enqueue(lambda: (True, "late"), description="late"),
                    lambda: pool.submit(lambda: None),
                    lambda: runtime.worker("late-worker", lambda stop: None).start(),
                    sweep.trigger,
                    lambda: runtime.activity(),
                ):
                    with pytest.raises(WorkBlocked):
                        submit()
                client = TestClient(create_app())
                try:
                    # Existing credentials are used: login itself is a write.
                    token = client.app.state.auth_store.issue()
                    client.headers["Authorization"] = "Bearer " + token
                    assert client.post("/api/app/update/check", json={}).status_code == 409
                finally:
                    client.close()
            finally:
                release.set()
            with pytest.raises(AppError):
                installing.result(3)
        with runtime.activity():
            pass
        queue.enqueue(lambda: (True, "reopened"), description="retry")
        pool.submit(lambda: None).result(3)
        sweep.trigger()
        wake.elapse()
        assert wake.waits.get(timeout=3) == (None, False)
        assert timer_calls == ["ran"]
    finally:
        release.set()
        queue.shutdown(3)
        sweep.stop(3)
        timer.stop(3)
        pool.shutdown()


@pytest.mark.parametrize("failure", ["start", "arm", "reset", "watchdog"])
def test_failed_automatic_attempt_releases_gate_and_waits_another_full_period(manager, clock, failure):
    make_ready(manager)
    clock[0] = 900
    if failure == "watchdog":
        manager.runtime.failed.return_value = True
    else:
        getattr(manager.runtime, failure).side_effect = updates.UpdateError("unavailable")
    manager._auto_install()
    if failure == "watchdog":
        assert not manager.writes_blocked()
    with manager._background.activity():
        pass
    count = manager.runtime.reset.call_count
    manager.runtime.reset.side_effect = None
    manager.runtime.start.side_effect = None
    manager.runtime.arm.side_effect = None
    manager.runtime.failed.return_value = False
    clock[0] = 1799
    manager._auto_install()
    assert manager.runtime.reset.call_count == count
    clock[0] = 1800
    manager._auto_install()
    assert manager.runtime.reset.call_count == count + 1
    assert manager.handoff.is_set()


def test_auto_opt_out_and_missing_installer_never_start_helper(manager, clock):
    make_ready(manager)
    clock[0] = 1000
    manager.state["auto"] = False
    manager._auto_install()
    manager.runtime.start.assert_not_called()
    manager.state["auto"] = True
    manager.prepared[0].unlink()
    manager._auto_install()
    manager.runtime.start.assert_not_called()
    assert manager.prepared is None
    with manager._background.activity():
        pass


def test_an_inflight_api_write_prevents_installation_before_it_enqueues_work(manager, clock):
    make_ready(manager)
    app = create_app()
    entered, release = Event(), Event()

    @app.post("/api/test-held-write")
    def held_write():
        entered.set()
        assert release.wait(3)
        return {"ok": True}

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        authenticate(client)
        request = executor.submit(client.post, "/api/test-held-write")
        try:
            assert entered.wait(3)
            clock[0] = 1000
            manager._auto_install()
            with pytest.raises(AppError):
                manager.install_update("opaque")
            manager.runtime.start.assert_not_called()
        finally:
            release.set()
        assert request.result(3).status_code == 200
        manager.install_update("opaque")
        manager.runtime.start.assert_called_once()
