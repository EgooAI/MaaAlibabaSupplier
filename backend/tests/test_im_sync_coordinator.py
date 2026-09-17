"""Controlled scheduling, target waits and explicit service lifecycle."""

from concurrent.futures import Future, ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.shared.backend import im_db_middleware as module
from backend.app.shared.backend import sync_coordinator as service_module
from backend.app.shared.backend.account_context import AccountContext, account_lock
from backend.app.shared.backend.sync_coordinator import SyncCoordinator
from backend.app.shared.crm.account_keys import save_key
from backend.tests.test_im_db_isolation import KEY, committed, encrypted_db, mw, submissions


@pytest.fixture
def coordinator():
    calls = []
    now = [100.0]

    def submit(target):
        future = Future()
        calls.append((target, future))
        return future

    coordinator = SyncCoordinator(submit, clock=lambda: now[0])
    coordinator.select(AccountContext("A", "source-A", "epoch-A"))
    return coordinator, calls, now


def request(coordinator, revision):
    return coordinator.request(coordinator._context, Path(f"im_{revision}.sqlite"), revision)


def finish(coordinator, calls, index, revision):
    target, future = calls[index]
    future.set_result(committed(target.source_revision, revision))
    coordinator.tick()


def test_latest_pending_and_waiters_cover_requested_target(coordinator):
    coordinator, calls, _ = coordinator
    first = request(coordinator, 1)
    second = request(coordinator, 2)
    third = request(coordinator, 3)
    assert len(calls) == 1
    assert coordinator.pinned_paths() == {Path("im_1.sqlite"), Path("im_3.sqlite")}
    finish(coordinator, calls, 0, 1)
    assert first.done() and not second.done() and not third.done()
    assert [target.source_revision for target, _ in calls] == [1, 3]
    finish(coordinator, calls, 1, 2)
    assert second.result()["applied_source_revision"] == third.result()["applied_source_revision"] == 3
    assert coordinator.snapshot()["revision"] == 2
    assert request(coordinator, 3) is third
    coordinator.tick()
    assert len(calls) == 2


@pytest.mark.parametrize("submission_error", [False, True])
def test_crm_failures_back_off_and_explicit_clear_retries(coordinator, submission_error):
    coordinator, calls, now = coordinator
    submit = coordinator._submit
    if submission_error:
        coordinator._submit = Mock(side_effect=RuntimeError("submit failed"))
    first = request(coordinator, 1)
    if not submission_error:
        calls[0][1].set_exception(RuntimeError("transaction failed"))
    coordinator.tick()
    assert isinstance(first.exception(), RuntimeError)
    status = coordinator.snapshot()
    assert status["retry_at"] == 103 and status["last_attempt"] == 100
    assert status["revision"] == 0 and status["last_success"] is None
    count = len(calls)
    for _ in range(3):
        request(coordinator, 1)
        coordinator.tick()
    assert len(calls) == count
    coordinator._submit = submit
    now[0] = 103
    coordinator.tick()
    assert len(calls) == count + 1
    calls[-1][1].set_exception(RuntimeError("second failure"))
    coordinator.tick()
    assert coordinator.snapshot()["retry_at"] == 109
    coordinator.clear_retry()
    coordinator.tick()
    assert len(calls) == count + 2
    finish(coordinator, calls, -1, 1)
    assert coordinator.snapshot()["retry_at"] is None


def test_failure_keeps_latest_pending_instead_of_replaying_old_source(coordinator):
    coordinator, calls, now = coordinator
    request(coordinator, 1)
    request(coordinator, 2)
    latest = request(coordinator, 3)
    calls[0][1].set_exception(RuntimeError("failed"))
    coordinator.tick()
    now[0] = 103
    coordinator.tick()
    assert [target.source_revision for target, _ in calls] == [1, 3]
    finish(coordinator, calls, 1, 1)
    assert latest.result()["applied_source_revision"] == 3


@pytest.mark.parametrize("change", ["roundtrip", "keydrop", "restore_race"])
def test_old_epoch_commit_updates_only_same_archive_view(mw, submissions, monkeypatch, change):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    old = submissions[0]
    restored = committed(20, 7)
    result = {**committed(old[4], 8), "inserted": 11, "last_success": 2345.0}

    def read_archive(seller, directory):
        if change == "restore_race" and not old[3].done():
            old[3].set_result(result)
            mw._coordinator.tick()
        return restored if seller == "10001" else None

    monkeypatch.setattr(module, "read_sync_state", read_archive)
    if change == "roundtrip":
        mw.set_self_ali_id("10002")
        mw.set_self_ali_id("10001")
    else:
        mw.drop_cached_key()
    before = mw.sync_status()
    if not old[3].done():
        old[3].set_result(result)
    mw.sync_tick()
    status = mw.sync_status()
    assert status["revision"] == 8 and status["counts"]["inserted"] == 11
    assert status["last_success"] == 2345.0
    for field in ("phase", "applied_source_revision", "auto_enabled", "key_validation", "last_attempt"):
        assert status[field] == before[field]
    assert status["applied_source_revision"] == 20
    assert not status["auto_enabled"] and not status["ready"] and status["stale"]


