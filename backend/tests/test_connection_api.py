import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from Crypto.Cipher import AES
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from backend.app.api import account_scope, connection, main
from backend.app.api.account_scope import AccountRoute
from backend.app.api.envelope import AppError, ok
from backend.app.api.routers import conversations, messages, settings, status
from backend.app.shared.backend import account_context, gui_session, outbox_service
from backend.app.shared.backend import im_db_middleware as middleware
from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm.account_keys import save_key
from backend.app.shared.crm import sync as crm_sync
from backend.app.shared.crm.identities import message_external_id, self_sender_id
from backend.app.shared.crm.sdk import LLMApiConfig, LLMApiConfigManager
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.mitm.pool import SelfInfo
from backend.app.shared.utils import app_config
from backend.app.task_queue import DEFAULT_QUEUE_NAME, TaskQueue, TaskSnapshot, TaskStatus
from backend.tests.test_maafw_runner import sdk, window


@pytest.fixture
def client(sdk, monkeypatch, tmp_path):
    directory = tmp_path / "source"
    for seller in ("seller-a", "seller-b", "10001", "10002"):
        database = directory / "IMServiceDir" / "MessageSDK" / f"{seller}@icbu" / "database"
        database.mkdir(parents=True)
        (database / "im.sqlite").write_bytes(b"invalid encrypted test fixture")
    app_config.write_app_config({"self_ali_id": "seller-a", "alibaba_data_dir": str(directory.resolve())})
    monkeypatch.setattr(account_context, "read_app_config", app_config.read_app_config)
    monkeypatch.setattr(status, "_last_node_result", None)
    with TestClient(main.create_app(), raise_server_exceptions=False) as client:
        client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
        yield client


def connect(client, *, confirm=False):
    epoch = client.headers["X-Account-Epoch"]
    response = client.post("/api/settings/connection/connect", json={"epoch": epoch})
    assert response.status_code == 200, response.text
    snapshot = response.json()["data"]
    if confirm:
        response = client.post("/api/settings/connection/confirm", json={
            "epoch": epoch, "window_generation": snapshot["client"]["window_generation"],
        })
        assert response.status_code == 200, response.text
        snapshot = response.json()["data"]
    return snapshot


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setattr(conversations, "_ready", lambda: "seller-a")
    monkeypatch.setattr(conversations, "crm_get_conversation_detail", lambda *args: SimpleNamespace(contact_ali_id="buyer"))
    monkeypatch.setattr(conversations, "crm_get_user_info", lambda *args: SimpleNamespace(login_id="buyer-login"))
    monkeypatch.setattr(conversations, "CRMAdapter", lambda: object())
    monkeypatch.setattr(conversations, "_build_aggregate", lambda *args: {"sid": 1})


def test_connection_poll_is_observation_only(client, sdk, monkeypatch):
    mw = get_im_db_middleware()
    retry = Mock(side_effect=AssertionError("poll must not retry"))
    extract = Mock(side_effect=AssertionError("poll must not extract"))
    monkeypatch.setattr(mw, "retry_connection", retry)
    monkeypatch.setattr(mw, "_ensure_key", extract)
    for _ in range(2):
        response = client.get("/api/settings/connection")
        assert response.status_code == 200
        snapshot = response.json()["data"]
        assert snapshot["account"]["epoch"] == client.headers["X-Account-Epoch"]
        assert snapshot["account"]["data_dir"] == str(Path(snapshot["data_dir"]["path"]).resolve())
        assert snapshot["source"]["phase"] == "idle"
        assert snapshot["source"]["key_validation"] == "unverified"
        assert snapshot["source"]["last_success"] is None
        assert snapshot["source"]["error_code"] is None
        assert snapshot["capabilities"] == {"read_chat": False, "use_ai": False, "operate_client": False}
        assert snapshot["model"] == {"configured": False}
    retry.assert_not_called()
    extract.assert_not_called()
    assert not Path(os.environ["MAA_CRM_DB_PATH"]).exists()
    assert not sdk.controllers and not sdk.calls


def test_connection_and_revision_polls_do_not_migrate_existing_crm(client, monkeypatch):
    from backend.app.shared.crm import migrations

    CRMAdapter().sync_conversations([], SelfInfo(ali_id="seller-a"))
    migrate = Mock(side_effect=AssertionError("poll must not migrate"))
    monkeypatch.setattr(migrations, "migrate_message_ids", migrate)
    snapshot = client.get("/api/settings/connection").json()["data"]
    assert snapshot["capabilities"]["read_chat"]
    revision = client.get("/api/conversations/revision")
    assert revision.status_code == 200, revision.text
    assert revision.json()["data"]["ready"]
    migrate.assert_not_called()


def test_connection_reports_broken_crm_as_unreadable(client):
    database = Path(os.environ["MAA_CRM_DB_PATH"])
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"not a database")
    response = client.get("/api/settings/connection")
    assert response.status_code == 200, response.text
    snapshot = response.json()["data"]
    assert not snapshot["capabilities"]["read_chat"]
    assert not snapshot["capabilities"]["use_ai"]
    assert not snapshot["model"]["configured"]
    assert database.read_bytes() == b"not a database"


def test_connection_confirmation_and_reconnect(client, sdk):
    snapshot = connect(client)
    assert snapshot["client"]["connected"] and not snapshot["client"]["confirmed"]
    assert not sdk.calls
    epoch = snapshot["account"]["epoch"]
    response = client.post("/api/settings/connection/confirm", json={"epoch": epoch, "window_generation": "old"})
    assert response.status_code == 409
    snapshot = connect(client, confirm=True)
    assert snapshot["capabilities"]["operate_client"]
    assert not connect(client)["client"]["confirmed"]
    assert not sdk.calls


