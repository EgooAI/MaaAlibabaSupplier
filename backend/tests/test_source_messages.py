"""Source evidence uses synthetic encrypted databases in the pytest sandbox."""

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from Crypto.Cipher import AES

from backend.app.shared.backend import source_messages as module
from backend.app.shared.backend.account_context import account_lock, get_account_context
from backend.app.shared.backend.im_chat_db import open_readonly
from backend.app.shared.crm.account_keys import save_key
from backend.tests.test_im_db_isolation import KEY, mw, submissions


@pytest.fixture
def source(mw, submissions):
    path = mw._data_dir / "IMServiceDir/MessageSDK/10001@icbu/database/im.sqlite"
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE "msg_odd""table" (mid TEXT, sender_id TEXT, cid TEXT, '
                     'created_at REAL, user_content_type INTEGER, content_label TEXT, extension TEXT)')
        conn.executemany('INSERT INTO "msg_odd""table" VALUES (?, ?, ?, ?, ?, ?, ?)', [
            ("one", "10001@icbu", "10001-20002", 1700000000000, 0, "hello", "{}"),
            ("two", "20002@icbu", "20002-10001#suffix", 1700000001, 0, "reply", "{}"),
            ("three", "10001@icbu", "10001-20002#suffix", 1700000002, 0, "hello", "{}"),
            ("four", "10001@icbu", "20002-10001", 1700000003, 0, "hello", "{}"),
            ("wrong", "10001@icbu", "10001-200020", 1700000004, 0, "hello", "{}"),
            ("group", "10001@icbu", "10001-20002-30003", 1700000005, 0, "hello", "{}"),
            ("other", "10001@icbu", "10001-30003", 1700000006, 0, "hello", "{}"),
        ])
        conn.commit()
    path.write_bytes(AES.new(KEY, AES.MODE_ECB).encrypt(path.read_bytes()))
    save_key("10001", KEY, "manual")
    assert mw.retry_connection() is not None
    # Remove coordinator protection so tests exercise only reader pins.
    submissions[0][3].set_result(dict(revision=1, applied_source_revision=mw._source_revision,
                                     last_success=1.0, inserted=0, updated=0, unchanged=0))
    mw._coordinator.tick()
    return path


def test_target_only_independent_readonly_transaction(source, mw, monkeypatch):
    statements = []
    connections = []

    def observe(path):
        conn = open_readonly(path)
        assert conn is not mw._conn
        conn.set_trace_callback(statements.append)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE forbidden (id)")
        connections.append(conn)
        return conn

    monkeypatch.setattr(module, "open_readonly", observe)
    monkeypatch.setattr(mw, "get_connection", Mock(side_effect=AssertionError("implicit refresh")))
    monkeypatch.setattr(mw, "_ensure_key", Mock(side_effect=AssertionError("implicit key capture")))
    snapshot = module.read_source_messages(get_account_context(), "20002")
    assert snapshot["valid"] and snapshot["reason"] == "fresh"
    assert {message["id"] for message in snapshot["messages"]} == {
        'msg_odd"table:one', 'msg_odd"table:two', 'msg_odd"table:three', 'msg_odd"table:four',
    }
    assert snapshot["messages"][0]["created_at"] == 1700000000.0
    assert snapshot["origin"]["source_path"] == os.path.normcase(str(source))
    assert snapshot["source_revision"] == mw._source_revision
    assert "BEGIN" in statements
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert not mw._reader_pins


def test_fresh_empty_is_distinct_from_unreadable(source, mw, monkeypatch):
    context = get_account_context()
    empty = module.read_source_messages(context, "99999")
    assert empty["valid"] and empty["messages"] == []
    monkeypatch.setattr(mw, "_crc32_of", lambda path: None)
    unavailable = module.read_source_messages(context, "99999")
    assert not unavailable["valid"] and unavailable["reason"] == "source_unreadable"


@pytest.mark.parametrize("change", ["main_crc", "wal_crc", "metadata", "missing"])
def test_strict_freshness_inside_ttl(source, mw, change):
    if change == "main_crc":
        stat = source.stat()
        raw = bytearray(source.read_bytes())
        raw[-1] ^= 1
        source.write_bytes(raw)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif change == "wal_crc":
        wal = mw._wal_source_for(source)
        wal.write_bytes(b"before")
        mw._source_fingerprint, mw._source_crc32, mw._source_wal_crc32 = mw._capture_source(source)
        stat = wal.stat()
        wal.write_bytes(b"after!")
        os.utime(wal, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif change == "metadata":
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10000000))
    else:
        source.unlink()
    snapshot = module.read_source_messages(get_account_context(), "20002")
    assert not snapshot["valid"] and snapshot["messages"] == []
    assert snapshot["reason"] == ("source_unreadable" if change == "missing" else "source_stale")