@pytest.mark.parametrize("scope", ["canonical_alias", "other_directory", "other_seller"])
def test_old_commit_publication_uses_canonical_archive_scope(coordinator, scope):
    coordinator, calls, _ = coordinator
    old = coordinator._context
    request(coordinator, 1)
    directory = str(Path(old.data_dir).resolve()) + os.sep + "."
    if scope == "other_directory":
        directory = str(Path("another-source").resolve())
    current = AccountContext("B" if scope == "other_seller" else old.self_ali_id, directory, "new-epoch")
    coordinator.select(current)
    finish(coordinator, calls, 0, 2)
    status = coordinator.snapshot()
    assert status["revision"] == (2 if scope == "canonical_alias" else 0)
    assert status["applied_source_revision"] == 0 and status["phase"] == "idle"


def test_delayed_archive_completions_cannot_regress_current_or_pending_revision(coordinator):
    coordinator, calls, _ = coordinator
    original = coordinator._context
    request(coordinator, 1)
    coordinator.select(AccountContext(original.self_ali_id, original.data_dir, "old-2"))
    request(coordinator, 2)
    coordinator.select(AccountContext(original.self_ali_id, original.data_dir, "current"))
    current = request(coordinator, 3)
    pending = request(coordinator, 4)
    # Commit callbacks can arrive out of transaction order.
    finish(coordinator, calls, 0, 9)
    status = coordinator.snapshot()
    assert status["revision"] == 9 and status["applied_source_revision"] == 0
    assert status["syncing"] and status["pending"] and status["phase"] == "syncing"
    finish(coordinator, calls, 2, 8)
    assert current.done() and not pending.done()
    assert coordinator.snapshot()["revision"] == 9
    assert coordinator.snapshot()["applied_source_revision"] == 3
    finish(coordinator, calls, 3, 10)
    assert pending.done()
    before = coordinator.snapshot()
    finish(coordinator, calls, 1, 7)
    assert coordinator.snapshot() == before
    assert before["revision"] == 10 and before["applied_source_revision"] == 4


def test_shutdown_cancels_pending_and_drains_even_before_callbacks(coordinator, monkeypatch):
    coordinator, calls, _ = coordinator
    request(coordinator, 1)
    pending = request(coordinator, 2)
    # Model Future.result waking before the CRM thread invokes done callbacks.
    monkeypatch.setattr(calls[0][1], "_invoke_callbacks", lambda: None)
    calls[0][1].set_result(committed(1))
    coordinator.shutdown()
    assert pending.cancelled()
    assert not coordinator.pinned_paths()
    assert len(calls) == 1


def test_wait_true_releases_locks_and_waits_for_pending_target(mw, submissions, monkeypatch):
    source = encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    source.touch()
    mw._last_refresh_start = 0
    mw.sync_tick()
    requested = mw._source_revision
    waiting = Event()
    wait = mw._coordinator.wake.wait

    def observe_wait(timeout=None):
        waiting.set()
        return wait(timeout)

    monkeypatch.setattr(mw._coordinator.wake, "wait", observe_wait)
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiter = pool.submit(mw.sync_to_crm, wait=True)
        try:
            assert waiting.wait(5)
            assert account_lock.acquire(timeout=1)
            try:
                assert mw._lock.acquire(timeout=1)
                mw._lock.release()
            finally:
                account_lock.release()
            submissions[0][3].set_result(committed(submissions[0][4]))
            mw._coordinator.tick()
            assert not waiter.done()
            assert submissions[1][4] == requested
            submissions[1][3].set_result(committed(requested, 2))
            waiter.result(timeout=5)
        finally:
            for call in submissions:
                if not call[3].done():
                    call[3].set_result(committed(call[4], 2))
    assert mw.sync_status()["ready"]
    assert mw.sync_status()["applied_source_revision"] == requested


