"""Customer portrait store, API, filters and identity-protection regressions."""

import csv
import json
import sqlite3
from contextlib import closing

import pytest
from sqlmodel import Session, select

from backend.app.shared.backend import account_context
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm import sync
from backend.app.shared.crm.customer_portrait_store import CustomerPortraitStore
from backend.app.shared.crm.inbox_store import InboxStore
from backend.app.shared.crm.sdk import AccountMapping
from backend.app.shared.mitm.pool import SelfInfo, UserInfo
from backend.tests.test_connection_api import client, sdk

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


def listing_item(client, **params):
    listing = data(client, **params)
    assert listing["total"] == 1, listing
    return listing["items"][0]


def create_tag(client, name="高潜", description=""):
    response = client.post("/api/portraits/tags", json={"name": name, "description": description})
    assert response.status_code == 200, response.text
    return response.json()["data"]["tag"]


def batch(client, sid, cid, version, changes, request_id="req-1", expect=200):
    response = client.post("/api/portraits/batch", json={
        "request_id": request_id,
        "targets": [{"sid": sid, "expected_cid": cid, "expected_version": version}],
        "changes": changes,
    })
    assert response.status_code == expect, response.text
    return response.json().get("data")


def redirect_alias_to(alias, aid):
    with closing(sqlite3.connect(InboxStore().database_path)) as conn:
        conn.execute("UPDATE accountmapping SET aid=? WHERE key=? AND type='ali_id'", (aid, alias))
        conn.commit()


def test_portrait_stage_tags_filters_and_platform_tag_separation(client, archive):
    adapter, apply = archive
    apply(("buyer", [row(1)]))
    apply(("other", [row(2, "other")]))
    adapter.upsert_user_info(UserInfo(ali_id="buyer", high_quality_level_tag="Gold"))
    item = listing_item(client, tag="Gold")
    view = item["customer_view"]
    assert item["sid"] and view["cid"] and view["portrait_editable"]
    assert view["business_stage"] == "unknown" and view["business_tags"] == []
    tag = create_tag(client, "高潜", "高潜力客户")
    cid, sid = view["cid"], item["sid"]
    result = batch(client, sid, cid, 0, {"stage": "following_up", "add_tag_ids": [tag["tag_id"]]})
    assert result["changed"] == [{"cid": cid, "sids": [sid], "version": 1}]
    view = listing_item(client, portrait_stage="following_up", business_tag=[tag["tag_id"]])["customer_view"]
    assert view["business_stage"] == "following_up" and view["business_version"] == 1
    assert view["business_tags"] == [{"tag_id": tag["tag_id"], "name": "高潜", "archived": False}]
    # Business and platform tag filters stay independent.
    assert data(client, business_tag=[tag["tag_id"]])["total"] == 1
    assert data(client, tag="高潜")["total"] == 0
    assert data(client, tag="Gold")["total"] == 1
    assert data(client, business_tag=[tag["tag_id"]], tag="Gold")["total"] == 1
    assert data(client, portrait_stage="won")["total"] == 0
    # Re-applying identical values is a no-op that keeps the version.
    result = batch(client, sid, cid, 1, {"stage": "following_up", "add_tag_ids": [tag["tag_id"]]}, request_id="req-2")
    assert result["changed"] == [] and result["unchanged"][0]["version"] == 1
    detail = data(client, f"/api/conversations/{sid}")
    assert detail["customer_view"]["business_stage"] == "following_up"
    assert detail["customer_view"]["business_tags"][0]["name"] == "高潜"


