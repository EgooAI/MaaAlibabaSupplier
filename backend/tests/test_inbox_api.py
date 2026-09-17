import os
import sqlite3
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from backend.app.api.routers import conversations
from backend.app.shared.backend import account_context
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.crm import inbox_queries, sync
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.crm.inbox_store import InboxStore
from backend.app.shared.crm.sdk import AccountMapping
from backend.app.shared.mitm.pool import SelfInfo, UserInfo
from backend.tests.test_connection_api import client, sdk
from sqlmodel import Session


T = 1756720000


def row(mid, buyer="buyer", *, text="visible", at=T, sender=None, system=False):
    return MessageRow(
        table_name="messages", cid=f"seller-a-{buyer}", mid=str(mid),
        sender_id=f"{sender or buyer}@icbu", created_at=at, user_content_type=0,
        content_label=text, content=b"raw-secret-not-searchable", is_system=system,
    )


@pytest.fixture
def archive(client):
    adapter = sync.CRMAdapter()
    context = account_context.get_account_context()

    def apply(*groups, seller="seller-a", data_dir=None):
        adapter.sync_conversations(
            [ContactConv(buyer, list(messages), None, None) for buyer, messages in groups],
            SelfInfo(ali_id=seller), data_dir=data_dir or context.data_dir,
        )

    yield adapter, apply
    adapter.engine.dispose()


def data(client, path="/api/conversations", **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_read_snapshot_100_101_and_epoch_roundtrip(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [row(0)]))
    apply(("buyer", [row(mid, at=T + mid) for mid in range(1, 101)]))
    listing = data(client)
    sid = listing["items"][0]["sid"]
    assert listing["items"][0]["unread_count"] == 100
    original = conversations.crm_get_conversation_detail

    def arrive_before_detail(*args):
        apply(("buyer", [row(101, at=T + 101)]))
        return original(*args)

    monkeypatch.setattr(conversations, "crm_get_conversation_detail", arrive_before_detail)
    detail = data(client, f"/api/conversations/{sid}")
    assert len(detail["messages"]) == 102
    assert detail["unread_count"] == 101
    assert "status" not in detail and "priority" not in detail
    body = {"read_snapshot": detail["read_snapshot"]}
    response = client.post(f"/api/conversations/{sid}/read", json=body)
    assert response.status_code == 200, response.text
    marked = response.json()["data"]
    assert marked["state"]["unread_count"] == 1
    assert marked["state"]["read_seq"] == 101 and marked["state"]["snapshot_seq"] == 102
    assert marked["inbox_revision"] > listing["inbox_revision"]
    assert client.post(f"/api/conversations/{sid}/read", json=body).json()["data"] == marked
    for seller in ("seller-b", "seller-a"):
        assert client.put("/api/settings/ali-id", json={"ali_id": seller}).status_code == 200
        client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
    assert client.post(f"/api/conversations/{sid}/read", json=body).status_code == 409
    assert data(client)["items"][0]["unread_count"] == 1


def test_overview_counts_conversations_and_matches_list_filters(client, archive):
    _, apply = archive
    apply(("history", [row(1, "history")]), ("waiting", [row(2, "waiting", sender="seller-a")]),
          ("none", [row(3, "none", system=True)]), ("unknown", [row(4, "unknown", sender="other")]))
    apply(("new", [row(5, "new"), row(6, "new", at=T + 1)]))
    overview = data(client, "/api/inbox/overview")
    assert overview["counts"] == {
        "total": 5, "unread": 1, "needs_reply": 1, "overdue": 1,
        "history_pending": 1, "waiting_customer": 1,
    }
    assert overview["timeout_seconds"] == 86400 and isinstance(overview["updated_at"], (int, float))
    for params in ({"unread": True}, {"unread": False}, {"overdue": True}, {"overdue": False},
                   *({"reply_state": state} for state in ("needs_reply", "waiting_customer", "history_pending", "none", "unknown"))):
        listing = data(client, **params)
        filtered = data(client, "/api/inbox/overview", **params)
        assert filtered["counts"]["total"] == listing["total"] == len(listing["items"])
        assert filtered["inbox_revision"] == listing["inbox_revision"]
    history = data(client, reply_state="history_pending")["items"][0]
    assert history["unread_count"] == 0 and history["due_at"] is None and not history["is_overdue"]