def test_wait_propagates_transaction_failure(mw, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    future = Future()
    future.set_exception(RuntimeError("transaction failed"))
    submit = Mock(return_value=future)
    monkeypatch.setattr(module, "sync_im_database", submit)
    with pytest.raises(RuntimeError, match="transaction failed"):
        mw.retry_connection(wait=True)
    assert mw.sync_status()["error_code"] == "sync_error"
    assert mw.sync_status()["revision"] == 0
    assert submit.call_count == 1
    status = mw.sync_status()
    assert status["auto_enabled"] and status["pending"]
    retry = Future()
    retry.set_result(committed(mw._source_revision))
    submit.return_value = retry
    monkeypatch.setattr(mw._coordinator, "_clock", lambda: status["retry_at"])
    mw.sync_tick()
    mw.sync_tick()
    assert submit.call_count == 2
    assert mw.sync_status()["ready"]


def test_first_retry_copy_failure_recovers_on_service_tick(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    now = [module.time.time()]
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: now[0], perf_counter=module.time.perf_counter))
    copy = mw._copy_source_pair
    attempts = []

    def copy_with_transient_failure(source, target):
        attempts.append(target)
        if len(attempts) == 1:
            raise OSError("source temporarily locked")
        return copy(source, target)

    verify = Mock(wraps=mw._ensure_key)
    monkeypatch.setattr(mw, "_ensure_key", verify)
    monkeypatch.setattr(mw, "_copy_source_pair", copy_with_transient_failure)
    assert mw.retry_connection(wait=True) is None
    status = mw.sync_status()
    assert status["key_validation"] == "valid" and status["auto_enabled"]
    assert status["error_code"] == "source_copy_error" and status["stale"]
    assert not submissions
    monkeypatch.setattr(mw, "retry_connection", Mock(side_effect=AssertionError("no second user action")))
    service = service_module.SyncService(mw)
    service.tick()
    assert len(attempts) == 1
    now[0] = status["retry_at"]
    service.tick()
    assert len(attempts) == 2 and len(submissions) == 1
    assert verify.call_count == 1
    submissions[0][3].set_result(committed(submissions[0][4]))
    service.tick()
    assert mw.sync_status()["ready"]


@pytest.mark.parametrize("entry", ["worker", "retry"])
@pytest.mark.parametrize("failure", ["permission", "short_header"])
def test_unreadable_header_preserves_valid_key_and_auto_recovers(mw, submissions, monkeypatch, entry, failure):
    source = encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    submissions[0][3].set_result(committed(submissions[0][4]))
    mw._coordinator.tick()
    old_cache = mw._cached_db_path
    now = [module.time.time() + 10]
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: now[0], perf_counter=module.time.perf_counter))
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    original_open = Path.open
    failed = []

    def transient_open(path, mode="r", *args, **kwargs):
        if path == source and mode == "rb" and not failed:
            failed.append(True)
            if failure == "permission":
                raise PermissionError("temporary sharing violation")
            return BytesIO(b"short")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", transient_open)
    if entry == "worker":
        mw.sync_tick()
    else:
        mw.retry_connection()
    status = mw.sync_status()
    assert failed and status["error_code"] == "source_unreadable"
    assert status["auto_enabled"] and status["key_validation"] == "valid"
    assert status["stale"] and not status["ready"]
    assert mw._cached_db_path == old_cache
    mw.sync_tick()
    assert len(submissions) == 1
    now[0] = status["retry_at"]
    mw.sync_tick()
    assert len(submissions) == 2
    submissions[1][3].set_result(committed(submissions[1][4], 2))
    mw.sync_tick()
    assert mw.sync_status()["ready"]


def test_full_header_key_mismatch_disables_automatic_checks(mw, submissions):
    source = encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    source.write_bytes(b"x" * 16 + source.read_bytes()[16:])
    mw._last_refresh_start = 0
    mw.sync_tick()
    status = mw.sync_status()
    assert status["error_code"] == "key_unavailable"
    assert status["key_validation"] == "invalid" and not status["auto_enabled"]
    mw.sync_tick()
    assert len(submissions) == 1


def test_explicit_retry_clears_crm_backoff_without_rebuilding_same_source(mw, submissions):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    original_path, original_revision = mw._cached_db_path, mw._source_revision
    submissions[0][3].set_exception(RuntimeError("transaction failed"))
    mw._coordinator.tick()
    mw.sync_to_crm()
    assert len(submissions) == 1
    mw.retry_connection()
    assert len(submissions) == 2
    assert submissions[1][0] == original_path
    assert submissions[1][4] == original_revision
    submissions[1][3].set_result(committed(original_revision))
    mw._coordinator.tick()
    assert mw.sync_status()["ready"]
    mw.retry_connection()
    assert len(submissions) == 2