def test_portrait_stage_or_and_completeness_filters(client, archive):
    adapter, apply = archive
    apply(("buyer", [row(1)]))
    apply(("other", [row(2, "other")]))
    apply(("third", [row(3, "third")]))
    first = listing_item(client, q="buyer")
    batch(client, first["sid"], first["customer_view"]["cid"], 0, {"stage": "won"}, request_id="or-won")
    second = listing_item(client, q="other")
    batch(client, second["sid"], second["customer_view"]["cid"], 0, {"stage": "following_up"}, request_id="or-follow")
    third = listing_item(client, q="third")
    assert third["customer_view"]["business_version"] == 0
    # Repeatable stage filter matches any supplied stage (OR).
    assert data(client, portrait_stage=["won", "following_up"])["total"] == 2
    assert data(client, portrait_stage="won")["total"] == 1
    assert data(client, portrait_stage=["dormant", "lost"])["total"] == 0
    assert client.get("/api/conversations", params={"portrait_stage": ["won", "bogus"]}).status_code == 422
    # Completeness splits maintained portraits from never-maintained and cid-less sessions.
    maintained = data(client, portrait_completeness="maintained")
    assert maintained["total"] == 2 and all(item["customer_view"]["business_version"] > 0 for item in maintained["items"])
    unmaintained = data(client, portrait_completeness="unmaintained")
    assert [item["sid"] for item in unmaintained["items"]] == [third["sid"]]
    with closing(sqlite3.connect(InboxStore().database_path)) as conn:
        conn.execute("UPDATE session_meta SET participants=? WHERE sid=?", (json.dumps([]), third["sid"]))
        conn.commit()
    unmaintained = data(client, portrait_completeness="unmaintained")
    assert unmaintained["total"] == 1 and unmaintained["items"][0]["customer_view"]["cid"] is None
    assert data(client, portrait_completeness="maintained")["total"] == 2
    # The pagination fingerprint is filter-sensitive and order-insensitive.
    token = data(client, portrait_stage=["won", "following_up"], limit=1)["pagination_revision"]
    assert data(client, portrait_stage=["following_up", "won"], limit=1)["pagination_revision"] == token
    assert data(client, portrait_stage="won", limit=1)["pagination_revision"] != token
    assert data(client, portrait_completeness="maintained", limit=1)["pagination_revision"] != token


def test_portrait_batch_conflicts_idempotency_and_scope(client, archive):
    _, apply = archive
    apply(("buyer", [row(1)]))
    apply(("outsider", [row(2, "outsider")]), seller="seller-b")
    item = listing_item(client)
    sid, cid = item["sid"], item["customer_view"]["cid"]
    first = batch(client, sid, cid, 0, {"stage": "won"}, request_id="same")
    assert first["changed"][0]["version"] == 1
    # Same request id and payload replays the stored receipt without re-applying.
    operations_before = data(client, "/api/portraits/operations")["operations"]
    assert batch(client, sid, cid, 0, {"stage": "won"}, request_id="same") == first
    assert data(client, "/api/portraits/operations")["operations"] == operations_before
    batch(client, sid, cid, 0, {"stage": "lost"}, request_id="same", expect=409)
    # The receipt cannot be replayed from another workspace after an account switch.
    assert client.put("/api/settings/ali-id", json={"ali_id": "seller-b"}).status_code == 200
    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
    outsider = listing_item(client)
    batch(client, outsider["sid"], outsider["customer_view"]["cid"], 0, {"stage": "won"}, request_id="same", expect=409)
    assert listing_item(client)["customer_view"]["business_stage"] == "unknown"
    assert client.put("/api/settings/ali-id", json={"ali_id": "seller-a"}).status_code == 200
    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
    # Stale version: the whole batch is rejected and nothing changes.
    batch(client, sid, cid, 0, {"stage": "dormant"}, request_id="stale", expect=409)
    assert listing_item(client)["customer_view"]["business_stage"] == "won"
    # Wrong customer identity is rejected.
    batch(client, sid, cid + 1, 1, {"stage": "dormant"}, request_id="wrong-cid", expect=409)
    # A session owned by another workspace is not visible here.
    store = InboxStore()
    outsider_sid = next(iter(store.states("seller-b", account_context.get_account_context().data_dir)))
    batch(client, outsider_sid, cid, 0, {"stage": "won"}, request_id="foreign", expect=409)
    # Invalid stage and lost fields outside lost are request errors.
    batch(client, sid, cid, 1, {"stage": "bogus"}, request_id="bad-stage", expect=422)
    batch(client, sid, cid, 1, {"lost_reason": "price"}, request_id="bad-reason", expect=422)
    # Unresolved identity disables maintenance instead of guessing.
    with closing(sqlite3.connect(store.database_path)) as conn:
        conn.execute("UPDATE session_meta SET participants=? WHERE sid=?", (json.dumps([]), sid))
        conn.commit()
    item = listing_item(client)
    assert item["customer_view"]["cid"] is None and item["customer_view"]["portrait_editable"] is False
    batch(client, sid, cid, 1, {"stage": "won"}, request_id="unresolved", expect=409)