@pytest.mark.parametrize("state", ["disabled", "invalid_key", "wrong_key_owner", "other_dir", "other_seller"])
def test_rejects_unverified_or_other_scope(source, mw, state):
    context = get_account_context()
    if state == "disabled":
        mw._auto_enabled = False
    elif state == "invalid_key":
        mw._key_validation = "invalid"
    elif state == "wrong_key_owner":
        mw._key_ali_id = "other"
    elif state == "other_dir":
        context = replace(context, data_dir=str(Path.cwd() / "other"))
    else:
        context = replace(context, self_ali_id="other")
    assert module.read_source_messages(context, "20002")["valid"] is False


def test_same_scope_old_epoch_can_read(source):
    context = replace(get_account_context(), epoch="before-restart")
    assert module.read_source_messages(context, "20002")["valid"]


def test_reader_pins_survive_reset_and_protect_sidecars_until_last_release(source, mw):
    context = get_account_context()
    keep = mw._cache_dir / "im_keep.sqlite"
    with mw.pin_source_reader(context) as first:
        paths = [first["path"], mw._wal_cache_for(first["path"]), mw._shm_cache_for(first["path"])]
        for sidecar in paths[1:]:
            sidecar.write_bytes(b"pinned")
        with mw.pin_source_reader(context):
            with account_lock, mw._lock:
                mw._reset_runtime_state()
            mw._cleanup_stale_caches(keep)
            assert all(path.exists() for path in paths)
            assert mw._reader_pins[first["path"]] == 2
        mw._cleanup_stale_caches(keep)
        assert all(path.exists() for path in paths)
    mw._cleanup_stale_caches(keep)
    assert all(not path.exists() for path in paths)


def test_read_releases_locks_but_rejects_reset_during_read(source, mw, monkeypatch):
    keep = mw._cache_dir / "im_keep.sqlite"
    cached = mw._cached_db_path

    def reset():
        with account_lock, mw._lock:
            mw._reset_runtime_state()
            mw._cleanup_stale_caches(keep)

    def observe(path):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(reset).result(timeout=5)
        assert path.exists()
        conn = open_readonly(path)
        assert conn.execute('SELECT count(*) FROM "msg_odd""table"').fetchone()[0] == 7
        return conn

    monkeypatch.setattr(module, "open_readonly", observe)
    result = module.read_source_messages(get_account_context(), "20002")
    assert not result["valid"] and result["reason"] == "source_not_verified"
    assert not mw._reader_pins
    mw._cleanup_stale_caches(keep)
    assert not cached.exists()


def test_change_during_read_discards_partial_results(source, mw, monkeypatch):
    capture = mw._capture_source
    calls = 0

    def changed(path):
        nonlocal calls
        calls += 1
        return capture(path) if calls == 1 else None

    monkeypatch.setattr(mw, "_capture_source", changed)
    result = module.read_source_messages(get_account_context(), "20002")
    assert not result["valid"] and result["messages"] == []
    assert not mw._reader_pins


def test_sql_failure_never_becomes_empty_baseline(source, mw, monkeypatch):
    monkeypatch.setattr(module, "list_msg_tables", lambda conn: ["msg_missing"])
    result = module.read_source_messages(get_account_context(), "99999")
    assert not result["valid"] and result["reason"] == "source_read_failed"
    assert not mw._reader_pins


def test_nullable_source_fields_keep_ids_without_manufacturing_text_evidence(source, mw):
    with closing(sqlite3.connect(mw._cached_db_path)) as conn:
        conn.execute('UPDATE "msg_odd""table" SET sender_id=NULL, user_content_type=NULL, content_label=NULL')
        conn.commit()
    result = module.read_source_messages(get_account_context(), "20002")
    assert result["valid"] and len(result["messages"]) == 4
    for message in result["messages"]:
        assert message["id"] and message["type"] == -1
        assert message["sender_id"] == message["text"] == ""


@pytest.mark.parametrize("extension,system,auto,valid", [
    ("{}", False, False, True),
    (json.dumps({"basicMessageInfo": {"autoReply": True}}), False, True, True),
    (json.dumps({"basicMessageInfo": json.dumps({"systemMessage": True})}), True, False, True),
    ('{"autoReply":true}', False, True, True),
    (None, False, False, False), ("", False, False, False),
    ("{broken", False, False, False), ("[]", False, False, False),
    ('{"basicMessageInfo":"broken"}', False, False, False),
    ('{"basicMessageInfo":[]}', False, False, False),
    ('{"basicMessageInfo":{"autoReply":"false"}}', False, False, False),
])
def test_extension_parsing_is_conservative(source, mw, extension, system, auto, valid):
    with closing(sqlite3.connect(mw._cached_db_path)) as conn:
        conn.execute('UPDATE "msg_odd""table" SET extension=?', (extension,))
        conn.commit()
    message = module.read_source_messages(get_account_context(), "20002")["messages"][0]
    assert (message["is_system"], message["is_auto_reply"], message["extension_valid"]) == (system, auto, valid)