def test_old_callback_cannot_block_new_account_wait_under_outer_locks(mw, monkeypatch):
    encrypted_db(mw._data_dir)
    encrypted_db(mw._data_dir, "10002")
    save_key("10001", KEY, "manual")
    save_key("10002", KEY, "manual")
    release = Event()

    def run(ali_id, revision):
        if ali_id == "10001":
            assert release.wait(5)
        return committed(revision)

    with ThreadPoolExecutor(max_workers=1) as executor:
        def submit(path, ali_id, info, *, source_revision, data_dir):
            return executor.submit(run, ali_id, source_revision)

        monkeypatch.setattr(module, "sync_im_database", submit)
        try:
            mw.retry_connection()
            mw.set_self_ali_id("10002")
            with account_lock, mw._lock:
                release.set()
                mw.retry_connection(wait=True)
                assert mw.sync_status()["ready"]
                assert mw.sync_status()["self_ali_id"] == "10002"
        finally:
            release.set()


def test_background_completion_runs_latest_without_another_request(mw, submissions, monkeypatch):
    source = encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    for _ in range(2):
        source.touch()
        mw._last_refresh_start = 0
        mw.sync_tick()
    latest = mw._sync_future
    submitted = Event()
    completed = Event()
    original_submit = mw._coordinator._submit

    def submit(target):
        future = original_submit(target)
        submitted.set()
        return future

    monkeypatch.setattr(mw._coordinator, "_submit", submit)
    latest.add_done_callback(lambda _: completed.set())
    service = service_module.start_sync_service()
    assert service_module.start_sync_service() is service
    try:
        submissions[0][3].set_result(committed(submissions[0][4]))
        assert submitted.wait(5)
        assert len(submissions) == 2
        assert submissions[1][4] == mw._source_revision
        submissions[1][3].set_result(committed(submissions[1][4], 2))
        assert completed.wait(5)
        assert mw.sync_status()["ready"]
    finally:
        for call in submissions:
            if not call[3].done():
                call[3].set_result(committed(call[4]))
        service_module.stop_sync_service()
    assert not service._thread.is_alive()
    service_module.stop_sync_service()


def test_service_checks_source_without_browser_and_restart_requires_validation(mw, submissions, monkeypatch):
    source = encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    submissions[0][3].set_result(committed(submissions[0][4]))
    mw._coordinator.tick()
    source.touch()
    mw._last_refresh_start = 0
    submitted = Event()
    original_submit = mw._coordinator._submit

    def submit(target):
        future = original_submit(target)
        submitted.set()
        return future

    monkeypatch.setattr(mw._coordinator, "_submit", submit)
    service_module.start_sync_service()
    try:
        assert submitted.wait(5)
        assert len(submissions) == 2
    finally:
        for call in submissions:
            if not call[3].done():
                call[3].set_result(committed(call[4]))
        service_module.stop_sync_service()
    assert not mw._coordinator.pinned_paths()
    capture = Mock(side_effect=AssertionError("restart requires retry"))
    monkeypatch.setattr(mw, "_ensure_key", capture)
    restarted = service_module.start_sync_service()
    try:
        restarted.tick()
        assert mw.key_validation_status() == "unverified"
        assert mw.get_connection() is None
        capture.assert_not_called()
        assert len(submissions) == 2
    finally:
        service_module.stop_sync_service()


def test_restart_restores_only_scoped_archive_state(mw, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection(wait=True)
    previous = mw.sync_status()
    mw._coordinator.shutdown()
    mw._reset_runtime_state()
    monkeypatch.setattr(module.IMDBMiddleware, "_instance", None)
    restarted = module.IMDBMiddleware()
    try:
        status = restarted.sync_status()
        assert status["revision"] == previous["revision"]
        assert status["applied_source_revision"] == previous["applied_source_revision"]
        assert status["last_success"] == previous["last_success"]
        assert status["stale"] and not status["ready"]
        assert status["key_validation"] == "unverified"
        capture = Mock(side_effect=AssertionError("restart must not validate automatically"))
        monkeypatch.setattr(restarted, "_ensure_key", capture)
        restarted.sync_tick()
        assert restarted.get_connection() is None
        capture.assert_not_called()
        restarted.set_self_ali_id("10002")
        assert restarted.sync_status()["revision"] == 0
        restarted.set_self_ali_id("10001")
        assert restarted.sync_status()["revision"] == previous["revision"]
        restarted.set_data_dir(str(Path.cwd() / "other-client"))
        restarted.set_self_ali_id("10001")
        assert restarted.sync_status()["revision"] == 0
    finally:
        restarted._coordinator.shutdown()
        restarted._reset_runtime_state()