def test_portrait_multi_session_customer_and_dedup(client, archive):
    adapter, apply = archive
    apply(("buyer", [row(1)]), ("alias", [row(2, "alias")]))
    buyer_account = adapter._account_by_mapping("ali_id", "buyer")
    redirect_alias_to("alias", buyer_account.aid)
    apply(("alias", [row(3, "alias", at=T + 1)]))
    listing = data(client)
    assert listing["total"] == 2
    cids = {item["customer_view"]["cid"] for item in listing["items"]}
    assert len(cids) == 1 and None not in cids
    cid = cids.pop()
    sids = [item["sid"] for item in listing["items"]]
    tag = create_tag(client, "忠实")
    response = client.post("/api/portraits/batch", json={
        "request_id": "multi",
        "targets": [{"sid": sid, "expected_cid": cid, "expected_version": 0} for sid in sids],
        "changes": {"stage": "won", "add_tag_ids": [tag["tag_id"]]},
    })
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert len(result["changed"]) == 1
    assert sorted(result["changed"][0]["sids"]) == sorted(sids)
    items = data(client, portrait_stage="won")["items"]
    assert {item["customer_view"]["business_tags"][0]["name"] for item in items} == {"忠实"}


def test_portrait_lost_lifecycle_operations_and_revision(client, archive):
    _, apply = archive
    apply(("buyer", [row(1)]))
    item = listing_item(client)
    sid, cid = item["sid"], item["customer_view"]["cid"]
    before = data(client, "/api/conversations/revision")
    result = batch(client, sid, cid, 0, {"stage": "lost", "lost_reason": "price", "lost_note": "嫌贵"}, request_id="lost")
    assert result["portrait_revision"] == before["portrait_revision"] + 1
    view = listing_item(client, lost_reason="price")["customer_view"]
    assert view["business_stage"] == "lost" and view["lost_note"] == "嫌贵" and view["lost_at"] is not None
    assert data(client, lost_reason="unclear")["total"] == 0
    # Leaving lost clears the failure fields but keeps the edit trail.
    batch(client, sid, cid, 1, {"stage": "won"}, request_id="won")
    view = listing_item(client)["customer_view"]
    assert view["business_stage"] == "won" and view["lost_reason"] is None and view["lost_note"] is None
    after = data(client, "/api/conversations/revision")
    assert after["portrait_revision"] == result["portrait_revision"] + 1
    operations = data(client, "/api/portraits/operations", cid=cid)["operations"]
    assert [entry["request_id"] for entry in operations][:2] == ["won", "lost"]
    assert operations[1]["changes"]["lost_reason"] == "price"


def test_lost_note_only_update_keeps_lost_stage(client, archive):
    _, apply = archive
    apply(("buyer", [row(1)]))
    item = listing_item(client)
    sid, cid = item["sid"], item["customer_view"]["cid"]
    batch(client, sid, cid, 0, {"stage": "lost", "lost_reason": "price"}, request_id="lost")
    # Editing only the note on an already-lost customer does not restate the stage.
    result = batch(client, sid, cid, 1, {"lost_note": "同意账期后再谈"}, request_id="note")
    assert result["changed"][0]["version"] == 2
    view = listing_item(client, lost_reason="price")["customer_view"]
    assert view["business_stage"] == "lost" and view["lost_note"] == "同意账期后再谈"
    # Leaving lost clears the fields, so a bare note update becomes a request error.
    batch(client, sid, cid, 2, {"stage": "won"}, request_id="reopen")
    batch(client, sid, cid, 3, {"lost_note": "重新写备注"}, request_id="bad-note", expect=422)


