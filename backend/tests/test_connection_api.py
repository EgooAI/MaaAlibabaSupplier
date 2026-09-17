import os
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
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
from backend.app.shared.backend import account_context, gui_session
from backend.app.shared.backend import im_db_middleware as middleware
from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm.account_keys import save_key
from backend.app.shared.crm.identities import message_external_id, self_sender_id
from backend.app.shared.crm.sdk import LLMApiConfig, LLMApiConfigManager
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.mitm.pool import SelfInfo
from backend.app.shared.utils import app_config
from backend.app.task_queue import TaskQueue, TaskStatus
from backend.tests.test_maafw_runner import sdk, window


@pytest.fixture
def client(sdk, monkeypatch, tmp_path):
    directory = tmp_path / "source"
    for seller in ("seller-a", "seller-b"):
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
        assert snapshot["model"] == {"configured": False, "verified": False}
        assert {step["id"] for step in snapshot["steps"]} == {"data_dir", "identity", "key", "crm", "client", "model"}
    retry.assert_not_called()
    extract.assert_not_called()
    assert not Path(os.environ["MAA_CRM_DB_PATH"]).exists()
    assert not sdk.controllers and not sdk.calls


def test_connection_poll_does_not_migrate_existing_crm(client, monkeypatch):
    from backend.app.shared.crm import migrations

    CRMAdapter().sync_conversations([], SelfInfo(ali_id="seller-a"))
    migrate = Mock(side_effect=AssertionError("poll must not migrate"))
    monkeypatch.setattr(migrations, "migrate_message_ids", migrate)
    snapshot = client.get("/api/settings/connection").json()["data"]
    assert snapshot["capabilities"]["read_chat"]
    migrate.assert_not_called()


def test_connection_reports_broken_crm_as_a_step_error(client):
    database = Path(os.environ["MAA_CRM_DB_PATH"])
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"not a database")
    response = client.get("/api/settings/connection")
    assert response.status_code == 200, response.text
    snapshot = response.json()["data"]
    assert not snapshot["capabilities"]["read_chat"]
    assert not snapshot["model"]["configured"]
    steps = {step["id"]: step for step in snapshot["steps"]}
    assert steps["crm"]["state"] == steps["model"]["state"] == "error"
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
    mw = get_im_db_middleware()
    extract = Mock(return_value=False)
    monkeypatch.setattr(mw, "_ensure_key", extract)
    response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
    assert response.status_code == 200, response.text
    source = response.json()["data"]["source"]
    assert source["phase"] == "error"
    assert source["error_code"] == "key_unavailable"
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
    assert snapshot["source"]["stale"] is readable
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
    assert snapshot["model"] == {"configured": configured, "verified": False}
    assert "尚未验证" in next(step["detail"] for step in snapshot["steps"] if step["id"] == "model")
    if key.strip():
        assert key not in str(snapshot)


def test_explicit_retry_decrypts_and_reports_async_sync_outcome(client, sdk, monkeypatch, tmp_path):
    plain = tmp_path / "plain.sqlite"
    with sqlite3.connect(plain) as database:
        database.execute("CREATE TABLE test (id INTEGER)")
    key = bytes(range(16))
    mw = get_im_db_middleware()
    mw.resolve_encrypted_db_path("seller-a").write_bytes(AES.new(key, AES.MODE_ECB).encrypt(plain.read_bytes()))
    save_key("seller-a", key, "manual")
    future = Future()
    submit = Mock(return_value=future)
    monkeypatch.setattr(middleware, "sync_im_database", submit)
    response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
    assert response.status_code == 200, response.text
    source = response.json()["data"]["source"]
    assert source["phase"] == "syncing" and not source["ready"]
    assert source["key_validation"] == "valid"
    assert source["revision"] > 0 and source["source_mtime"] > 0
    submit.assert_called_once()
    future.set_exception(RuntimeError("test CRM migration failed"))
    failed = client.get("/api/settings/connection").json()["data"]["source"]
    assert failed["phase"] == "error" and failed["error_code"] == "sync_error"
    assert failed["last_error"] == "test CRM migration failed"
    recovered = Future()
    recovered.set_result(None)
    submit.return_value = recovered
    response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
    source = response.json()["data"]["source"]
    assert source["ready"] and source["phase"] == "ready"
    assert source["last_success"] > 1_000_000_000
    assert source["last_error"] is None and source["error_code"] is None
    assert not sdk.controllers and not sdk.calls