@pytest.mark.parametrize("operation", ["connect", "retry", "confirm"])
def test_connection_rejects_stale_body_epoch(client, sdk, monkeypatch, operation):
    retry = Mock()
    monkeypatch.setattr(get_im_db_middleware(), "retry_connection", retry)
    response = client.post(f"/api/settings/connection/{operation}", json={"epoch": "stale", "window_generation": "stale"})
    assert response.status_code == 409
    retry.assert_not_called()
    assert not sdk.controllers and not sdk.calls


def test_retry_retains_failure_and_never_initializes_gui(client, sdk, monkeypatch):
    extract = Mock(side_effect=OSError("test client unavailable"))
    monkeypatch.setattr(middleware, "retrieve_db_key", extract)
    response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
    assert response.status_code == 200, response.text
    source = response.json()["data"]["source"]
    assert source["phase"] == "error"
    assert source["error_code"] == "key_unavailable"
    assert source["key_validation"] == "unavailable" and not source["auto_enabled"]
    assert source["revision"] == source["applied_source_revision"] == 0
    assert source["last_checked"] > 0 and source["last_success"] is None
    assert source["last_error"]
    assert client.get("/api/settings/connection").json()["data"]["source"] == source
    extract.assert_called_once()
    assert not sdk.controllers and not sdk.calls


@pytest.mark.parametrize("archive_seller, readable", [("seller-a", True), ("seller-b", False), (None, False)])
def test_only_selected_seller_archive_is_readable_offline(client, archive_seller, readable):
    if archive_seller:
        CRMAdapter().sync_conversations([], SelfInfo(ali_id=archive_seller))
    snapshot = client.get("/api/settings/connection").json()["data"]
    assert snapshot["capabilities"]["read_chat"] is readable
    assert snapshot["source"]["stale"]
    if readable:
        assert conversations._ready() == "seller-a"
    else:
        with pytest.raises(AppError):
            conversations._ready()


@pytest.mark.parametrize("url,key,model,context,configured", [
    ("", "", "", 12000, False),
    ("https://example.invalid/v1", "secret", "model", 12000, True),
    ("not-a-url", "secret", "model", 12000, False),
    ("https://example.invalid/v1", " ", "model", 12000, False),
    ("https://example.invalid/v1", "secret", " ", 12000, False),
    ("https://example.invalid/v1", "secret", "model", 0, False),
])
def test_model_flag_checks_configuration_without_invocation(client, url, key, model, context, configured):
    LLMApiConfigManager().upsert_config(LLMApiConfig(level=0, base_url=url, api_key=key, model_name=model, context=context))
    snapshot = client.get("/api/settings/connection").json()["data"]
    assert snapshot["model"] == {"configured": configured}
    if key.strip():
        assert key not in str(snapshot)


@pytest.mark.parametrize("key_validation", ["unverified", "invalid", "unavailable"])
@pytest.mark.parametrize("archive", [False, True])
def test_revision_observes_unverified_or_archive_only_source_without_key_lookup(client, monkeypatch, key_validation, archive):
    if archive:
        CRMAdapter().sync_conversations([], SelfInfo(ali_id="seller-a"))
    mw = get_im_db_middleware()
    monkeypatch.setattr(mw, "_key_validation", key_validation)
    mw._publish_source_status()
    refresh = Mock(side_effect=AssertionError("source requires explicit retry"))
    extract = Mock(side_effect=AssertionError("poll must not look up keys"))
    sync = Mock(side_effect=AssertionError("archive read must not submit a sync"))
    monkeypatch.setattr(mw, "get_connection", refresh)
    monkeypatch.setattr(mw, "_ensure_key", extract)
    monkeypatch.setattr(mw, "sync_to_crm", sync)
    for _ in range(2):
        response = client.get("/api/conversations/revision")
        assert response.status_code == 200, response.text
        state = response.json()["data"]
        assert state["ready"] is archive
        assert state["stale"]
        assert state["epoch"] == client.headers["X-Account-Epoch"]
        assert client.get("/api/settings/connection").status_code == 200
    refresh.assert_not_called()
    extract.assert_not_called()
    sync.assert_not_called()


@pytest.fixture
def revision_source(client, monkeypatch, tmp_path):
    # IM conversation IDs use '-' as a separator, so use realistic numeric IDs.
    response = client.put("/api/settings/ali-id", json={"ali_id": "10001"})
    assert response.status_code == 200
    client.headers["X-Account-Epoch"] = response.headers["X-Account-Epoch"]
    key = bytes(range(16))
    mw = get_im_db_middleware()
    submissions = []

    def publish(seller, text="original"):
        plain = tmp_path / f"{seller}.sqlite"
        with closing(sqlite3.connect(plain)) as database, database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS msg_table (cid TEXT, mid TEXT PRIMARY KEY, sender_id TEXT, "
                "created_at INTEGER, user_content_type INTEGER, content_label TEXT, extension TEXT, content BLOB)"
            )
            database.execute(
                "INSERT OR REPLACE INTO msg_table VALUES (?, 'same-id', 'buyer@icbu', 1756720000, 0, ?, '', ?)",
                (f"{seller}-buyer", text, text.encode()),
            )
        source = mw.resolve_encrypted_db_path(seller)
        previous_mtime = source.stat().st_mtime_ns
        source.write_bytes(AES.new(key, AES.MODE_ECB).encrypt(plain.read_bytes()))
        os.utime(source, ns=(previous_mtime + 1_000_000_000, previous_mtime + 1_000_000_000))
        save_key(seller, key, "manual")

    original = middleware.sync_im_database

    def submit(path, seller, info, **kwargs):
        future = original(path, seller, info, **kwargs)
        submissions.append(SimpleNamespace(path=path, seller=seller, info=info, future=future, **kwargs))
        return future

    monkeypatch.setattr(middleware, "sync_im_database", submit)
    # Exercise fingerprint/version dedup without sleeping through burst coalescing.
    monkeypatch.setattr(middleware, "_MIN_REFRESH_INTERVAL", 0)
    return publish, submissions