def test_contact_resolver_refuses_ambiguous_or_self_only_participants():
    from backend.app.shared.crm.views import resolve_contact_cid

    self_account = {"aid": 1, "cid": 11, "extra": {"is_self": True}}
    unmarked_self = {"aid": 1, "cid": 11, "extra": {}}
    contact = {"aid": 2, "cid": 22, "extra": {}}
    third = {"aid": 3, "cid": 33, "extra": {}}
    assert resolve_contact_cid([1, 2], {1: self_account, 2: contact}) == 22
    # Unmarked legacy self: the positional contact still wins.
    assert resolve_contact_cid([1, 2], {1: unmarked_self, 2: contact}) == 22
    # Only the unmarked self resolved: never bind the seller's own customer.
    assert resolve_contact_cid([1, 2], {1: unmarked_self}) is None
    # Conflicting third parties are ambiguous; a shared cid is not.
    assert resolve_contact_cid([1, 2, 3], {1: self_account, 2: contact, 3: third}) is None
    assert resolve_contact_cid([1, 2, 3], {1: self_account, 2: contact, 3: {**third, "cid": 22}}) == 22


def test_observe_revisions_tolerates_missing_seller_and_database(tmp_path):
    store = CustomerPortraitStore(tmp_path / "missing.sqlite")
    assert store.observe_revisions("", str(tmp_path)) == {"portrait_revision": 0, "tag_revision": 0}
    assert store.observe_revisions("seller-a", str(tmp_path)) == {"portrait_revision": 0, "tag_revision": 0}


def test_portrait_export_deduplicates_and_reports_missing(client, archive):
    adapter, apply = archive
    apply(("buyer", [row(1)]), ("alias", [row(2, "alias")]))
    buyer_account = adapter._account_by_mapping("ali_id", "buyer")
    redirect_alias_to("alias", buyer_account.aid)
    apply(("alias", [row(3, "alias", at=T + 1)]))
    listing = data(client)
    sids = sorted(item["sid"] for item in listing["items"])
    cid = listing["items"][0]["customer_view"]["cid"]
    tag = create_tag(client, "需要,逗号")
    formula = create_tag(client, "=命令")
    batch(client, sids[0], cid, 0, {"stage": "won", "add_tag_ids": [tag["tag_id"], formula["tag_id"]]},
          request_id="export-setup")
    response = client.post("/api/portraits/export", json={"conversationIds": [*sids, 99999]})
    assert response.status_code == 200, response.text
    exported = response.json()["data"]
    assert exported["exported"] == 1 and exported["missing"] == [99999]
    assert exported["filename"].startswith("customer-portraits-") and exported["filename"].endswith(".csv")
    rows = list(csv.reader(exported["content"].removeprefix("\ufeff").strip().splitlines()))
    assert rows[0][:6] == ["cid", "ali_id", "name", "company", "country", "stage"]
    export_row = dict(zip(rows[0], rows[1]))
    assert export_row["stage"] == "won"
    assert export_row["business_tags"] == "'=命令; 需要,逗号"
    assert export_row["cid"] == str(cid)
    assert export_row["updated_at"]


def test_conflicting_aliases_do_not_redirect_and_profile_enrichment_skips(client, archive):
    adapter, apply = archive
    apply(("buyer", [row(1)]))
    buyer_account = adapter._account_by_mapping("ali_id", "buyer")
    second = adapter.upsert_user_info(UserInfo(login_id="buyer-login", first_name="Second"))
    assert second is not None and second != buyer_account.aid
    with pytest.raises(sync.IdentityConflictError):
        adapter.upsert_user_info(UserInfo(ali_id="buyer", login_id="buyer-login", first_name="Conflict"))
    # The standalone MITM batch skips the disputed item and commits the rest.
    sync._sync_user_infos_now([
        UserInfo(ali_id="buyer", login_id="buyer-login", first_name="Conflict"),
        UserInfo(ali_id="clean", first_name="Clean"),
    ])
    with Session(adapter.engine) as session:
        mapping = session.exec(select(AccountMapping).where(
            AccountMapping.type == "login_id", AccountMapping.key == "buyer-login",
        )).one()
    assert mapping.aid == second
    assert adapter._account_by_mapping("ali_id", "buyer").aid == buyer_account.aid
    fresh = sync.CRMAdapter()
    assert fresh._account_by_mapping("ali_id", "clean") is not None
    fresh.engine.dispose()


