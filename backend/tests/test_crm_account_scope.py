import sqlite3
from contextlib import closing
from threading import Event

import pytest
from Crypto.Cipher import AES

from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow, build_conversations, open_readonly
from backend.app.shared.crm import sync
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.mitm.pool import SelfInfo
from backend.tests.crm_helpers import conversations_for
from backend.tests.test_im_db_isolation import KEY


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
        assert conversations_for(adapter, seller)[0].messages[0].content_label == text


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
    assert sync.CRMAdapter().get_self_info().ali_id == "B"
    assert sync.CRMAdapter().get_self_info("A").ali_id == "A"
    assert adapter.get_self_info("missing") is None
    assert adapter.get_self_info("seller-A") is None
    assert adapter.get_self_info("") is None
    selected["id"] = ""
    assert sync.CRMAdapter().get_self_info() is None


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
    assert len(conversations_for(adapter, "A")[0].messages) == 2
    assert conversations_for(adapter, "B") == []
    with pytest.raises(ValueError, match="matching selected seller"):
        sync.sync_im_database(source, "A", SelfInfo(ali_id="B"))


def test_middleware_sync_commits_source_and_reads_conversations(tmp_path, monkeypatch):
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
    assert not middleware.sync_status()["ready"]
    assert middleware.retry_connection() is not None
    future = middleware.sync_to_crm()
    assert future is not None
    result = middleware.wait_for_sync(future)
    assert result["revision"] == 1
    assert result["applied_source_revision"] == middleware.sync_status()["source_revision"]
    assert result["inserted"] == 2
    assert len(conversations_for(sync.CRMAdapter(), "A")[0].messages) == 2
    assert middleware.sync_status()["ready"]