def finish_sync(mw):
    # Drain completion through the real coordinator, including its callback race.
    result = mw._coordinator.wait(mw._sync_future)
    mw.sync_tick()
    return result


def test_revision_poll_100_calls_never_reads_source_or_submits_sync(client, revision_source, monkeypatch):
    publish, submissions = revision_source
    publish("10001")
    mw = get_im_db_middleware()
    release = Event()
    blocker = crm_sync._SYNC_EXECUTOR.submit(lambda: release.wait(30))
    try:
        response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
        assert response.status_code == 200, response.text
        source = response.json()["data"]["source"]
        assert source["phase"] == "syncing" and not source["ready"]
        assert source["key_validation"] == "valid" and source["auto_enabled"]
        assert source["revision"] == source["applied_source_revision"] == 0
        assert source["source_revision"] > 0
        assert source["last_success"] is None
        assert source["last_checked"] > 0 and source["last_attempt"] > 0
        assert source["counts"] == {"inserted": 0, "updated": 0, "unchanged": 0}
        forbidden = Mock(side_effect=AssertionError("GET must only observe"))
        with monkeypatch.context() as patch:
            for name in ("get_connection", "sync_to_crm", "retry_connection", "sync_tick", "_ensure_key", "_capture_source"):
                patch.setattr(mw, name, forbidden)
            patch.setattr(middleware, "sync_im_database", forbidden)
            for _ in range(100):
                response = client.get("/api/conversations/revision")
                assert response.status_code == 200, response.text
                assert response.json()["data"] == {**source, "reason": "source_not_ready", "inbox_revision": 0, "next_due_at": None}
            assert client.get("/api/settings/connection").json()["data"]["source"] == source
            forbidden.assert_not_called()
        assert len(submissions) == 1
    finally:
        release.set()
        blocker.result(timeout=10)
    result = finish_sync(mw)
    assert result["revision"] == 1
    assert result["inserted"] == 1
    ready = client.get("/api/conversations/revision").json()["data"]
    assert ready["ready"] and not ready["stale"] and "reason" not in ready
    assert ready["revision"] == 1
    assert ready["applied_source_revision"] == ready["source_revision"] == source["source_revision"]
    assert ready["counts"] == {"inserted": 1, "updated": 0, "unchanged": 0}
    assert ready["last_success"] > 0
    assert {key: value for key, value in ready.items() if key not in {"inbox_revision", "next_due_at"}} == client.get("/api/settings/connection").json()["data"]["source"]
    assert ready["inbox_revision"] == 1 and ready["next_due_at"] is None
    state = client.get("/api/sync-state").json()["data"]
    for key in ("revision", "source_revision", "applied_source_revision", "counts", "last_success", "last_checked", "last_attempt"):
        assert ready[key] == state[key]


def test_revision_and_old_message_change_only_after_actual_crm_commit(client, revision_source, monkeypatch):
    publish, submissions = revision_source
    mw = get_im_db_middleware()
    publish("10001")
    assert client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]}).status_code == 200
    first = finish_sync(mw)
    summary = client.get("/api/conversations").json()["data"]["items"][0]
    sid = summary["sid"]
    entered, release = Event(), Event()
    original = crm_sync.write_sync_state

    def hold_commit(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(30)
        return result

    monkeypatch.setattr(crm_sync, "write_sync_state", hold_commit)
    publish("10001", "edited existing message")
    try:
        mw.sync_tick()
        assert entered.wait(10)
        pending = client.get("/api/conversations/revision").json()["data"]
        assert pending["revision"] == first["revision"] == 1
        assert pending["source_revision"] > pending["applied_source_revision"]
        assert pending["applied_source_revision"] == first["applied_source_revision"]
        assert pending["last_success"] == first["last_success"]
        assert pending["ready"] and pending["stale"] and pending["phase"] == "syncing"
        assert pending["counts"] == {"inserted": 1, "updated": 0, "unchanged": 0}
        for _ in range(3):
            observed = client.get("/api/conversations/revision").json()["data"]
            assert observed == pending
        listing = client.get("/api/conversations")
        assert listing.status_code == 200, listing.text
        assert isinstance(listing.json()["data"]["items"], list)
        assert "X-CRM-Revision" not in listing.headers
        assert listing.json()["data"]["items"][0]["latest"]["content"] == "original"
        detail = client.get(f"/api/conversations/{sid}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["messages"][0]["message"]["content"] == "original"
        connection_state = client.get("/api/settings/connection").json()["data"]
        assert connection_state["capabilities"]["read_chat"]
        assert not connection_state["source"]["ready"]
        assert len(submissions) == 2
    finally:
        release.set()
    second = finish_sync(mw)
    assert second["revision"] == 2
    updated = client.get("/api/conversations/revision").json()["data"]
    assert updated["revision"] == 2 and not updated["stale"]
    assert updated["source_revision"] == pending["source_revision"] == updated["applied_source_revision"]
    assert updated["counts"] == {"inserted": 0, "updated": 1, "unchanged": 0}
    listing = client.get("/api/conversations").json()["data"]["items"]
    assert len(listing) == 1 and listing[0]["sid"] == sid
    assert listing[0]["latest"]["content"] == "edited existing message"
    detail = client.get(f"/api/conversations/{sid}").json()["data"]
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["message"]["external_mid"] == message_external_id("10001", "msg_table", "same-id")
    assert detail["messages"][0]["message"]["content"] == "edited existing message"


def test_manual_retry_copy_failure_reports_phase_error_with_readable_archive(client, revision_source, monkeypatch):
    publish, submissions = revision_source
    mw = get_im_db_middleware()
    publish("10001")
    assert client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]}).status_code == 200
    committed = finish_sync(mw)
    publish("10001", "not committed")
    copy = Mock(side_effect=PermissionError("source temporarily locked"))
    monkeypatch.setattr(mw, "_copy_source_pair", copy)
    response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["capabilities"]["read_chat"]
    source = data["source"]
    assert source["phase"] == "error" and source["stale"] and not source["ready"]
    assert source["error_code"] == "source_copy_error" and source["last_error"]
    assert source["retry_at"] is not None
    assert source["revision"] == committed["revision"]
    assert source["last_success"] == committed["last_success"]
    copy.assert_called_once()
    assert len(submissions) == 1
    assert client.get("/api/settings/connection").json()["data"]["source"] == source
    assert client.get("/api/conversations").json()["data"]["items"][0]["latest"]["content"] == "original"