def test_dangling_mapping_does_not_redirect(client, archive):
    adapter, apply = archive
    apply(("buyer", [row(1)]))
    account = adapter._account_by_mapping("ali_id", "buyer")
    with closing(sqlite3.connect(InboxStore().database_path)) as conn:
        conn.execute("DELETE FROM account WHERE aid=?", (account.aid,))
        conn.commit()
    # The alias still maps to a deleted account; rebuilding would move the customer.
    with pytest.raises(sync.IdentityConflictError):
        adapter.upsert_user_info(UserInfo(ali_id="buyer", first_name="Redirect"))


def test_conflicted_contact_keeps_committed_target_or_rejects(client, archive):
    adapter, apply = archive
    apply(("buyer", [row(1)]))
    buyer_account = adapter._account_by_mapping("ali_id", "buyer")
    second = adapter.upsert_user_info(UserInfo(login_id="buyer-login"))
    with Session(adapter.engine) as session:
        # A committed session contact wins over an agreeing exact alias.
        assert adapter._resolve_conflicted_contact("seller-a", "buyer", session) == buyer_account.aid
        # A contradictory exact alias has no safe target.
        mapping = session.exec(select(AccountMapping).where(
            AccountMapping.type == "ali_id", AccountMapping.key == "buyer",
        )).one()
        mapping.aid = second
        session.add(mapping)
        session.commit()
        assert adapter._resolve_conflicted_contact("seller-a", "buyer", session) is None
        # Without any committed mapping or session there is no definite target.
        assert adapter._resolve_conflicted_contact("seller-a", "unknown-contact", session) is None


def test_shared_tag_definitions_per_workspace_relations(client, archive):
    _, apply = archive
    apply(("buyer", [row(1)]))
    apply(("buyer", [row(2)]), seller="seller-b")
    tag = create_tag(client, "老客户")
    item = listing_item(client)
    sid, cid = item["sid"], item["customer_view"]["cid"]
    batch(client, sid, cid, 0, {"add_tag_ids": [tag["tag_id"]]}, request_id="a-tag")
    assert client.put("/api/settings/ali-id", json={"ali_id": "seller-b"}).status_code == 200
    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
    catalog = data(client, "/api/portraits/tags")["tags"]
    assert [(entry["name"], entry["usage_count"]) for entry in catalog] == [("老客户", 0)]
    assert listing_item(client)["customer_view"]["business_tags"] == []
    duplicate = client.post("/api/portraits/tags", json={"name": "高潜"})
    assert duplicate.status_code == 200
    duplicate_id = duplicate.json()["data"]["tag"]["tag_id"]
    assert client.patch(f"/api/portraits/tags/{duplicate_id}", json={"name": "老客户"}).status_code == 409
    response = client.patch(f"/api/portraits/tags/{tag['tag_id']}", json={"name": "老客", "archived": True})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["tag"]["version"] == 2
    assert client.put("/api/settings/ali-id", json={"ali_id": "seller-a"}).status_code == 200
    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch
    item = listing_item(client)
    assert item["customer_view"]["business_tags"] == [
        {"tag_id": tag["tag_id"], "name": "老客", "archived": True},
    ]
    # Archived tags cannot be added but can still be removed.
    batch(client, sid, cid, 1, {"add_tag_ids": [tag["tag_id"]]}, request_id="add-archived", expect=422)
    batch(client, sid, cid, 1, {"remove_tag_ids": [tag["tag_id"]]}, request_id="remove-archived")
    assert listing_item(client)["customer_view"]["business_tags"] == []


def test_portrait_pagination_token_invalidates_after_edit(client, archive):
    _, apply = archive
    apply(("buyer", [row(1)]))
    apply(("other", [row(2, "other")]))
    first = data(client, limit=1)
    token = first["pagination_revision"]
    assert data(client, limit=1)["pagination_revision"] == token
    item = first["items"][0]
    batch(client, item["sid"], item["customer_view"]["cid"], 0, {"stage": "won"}, request_id="token")
    assert client.get("/api/conversations", params={
        "limit": 1, "offset": 1, "pagination_revision": token,
    }).status_code == 409
    assert data(client, portrait_stage="won", limit=1)["pagination_revision"] != token
