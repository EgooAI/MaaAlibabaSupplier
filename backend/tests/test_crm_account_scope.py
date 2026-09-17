import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from threading import Event

import pytest
from Crypto.Cipher import AES

from backend.app.shared.backend import account_context
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow, build_conversations, open_readonly
from backend.app.shared.crm import ingest, queries, sync
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.mitm.pool import SelfInfo
from backend.tests.test_im_db_isolation import KEY, committed


def conversation(seller, text="hello"):
    row = MessageRow(
        table_name="msg_table", cid=f"{seller}-buyer", mid="1", sender_id="buyer@icbu",
        created_at=1756720000, user_content_type=0, content_label=text, content=text.encode(),
    )
    return ContactConv("buyer", [row], row.created_at, text)


def test_messages_with_same_source_id_survive_across_sellers(tmp_path):
    adapter = sync.CRMAdapter(tmp_path / "crm.sqlite")
    adapter.sync_conversations([conversation("A", "for A")], SelfInfo(ali_id="A"))
    adapter.sync_conversations([conversation("B", "for B")], SelfInfo(ali_id="B"))
    adapter.sync_conversations([conversation("A", "A updated")], SelfInfo(ali_id="A"))
    messages = {message.external_mid: message for message in adapter.messages.list_message()}
    assert set(messages) == {message_external_id(seller, "msg_table", "1") for seller in ("A", "B")}
    assert len({message.sid for message in messages.values()}) == 2
    for seller, text in (("A", "A updated"), ("B", "for B")):
        assert adapter.list_conversations(seller)[0].messages[0].content_label == text


def test_self_query_follows_selection_and_explicit_identity(tmp_path, monkeypatch):
    from backend.app.shared.utils import app_config

    path = tmp_path / "crm.sqlite"
    monkeypatch.setenv("MAA_CRM_DB_PATH", str(path))
    adapter = sync.CRMAdapter(path)
    adapter.ensure_platform()
    for seller in ("A", "B"):
        adapter.upsert_self_info(SelfInfo(ali_id=seller, login_id=f"seller-{seller}"))
    selected = {"id": "A"}
    monkeypatch.setattr(app_config, "get_configured_self_ali_id", lambda: selected["id"])
    assert adapter.get_self_info().ali_id == "A"
    selected["id"] = "B"
    assert adapter.get_self_info().ali_id == "B"
    assert queries.get_self_info().ali_id == "B"
    assert queries.get_self_info("A").ali_id == "A"
    assert adapter.get_self_info("missing") is None
    assert adapter.get_self_info("seller-A") is None
    assert adapter.get_self_info("") is None
    selected["id"] = ""
    assert queries.get_self_info() is None


def source_database(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE msg_table (cid TEXT, mid TEXT, sender_id TEXT, created_at INTEGER, "
            "user_content_type INTEGER, content_label TEXT, extension TEXT, content BLOB)"
        )
        conn.executemany("INSERT INTO msg_table VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ("A-buyer", "1", "buyer@icbu", 1756720000, 0, "for A", "", b"for A"),
            ("B-buyer", "1", "buyer@icbu", 1756720000, 0, "for B", "", b"for B"),
            ("buyer-A#suffix", "2", "A@icbu", 1756720001, 0, "reply", "", b"reply"),
        ])
        conn.commit()


def test_source_cid_must_contain_selected_seller(tmp_path):
    path = tmp_path / "im.sqlite"
    source_database(path)
    with closing(open_readonly(path)) as conn:
        conversations = build_conversations(conn, "A")
        assert len(conversations) == 1
        assert [row.mid for row in conversations[0].messages] == ["1", "2"]
        assert build_conversations(conn, "unrelated") == []
        assert build_conversations(conn, "") == []


def test_queued_sync_captures_source_and_self(tmp_path, monkeypatch):
    from backend.app.shared.utils import app_config

    source = tmp_path / "im.sqlite"
    target = tmp_path / "crm.sqlite"
    source_database(source)
    monkeypatch.setenv("MAA_CRM_DB_PATH", str(target))
    monkeypatch.setattr(app_config, "get_configured_self_ali_id", lambda: pytest.fail("Worker read current identity"))
    release = Event()
    blocker = sync._SYNC_EXECUTOR.submit(lambda: release.wait(5))
    info = SelfInfo(ali_id="A")
    future = sync.sync_im_database(source, "A", info)
    info.ali_id = "B"
    release.set()
    blocker.result(timeout=5)
    future.result(timeout=5)
    adapter = sync.CRMAdapter(target)
    assert adapter.get_self_info("A").ali_id == "A"
    assert adapter.get_self_info("B") is None
    assert len(adapter.list_conversations("A")[0].messages) == 2
    assert adapter.list_conversations("B") == []
    with pytest.raises(ValueError, match="matching selected seller"):
        sync.sync_im_database(source, "A", SelfInfo(ali_id="B"))


