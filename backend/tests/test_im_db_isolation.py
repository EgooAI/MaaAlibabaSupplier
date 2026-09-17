"""Account and recovery regressions using only conftest's sandbox databases."""

import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from Crypto.Cipher import AES

from backend.app.api.envelope import AppError
from backend.app.shared.backend import im_db_middleware as module
from backend.app.shared.backend.account_context import account_lock, get_account_context
from backend.app.shared.crm.account_keys import delete_key, get_key_hex, save_key
from backend.app.shared.utils import app_config


KEY = bytes(range(16))


def encrypted_db(root, ali_id="10001", key=KEY):
    path = root / "IMServiceDir" / "MessageSDK" / f"{ali_id}@icbu" / "database" / "im.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    path.write_bytes(AES.new(key, AES.MODE_ECB).encrypt(path.read_bytes()))
    return path


@pytest.fixture
def mw():
    middleware = module.IMDBMiddleware()
    middleware.set_data_dir(str(Path.cwd() / "client"))
    middleware.set_self_ali_id("10001")
    return middleware


@pytest.fixture
def submissions(monkeypatch):
    calls = []

    def submit(path, ali_id, info):
        future = Future()
        calls.append((path, ali_id, info, future))
        return future

    monkeypatch.setattr(module, "sync_im_database", submit)
    yield calls
    for *_, future in calls:
        if not future.done():
            future.set_result(None)


def test_same_selection_preserves_runtime_and_epoch(mw):
    context = get_account_context()
    connection = Mock()
    mw._conn = connection
    mw.set_self_ali_id(" 10001 ")
    mw.set_data_dir(context.data_dir)
    assert mw._conn is connection
    connection.close.assert_not_called()
    assert get_account_context() == context


def test_directory_change_clears_selection_before_invalidation(mw, monkeypatch):
    old_context = get_account_context()
    invalidate = module.invalidate_account_context
    seen = []

    def observe():
        seen.append((app_config.read_app_config(), mw._data_dir, mw._conn))
        return invalidate()

    monkeypatch.setattr(module, "invalidate_account_context", observe)
    target = Path.cwd() / "other-client"
    mw.set_data_dir(str(target))
    assert seen[0][0]["self_ali_id"] == ""
    assert seen[0][0]["alibaba_data_dir"] == str(target)
    assert seen[0][1:] == (target, None)
    assert get_account_context().epoch != old_context.epoch


@pytest.mark.parametrize("setter,value", [("set_self_ali_id", "10002"), ("set_data_dir", "new-client")])
def test_selection_conflicts_with_running_account_task(mw, setter, value):
    context = get_account_context()
    entered, release = Event(), Event()

    def task():
        with account_lock:
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(task)
        try:
            assert entered.wait(10)
            with pytest.raises(AppError) as error:
                getattr(mw, setter)(value)
            assert error.value.status_code == 409
        finally:
            release.set()
        worker.result(timeout=10)
    assert get_account_context() == context