def test_revision_observes_worker_commits_for_current_account_without_duplicate_imports(client, revision_source):
    publish, submissions = revision_source
    mw = get_im_db_middleware()
    for seller in ("10001", "10002"):
        if seller == "10002":
            response = client.put("/api/settings/ali-id", json={"ali_id": seller})
            assert response.status_code == 200
            assert client.get("/api/conversations/revision").status_code == 409
            client.headers["X-Account-Epoch"] = response.headers["X-Account-Epoch"]
            assert client.get("/api/conversations/revision").status_code == 200
        before = len(submissions)
        publish(seller)
        response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
        assert response.status_code == 200, response.text
        assert len(submissions) == before + 1
        assert submissions[-1].seller == submissions[-1].info.ali_id == seller
        original_revision = finish_sync(mw)["revision"]
        assert client.get("/api/conversations/revision").json()["data"]["ready"]
        assert len(submissions) == before + 1
        old_path = submissions[-1].path

        publish(seller, f"new message for {seller}")
        observed = client.get("/api/settings/connection").json()["data"]
        assert observed["source"]["revision"] == original_revision
        assert len(submissions) == before + 1
        assert client.get("/api/conversations/revision").json()["data"]["revision"] == original_revision
        assert len(submissions) == before + 1
        mw.sync_tick()
        result = finish_sync(mw)
        response = client.get("/api/conversations/revision")
        assert response.status_code == 200, response.text
        updated = response.json()["data"]
        assert updated["revision"] == original_revision + 1 == result["revision"]
        assert updated["source_revision"] == updated["applied_source_revision"]
        assert updated["counts"] == {"inserted": 0, "updated": 1, "unchanged": 0}
        assert updated["epoch"] == client.headers["X-Account-Epoch"]
        assert len(submissions) == before + 2
        assert submissions[-1].seller == submissions[-1].info.ali_id == seller
        assert submissions[-1].path != old_path
        assert client.get("/api/conversations/revision").json()["data"]["revision"] == updated["revision"]
        assert client.get("/api/conversations/revision").json()["data"]["ready"]
        mw.sync_tick()
        assert len(submissions) == before + 2


def test_worker_retries_failed_crm_import_without_revision_poll_side_effects(client, revision_source, monkeypatch):
    publish, submissions = revision_source
    mw = get_im_db_middleware()
    clock = [1_800_000_000.0]
    monkeypatch.setattr(mw._coordinator, "_clock", lambda: clock[0])
    original = crm_sync.write_sync_state
    monkeypatch.setattr(crm_sync, "write_sync_state", Mock(side_effect=RuntimeError("test sync failure")))
    publish("10001")
    response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
    assert response.status_code == 200
    revision = response.json()["data"]["source"]["revision"]
    with pytest.raises(RuntimeError, match="test sync failure"):
        finish_sync(mw)
    observed = client.get("/api/settings/connection").json()["data"]["source"]
    assert observed["phase"] == "error" and observed["key_validation"] == "valid"
    assert len(submissions) == 1
    response = client.get("/api/conversations/revision")
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == revision
    assert response.json()["data"]["pending"]
    assert response.json()["data"]["retry_at"] == clock[0] + 3
    assert len(submissions) == 1
    monkeypatch.setattr(crm_sync, "write_sync_state", original)
    clock[0] += 3
    mw.sync_tick()
    assert len(submissions) == 2
    assert submissions[1].path == submissions[0].path
    assert submissions[1].seller == "10001"
    submissions[1].future.result(timeout=10)
    # Wait for the retry target, which is owned by the coordinator.
    mw._coordinator.wait(mw._coordinator._last_target.waiters[-1])
    assert client.get("/api/conversations/revision").json()["data"]["ready"]
    assert client.get("/api/conversations/revision").json()["data"]["revision"] == revision + 1
    assert len(submissions) == 2


def test_revision_keeps_archive_readable_after_worker_detects_invalid_key(client, revision_source):
    publish, submissions = revision_source
    mw = get_im_db_middleware()
    publish("10001")
    assert client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]}).status_code == 200
    committed = finish_sync(mw)
    mw.resolve_encrypted_db_path("10001").write_bytes(b"invalid encrypted source")
    mw.sync_tick()
    for _ in range(2):
        response = client.get("/api/conversations/revision")
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "key_unavailable"
        assert response.json()["data"]["revision"] == committed["revision"]
        assert response.json()["data"]["ready"] and response.json()["data"]["stale"]
        assert response.json()["data"]["phase"] == "error"
    assert client.get("/api/settings/connection").json()["data"]["capabilities"]["read_chat"]
    assert client.get("/api/conversations").json()["data"]["items"][0]["latest"]["content"] == "original"
    assert len(submissions) == 1