@pytest.fixture
def selected_context(monkeypatch):
    config = {"self_ali_id": "A", "alibaba_data_dir": "temporary-source"}
    monkeypatch.setattr(account_context, "read_app_config", lambda: dict(config))
    monkeypatch.setattr(account_context, "_context", None)
    return config


def install_middleware(monkeypatch, submit):
    from backend.app.shared.backend import im_db_middleware

    class Middleware:
        def data_dir_status(self):
            return {"state": "ok"}

        def get_connection(self):
            return object()

        def sync_to_crm(self, wait=False):
            assert wait is False
            return submit()

        def wait_for_sync(self, future):
            return future.result(timeout=5)

    monkeypatch.setattr(im_db_middleware, "get_im_db_middleware", Middleware)


def test_ingest_returns_selected_identity_and_rejects_unselected(selected_context, monkeypatch):
    future = Future()
    future.set_result(committed(1))
    install_middleware(monkeypatch, lambda: future)
    requested = []
    monkeypatch.setattr(ingest, "get_self_info", lambda seller: requested.append(seller) or SelfInfo(ali_id=seller))
    assert ingest.refresh_chat_data().self_ali_id == "A"
    selected_context["self_ali_id"] = "B"
    assert ingest.refresh_chat_data(wait=True).self_ali_id == "B"
    assert requested == ["A", "B"]
    selected_context["self_ali_id"] = ""
    state = ingest.refresh_chat_data()
    assert not state.ready and state.reason == ingest.REASON_SELF_IDENTITY_NOT_SELECTED


@pytest.mark.parametrize("change", ["seller", "directory", "epoch", "none"])
def test_ingest_wait_releases_lock_and_rechecks_context(selected_context, monkeypatch, change):
    def complete():
        acquired = account_context.account_lock.acquire(timeout=2)
        assert acquired, "ingest waited while holding account_lock"
        try:
            if change == "seller":
                selected_context["self_ali_id"] = "B"
            elif change == "directory":
                selected_context["alibaba_data_dir"] = "other-temporary-source"
            elif change == "epoch":
                account_context.invalidate_account_context()
        finally:
            account_context.account_lock.release()
        return committed(1)

    with ThreadPoolExecutor(max_workers=1) as executor:
        install_middleware(monkeypatch, lambda: executor.submit(complete))
        monkeypatch.setattr(ingest, "get_self_info", lambda seller: SelfInfo(ali_id=seller))
        state = ingest.refresh_chat_data(wait=True)
    assert state.ready is (change == "none")
    if change != "none":
        assert state.reason == ingest.REASON_ACCOUNT_CHANGED
        assert state.self_ali_id == ""


def test_ingest_propagates_sync_failure(selected_context, monkeypatch):
    future = Future()
    future.set_exception(RuntimeError("migration blocked"))
    install_middleware(monkeypatch, lambda: future)
    with pytest.raises(RuntimeError, match="migration blocked"):
        ingest.refresh_chat_data(wait=True)


def test_ingest_waits_for_actual_middleware_future(tmp_path, monkeypatch):
    from backend.app.shared.backend import im_db_middleware
    from backend.app.shared.crm.account_keys import save_key

    client = tmp_path / "client"
    source = client / "IMServiceDir" / "MessageSDK" / "A@icbu" / "database" / "im.sqlite"
    source.parent.mkdir(parents=True)
    source_database(source)
    source.write_bytes(AES.new(KEY, AES.MODE_ECB).encrypt(source.read_bytes()))
    monkeypatch.setenv("MAA_CRM_DB_PATH", str(tmp_path / "crm.sqlite"))
    middleware = im_db_middleware.get_im_db_middleware()
    middleware.set_data_dir(str(client))
    middleware.set_self_ali_id("A")
    save_key("A", KEY, "manual")
    assert not ingest.refresh_chat_data(wait=True).ready
    assert middleware.retry_connection() is not None
    state = ingest.refresh_chat_data(wait=True)
    assert state.ready and state.self_ali_id == "A"
    future = middleware.sync_to_crm()
    assert future.done()
    result = middleware.wait_for_sync(future)
    assert result["revision"] == 1
    assert result["applied_source_revision"] == middleware.sync_status()["source_revision"]
    assert result["inserted"] == 2
    assert len(queries.list_conversations("A")[0].messages) == 2