@pytest.mark.parametrize("key_validation", ["unverified", "invalid", "unavailable"])
@pytest.mark.parametrize("archive", [False, True])
def test_revision_observes_unverified_or_archive_only_source_without_key_lookup(client, monkeypatch, key_validation, archive):
    if archive:
        CRMAdapter().sync_conversations([], SelfInfo(ali_id="seller-a"))
    mw = get_im_db_middleware()
    monkeypatch.setattr(mw, "_key_validation", key_validation)
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
        assert state["stale"] is archive
        assert state["epoch"] == client.headers["X-Account-Epoch"]
        assert client.get("/api/settings/connection").status_code == 200
    refresh.assert_not_called()
    extract.assert_not_called()
    sync.assert_not_called()


@pytest.fixture
def revision_source(client, monkeypatch, tmp_path):
    key = bytes(range(16))
    mw = get_im_db_middleware()
    submissions = []

    def publish(seller, text=None):
        plain = tmp_path / f"{seller}.sqlite"
        with closing(sqlite3.connect(plain)) as database, database:
            database.execute("CREATE TABLE IF NOT EXISTS updates (content TEXT)")
            if text is not None:
                database.execute("INSERT INTO updates VALUES (?)", (text,))
        source = mw.resolve_encrypted_db_path(seller)
        previous_mtime = source.stat().st_mtime_ns
        source.write_bytes(AES.new(key, AES.MODE_ECB).encrypt(plain.read_bytes()))
        os.utime(source, ns=(previous_mtime + 1_000_000_000, previous_mtime + 1_000_000_000))
        save_key(seller, key, "manual")

    def submit(path, seller, info):
        with closing(sqlite3.connect(path)) as database:
            rows = database.execute("SELECT content FROM updates").fetchall()
        future = Future()
        submissions.append(SimpleNamespace(path=path, seller=seller, info=info, rows=rows, future=future))
        return future

    monkeypatch.setattr(middleware, "sync_im_database", submit)
    # Exercise fingerprint/version dedup without sleeping through burst coalescing.
    monkeypatch.setattr(middleware, "_MIN_REFRESH_INTERVAL", 0)
    yield publish, submissions
    for submitted in submissions:
        if not submitted.future.done():
            submitted.future.set_result(None)


def test_revision_refreshes_changed_source_for_current_account_without_duplicate_imports(client, revision_source):
    publish, submissions = revision_source
    for seller in ("seller-a", "seller-b"):
        if seller == "seller-b":
            response = client.put("/api/settings/ali-id", json={"ali_id": seller})
            assert response.status_code == 200
            assert client.get("/api/conversations/revision").status_code == 409
            client.headers["X-Account-Epoch"] = response.headers["X-Account-Epoch"]
            assert client.get("/api/conversations/revision").status_code == 200
        before = len(submissions)
        publish(seller)
        response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
        assert response.status_code == 200, response.text
        original_revision = response.json()["data"]["source"]["revision"]
        assert len(submissions) == before + 1
        assert submissions[-1].seller == submissions[-1].info.ali_id == seller
        # Neither an in-flight import nor its completed version is resubmitted.
        assert client.get("/api/conversations/revision").json()["data"]["revision"] == original_revision
        submissions[-1].future.set_result(None)
        assert client.get("/api/conversations/revision").json()["data"]["ready"]
        assert len(submissions) == before + 1
        old_path = submissions[-1].path

        publish(seller, f"new message for {seller}")
        observed = client.get("/api/settings/connection").json()["data"]
        assert observed["source"]["revision"] == original_revision
        assert len(submissions) == before + 1
        response = client.get("/api/conversations/revision")
        assert response.status_code == 200, response.text
        updated = response.json()["data"]
        assert updated["revision"] > original_revision
        assert updated["epoch"] == client.headers["X-Account-Epoch"]
        assert len(submissions) == before + 2
        assert submissions[-1].seller == submissions[-1].info.ali_id == seller
        assert submissions[-1].path != old_path
        assert submissions[-1].rows == [(f"new message for {seller}",)]
        assert client.get("/api/conversations/revision").json()["data"]["revision"] == updated["revision"]
        submissions[-1].future.set_result(None)
        assert client.get("/api/conversations/revision").json()["data"]["ready"]
        assert len(submissions) == before + 2