def test_search_entire_visible_history_literal_unicode_wildcards_and_exact_seller(client, archive):
    _, apply = archive
    apply(("buyer", [row(1, text="\u4e2d\u6587 100%_ exact"), row(2, text="latest", at=T + 1)]),
          ("other", [row(3, "other", text="100xx exact", at=T + 2)]))
    for seller in ("SELLER-A", "seller-a_", "seller-a%"):
        apply(("intruder", [row(seller, "intruder", text="outside seller")]), seller=seller)
    assert data(client)["total"] == 2
    for q in ("\u4e2d\u6587", "%_", "100%_", "EXACT"):
        result = data(client, q=q, search_scope="messages")
        assert result["total"] == (2 if q == "EXACT" else 1)
    assert data(client, q="raw-secret-not-searchable")["total"] == 0
    assert data(client, q="outside seller")["total"] == 0
    assert data(client, q="\u4e2d\u6587", search_scope="customer")["total"] == 0
    assert data(client, q="buyer", search_scope="customer")["total"] == 1


def test_profile_resolution_country_priority_casefold_and_exact_tags(client, archive):
    adapter, apply = archive
    apply(("buyer", [row(1)]), ("fallback", [row(2, "fallback")]))
    adapter.upsert_user_info(UserInfo(
        ali_id="buyer", first_name="Stra\u00dfe", company_name="Export Company", email="hello@example.test",
        phone_number="555123", country_code="DE", high_quality_level_tag="Gold", growth_level="L3",
    ))
    adapter.upsert_user_info(UserInfo(ali_id="collision", country_code="US", first_name="Wrong"))
    collision = adapter._account_by_mapping("ali_id", "collision")
    with Session(adapter.engine) as session:
        session.add(AccountMapping(type="login_id", key="buyer", aid=collision.aid))
        session.commit()
    with closing(sqlite3.connect(InboxStore().database_path)) as conn:
        for contact, region in (("buyer", "US"), ("fallback", "Stra\u00dfe")):
            account = adapter._account_by_mapping("ali_id", contact)
            conn.execute("UPDATE customer SET region=? WHERE cid=?", (region, account.cid))
        conn.commit()
    result = data(client, country="de", tag="Gold")
    assert result["total"] == 1 and result["items"][0]["customer_view"]["country"] == "DE"
    assert data(client, country="US")["total"] == 0
    assert data(client, country="STRASSE")["total"] == 1
    assert data(client, tag="Gol")["total"] == 0
    assert data(client, tag="L3")["total"] == 1
    for q in ("STRASSE", "Export Company", "hello@example.test", "555123", "buyer"):
        assert data(client, q=q, search_scope="customer")["total"] == 1
    # Invalid UserInfo must follow the resolver's fallback, not raw JSON fields.
    with closing(sqlite3.connect(InboxStore().database_path)) as conn:
        account = adapter._account_by_mapping("ali_id", "buyer")
        conn.execute("UPDATE account SET extra=? WHERE aid=?",
                     ('{"country_code":"TR","preferred_industries":42}', account.aid))
        conn.commit()
    assert data(client, country="TR")["total"] == 0
    assert data(client, country="US")["total"] == 1


