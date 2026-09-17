"""Stable source snapshots and refresh backoff using synthetic encrypted SQLite."""

import os
from unittest.mock import Mock

import pytest

from backend.tests.test_im_db_isolation import encrypted_db, mw, submissions, KEY
from backend.app.shared.backend import im_db_middleware as module
from backend.app.shared.crm.account_keys import save_key


def prime(mw):
    source = encrypted_db(mw._data_dir)
    save_key("10001", KEY, "manual")
    assert mw.retry_connection() is not None
    return source


def expire(mw):
    mw._cache_time = mw._last_refresh_start = mw._backoff_until = 0.0


def test_unchanged_source_skips_decrypt_and_sync(mw, submissions, monkeypatch):
    prime(mw)
    expire(mw)
    build = Mock(wraps=mw._rebuild_cache)
    monkeypatch.setattr(mw, "_rebuild_cache", build)
    mw.sync_tick()
    assert len(submissions) == 1
    build.assert_not_called()


def test_same_metadata_changed_crc_rebuilds(mw, submissions):
    source = prime(mw)
    before = mw._source_revision
    stat = source.stat()
    changed = bytearray(source.read_bytes())
    changed[-1] ^= 1
    source.write_bytes(changed)
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    expire(mw)
    mw.sync_tick()
    assert mw._source_revision == before + 1
    assert mw.sync_status()["pending"]


def test_burst_changes_coalesce(mw, submissions):
    source = prime(mw)
    source.touch()
    mw.sync_tick()
    assert len(submissions) == 1
    assert not mw.sync_status()["pending"]


@pytest.mark.parametrize("change", ["source", "copy", "metadata_preserved"])
def test_unstable_or_mismatched_copy_never_publishes(mw, submissions, monkeypatch, change):
    source = prime(mw)
    old_path, revision = mw._cached_db_path, mw._source_revision
    copy = mw._copy_source_pair

    def changed_copy(src, dst):
        wal = copy(src, dst)
        target = dst if change == "copy" else src
        stat = target.stat()
        data = bytearray(target.read_bytes())
        data[-1] ^= 1
        target.write_bytes(data)
        if change == "metadata_preserved":
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return wal

    source.touch()
    expire(mw)
    monkeypatch.setattr(mw, "_copy_source_pair", changed_copy)
    mw.sync_tick()
    assert mw._cached_db_path == old_path and old_path.exists()
    assert mw._source_revision == revision
    assert mw.sync_status()["error_code"] == "source_changed"
    assert len(list(mw._cache_dir.glob("im_*.sqlite"))) == 1


def test_failed_rebuild_keeps_old_cache_and_backs_off(mw, submissions, monkeypatch):
    source = prime(mw)
    old_path = mw._cached_db_path
    source.write_bytes(source.read_bytes()[:16] + b"broken")
    expire(mw)
    build = Mock(wraps=mw._rebuild_cache)
    monkeypatch.setattr(mw, "_rebuild_cache", build)
    mw.sync_tick()
    mw.sync_tick()
    assert build.call_count == 1
    assert mw._cached_db_path == old_path
    assert mw.sync_status()["stale"]
    assert mw.sync_status()["retry_at"] > module.time.time()


def test_source_revision_does_not_publish_committed_revision(mw, submissions):
    prime(mw)
    status = mw.sync_status()
    assert status["source_revision"] > 0
    assert status["revision"] == status["applied_source_revision"] == 0
    assert status["freshness"] == "syncing"