def test_revision_retries_failed_crm_import_when_key_remains_valid(client, revision_source):
    publish, submissions = revision_source
    publish("seller-a")
    response = client.post("/api/settings/connection/retry", json={"epoch": client.headers["X-Account-Epoch"]})
    assert response.status_code == 200
    revision = response.json()["data"]["source"]["revision"]
    submissions[0].future.set_exception(RuntimeError("test sync failure"))
    observed = client.get("/api/settings/connection").json()["data"]["source"]
    assert observed["phase"] == "error" and observed["key_validation"] == "valid"
    assert len(submissions) == 1
    response = client.get("/api/conversations/revision")
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == revision
    assert len(submissions) == 2
    assert submissions[1].path == submissions[0].path
    assert submissions[1].seller == "seller-a"
    submissions[1].future.set_result(None)
    assert client.get("/api/conversations/revision").json()["data"]["ready"]
    assert len(submissions) == 2


def test_revision_stops_refreshing_after_key_becomes_unavailable(client, monkeypatch):
    mw = get_im_db_middleware()
    monkeypatch.setattr(mw, "_key_validation", "valid")

    def lose_key():
        mw._key_validation = "unavailable"
        mw._note_failure("密钥不可用，请显式重试", "key_unavailable")
        return object()  # A previous cached connection must not authorize sync.

    refresh = Mock(side_effect=lose_key)
    sync = Mock(side_effect=AssertionError("invalid key must not submit a sync"))
    monkeypatch.setattr(mw, "get_connection", refresh)
    monkeypatch.setattr(mw, "sync_to_crm", sync)
    for _ in range(2):
        response = client.get("/api/conversations/revision")
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "key_unavailable"
    refresh.assert_called_once()
    sync.assert_not_called()


@pytest.mark.parametrize("path,body", [
    ("/api/conversations/1/messages", {"content": "hello"}),
    ("/api/conversations/1/messages", {"content": "hello", "action": "test"}),
    ("/api/conversations/1/goto-contact", {"login_id": "buyer-login"}),
])
def test_gui_writes_require_manual_confirmation(client, sdk, chat, path, body):
    connect(client)
    response = client.post(path, json=body)
    assert response.status_code == 409
    assert TaskQueue._instance is None
    assert not sdk.calls


def test_send_rejects_missing_login_and_goto_rejects_unrelated_target(client, sdk, chat, monkeypatch):
    connect(client, confirm=True)
    response = client.post("/api/conversations/1/goto-contact", json={"login_id": "another-buyer"})
    assert response.status_code == 409
    monkeypatch.setattr(conversations, "crm_get_user_info", lambda *args: SimpleNamespace(login_id=""))
    response = client.post("/api/conversations/1/messages", json={"content": "hello"})
    assert response.status_code == 503
    assert TaskQueue._instance is None
    assert not sdk.calls


@pytest.mark.parametrize("change", ["account", "reconnect", "window"])
def test_queued_send_fails_when_session_expires(client, sdk, chat, monkeypatch, change):
    connect(client, confirm=True)
    queue = TaskQueue()
    entered, release = Event(), Event()

    def block():
        entered.set()
        assert release.wait(5)
        return True, "released"

    queue.enqueue(block, description="gate")
    assert entered.wait(2)
    try:
        response = client.post("/api/conversations/1/messages", json={"content": "hello"})
        assert response.status_code == 200, response.text
        task_id = response.json()["data"]["execution"]["task_snapshot"]["task_id"]
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
    assert queue.get(task_id).status == TaskStatus.FAILED
    assert not sdk.calls
    monkeypatch.setattr(status, "get_task_queue", lambda: queue)
    tasks = client.get("/api/status/tasks").json()["data"]
    failed = next(task for task in tasks if task["task_id"] == task_id)
    reason = "账号已切换" if change == "account" else "授权已失效"
    assert reason in failed["message"]
    assert failed["result"][1] == failed["message"]


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
    translate = Mock(return_value=1)
    monkeypatch.setattr(messages, "request_translations", translate)
    client.headers.pop("X-Account-Epoch")
    response = client.get("/api/self-info")
    assert response.status_code == 200
    epoch = response.headers["X-Account-Epoch"]
    for headers in ({}, {"X-Account-Epoch": "stale"}):
        response = client.post("/api/messages/translations", json={"texts": ["hello"]}, headers=headers)
        assert response.status_code == 409
        assert response.headers["X-Account-Epoch"] == epoch
    translate.assert_not_called()
    response = client.post("/api/messages/translations", json={"texts": ["hello"]}, headers={"X-Account-Epoch": epoch})
    assert response.status_code == 200
    assert response.headers["X-Account-Epoch"] == epoch
    translate.assert_called_once()