def test_stable_pagination_order_and_only_page_dtos(client, archive, monkeypatch):
    _, apply = archive
    apply(*((f"buyer{i}", [row(i, f"buyer{i}")]) for i in range(12)))
    original = conversations._build_summary
    build = Mock(wraps=original)
    monkeypatch.setattr(conversations, "_build_summary", build)
    first = data(client, limit=3)
    assert first["total"] == 12 and first["offset"] == 0 and first["limit"] == 3
    assert build.call_count == 3
    sids = [item["sid"] for item in first["items"]]
    assert sids == [12, 11, 10]
    token = first["pagination_revision"]
    assert data(client, limit=1)["pagination_revision"] == token
    assert client.get("/api/conversations", params={"offset": 3}).status_code == 409
    second = data(client, offset=3, limit=3, pagination_revision=token)
    assert [item["sid"] for item in second["items"]] == [9, 8, 7]
    assert data(client, offset=100, pagination_revision=token)["items"] == []
    assert client.get("/api/conversations", params={"offset": 3, "pagination_revision": token, "q": "buyer"}).status_code == 409


@pytest.mark.parametrize("change", ["new_message", "old_body", "settings", "read", "profile"])
def test_pagination_invalidates_for_every_relevant_change(client, archive, change):
    adapter, apply = archive
    apply(("buyer", [row(1)]))
    apply(("buyer", [row(2, at=T + 1)]))
    first = data(client)
    sid = first["items"][0]["sid"]
    if change == "new_message":
        apply(("buyer", [row(3, at=T + 2)]))
    elif change == "old_body":
        apply(("buyer", [row(1, text="edited old body")]))
    elif change == "settings":
        assert client.put("/api/inbox/settings", json={"timeout_seconds": 3600}).status_code == 200
    elif change == "read":
        detail = data(client, f"/api/conversations/{sid}")
        assert client.post(f"/api/conversations/{sid}/read", json={"read_snapshot": detail["read_snapshot"]}).status_code == 200
    else:
        adapter.upsert_user_info(UserInfo(ali_id="buyer", company_name="changed profile"))
    fresh = data(client)
    if change in {"old_body", "profile"}:
        assert first["items"][0]["latest"] == fresh["items"][0]["latest"]
    assert fresh["pagination_revision"] != first["pagination_revision"]
    assert client.get("/api/conversations", params={"offset": 1, "pagination_revision": first["pagination_revision"]}).status_code == 409


def test_deadline_changes_token_only_on_threshold_and_revision_observes_next_due(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [row(1)]))
    apply(("buyer", [row(2, at=T + 1)]))
    clock = SimpleNamespace(time=lambda: T + 2)
    monkeypatch.setattr(inbox_queries, "time", clock)
    before = data(client)
    due = before["items"][0]["due_at"]
    assert data(client, "/api/conversations/revision")["next_due_at"] == due
    clock.time = lambda: due - 1
    assert data(client)["pagination_revision"] == before["pagination_revision"]
    clock.time = lambda: due
    overdue = data(client)
    assert overdue["items"][0]["is_overdue"]
    assert overdue["pagination_revision"] != before["pagination_revision"]
    assert overdue["inbox_revision"] == before["inbox_revision"]
    assert data(client, "/api/conversations/revision")["next_due_at"] is None


def test_archive_initialization_is_only_read_side_effect_and_revision_never_initializes(client, archive, monkeypatch, sdk):
    _, apply = archive
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply(("buyer", [row(1)]))
    store = InboxStore()
    context = account_context.get_account_context()
    scope = {"seller": context.self_ali_id, "data_dir": context.data_dir}
    forbidden = Mock(side_effect=AssertionError("GET must not touch source or migrate SDK"))
    mw = get_im_db_middleware()
    for name in ("get_connection", "sync_to_crm", "retry_connection", "sync_tick", "_ensure_key", "_capture_source"):
        monkeypatch.setattr(mw, name, forbidden)
    monkeypatch.setattr(conversations, "CRMAdapter", forbidden)
    with closing(sqlite3.connect(store.database_path)) as conn:
        before = list(conn.iterdump())
    assert data(client, "/api/conversations/revision")["inbox_revision"] == 0
    assert not store.metadata(**scope)["baseline_complete"]
    with closing(sqlite3.connect(store.database_path)) as conn:
        assert list(conn.iterdump()) == before
    initialized = data(client)
    assert initialized["items"][0]["reply_state"] == "history_pending"
    assert initialized["items"][0]["unread_count"] == 0
    with closing(sqlite3.connect(store.database_path)) as conn:
        seeded = list(conn.iterdump())
    assert set(before) <= set(seeded)
    assert data(client) == initialized
    data(client, "/api/inbox/settings")
    data(client, "/api/inbox/overview")
    with closing(sqlite3.connect(store.database_path)) as conn:
        assert list(conn.iterdump()) == seeded
    forbidden.assert_not_called()
    assert not sdk.calls