@pytest.mark.parametrize("path,body", [
    ("/api/conversations/1/messages", {"content": "hello", "action": "send", "idempotency_key": "key"}),
    ("/api/conversations/1/messages", {"content": "hello", "action": "test", "idempotency_key": "key"}),
    ("/api/conversations/1/goto-contact", {"login_id": "buyer-login"}),
])
def test_gui_writes_require_manual_confirmation(client, sdk, chat, path, body):
    connect(client)
    response = client.post(path, json=body)
    if path.endswith("/messages"):
        assert response.status_code == 200
        task = response.json()["data"]["outbox"]
        assert task["status"] == "failed" and not task["may_have_sent"]
        assert "人工确认" in task["reason"]
    else:
        assert response.status_code == 409
    assert TaskQueue._instances.get(DEFAULT_QUEUE_NAME) is None
    assert not sdk.calls


def test_send_rejects_missing_login_and_goto_rejects_unrelated_target(client, sdk, chat, monkeypatch):
    connect(client, confirm=True)
    response = client.post("/api/conversations/1/goto-contact", json={"login_id": "another-buyer"})
    assert response.status_code == 409
    monkeypatch.setattr(conversations, "crm_get_user_info", lambda *args: SimpleNamespace(login_id=""))
    response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": "send", "idempotency_key": "key"})
    assert response.status_code == 503
    assert TaskQueue._instances.get(DEFAULT_QUEUE_NAME) is None
    assert not sdk.calls


@pytest.mark.parametrize("change", ["account", "reconnect", "window"])
def test_queued_send_fails_when_session_expires(client, sdk, chat, monkeypatch, change):
    connect(client, confirm=True)
    context = account_context.get_account_context()
    queue = TaskQueue()
    entered, release = Event(), Event()

    def block():
        entered.set()
        assert release.wait(5)
        return True, "released"

    queue.enqueue(block, description="gate")
    assert entered.wait(2)
    try:
        response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": "send", "idempotency_key": "key"})
        assert response.status_code == 200, response.text
        task_id = response.json()["data"]["outbox"]["id"]
        if change == "account":
            response = client.put("/api/settings/ali-id", json={"ali_id": "seller-b"})
            assert response.status_code == 200
        elif change == "reconnect":
            connect(client, confirm=True)
        else:
            sdk.windows = [window(2)]
    finally:
        release.set()
        queue.shutdown()
    failed = outbox_service.get_outbox_service().get(context, task_id)
    assert failed["status"] == "failed"
    assert not sdk.calls
    monkeypatch.setattr(status, "get_task_queue", lambda: queue)
    tasks = client.get("/api/status/tasks").json()["data"]
    assert all(not task["description"].startswith("Outbox ") for task in tasks)
    reason = "Account context changed" if change == "account" else "GUI session expired"
    assert reason in failed["reason"]


@pytest.mark.parametrize("operation", ["account", "directory", "key", "connect", "retry", "confirm"])
def test_settings_conflict_immediately_during_guarded_gui(client, sdk, operation):
    snapshot = connect(client, confirm=True)
    epoch = snapshot["account"]["epoch"]
    token = gui_session.capture_gui_session(epoch)
    entered, release = Event(), Event()

    def running():
        entered.set()
        assert release.wait(5)
        return True, "finished"

    with ThreadPoolExecutor(max_workers=2) as executor:
        task = executor.submit(gui_session.run_guarded, token, running)
        assert entered.wait(2)
        try:
            if operation == "account":
                request = executor.submit(client.put, "/api/settings/ali-id", json={"ali_id": "seller-b"})
            elif operation == "directory":
                request = executor.submit(client.put, "/api/settings/alibaba-data-dir", json={"path": snapshot["data_dir"]["path"]})
            elif operation == "key":
                request = executor.submit(client.delete, "/api/settings/ali-keys/seller-a")
            else:
                request = executor.submit(client.post, f"/api/settings/connection/{operation}", json={"epoch": epoch, "window_generation": snapshot["client"]["window_generation"]})
            assert request.result(timeout=2).status_code == 409
        finally:
            release.set()
        assert task.result(timeout=2)[0]
    assert account_context.get_account_context().epoch == epoch
    assert not sdk.calls


def test_account_reads_allow_legacy_header_but_writes_require_epoch(client, monkeypatch):
    submitted = []
    pending = TaskSnapshot(
        task_id="t1", description="Translation texts", status=TaskStatus.PENDING,
        message="等待执行", result=None, created_at=1.0, started_at=None, completed_at=None,
    )

    def submit(texts, *, force, expected_epoch, conversation_id=None):
        submitted.append((texts, force, expected_epoch))
        return pending

    monkeypatch.setattr(messages, "submit_translation_job", submit)
    client.headers.pop("X-Account-Epoch")
    response = client.get("/api/self-info")
    assert response.status_code == 200
    epoch = response.headers["X-Account-Epoch"]
    for headers in ({}, {"X-Account-Epoch": "stale"}):
        response = client.post("/api/messages/translations", json={"texts": ["hello"]}, headers=headers)
        assert response.status_code == 409
        assert response.headers["X-Account-Epoch"] == epoch
    assert submitted == []
    response = client.post("/api/messages/translations", json={"texts": ["hello"], "force": True}, headers={"X-Account-Epoch": epoch})
    assert response.status_code == 200
    assert response.headers["X-Account-Epoch"] == epoch
    assert submitted == [(["hello"], True, epoch)]
    assert response.json()["data"]["status"] == "pending"