def test_long_account_write_holds_lock_through_endpoint(client, monkeypatch):
    entered, release = Event(), Event()

    def translate(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return 1

    monkeypatch.setattr(messages, "request_translations", translate)
    with ThreadPoolExecutor(max_workers=2) as executor:
        request = executor.submit(client.post, "/api/messages/translations", json={"texts": ["hello"]})
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
    assert client.post("/api/messages/translations", json={"texts": ["hello"]}).status_code == 409


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
        adapter.sync_conversations([conv], SelfInfo(ali_id=seller))
    assert client.get("/api/self-info").json()["data"]["ali_id"] == "seller-a"
    summaries = client.get("/api/conversations").json()["data"]
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
    summaries = client.get("/api/conversations").json()["data"]
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
    queue = TaskQueue._instance
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


@pytest.mark.parametrize("action,send", [("send", True), ("test", False)])
def test_confirmed_send_runs_real_guard_with_fake_sdk(client, sdk, chat, action, send):
    connect(client, confirm=True)
    response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": action})
    assert response.status_code == 200, response.text
    task_id = response.json()["data"]["execution"]["task_snapshot"]["task_id"]
    queue = TaskQueue._instance
    queue.shutdown()
    assert queue.get(task_id).status == TaskStatus.SUCCEEDED
    assert [entry for entry, _ in sdk.calls] == ["ContactSearch", "ChatInput"]
    assert sdk.calls[0][1]["ContactSearch_InputText"]["action"]["param"]["input_text"] == "buyer-login"
    assert sdk.calls[1][1]["ChatInput_SendMessage"]["enabled"] is send


def test_running_send_returns_task_id_and_all_observers_remain_responsive(client, sdk, chat, monkeypatch):
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
            request = executor.submit(client.post, "/api/conversations/1/messages", json={"content": "hello"})
            assert entered.wait(2)
            response = request.result(timeout=2)
            assert response.status_code == 200, response.text
            task_id = response.json()["data"]["execution"]["task_snapshot"]["task_id"]
            assert queue.get(task_id).status == TaskStatus.RUNNING
            acquired = account_context.account_lock.acquire(blocking=False)
            if acquired:
                account_context.account_lock.release()
            assert not acquired
            for path in ("/api/settings/connection", "/api/status", "/api/status/tasks", "/api/status/user"):
                response = executor.submit(client.get, path).result(timeout=2)
                assert response.status_code == 200, response.text
                assert queue.get(task_id).status == TaskStatus.RUNNING
                assert not release.is_set()
                data = response.json()["data"]
                if path == "/api/settings/connection":
                    assert data["client"]["confirmed"]
                    assert "操作正在执行" in data["client"]["detail"]
                elif path == "/api/status/tasks":
                    assert next(task for task in data if task["task_id"] == task_id)["status"] == "running"
                elif path == "/api/status":
                    assert next(task for task in data["tasks"] if task["id"] == task_id)["status"] == "running"
        finally:
            release.set()
            queue.shutdown()
    assert queue.get(task_id).status == TaskStatus.SUCCEEDED


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
    assert response.headers["X-Account-Epoch"] == client.headers["X-Account-Epoch"]
    response = client.delete("/api/settings/ali-keys/seller-a")
    assert response.status_code == 200, response.text
    assert get_key_hex("seller-a") is None
    assert response.headers["X-Account-Epoch"] == client.headers["X-Account-Epoch"]
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
    steps = {step["id"]: step for step in snapshot["steps"]}
    assert "人工确认" in steps["client"]["detail"]
    assert "自动核验登录身份" in steps["client"]["detail"]
    assert "尚未验证" in steps["model"]["detail"]
    assert "密钥" in steps["key"]["detail"]
    assert "存档" in steps["crm"]["detail"]
    response = client.post("/api/conversations/1/messages", json={"content": "hello"})
    assert response.status_code == 409
    assert response.json()["msg"] == "请先人工确认所选账号与客户端窗口一致。"
    sdk.windows = [window(2)]
    response = client.post("/api/settings/connection/confirm", json={
        "epoch": client.headers["X-Account-Epoch"], "window_generation": snapshot["client"]["window_generation"],
    })
    assert response.status_code == 409
    assert response.json()["msg"] == "客户端窗口已变化，请重新连接并人工确认。"