def test_empty_unready_scope_remains_503_without_baseline(client):
    for path in ("/api/conversations", "/api/inbox/overview", "/api/inbox/settings"):
        assert client.get(path).status_code == 503
    assert not InboxStore().database_path.exists()
    assert data(client, "/api/conversations/revision")["inbox_revision"] == 0


def test_new_directory_get_does_not_consume_first_history_baseline(client, archive, tmp_path):
    _, apply = archive
    apply(("buyer", [row(1, text="directory A history")]))
    mw = get_im_db_middleware()
    directory = str(tmp_path / "source-b")
    mw.set_data_dir(directory)
    mw.set_self_ali_id("seller-a")
    context = account_context.get_account_context()
    client.headers["X-Account-Epoch"] = context.epoch
    store = InboxStore()

    for path in ("/api/conversations", "/api/inbox/overview", "/api/inbox/settings"):
        response = client.get(path)
        assert response.status_code == 503, response.text
        assert not store.metadata(context.self_ali_id, directory)["baseline_complete"]
    response = client.put("/api/inbox/settings", json={"timeout_seconds": 3600})
    assert response.status_code == 503

    apply(("buyer", [row(2, text="directory B history")]), data_dir=directory)
    listing = data(client)
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["latest"]["content"] == "directory B history"
    assert item["unread_count"] == 0 and item["reply_state"] == "history_pending"
    assert item["due_at"] is None and not item["is_overdue"]
    apply(("buyer", [row(2), row(3, at=T + 1)]), data_dir=directory)
    assert data(client)["items"][0]["unread_count"] == 1


def test_identity_only_unready_archive_does_not_get_a_baseline(client, archive, monkeypatch):
    _, apply = archive
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply()
    context = account_context.get_account_context()
    for path in ("/api/conversations", "/api/inbox/overview", "/api/inbox/settings"):
        assert client.get(path).status_code == 503
    assert not InboxStore().metadata(context.self_ali_id, context.data_dir)["baseline_complete"]


@pytest.mark.parametrize("params", [
    {"q": "x" * 201}, {"search_scope": "raw"}, {"reply_state": "following"},
    {"unread": "maybe"}, {"overdue": "maybe"}, {"offset": -1}, {"limit": 0}, {"limit": 101},
])
def test_query_validation(client, params):
    assert client.get("/api/conversations", params=params).status_code == 422


