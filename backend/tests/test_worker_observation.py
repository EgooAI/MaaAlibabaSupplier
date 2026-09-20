"""Worker observations remain available during blocked work; clocks are controlled."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import time

from backend.app.api import connection
from backend.app.api.routers import status as status_router
from backend.app.shared.backend import im_db_middleware as source_module
from backend.app.shared.backend import sync_coordinator as sync_module
from backend.app.shared.backend import outbox_service as outbox_module
from backend.app.shared.backend import maafw_runner, status as status_module
from backend.app.shared.backend.account_context import account_lock, get_account_context
from backend.app.shared.crm.account_keys import save_key
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.mitm.pool import SelfInfo
from backend.tests.test_im_db_isolation import KEY, committed, encrypted_db, mw, submissions
from backend.tests.test_outbox_service import env, prepared, confirm


def test_sync_observation_survives_blocked_iteration_and_gui_lifecycle_locks(mw, monkeypatch):
    entered, release, waited, held, unlock = (Event() for _ in range(5))
    now = [100.0]
    service = sync_module.SyncService(mw, interval=3600, clock=lambda: now[0])
    monkeypatch.setattr(sync_module, "_service", service)
    monkeypatch.setattr(outbox_module, "_service", None)

    def tick(**kwargs):
        entered.set()
        assert release.wait(5)

    observe = service._observe

    def record(phase, **changes):
        observe(phase, **changes)
        if phase == "waiting":
            waited.set()

    monkeypatch.setattr(mw, "sync_tick", tick)
    monkeypatch.setattr(service, "_observe", record)
    monkeypatch.setattr(status_module, "_check_port", lambda host, port: status_module.NetworkStatus(False, host, port, None, "offline"))
    monkeypatch.setattr(maafw_runner, "_window_generation", "window-a")
    monkeypatch.setattr(status_router, "_last_node_result", {
        "success": True, "message": "read-only check", "completed_at": 99,
        "context": get_account_context().to_dict(), "window_generation": "window-a",
    })

    def hold_locks():
        with account_lock, mw._lock, maafw_runner._run_lock, service._lifecycle_lock, sync_module._service_lock:
            held.set()
            assert unlock.wait(5)

    service.start()
    try:
        assert entered.wait(5)
        now[0] = 145.0
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(hold_locks)
            try:
                assert held.wait(5)
                snapshot = pool.submit(status_router._build_system_snapshot).result(timeout=2)
                assert snapshot["lastDiagnostic"]["currentContext"]
                assert snapshot["lastDiagnostic"]["completed_at"] == 99
                worker = snapshot["workers"]["im-source-check"]
                assert worker["started"] and worker["alive"]
                assert worker["phase"] == "checking_source" and worker["phase_age_s"] == 45
                assert worker["heartbeat_at"] == 100 and worker["last_progress_at"] is None
                assert worker["completed_iterations"] == 0 and worker["last_error"] is None
            finally:
                unlock.set()
            holder.result(timeout=5)
        release.set()
        assert waited.wait(5)
        worker = service.observation()
        assert worker["completed_iterations"] == 1 and worker["last_progress_at"] == 145
        assert worker["phase"] == "waiting"
    finally:
        release.set()
        unlock.set()
        service.stop()
    worker = service.observation()
    assert worker["started"] and not worker["alive"] and worker["stopping"]
    assert worker["phase"] == "stopped"


def test_verifier_observes_blocked_scan_without_gui_locks_or_resend(env, monkeypatch):
    wake = Event()
    original_wait = env.service._stopping.wait
    first = [True]

    def wait(timeout):
        if first[0]:
            first[0] = False
            assert wake.wait(5)
            return env.service._stopping.is_set()
        return original_wait(timeout)

    monkeypatch.setattr(env.service._stopping, "wait", wait)
    # The normal send flow creates a real temporary outbox record to reconcile.
    task = prepared(env)
    confirm(env, task)
    env.queue.run()
    before = env.service.get(env.context, task["id"])
    assert before["status"] == "verifying"
    entered, release, finished, held, unlock = (Event() for _ in range(5))
    from backend.app.shared.backend import source_messages

    def read(context, contact):
        entered.set()
        assert release.wait(5)
        return {"valid": False, "reason": "test source unavailable"}

    monkeypatch.setattr(source_messages, "read_source_messages", read)
    monkeypatch.setattr(outbox_module, "_service", env.service)
    original_observe = env.service._observe_verifier

    def observe(phase, **changes):
        original_observe(phase, **changes)
        if phase == "waiting" and changes.get("last_progress_at") is not None:
            finished.set()

    monkeypatch.setattr(env.service, "_observe_verifier", observe)

    def hold_locks():
        with maafw_runner._run_lock, outbox_module._service_lock, env.service._condition:
            held.set()
            assert unlock.wait(5)

    try:
        wake.set()
        assert entered.wait(5)
        env.now += 45
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(hold_locks)
            try:
                assert held.wait(5)
                worker = pool.submit(outbox_module.observe_outbox_verifier).result(timeout=2)
                assert worker["started"] and worker["alive"] and worker["pending"] == 1
                assert worker["context"] == env.context.to_dict()
                assert worker["phase"] == "reading_source" and worker["phase_age_s"] == 45
                assert worker["last_progress_at"] is None and worker["completed_iterations"] == 0
            finally:
                unlock.set()
            holder.result(timeout=5)
        # Observation alone cannot turn a slow verification into a failure.
        assert env.service.get(env.context, task["id"]) == before
        release.set()
        assert finished.wait(5)
        worker = env.service.verifier_observation()
        assert worker["completed_iterations"] == 1 and worker["last_progress_at"] == env.now
        assert env.calls.count("click") == 1 and not env.queue.jobs
    finally:
        release.set()
        unlock.set()
        env.service.stop()
    assert not env.service.verifier_observation()["alive"]


def test_source_observation_expires_without_changing_committed_archive(mw, submissions, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(source_module, "time", SimpleNamespace(time=lambda: now[0], perf_counter=time.perf_counter))
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    pending = mw.sync_status()
    assert pending["last_observed_at"] == pending["last_checked"] == 100
    assert pending["revision"] == 0 and not pending["ready"]
    submissions[0][3].set_result(committed(submissions[0][4], 7))
    mw._coordinator.tick()
    assert mw.sync_status()["ready"]
    now[0] = 106
    mw.sync_tick()
    fresh = mw.sync_status()
    assert fresh["last_observed_at"] == 106 and fresh["revision"] == 7
    assert len(submissions) == 1  # Unchanged sources advance observation, not CRM.
    now[0] += source_module.SOURCE_OBSERVATION_MAX_AGE + 1
    stale = mw.sync_status()
    assert stale["observation_stale"] and stale["stale"] and not stale["ready"]
    assert stale["phase"] == "ready" and stale["last_error"] == ""
    for key in ("revision", "applied_source_revision", "source_revision", "last_success", "last_observed_at", "last_checked"):
        assert stale[key] == fresh[key]
    CRMAdapter().sync_conversations([], SelfInfo(ali_id="10001"))
    monkeypatch.setattr(connection, "get_client_status", lambda: {"connected": False, "window_generation": "", "detail": "test"})
    assert connection.connection_snapshot()["capabilities"]["read_chat"]
    mw.sync_tick()
    assert mw.sync_status()["ready"] and mw.sync_status()["last_observed_at"] == now[0]
    assert mw.sync_status()["revision"] == 7 and len(submissions) == 1


def test_failed_skipped_and_reset_checks_do_not_renew_source_observation(mw, submissions, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(source_module, "time", SimpleNamespace(time=lambda: now[0], perf_counter=time.perf_counter))
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    submissions[0][3].set_result(committed(submissions[0][4]))
    mw._coordinator.tick()
    held, release = Event(), Event()

    def gui():
        with account_lock:
            held.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(gui)
        try:
            assert held.wait(5)
            now[0] = 140
            mw.sync_tick()
            assert mw.sync_status()["last_checked"] == mw.sync_status()["last_observed_at"] == 100
            assert mw.sync_status()["observation_stale"]
        finally:
            release.set()
        work.result(timeout=5)
    monkeypatch.setattr(mw, "resolve_encrypted_db_path", lambda *args: None)
    mw.sync_tick()
    failed = mw.sync_status()
    assert failed["last_checked"] == 140 and failed["last_observed_at"] == 100
    now[0] = 141
    mw.sync_tick()  # Backoff is not a successful source observation.
    assert mw.sync_status()["last_checked"] == 140 and mw.sync_status()["last_observed_at"] == 100
    mw.set_self_ali_id("10002")
    assert mw.sync_status()["last_observed_at"] is None


def test_valid_key_health_requires_current_successful_source_observation(mw, submissions, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(source_module, "time", SimpleNamespace(time=lambda: now[0], perf_counter=time.perf_counter))
    monkeypatch.setattr(status_module, "_check_port", lambda host, port: status_module.NetworkStatus(False, host, port, None, "offline"))
    monkeypatch.setattr(status_router, "_last_node_result", None)
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")

    def identity():
        return next(item for item in status_router._build_system_snapshot()["modules"] if item["id"] == "health-identity")

    assert identity()["status"] == "uncertain"  # Saved key alone is not validation.
    mw.retry_connection()
    assert identity()["status"] == "healthy"
    assert identity()["observedAt"] == 100
    assert not mw.sync_status()["ready"] and mw.sync_status()["revision"] == 0
    now[0] = 131
    assert identity()["status"] == "uncertain"
    mw._key_validation = "invalid"
    mw._publish_source_status()
    assert identity()["status"] == "warning"


def test_worker_exception_is_observed_without_counting_successful_progress(mw, monkeypatch):
    now = [100.0]
    waiting = Event()
    service = sync_module.SyncService(mw, interval=3600, clock=lambda: now[0])
    observation = service._observe

    def record(phase, **changes):
        observation(phase, **changes)
        if phase == "waiting":
            waiting.set()

    def fail(**kwargs):
        raise OSError("test source I/O failed")

    monkeypatch.setattr(service, "_observe", record)
    monkeypatch.setattr(mw, "sync_tick", fail)
    service.start()
    try:
        assert waiting.wait(5)
        state = service.observation()
        assert state["alive"] and state["last_error"] == "OSError"
        assert state["completed_iterations"] == 0 and state["last_progress_at"] is None
        waiting.clear()
        now[0] = 105
        monkeypatch.setattr(mw, "sync_tick", lambda **kwargs: None)
        mw._coordinator.wake.set()
        assert waiting.wait(5)
        state = service.observation()
        assert state["last_error"] is None and state["last_progress_at"] == 105
        assert state["completed_iterations"] == 1
    finally:
        service.stop()