def test_failed_persistence_preserves_selection_and_cache(mw, monkeypatch):
    context = get_account_context()
    connection = Mock()
    mw._conn = connection
    monkeypatch.setattr(module, "write_app_config", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        mw.set_data_dir(str(Path.cwd() / "other-client"))
    assert get_account_context() == context
    assert mw._conn is connection
    connection.close.assert_not_called()


@pytest.mark.parametrize("old_error", [False, True])
def test_old_sync_does_not_block_or_publish_to_new_account(mw, submissions, old_error):
    encrypted_db(mw._data_dir)
    encrypted_db(mw._data_dir, "10002")
    save_key("10001", KEY, "manual")
    save_key("10002", KEY, "manual")
    assert mw.get_connection() is not None
    old_path, old_id, old_info, old_future = submissions[0]
    assert old_id == old_info.ali_id == "10001"
    old_sidecars = [mw._wal_cache_for(old_path), mw._shm_cache_for(old_path)]
    for path in old_sidecars:
        path.write_bytes(b"in use")
    mw.set_self_ali_id("10002")
    assert mw.get_connection() is not None
    new_path, new_id, new_info, new_future = submissions[1]
    assert new_id == new_info.ali_id == "10002"
    assert new_path != old_path
    assert old_path.exists() and all(path.exists() for path in old_sidecars)
    new_future.set_result(None)
    status = mw.sync_status()
    assert status["ready"] and status["last_success"] is not None
    if old_error:
        old_future.set_exception(RuntimeError("old account failed"))
    else:
        old_future.set_result(None)
    assert mw.sync_status() == status
    with account_lock, mw._lock:
        mw._cleanup_stale_caches(new_path)
    assert not old_path.exists()
    assert all(not path.exists() for path in old_sidecars)


def test_wait_existing_future_releases_locks_and_publishes_completion(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.get_connection()
    future = submissions[0][3]
    waiting = Event()
    original_result = future.result

    def result(timeout=None):
        waiting.set()
        return original_result(timeout)

    monkeypatch.setattr(future, "result", result)
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiter = pool.submit(mw.sync_to_crm, wait=True)
        try:
            assert waiting.wait(10)
            assert not waiter.done()
            assert account_lock.acquire(timeout=1)
            try:
                assert mw._lock.acquire(timeout=1)
                mw._lock.release()
            finally:
                account_lock.release()
        finally:
            future.set_result(None)
        waiter.result(timeout=10)
    assert len(submissions) == 1
    assert mw.sync_status()["ready"]


def test_refresh_keeps_account_guard_until_sync_submission(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    copied, release = Event(), Event()
    copy_pair = mw._copy_source_pair

    def pause_copy(source, cached):
        result = copy_pair(source, cached)
        copied.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(mw, "_copy_source_pair", pause_copy)
    with ThreadPoolExecutor(max_workers=1) as pool:
        refresh = pool.submit(mw.get_connection)
        try:
            assert copied.wait(10)
            with pytest.raises(AppError) as error:
                mw.set_self_ali_id("10002")
            assert error.value.status_code == 409
        finally:
            release.set()
        assert refresh.result(timeout=10) is not None
    assert submissions[0][1:3] == ("10001", module.SelfInfo(ali_id="10001"))


def test_initial_key_failure_backs_off_and_explicit_retry_recovers(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    capture = Mock(side_effect=[ValueError("client absent"), KEY])
    monkeypatch.setattr(module, "retrieve_db_key", capture)
    assert mw.get_connection() is None
    assert mw.get_connection() is None
    assert capture.call_count == 1
    assert mw.sync_status()["error_code"] == "key_unavailable"
    assert mw.key_validation_status() == "unavailable"
    assert mw.retry_connection() is not None
    assert capture.call_count == 2
    assert mw.key_validation_status() == "valid"
    assert mw.sync_status()["last_success"] is None
    submissions[0][3].set_result(None)
    assert mw.sync_status()["ready"]


def test_drop_key_forces_revalidation_even_with_fresh_cache(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    assert mw.get_connection() is not None
    old_future = submissions[0][3]
    assert delete_key("10001")
    mw.drop_cached_key()
    assert mw.key_status() == (False, "none")
    assert mw.key_validation_status() == "unverified"
    capture = Mock(side_effect=ValueError("client absent"))
    monkeypatch.setattr(module, "retrieve_db_key", capture)
    assert mw.get_connection() is None
    capture.assert_called_once()
    old_future.set_result(None)
    assert mw.sync_status()["last_success"] is None
    assert not mw.sync_status()["ready"]


def test_error_codes_and_last_success_track_crm_completion(mw, submissions):
    assert mw.get_connection() is None
    assert mw.sync_status()["error_code"] == "source_missing"
    path = encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    original = path.read_bytes()
    path.write_bytes(original[:16] + b"broken")
    assert mw.retry_connection() is None
    assert mw.sync_status()["error_code"] == "decrypt_error"
    path.write_bytes(original)
    assert mw.retry_connection() is not None
    assert mw.sync_status()["last_success"] is None
    submissions[-1][3].set_exception(RuntimeError("CRM unavailable"))
    assert mw.sync_status()["error_code"] == "sync_error"
    assert mw.sync_status()["last_error"] == "CRM unavailable"
    assert mw.sync_status()["last_success"] is None
    mw.sync_to_crm()
    submissions[-1][3].set_result(None)
    success = mw.sync_status()["last_success"]
    assert success is not None
    assert mw.sync_status()["ready"]
    mw.retry_connection()
    submissions[-1][3].set_exception(RuntimeError("later failure"))
    assert mw.sync_status()["last_success"] == success
    assert not mw.sync_status()["ready"]


def test_sync_submission_failure_and_already_completed_future(mw, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    submit = Mock(side_effect=RuntimeError("executor unavailable"))
    monkeypatch.setattr(module, "sync_im_database", submit)
    assert mw.get_connection() is not None
    assert mw.sync_status()["error_code"] == "sync_error"
    assert mw.sync_status()["last_success"] is None
    future = Future()
    future.set_result(None)
    submit.side_effect = None
    submit.return_value = future
    mw.sync_to_crm(wait=True)
    assert mw.sync_status()["ready"]
    assert mw.sync_status()["last_success"] is not None


def test_wait_propagates_existing_sync_failure(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.get_connection()
    future = submissions[0][3]
    waiting = Event()
    result = future.result

    def wait_result(timeout=None):
        waiting.set()
        return result(timeout)

    monkeypatch.setattr(future, "result", wait_result)
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiter = pool.submit(mw.sync_to_crm, wait=True)
        try:
            assert waiting.wait(10)
        finally:
            future.set_exception(RuntimeError("CRM failed"))
        with pytest.raises(RuntimeError, match="CRM failed"):
            waiter.result(timeout=10)
    assert mw.sync_status()["error_code"] == "sync_error"
    assert mw.sync_status()["last_success"] is None


def test_status_reads_return_while_gui_holds_account_lock(mw, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    refresh = Mock(side_effect=AssertionError("Status must not refresh"))
    capture = Mock(side_effect=AssertionError("Status must not capture keys"))
    submit = Mock(side_effect=AssertionError("Status must not submit CRM work"))
    monkeypatch.setattr(mw, "_refresh", refresh)
    monkeypatch.setattr(module, "retrieve_db_key", capture)
    monkeypatch.setattr(module, "sync_im_database", submit)

    def observe():
        return mw.data_dir_status(), mw.key_status(), mw.sync_status(), mw.key_validation_status()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with account_lock:
            directory, key, source, validation = pool.submit(observe).result(timeout=2)
    assert directory["state"] == "ok"
    assert key == (True, "manual")
    assert source["phase"] == "idle"
    assert validation == "unverified"
    refresh.assert_not_called()
    capture.assert_not_called()
    submit.assert_not_called()


def test_old_callback_cannot_block_new_sync_wait_under_outer_locks(mw, monkeypatch):
    encrypted_db(mw._data_dir)
    encrypted_db(mw._data_dir, "10002")
    save_key("10001", KEY, "manual")
    save_key("10002", KEY, "manual")
    release = Event()
    completed = []

    def run(ali_id):
        if ali_id == "10001":
            assert release.wait(10)
        completed.append(ali_id)

    with ThreadPoolExecutor(max_workers=1) as executor:
        def submit(path, ali_id, info):
            future = executor.submit(run, ali_id)
            result = future.result
            # Bound waits so a regression fails without hanging pytest shutdown.
            monkeypatch.setattr(future, "result", lambda timeout=None: result(timeout=timeout or 2))
            return future

        monkeypatch.setattr(module, "sync_im_database", submit)
        try:
            mw.get_connection()
            old_future = mw._sync_future
            mw.set_self_ali_id("10002")
            with account_lock, mw._lock:
                mw.get_connection()
                assert mw._sync_future is not old_future
                release.set()
                mw.sync_to_crm(wait=True)
                assert mw.sync_status()["ready"]
                assert mw.sync_status()["self_ali_id"] == "10002"
        finally:
            release.set()
    assert completed == ["10001", "10002"]


def test_completed_old_cache_stays_protected_until_caller_drains(mw, submissions):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.get_connection()
    old_path, _, _, old_future = submissions[0]
    mw.set_self_ali_id("10002")
    keep = mw._cache_dir / "im_keep.sqlite"
    keep.write_bytes(b"keep")
    with ThreadPoolExecutor(max_workers=1) as executor:
        with account_lock, mw._lock:
            executor.submit(old_future.set_result, None).result(timeout=2)
            mw._cleanup_stale_caches(keep)
            assert old_path.exists()
            status = mw.sync_status()
            assert status["last_success"] is None
            assert status["phase"] == "idle"
            mw._cleanup_stale_caches(keep)
            assert not old_path.exists()


@pytest.mark.parametrize("wait", [False, True])
def test_ingest_submission_failure_never_returns_ready_for_existing_self(mw, monkeypatch, wait):
    from backend.app.shared.crm import ingest
    from backend.app.shared.crm.sync import CRMAdapter

    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    adapter = CRMAdapter()
    adapter.ensure_platform()
    adapter.upsert_self_info(module.SelfInfo(ali_id="10001"))
    monkeypatch.setattr(module, "sync_im_database", Mock(side_effect=RuntimeError("executor unavailable")))
    state = ingest.refresh_chat_data(wait=wait)
    assert not state.ready
    assert state.reason == "sync_error"
    assert mw.sync_status()["error_code"] == "sync_error"
    assert mw.sync_status()["last_success"] is None


def test_empty_database_is_ready_after_real_crm_sync(mw):
    from backend.app.shared.crm.queries import get_self_info, list_conversations

    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    assert mw.retry_connection(wait=True) is not None
    status = mw.sync_status()
    assert status["phase"] == "ready"
    assert status["last_success"] is not None
    assert status["error_code"] == ""
    assert get_self_info().ali_id == "10001"
    assert list_conversations("10001") == []