def test_settings_and_read_require_current_epoch_and_valid_tokens(client, archive):
    _, apply = archive
    apply(("buyer", [row(1)]))
    assert data(client, "/api/inbox/settings")["timeout_seconds"] == 86400
    for value in (3599, 604801, True, "86400", 3600.5):
        assert client.put("/api/inbox/settings", json={"timeout_seconds": value}).status_code == 422
    for value in (3600, 604800):
        response = client.put("/api/inbox/settings", json={"timeout_seconds": value})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["timeout_seconds"] == value
    sid = data(client)["items"][0]["sid"]
    for token in ("bad", "\u4e2d\u6587.token", "abcd.token"):
        assert client.post(f"/api/conversations/{sid}/read", json={"read_snapshot": token}).status_code == 409
    detail = data(client, f"/api/conversations/{sid}")
    context = account_context.get_account_context()
    assert detail["read_snapshot"] == InboxStore().snapshot(
        sid, seller=context.self_ali_id, data_dir=context.data_dir, epoch=context.epoch,
    )
    unbound = InboxStore().snapshot(sid, seller=context.self_ali_id, data_dir=context.data_dir)
    assert client.post(f"/api/conversations/{sid}/read", json={"read_snapshot": unbound}).status_code == 409
    assert client.post(f"/api/conversations/{sid + 1}/read", json={"read_snapshot": detail["read_snapshot"]}).status_code == 409
    for epoch in (None, "stale"):
        if epoch is None:
            del client.headers["X-Account-Epoch"]
        else:
            client.headers["X-Account-Epoch"] = epoch
        assert client.put("/api/inbox/settings", json={"timeout_seconds": 3600}).status_code == 409
        assert client.post(f"/api/conversations/{sid}/read", json={"read_snapshot": detail["read_snapshot"]}).status_code == 409


