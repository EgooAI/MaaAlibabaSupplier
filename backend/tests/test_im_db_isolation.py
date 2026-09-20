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
from backend.app.shared.crm.account_keys import delete_key, save_key
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


def committed(source_revision, revision=1):
    return dict(revision=revision, applied_source_revision=source_revision,
                last_success=1234.0, inserted=0, updated=0, unchanged=0)


@pytest.fixture
def mw():
    middleware = module.IMDBMiddleware()
    middleware.set_data_dir(str(Path.cwd() / "client"))
    middleware.set_self_ali_id("10001")
    return middleware


@pytest.fixture
def submissions(monkeypatch):
    calls = []

    def submit(path, ali_id, info, *, source_revision=0, data_dir=""):
        future = Future()
        calls.append((path, ali_id, info, future, source_revision, data_dir))
        return future

    monkeypatch.setattr(module, "sync_im_database", submit)
    yield calls
    for _, _, _, future, revision, _ in calls:
        if not future.done():
            future.set_result(committed(revision))


def complete(mw, call, revision=1):
    call[3].set_result(committed(call[4], revision))
    mw._coordinator.tick()


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
def test_old_sync_cannot_publish_and_pending_is_invalidated(mw, submissions, old_error):
    source = encrypted_db(mw._data_dir)
    encrypted_db(mw._data_dir, "10002")
    save_key("10001", KEY, "manual")
    save_key("10002", KEY, "manual")
    mw.retry_connection()
    old = submissions[0]
    source.touch()
    mw._last_refresh_start = 0
    mw.sync_tick()
    pending = mw._sync_future
    pending_path = mw._cached_db_path
    assert mw.sync_status()["pending"]
    old_sidecars = [mw._wal_cache_for(old[0]), mw._shm_cache_for(old[0])]
    for path in old_sidecars:
        path.write_bytes(b"in use")
    mw.set_self_ali_id("10002")
    assert pending.cancelled()
    assert mw.get_connection() is None
    mw.retry_connection()
    new = submissions[1]
    assert old[1] == old[2].ali_id == "10001"
    assert new[1] == new[2].ali_id == "10002"
    assert old[5] == new[5] == get_account_context().data_dir
    assert old[0].exists() and all(path.exists() for path in old_sidecars)
    assert not pending_path.exists()
    complete(mw, new)
    status = mw.sync_status()
    if old_error:
        old[3].set_exception(RuntimeError("old account failed"))
    else:
        old[3].set_result(committed(old[4], 99))
    mw._coordinator.tick()
    assert mw.sync_status() == status
    assert status["ready"] and status["revision"] == 1
    mw._cleanup_stale_caches(new[0])
    assert not old[0].exists()
    assert all(not path.exists() for path in old_sidecars)
    assert len(submissions) == 2


def test_first_access_requires_explicit_retry_and_does_not_capture_key(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    capture = Mock(side_effect=[ValueError("client absent"), KEY])
    monkeypatch.setattr(module, "retrieve_db_key", capture)
    assert mw.get_connection() is None
    mw.sync_tick()
    capture.assert_not_called()
    assert mw.retry_connection() is None
    mw.sync_tick()
    assert mw.get_connection() is None
    assert capture.call_count == 1
    assert mw.sync_status()["error_code"] == "key_unavailable"
    assert mw.sync_status()["retry_at"] is None
    assert mw.retry_connection() is not None
    assert capture.call_count == 2
    assert mw.key_validation_status() == "valid"
    complete(mw, submissions[0])
    assert mw.sync_status()["ready"]


def test_drop_key_disables_auto_checks_and_old_publication(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    assert delete_key("10001")
    mw.drop_cached_key()
    capture = Mock(side_effect=AssertionError("Explicit retry required"))
    monkeypatch.setattr(module, "retrieve_db_key", capture)
    assert mw.get_connection() is None
    mw.sync_tick()
    complete(mw, submissions[0])
    assert mw.key_status() == (False, "none")
    assert mw.key_validation_status() == "unverified"
    assert not mw.sync_status()["ready"]
    assert mw.sync_status()["revision"] == 1
    assert mw.sync_status()["applied_source_revision"] == 0
    assert mw.sync_status()["phase"] == "idle"


def test_status_is_pure_and_nonblocking_during_source_copy(mw, submissions, monkeypatch):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    copied, release = Event(), Event()
    copy = mw._copy_source_pair

    def pause_copy(source, cached):
        result = copy(source, cached)
        copied.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(mw, "_copy_source_pair", pause_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(mw.retry_connection)
        try:
            assert copied.wait(10)
            status = pool.submit(mw.sync_status).result(timeout=2)
            assert status["phase"] == "idle"
            pool.submit(mw.key_status).result(timeout=2)
            pool.submit(mw.data_dir_status).result(timeout=2)
            with pytest.raises(AppError):
                mw.set_self_ali_id("10002")
        finally:
            release.set()
        assert refresh.result(timeout=10) is not None
    submissions[0][3].set_result(committed(submissions[0][4]))
    assert mw.sync_status()["revision"] == 0
    mw._coordinator.tick()
    assert mw.sync_status()["revision"] == 1


def test_worker_skips_busy_account_lock_and_callback_does_not_wait(mw, submissions):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with account_lock, mw._lock:
            pool.submit(submissions[0][3].set_result, committed(submissions[0][4])).result(timeout=2)
            pool.submit(mw.sync_tick).result(timeout=2)
            status = pool.submit(mw.sync_status).result(timeout=2)
    assert status["ready"]


@pytest.mark.parametrize("invalid_result", [None, {}, {"revision": 1}])
def test_missing_commit_result_is_failure(mw, submissions, invalid_result):
    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    mw.retry_connection()
    submissions[0][3].set_result(invalid_result)
    mw._coordinator.tick()
    status = mw.sync_status()
    assert status["error_code"] == "sync_error"
    assert status["revision"] == 0 and status["last_success"] is None
    assert status["retry_at"] is not None


def test_corrupt_archive_does_not_block_initialization_or_scope_change(tmp_path, monkeypatch):
    database = tmp_path / "corrupt-crm.sqlite"
    database.write_bytes(b"not a SQLite database")
    monkeypatch.setenv("MAA_CRM_DB_PATH", str(database))
    app_config.write_app_config({"self_ali_id": "10001", "alibaba_data_dir": str(tmp_path / "client")})
    middleware = module.IMDBMiddleware()
    for seller in ("10001", "10002"):
        middleware.set_self_ali_id(seller)
        status = middleware.sync_status()
        assert status["self_ali_id"] == seller
        assert status["phase"] == "error" and status["error_code"] == "sync_error"
        assert status["revision"] == 0 and status["last_success"] is None
        assert not status["ready"] and not status["auto_enabled"]
        assert status["last_error"] and status["stale"]
    assert database.read_bytes() == b"not a SQLite database"


def test_empty_database_is_ready_after_real_crm_sync(mw):
    from backend.app.shared.crm.queries import get_self_info, list_conversations

    encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    assert mw.retry_connection(wait=True) is not None
    status = mw.sync_status()
    assert status["phase"] == "ready"
    assert status["revision"] > 0 and status["last_success"] is not None
    assert status["error_code"] == ""
    assert get_self_info().ali_id == "10001"
    assert list_conversations("10001") == []