def test_long_account_write_holds_lock_through_endpoint(client, monkeypatch):
    entered, release = Event(), Event()

    def translate(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return 1

    monkeypatch.setattr(messages, "request_translations", translate)
    with ThreadPoolExecutor(max_workers=2) as executor:
        request = executor.submit(client.post, "/api/messages/translate", json={"text": "hello"})
        assert entered.wait(2)
        try:
            switching = executor.submit(client.put, "/api/settings/ali-id", json={"ali_id": "seller-b"})
            assert switching.result(timeout=2).status_code == 409
        finally:
            release.set()
        response = request.result(timeout=2)
    assert response.status_code == 200
    assert response.headers["X-Account-Epoch"] == client.headers["X-Account-Epoch"]


def test_app_error_is_preserved_by_existing_translation_handler(client, monkeypatch):
    monkeypatch.setattr(messages, "request_translations", Mock(side_effect=AppError("stale", status_code=409)))
    assert client.post("/api/messages/translate", json={"text": "hello"}).status_code == 409


def test_unhandled_account_error_still_marks_epoch(client, monkeypatch):
    monkeypatch.setattr(messages, "get_translation", Mock(side_effect=RuntimeError("test failure")))
    response = client.get("/api/messages/translations/hello")
    assert response.status_code == 500
    assert response.headers["X-Account-Epoch"] == client.headers["X-Account-Epoch"]
    assert response.headers["X-Request-ID"]


def test_self_info_and_chat_archive_follow_selected_seller(client):
    adapter = CRMAdapter()
    for seller in ("seller-a", "seller-b"):
        row = MessageRow(
            table_name="messages", cid=f"{seller}-buyer", mid="same-id",
            sender_id=self_sender_id("buyer"), created_at=1_756_720_000,
            user_content_type=0, content_label=seller, content=seller.encode(),
        )
        conv = ContactConv(contact_ali_id="buyer", messages=[row], last_created_at=row.created_at, last_content_label=seller)
        adapter.sync_conversations([conv], SelfInfo(ali_id=seller), data_dir=account_context.get_account_context().data_dir)
    assert client.get("/api/self-info").json()["data"]["ali_id"] == "seller-a"
    summaries = client.get("/api/conversations").json()["data"]["items"]
    assert len(summaries) == 1 and summaries[0]["latest"]["content"] == "seller-a"
    sid = summaries[0]["sid"]
    aggregate = client.get(f"/api/conversations/{sid}").json()["data"]
    assert aggregate["messages"][0]["message"]["external_mid"] == message_external_id("seller-a", "messages", "same-id")
    assert client.get("/api/conversations/revision").json()["data"]["stale"]
    assert client.put("/api/settings/ali-id", json={"ali_id": "seller-b"}).status_code == 200
    assert client.get("/api/self-info").status_code == 409
    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
    assert client.get("/api/self-info").json()["data"]["ali_id"] == "seller-b"
    assert client.get(f"/api/conversations/{sid}").status_code == 404
    summaries = client.get("/api/conversations").json()["data"]["items"]
    assert len(summaries) == 1 and summaries[0]["latest"]["content"] == "seller-b"
    state = client.get("/api/sync-state")
    assert state.json()["data"]["epoch"] == state.headers["X-Account-Epoch"]


def test_epoch_rejection_headers_are_visible_to_browser(client):
    origin = main.FRONTEND_DEV_ORIGINS[0]
    response = client.get("/api/self-info", headers={"Origin": origin, "X-Account-Epoch": "stale"})
    assert response.status_code == 409
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert "X-Account-Epoch" in response.headers["Access-Control-Expose-Headers"]


def test_epoch_is_rechecked_under_lock_after_request_dependencies(client):
    entered, release = Event(), Event()
    invoked = []
    router = APIRouter(route_class=AccountRoute)

    def delay():
        entered.set()
        assert release.wait(5)

    @router.post("/api/messages/race", dependencies=[Depends(delay)])
    def write():
        invoked.append(True)
        return ok()

    client.app.include_router(router)
    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(client.post, "/api/messages/race")
        assert entered.wait(2)
        try:
            changed = client.put("/api/settings/ali-id", json={"ali_id": "seller-b"})
            assert changed.status_code == 200
        finally:
            release.set()
        response = request.result(timeout=2)
    assert response.status_code == 409
    assert response.headers["X-Account-Epoch"] == account_context.get_account_context().epoch
    assert not invoked


def test_response_is_rejected_if_epoch_changes_after_endpoint(client, monkeypatch):
    calls = 0
    original = main.get_account_context

    def context():
        nonlocal calls
        calls += 1
        if calls == 2:
            get_im_db_middleware().set_self_ali_id("seller-b")
        return original()

    monkeypatch.setattr(main, "get_account_context", context)
    response = client.get("/api/self-info")
    assert response.status_code == 409
    assert response.json()["data"] is None
    assert response.headers["X-Account-Epoch"] != client.headers["X-Account-Epoch"]


def test_diagnostic_is_read_only_without_confirmation(client, sdk):
    connect(client)
    response = client.post("/api/status/node-test", json={})
    assert response.status_code == 200, response.text
    task_id = response.json()["data"]["task_snapshot"]["task_id"]
    queue = TaskQueue()
    queue.shutdown()
    assert queue.get(task_id).status == TaskStatus.SUCCEEDED
    assert sdk.calls == [("Diagnostics_ChatInput", {})]
    assert not gui_session.get_client_status()["confirmed"]


def test_queued_diagnostic_cannot_run_for_next_account(client, sdk):
    connect(client)
    queue = TaskQueue()
    entered, release = Event(), Event()

    def block():
        entered.set()
        assert release.wait(5)
        return True, "released"

    queue.enqueue(block, description="gate")
    assert entered.wait(2)
    try:
        response = client.post("/api/status/node-test", json={})
        assert response.status_code == 200
        task_id = response.json()["data"]["task_snapshot"]["task_id"]
        assert client.put("/api/settings/ali-id", json={"ali_id": "seller-b"}).status_code == 200
    finally:
        release.set()
        queue.shutdown()
    assert queue.get(task_id).status == TaskStatus.FAILED
    assert not sdk.calls


@pytest.mark.parametrize("action", ["send", "test"])
def test_confirmed_submission_only_navigates_with_fake_sdk(client, sdk, chat, monkeypatch, action):
    import numpy as np
    from backend.app.shared.backend.gui_evidence import frame_from_image

    monkeypatch.setattr(outbox_service.runner, "capture_client_frame", lambda: frame_from_image(np.zeros((4, 5, 3), dtype=np.uint8)))
    connect(client, confirm=True)
    response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": action, "idempotency_key": "key"})
    assert response.status_code == 200, response.text
    task_id = response.json()["data"]["outbox"]["id"]
    queue = TaskQueue()
    queue.shutdown()
    task = client.get(f"/api/outbox/{task_id}").json()["data"]
    assert task["status"] == "awaiting_confirmation" and not task["may_have_sent"]
    assert [entry for entry, _ in sdk.calls] == ["ContactSearch"]
    assert sdk.calls[0][1]["ContactSearch_InputText"]["action"]["param"]["input_text"] == "buyer-login"


def test_running_send_returns_task_id_and_all_observers_remain_responsive(client, sdk, chat, monkeypatch):
    import numpy as np
    from backend.app.shared.backend.gui_evidence import frame_from_image

    monkeypatch.setattr(outbox_service.runner, "capture_client_frame", lambda: frame_from_image(np.zeros((4, 5, 3), dtype=np.uint8)))
    connect(client, confirm=True)
    queue = TaskQueue()
    entered, release = Event(), Event()
    post_task = sdk.taskers[0].post_task

    def slow_task(entry, override):
        entered.set()
        assert release.wait(10)
        return post_task(entry, override)

    monkeypatch.setattr(sdk.taskers[0], "post_task", slow_task)
    # Force the worker to own both real GUI locks before middleware validates
    # the acceptance response. This cannot pass just by winning a timing race.
    observations = 0

    def response_context():
        nonlocal observations
        observations += 1
        if observations == 2:
            assert entered.wait(2)
        return account_context.get_account_context()

    monkeypatch.setattr(main, "get_account_context", response_context)
    monkeypatch.setattr(status.status_mod, "_check_port", lambda host, port: status.status_mod.NetworkStatus(False, host, port, None, "测试离线"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            request = executor.submit(client.post, "/api/conversations/1/messages", json={"content": "hello", "action": "send", "idempotency_key": "key"})
            assert entered.wait(2)
            response = request.result(timeout=2)
            assert response.status_code == 200, response.text
            task_id = response.json()["data"]["outbox"]["id"]
            service = outbox_service.get_outbox_service()
            context = account_context.get_account_context()
            assert service.get(context, task_id)["status"] == "navigating"
            acquired = account_context.account_lock.acquire(blocking=False)
            if acquired:
                account_context.account_lock.release()
            assert not acquired
            for path in ("/api/settings/connection", "/api/status", "/api/status/tasks", "/api/status/user", f"/api/outbox/{task_id}", "/api/conversations/1/outbox"):
                response = executor.submit(client.get, path).result(timeout=2)
                assert response.status_code == 200, response.text
                assert service.get(context, task_id)["status"] == "navigating"
                assert not release.is_set()
                data = response.json()["data"]
                if path == "/api/settings/connection":
                    assert data["client"]["confirmed"]
                    assert "操作正在执行" in data["client"]["detail"]
                elif path == "/api/status/tasks":
                    assert all(not task["description"].startswith("Outbox ") for task in data)
                elif path == "/api/status":
                    assert all(not task["type"].startswith("Outbox ") for task in data["tasks"])
        finally:
            release.set()
            queue.shutdown()
    assert service.get(context, task_id)["status"] == "awaiting_confirmation"


@pytest.mark.parametrize("phase", ["directory", "source", "archive", "client", "model"])
def test_connection_rejects_account_switch_between_observations(client, monkeypatch, phase):
    mw = get_im_db_middleware()
    target, name = {
        "directory": (mw, "data_dir_status"),
        "source": (mw, "sync_status"),
        "archive": (connection, "has_selected_archive"),
        "client": (connection, "get_client_status"),
        "model": (connection, "model_configured"),
    }[phase]
    original = getattr(target, name)
    selected = []
    archive = connection.has_selected_archive

    def observe_archive(seller):
        selected.append(seller)
        return archive(seller)

    monkeypatch.setattr(connection, "has_selected_archive", observe_archive)
    if phase == "archive":
        original = observe_archive

    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        mw.set_self_ali_id("seller-b")
        return result

    monkeypatch.setattr(target, name, change)
    response = client.get("/api/settings/connection")
    assert response.status_code == 409
    assert response.json()["data"] is None
    assert selected == ["seller-a"]


@pytest.mark.parametrize("phase", ["source", "archive"])
def test_revision_rejects_account_switch_between_observations(client, monkeypatch, phase):
    mw = get_im_db_middleware()
    target, name = (mw, "sync_status") if phase == "source" else (conversations, "safe_has_selected_archive")
    original = getattr(target, name)

    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        mw.set_self_ali_id("seller-b")
        return result

    monkeypatch.setattr(target, name, change)
    response = client.get("/api/conversations/revision")
    assert response.status_code == 409
    assert response.json()["data"] is None
    assert response.headers["X-Account-Epoch"] == account_context.get_account_context().epoch


SETTINGS_WRITES = [
    ("PUT", "/api/settings/alibaba-data-dir", {"path": "must-not-scan"}),
    ("PUT", "/api/settings/ali-id", {"ali_id": "seller-b"}),
    ("PUT", "/api/settings/ali-keys", {"ali_id": "seller-a", "aes_key_hex": "00" * 16}),
    ("DELETE", "/api/settings/ali-keys/seller-a", None),
]


@pytest.mark.parametrize("method,path,body", SETTINGS_WRITES)
@pytest.mark.parametrize("epoch", [None, "stale"])
def test_settings_writes_reject_missing_or_stale_epoch_before_scan(client, monkeypatch, method, path, body, epoch):
    original = account_context.get_account_context()
    client.headers.pop("X-Account-Epoch")
    if epoch is not None:
        client.headers["X-Account-Epoch"] = epoch
    access = Mock(side_effect=AssertionError("rejected request must not access settings"))
    monkeypatch.setattr(settings, "get_im_db_middleware", access)
    monkeypatch.setattr(settings, "Path", access)
    response = client.request(method, path, json=body)
    assert response.status_code == 409
    assert response.headers["X-Account-Epoch"] == original.epoch
    assert account_context.get_account_context() == original
    access.assert_not_called()


@pytest.mark.parametrize("method,path,body", SETTINGS_WRITES)
def test_settings_revalidate_epoch_inside_changing_account(client, monkeypatch, method, path, body):
    original = account_scope.changing_account

    @contextmanager
    def changed_before_lock():
        get_im_db_middleware().set_self_ali_id("seller-b")
        with original():
            yield

    monkeypatch.setattr(account_scope, "changing_account", changed_before_lock)
    access = Mock(side_effect=AssertionError("stale request must not enter settings endpoint"))
    monkeypatch.setattr(settings, "get_im_db_middleware", access)
    response = client.request(method, path, json=body)
    assert response.status_code == 409
    assert account_context.get_account_context().self_ali_id == "seller-b"
    access.assert_not_called()


def test_settings_reads_allow_missing_header_and_successful_switch_marks_new_epoch(client):
    epoch = client.headers.pop("X-Account-Epoch")
    response = client.get("/api/settings/alibaba-data-dir")
    assert response.status_code == 200
    assert response.headers["X-Account-Epoch"] == epoch
    response = client.put("/api/settings/ali-id", json={"ali_id": "seller-b"}, headers={"X-Account-Epoch": epoch})
    assert response.status_code == 200
    assert response.json()["data"]["selected"] == "seller-b"
    assert response.headers["X-Account-Epoch"] != epoch
    assert response.headers["X-Account-Epoch"] == account_context.get_account_context().epoch


def test_directory_write_persists_new_epoch_and_clears_identity(client, tmp_path):
    epoch = client.headers["X-Account-Epoch"]
    directory = tmp_path / "other-source"
    database = directory / "IMServiceDir" / "MessageSDK" / "seller-c@icbu" / "database"
    database.mkdir(parents=True)
    (database / "im.sqlite").write_bytes(b"test source")
    response = client.put("/api/settings/alibaba-data-dir", json={"path": str(directory)})
    assert response.status_code == 200, response.text
    context = account_context.get_account_context()
    assert context.epoch != epoch
    assert context.self_ali_id == ""
    assert context.data_dir == str(directory.resolve())
    assert response.headers["X-Account-Epoch"] == context.epoch
    assert response.json()["data"]["path"] == context.data_dir


def test_key_writes_with_current_epoch_persist_without_implicit_sync(client, monkeypatch):
    from backend.app.shared.crm.account_keys import get_key_hex

    mw = get_im_db_middleware()
    key = bytes(range(16))
    mw.resolve_encrypted_db_path("seller-a").write_bytes(AES.new(key, AES.MODE_ECB).encrypt(b"SQLite format 3\x00") + bytes(48))
    retry = Mock(side_effect=AssertionError("configuration write must not retry"))
    monkeypatch.setattr(mw, "retry_connection", retry)
    response = client.put("/api/settings/ali-keys", json={"ali_id": "seller-a", "aes_key_hex": key.hex()})
    assert response.status_code == 200, response.text
    assert get_key_hex("seller-a") == key
    assert response.headers["X-Account-Epoch"] != client.headers["X-Account-Epoch"]
    client.headers["X-Account-Epoch"] = response.headers["X-Account-Epoch"]
    response = client.delete("/api/settings/ali-keys/seller-a")
    assert response.status_code == 200, response.text
    assert get_key_hex("seller-a") is None
    assert response.headers["X-Account-Epoch"] != client.headers["X-Account-Epoch"]
    assert response.headers["X-Account-Epoch"] == account_context.get_account_context().epoch
    retry.assert_not_called()


def test_settings_response_rejects_another_switch_after_success(client, monkeypatch):
    calls = 0

    def context():
        nonlocal calls
        calls += 1
        if calls == 2:
            get_im_db_middleware().set_self_ali_id("seller-a")
        return account_context.get_account_context()

    monkeypatch.setattr(main, "get_account_context", context)
    response = client.put("/api/settings/ali-id", json={"ali_id": "seller-b"})
    assert response.status_code == 409
    assert response.json()["data"] is None
    assert account_context.get_account_context().self_ali_id == "seller-a"


def test_connection_actions_continue_to_use_body_epoch_without_header(client):
    epoch = client.headers.pop("X-Account-Epoch")
    response = client.post("/api/settings/connection/connect", json={"epoch": epoch})
    assert response.status_code == 200
    generation = response.json()["data"]["client"]["window_generation"]
    response = client.post("/api/settings/connection/confirm", json={"epoch": epoch, "window_generation": generation})
    assert response.status_code == 200
    assert response.json()["data"]["client"]["confirmed"]


def test_connection_details_and_guard_errors_are_chinese(client, sdk, chat):
    snapshot = connect(client)
    assert "人工确认" in snapshot["client"]["detail"]
    assert "自动核验登录身份" in snapshot["client"]["detail"]
    assert snapshot["source"]["key_validation"] == "unverified"
    response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": "send", "idempotency_key": "key"})
    assert response.status_code == 200
    assert response.json()["data"]["outbox"]["status"] == "failed"
    assert response.json()["data"]["outbox"]["reason"] == "请先人工确认所选账号与客户端窗口一致。"
    sdk.windows = [window(2)]
    response = client.post("/api/settings/connection/confirm", json={
        "epoch": client.headers["X-Account-Epoch"], "window_generation": snapshot["client"]["window_generation"],
    })
    assert response.status_code == 409
    assert response.json()["msg"] == "客户端窗口已变化，请重新连接并人工确认。"