def test_list_metadata_profiles_messages_and_states_share_one_read_snapshot(client, archive, monkeypatch):
    adapter, apply = archive
    apply(("buyer", [row(1)]))
    apply(("buyer", [row(2, at=T + 1)]))
    store = InboxStore()
    with closing(sqlite3.connect(store.database_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(inbox_queries, "time", SimpleNamespace(time=lambda: T + 7200))
    before = data(client)
    context = account_context.get_account_context()
    original = InboxStore._states
    committed = False

    def commit_between_projection_and_states(self, conn, key, sids, now):
        nonlocal committed
        if not committed:
            committed = True
            store.set_timeout(3600, seller=context.self_ali_id, data_dir=context.data_dir)
            adapter.upsert_user_info(UserInfo(ali_id="buyer", company_name="new profile"))
            apply(("buyer", [row(3, at=T + 3, text="new message")]))
        return original(self, conn, key, sids, now)

    monkeypatch.setattr(InboxStore, "_states", commit_between_projection_and_states)
    during = data(client)
    assert committed and during == before
    after = data(client)
    assert after["inbox_revision"] > before["inbox_revision"]
    assert after["pagination_revision"] != before["pagination_revision"]
    assert after["items"][0]["latest"]["content"] == "new message"
    assert after["items"][0]["customer_view"]["company"] == "new profile"
    assert after["items"][0]["is_overdue"] and not before["items"][0]["is_overdue"]
    assert after["items"][0]["unread_count"] == 2 and before["items"][0]["unread_count"] == 1


def test_settings_reject_busy_account_without_waiting_or_writing(client, archive):
    _, apply = archive
    apply(("buyer", [row(1)]))
    entered, release = Event(), Event()

    def hold_account():
        with account_context.account_lock:
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(hold_account)
        assert entered.wait(5)
        try:
            response = executor.submit(client.put, "/api/inbox/settings", json={"timeout_seconds": 3600}).result(timeout=2)
            assert response.status_code == 409
        finally:
            release.set()
            holder.result(timeout=5)
    assert data(client, "/api/inbox/settings")["timeout_seconds"] == 86400


def test_settings_are_scoped_to_seller_and_directory(client, archive, tmp_path):
    _, apply = archive
    apply(("buyer", [row(1)]))
    apply(("buyer", [row(2)]), seller="seller-b")
    assert client.put("/api/inbox/settings", json={"timeout_seconds": 3600}).status_code == 200
    assert client.put("/api/settings/ali-id", json={"ali_id": "seller-b"}).status_code == 200
    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
    assert data(client, "/api/inbox/settings")["timeout_seconds"] == 86400
    store = InboxStore()
    store.initialize("seller-a", str(tmp_path / "other-source"))
    assert store.metadata("seller-a", str(tmp_path / "other-source"))["timeout_seconds"] == 86400
    assert client.put("/api/settings/ali-id", json={"ali_id": "seller-a"}).status_code == 200
    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
    assert data(client, "/api/inbox/settings")["timeout_seconds"] == 3600


@pytest.mark.parametrize("spelling", ["original", "dot", "case"])
def test_shared_sid_messages_follow_directory_membership(client, archive, tmp_path, spelling):
    if spelling == "case" and os.name != "nt":
        pytest.skip("Directory case aliases require a case-insensitive filesystem")
    adapter, apply = archive
    directory_a = account_context.get_account_context().data_dir
    if spelling == "dot":
        directory_a = str(Path(directory_a) / "unused" / "..")
    elif spelling == "case":
        directory_a = directory_a.upper()
    directory_b = tmp_path / "other-source"
    database_b = directory_b / "IMServiceDir" / "MessageSDK" / "seller-a@icbu" / "database"
    database_b.mkdir(parents=True)
    (database_b / "im.sqlite").write_bytes(b"source must not be opened by inbox reads")
    apply(data_dir=directory_a)
    apply(data_dir=str(directory_b))
    apply(("buyer", [row("m1", text="A-only")]), data_dir=directory_a)
    apply(("buyer", [row("m2", text="B-only", at=T + 1, sender="seller-a")]), data_dir=str(directory_b))
    mids = {label: message_external_id("seller-a", "messages", mid) for label, mid in (("A", "m1"), ("B", "m2"))}
    store = InboxStore()
    first = data(client)
    sid = first["items"][0]["sid"]
    assert first["total"] == 1
    assert first["items"][0]["dialogue_count"] == 1
    assert first["items"][0]["latest"]["content"] == "A-only"
    assert first["items"][0]["reply_state"] == "needs_reply"
    assert first["items"][0]["unread_count"] == 1
    for scope in ("all", "messages"):
        assert data(client, q="B-only", search_scope=scope)["total"] == 0
        assert data(client, q="A-only", search_scope=scope)["total"] == 1
    assert data(client, "/api/inbox/overview", q="B-only")["counts"]["total"] == 0
    detail_a = data(client, f"/api/conversations/{sid}")
    assert [m["message"]["external_mid"] for m in detail_a["messages"]] == [mids["A"]]
    assert detail_a["dialogue_count"] == 1 and detail_a["latest"] == first["items"][0]["latest"]
    state_a = store.states("seller-a", directory_a)[sid]
    assert {key: detail_a[key] for key in inbox_queries.STATE_FIELDS} == inbox_queries.public_state(state_a)
    # Editing a B-only body must not affect A's search, latest or page token.
    with closing(sqlite3.connect(store.database_path)) as conn:
        conn.execute("UPDATE message SET content=json_set(content, '$.content_label', ?) WHERE external_mid=?",
                     ("B-edited", mids["B"]))
        conn.commit()
    assert data(client) == first
    assert data(client, q="B-edited", search_scope="messages")["total"] == 0
    response = client.post(f"/api/conversations/{sid}/read", json={"read_snapshot": detail_a["read_snapshot"]})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"]["unread_count"] == 0
    assert store.states("seller-a", str(directory_b))[sid]["read_seq"] == 0
    for directory, label, content in ((str(directory_b), "B", "B-edited"), (directory_a, "A", "A-only")):
        response = client.put("/api/settings/alibaba-data-dir", json={"path": directory})
        assert response.status_code == 200, response.text
        client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
        assert client.put("/api/settings/ali-id", json={"ali_id": "seller-a"}).status_code == 200
        client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
        summary = data(client)["items"][0]
        assert summary["sid"] == sid and summary["dialogue_count"] == 1
        assert summary["latest"]["content"] == content
        assert summary["reply_state"] == ("waiting_customer" if label == "B" else "needs_reply")
        detail = data(client, f"/api/conversations/{sid}")
        assert [m["message"]["external_mid"] for m in detail["messages"]] == [mids[label]]
        assert detail["latest"] == summary["latest"] and detail["dialogue_count"] == 1
        assert client.post(f"/api/conversations/{sid}/read", json={"read_snapshot": detail_a["read_snapshot"]}).status_code == 409
    # Filtering is only a view: both SDK rows still exist under the same SID.
    assert {m.external_mid for m in adapter.messages.list_message()} == set(mids.values())


def test_ai_and_export_use_the_visible_ledger_without_marking_read(client, archive, tmp_path, monkeypatch):
    _, apply = archive
    context = account_context.get_account_context()
    directory_b = str(tmp_path / "source-b")
    apply()
    apply(data_dir=directory_b)
    apply(("buyer", [row("a-message", text="A_VISIBLE_BODY")]))
    apply(("buyer", [row("b-message", text="B_SHARED_SID_SECRET", at=T + 1)]),
          ("b-only", [row("b-only-message", "b-only", text="B_ONLY_CONVERSATION_SECRET")]),
          data_dir=directory_b)
    store = InboxStore()
    before = {directory: (store.states(context.self_ali_id, directory), store.metadata(context.self_ali_id, directory))
              for directory in (context.data_dir, directory_b)}
    sid, = before[context.data_dir][0]
    b_only_sid, = set(before[directory_b][0]) - {sid}
    assert before[context.data_dir][0][sid]["unread_count"] == 1
    suggest = Mock(return_value=SimpleNamespace(items=[SimpleNamespace(zh="suggestion", reply="reply")]))
    analyze = Mock(return_value='{"stage":"new","confidence":0.5}')
    mark_read = Mock(side_effect=AssertionError("AI/export must not mark read"))
    snapshot = Mock(side_effect=AssertionError("AI/export must not capture read snapshots"))
    monkeypatch.setattr(conversations, "generate_reply_suggestions", suggest)
    monkeypatch.setattr(conversations, "run_chat_tool_agent", analyze)
    monkeypatch.setattr(InboxStore, "mark_read", mark_read)
    monkeypatch.setattr(InboxStore, "snapshot", snapshot)

    suggestions = data(client, f"/api/conversations/{sid}/suggestions")
    assert suggestions[0]["content"] == "reply"
    suggest.assert_called_once()
    assert [entry[2] for entry in suggest.call_args.args[0]] == ["A_VISIBLE_BODY"]
    data(client, f"/api/conversations/{sid}/analysis")
    analyze.assert_called_once()
    prompt = analyze.call_args.args[1]
    assert "A_VISIBLE_BODY" in prompt
    assert "B_SHARED_SID_SECRET" not in prompt and "B_ONLY_CONVERSATION_SECRET" not in prompt
    for endpoint in ("suggestions", "analysis"):
        assert client.get(f"/api/conversations/{b_only_sid}/{endpoint}").status_code == 404
    assert suggest.call_count == analyze.call_count == 1

    response = client.post("/api/conversations/export", json={"conversationIds": [sid, b_only_sid]})
    assert response.status_code == 200, response.text
    exported = response.json()["data"]
    assert exported["missing"] == [b_only_sid]
    with ZipFile(BytesIO(b64decode(exported["content"]))) as archive_zip:
        assert len(archive_zip.namelist()) == 1
        text = archive_zip.read(archive_zip.namelist()[0]).decode("utf-8")
    assert "A_VISIBLE_BODY" in text
    assert "B_SHARED_SID_SECRET" not in text and "B_ONLY_CONVERSATION_SECRET" not in text
    assert client.post("/api/conversations/export", json={"conversationIds": [b_only_sid]}).status_code == 404
    mark_read.assert_not_called()
    snapshot.assert_not_called()
    for directory, (states, metadata) in before.items():
        assert store.states(context.self_ali_id, directory) == states
        assert store.metadata(context.self_ali_id, directory) == metadata
