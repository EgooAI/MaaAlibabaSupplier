import base64
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from sqlalchemy import event
from sqlmodel import Session

from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm import inbox_store, sync
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.crm.inbox_store import InboxStore
from backend.app.shared.crm.sdk import Message
from backend.app.shared.crm.sync_store import canonical_source_dir, read_sync_state
from backend.app.shared.mitm.pool import SelfInfo


T = 1756720000


def row(mid, sender="buyer", at=T, *, system=False, auto=False, kind=0):
    return MessageRow(
        table_name="msg_table", cid="seller-buyer", mid=str(mid),
        sender_id=f"{sender}@icbu" if sender else None, created_at=at,
        user_content_type=kind, content_label="private body", content=b"private body",
        is_system=system, is_auto_reply=auto,
    )


def conv(*messages, contact="buyer"):
    return ContactConv(contact, list(messages), None, None)


def tables(path):
    with closing(sqlite3.connect(path)) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY 1, 2').fetchall() for name in names}


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    path = tmp_path / "crm.sqlite"
    monkeypatch.setenv("MAA_CRM_DB_PATH", str(path))
    adapter = sync.CRMAdapter(path)
    store = InboxStore(path)
    scope = {"seller": "seller", "data_dir": str(tmp_path / "client")}

    def apply(*conversations, seller="seller", data_dir=None):
        return adapter.sync_conversations(
            list(conversations), SelfInfo(ali_id=seller),
            data_dir=scope["data_dir"] if data_dir is None else data_dir,
        )

    return store, scope, apply, adapter


def only(store, scope, **kwargs):
    states = store.states(**scope, **kwargs)
    assert len(states) == 1
    return next(iter(states.items()))


def test_initial_snapshot_history_pending_and_later_old_records_are_unread(inbox):
    store, scope, apply, _ = inbox
    apply(conv(row(1)))
    sid, state = only(store, scope, now=T + 10000000)
    assert state == {
        "unread_count": 0, "reply_state": "history_pending", "history_pending": True,
        "pending_since": None, "due_at": None, "is_overdue": False,
        "read_seq": 0, "snapshot_seq": 1, "uncertain": False,
    }
    baseline = store.metadata(**scope)
    assert baseline["baseline_complete"] is True and baseline["timeout_seconds"] == 86400
    apply(conv(row(1), row(2, at=T - 100)))
    apply(conv(row(3, sender="newbuyer", at=T - 1000), contact="newbuyer"))
    states = store.states(**scope, now=T)
    assert states[sid]["unread_count"] == 1
    assert states[sid]["pending_since"] == T - 100
    assert states[sid]["reply_state"] == "needs_reply"
    assert not states[sid]["history_pending"]
    new_state = next(state for key, state in states.items() if key != sid)
    assert new_state["unread_count"] == 1 and new_state["reply_state"] == "needs_reply"
    assert store.metadata(**scope)["last_seq"] == 3
    before = tables(store.database_path)
    store.initialize(**scope)
    assert tables(store.database_path) == before
    assert store.states(**scope, sids=[]) == {}
    assert store.states(**scope, sids=[sid])[sid]["unread_count"] == 1


def test_explicit_old_archive_baseline_is_additive_idempotent_and_seller_exact(inbox, monkeypatch):
    store, scope, apply, adapter = inbox
    # Simulate a pre-inbox archive without deleting tables or altering real data.
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply(conv(row(1)))
        apply(conv(row(1, sender="seller_extra")), seller="seller_extra")
        apply(conv(row(1)), seller="SELLER")
        apply(conv(row(2)), seller="seller%")
        apply(conv(row(3)), seller="seller_")
    before = tables(store.database_path)
    assert store.states(**scope) == {}
    assert not store.metadata(**scope)["baseline_complete"]
    metadata = store.initialize(**scope)
    assert metadata["last_seq"] == 1
    assert only(store, scope)[1]["reply_state"] == "history_pending"
    for table, rows in before.items():
        assert tables(store.database_path)[table] == rows
    assert store.initialize(**scope) == metadata
    wildcard_scope = {**scope, "seller": "seller%"}
    assert store.initialize(**wildcard_scope)["last_seq"] == 1
    assert len(store.states(**wildcard_scope)) == 1
    apply(conv(row(1), row(4, at=T + 1)))
    assert only(store, scope)[1]["unread_count"] == 1
    adapter.engine.dispose()
    reopened = InboxStore(store.database_path)
    assert reopened.initialize(**scope)["last_seq"] == 2
    assert only(reopened, scope)[1]["unread_count"] == 1


def test_snapshot_100_does_not_read_concurrent_101_and_read_is_monotonic(inbox):
    store, scope, apply, adapter = inbox
    apply()
    apply(conv(*(row(n) for n in range(1, 101))))
    sid, state = only(store, scope)
    assert state["unread_count"] == 100
    token = store.snapshot(sid, **scope)
    revision = store.metadata(**scope)["inbox_revision"]
    # Detail/list loads and snapshot issuance never mark anything read.
    assert len(adapter.get_conversation_detail("seller", sid).messages) == 100
    assert only(store, scope)[1] == state
    assert store.metadata(**scope)["inbox_revision"] == revision
    apply(conv(row(101, at=T + 1)))
    state = store.mark_read(sid, token, **scope)
    assert state["unread_count"] == 1
    assert state["read_seq"] == 100 and state["snapshot_seq"] == 101
    assert state["reply_state"] == "needs_reply"
    newer = store.snapshot(sid, **scope)
    assert store.mark_read(sid, newer, **scope)["unread_count"] == 0
    after = store.metadata(**scope)
    assert store.mark_read(sid, token, **scope)["read_seq"] == 101
    assert store.metadata(**scope) == after
    with Session(adapter.engine) as session:
        assert session.get(Message, message_external_id("seller", "msg_table", "1")).read is None


@pytest.mark.parametrize("initialize_first", [False, True])
@pytest.mark.parametrize("provenance", [False, True])
@pytest.mark.parametrize("empty_source", [False, True])
def test_upgrade_baselines_all_archived_sessions_and_retained_messages(
    inbox, monkeypatch, initialize_first, provenance, empty_source,
):
    store, scope, apply, adapter = inbox
    archived = (conv(row(1), row(2, at=T + 1)),
                conv(row(3, sender="second"), contact="second"))
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        if not provenance:
            patch.setattr(sync, "write_sync_state", lambda *args: {})
        apply(*archived)
    assert not store.metadata(**scope)["baseline_complete"]
    if initialize_first:
        store.initialize(**scope)
    # The source has lost an entire conversation and part of the other one.
    current = () if empty_source else (conv(row(1)),)
    apply(*current)
    states = store.states(**scope, now=T + 10000000)
    assert len(states) == 2
    assert all(state["reply_state"] == "history_pending" for state in states.values())
    assert all(state["unread_count"] == 0 and not state["is_overdue"] for state in states.values())
    assert store.metadata(**scope)["last_seq"] == 3
    with Session(adapter.engine) as session:
        ledger = session.execute(inbox_store.inbox_message.select()).mappings().all()
    assert len(ledger) == 3 and all(message["is_history"] for message in ledger)
    # Detail membership and the retained message IDs used by inbox queries agree.
    for sid in states:
        detail = adapter.get_conversation_detail(scope["seller"], sid)
        assert len(detail.messages) == sum(message["sid"] == sid for message in ledger)
    metadata = store.metadata(**scope)
    before = tables(store.database_path)["app_inbox_message"]
    store.initialize(**scope)
    apply(*archived)
    assert store.metadata(**scope) == metadata
    assert tables(store.database_path)["app_inbox_message"] == before
    assert store.states(**scope, now=T + 10000000) == states


@pytest.mark.parametrize("initialize_first", [False, True])
def test_upgrade_new_source_messages_are_live_regardless_of_initialize_order(inbox, monkeypatch, initialize_first):
    store, scope, apply, _ = inbox
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply(conv(row(1)))
    if initialize_first:
        store.initialize(**scope)
    apply(conv(row(2, at=T + 1)))
    _, state = only(store, scope)
    assert state["snapshot_seq"] == 2 and state["unread_count"] == 1
    assert state["reply_state"] == "needs_reply" and state["pending_since"] == T + 1


@pytest.mark.parametrize("initialize_first", [False, True])
@pytest.mark.parametrize("a_has_ledger", [False, True])
def test_new_directory_never_adopts_other_directory_archive(inbox, monkeypatch, initialize_first, a_has_ledger):
    store, scope, apply, adapter = inbox
    with monkeypatch.context() as patch:
        if not a_has_ledger:
            patch.setattr(sync, "write_inbox", lambda *args: None)
        apply(conv(row(1)), conv(row(2, sender="a-only"), contact="a-only"))
    b = {**scope, "data_dir": scope["data_dir"] + "-B"}
    pending = store.metadata(**b)
    assert not pending["baseline_complete"] and pending["inbox_revision"] == 0
    if initialize_first:
        assert store.initialize(**b) == pending
        assert store.states(**b) == {}
        with Session(adapter.engine) as session:
            assert session.execute(inbox_store.inbox_scope.select().where(
                inbox_store.inbox_scope.c.data_dir == canonical_source_dir(b["data_dir"]),
            )).first() is None
    apply(conv(row(3, sender="b-only"), contact="b-only"), data_dir=b["data_dir"])
    _, state = only(store, b, now=T + 10000000)
    assert state["unread_count"] == 0 and state["reply_state"] == "history_pending"
    assert state["history_pending"] and not state["is_overdue"]
    assert state["pending_since"] is None and state["due_at"] is None
    assert store.metadata(**b)["baseline_complete"]
    with Session(adapter.engine) as session:
        messages = session.execute(inbox_store.inbox_message.select().where(
            inbox_store.inbox_message.c.data_dir == canonical_source_dir(b["data_dir"]),
        )).mappings().all()
    assert [message["external_mid"] for message in messages] == [message_external_id("seller", "msg_table", "3")]
    assert messages[0]["is_history"]
    apply(conv(row(3, sender="b-only"), row(4, sender="b-only", at=T + 1), contact="b-only"), data_dir=b["data_dir"])
    _, state = only(store, b, now=T + 2)
    assert state["unread_count"] == 1 and state["reply_state"] == "needs_reply"
    assert state["pending_since"] == T + 1 and state["due_at"] == T + 1 + 86400


@pytest.mark.parametrize("empty_first_sync", [False, True])
def test_empty_crm_initialize_waits_for_explicit_source_snapshot(inbox, empty_first_sync):
    store, scope, apply, adapter = inbox
    pending = store.metadata(**scope)
    for _ in range(2):
        assert store.initialize(**scope) == pending
        assert store.states(**scope) == {}
        assert not store.metadata(**scope)["baseline_complete"]
    with Session(adapter.engine) as session:
        assert session.execute(inbox_store.inbox_scope.select()).first() is None
    if empty_first_sync:
        apply()
        assert store.metadata(**scope)["baseline_complete"]
        assert store.metadata(**scope)["last_seq"] == 0
    apply(conv(row(1)))
    _, state = only(store, scope)
    assert state["unread_count"] == (1 if empty_first_sync else 0)
    assert state["reply_state"] == ("needs_reply" if empty_first_sync else "history_pending")
    assert store.metadata(**scope)["baseline_complete"]


def test_verified_empty_legacy_scope_can_complete_baseline(inbox, monkeypatch):
    store, scope, apply, _ = inbox
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply()
    metadata = store.initialize(**scope)
    assert metadata["baseline_complete"] and metadata["last_seq"] == 0
    assert store.states(**scope) == {}
    apply(conv(row(1)))
    _, state = only(store, scope)
    assert state["unread_count"] == 1 and state["reply_state"] == "needs_reply"


@pytest.mark.parametrize("initialize_first", [False, True])
def test_legacy_scope_evidence_adopts_only_archive_not_claimed_by_other_ledger(inbox, monkeypatch, initialize_first):
    store, scope, apply, _ = inbox
    apply(conv(row(1)))
    b = {**scope, "data_dir": scope["data_dir"] + "-B"}
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply(conv(row(2, sender="b-only"), contact="b-only"), data_dir=b["data_dir"])
    if initialize_first:
        store.initialize(**b)
    apply(data_dir=b["data_dir"])
    _, state = only(store, b)
    assert state["reply_state"] == "history_pending" and state["unread_count"] == 0
    assert store.metadata(**b)["last_seq"] == 1
    # Actual B source membership permits a shared external ID later, not adoption.
    apply(conv(row(1)), data_dir=b["data_dir"])
    assert len(store.states(**b)) == 2
    assert sum(state["unread_count"] for state in store.states(**b).values()) == 1


def test_unscoped_archive_can_only_be_adopted_by_first_selected_directory(inbox, monkeypatch):
    store, scope, apply, _ = inbox
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        patch.setattr(sync, "write_sync_state", lambda *args: {})
        apply(conv(row(1)))
    store.initialize(**scope)
    assert only(store, scope)[1]["reply_state"] == "history_pending"
    b = {**scope, "data_dir": scope["data_dir"] + "-B"}
    store.initialize(**b)
    assert store.states(**b) == {}
    assert store.metadata(**b)["last_seq"] == 0


def test_upgrade_baseline_and_new_messages_roll_back_together(inbox, monkeypatch):
    store, scope, apply, _ = inbox
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply(conv(row(1), row(2)), conv(row(3, sender="second"), contact="second"))
    before = tables(store.database_path)
    original = sync.write_sync_state

    def fail(*args):
        original(*args)
        assert not store.metadata(**scope)["baseline_complete"]
        raise RuntimeError("upgrade commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_sync_state", fail)
        with pytest.raises(RuntimeError, match="upgrade commit failed"):
            apply(conv(row(1), row(4, at=T + 1)))
    assert tables(store.database_path) == before
    apply(conv(row(1), row(4, at=T + 1)))
    states = store.states(**scope)
    assert len(states) == 2
    assert sum(state["unread_count"] for state in states.values()) == 1
    assert {state["reply_state"] for state in states.values()} == {"history_pending", "needs_reply"}
    assert store.metadata(**scope)["last_seq"] == 4


def test_snapshots_reject_tampering_scope_database_session_and_restart(inbox, monkeypatch, tmp_path):
    store, scope, apply, _ = inbox
    apply(conv(row(1)), conv(row(2, sender="second"), contact="second"))
    sid, other_sid = store.states(**scope)
    token = store.snapshot(sid, **scope)
    raw = base64.urlsafe_b64decode(token)
    payload = json.loads(raw[32:])
    payload[-1] = 999
    tampered = base64.urlsafe_b64encode(raw[:32] + json.dumps(payload).encode()).decode()
    before = tables(store.database_path)
    for invalid in (tampered, "100", "bad!", "", None):
        with pytest.raises(ValueError, match="snapshot"):
            store.mark_read(sid, invalid, **scope)
    for changed in ({**scope, "seller": "other"}, {**scope, "data_dir": str(tmp_path / "other")}):
        with pytest.raises(ValueError, match="snapshot"):
            store.mark_read(sid, token, **changed)
    with pytest.raises(ValueError, match="snapshot"):
        store.mark_read(other_sid, token, **scope)
    with pytest.raises(ValueError, match="snapshot"):
        InboxStore(tmp_path / "other.sqlite").mark_read(sid, token, **scope)
    monkeypatch.setattr(inbox_store, "_TOKEN_KEY", b"different-process-key")
    with pytest.raises(ValueError, match="snapshot"):
        store.mark_read(sid, token, **scope)
    assert tables(store.database_path) == before


def test_repeats_edits_deletions_preserve_sequence_first_seen_and_read(inbox, monkeypatch):
    store, scope, apply, _ = inbox
    apply()
    monkeypatch.setattr(inbox_store.time, "time", lambda: T + 10)
    apply(conv(row(1)))
    sid, _ = only(store, scope)
    store.mark_read(sid, store.snapshot(sid, **scope), **scope)
    before = tables(store.database_path)
    metadata = store.metadata(**scope)
    monkeypatch.setattr(inbox_store.time, "time", lambda: T + 20)
    apply(conv(row(1)))
    assert store.metadata(**scope) == metadata
    assert tables(store.database_path)["app_inbox_message"] == before["app_inbox_message"]
    apply(conv(row(1, sender="seller", at=T + 5)))
    state = only(store, scope)[1]
    assert state["reply_state"] == "waiting_customer" and state["unread_count"] == 0
    assert state["read_seq"] == state["snapshot_seq"] == 1
    assert store.metadata(**scope)["inbox_revision"] == metadata["inbox_revision"] + 1
    with closing(sqlite3.connect(store.database_path)) as conn:
        conn.row_factory = sqlite3.Row
        message = dict(conn.execute("SELECT * FROM app_inbox_message").fetchone())
    assert message["first_seen_at"] == T + 10 and not message["is_history"]
    assert "content" not in message and "private body" not in str(message)
    apply()
    assert only(store, scope)[1] == state
    apply(conv(row(1)))
    assert only(store, scope)[1]["unread_count"] == 0


def test_snapshot_epoch_rejects_return_to_same_account_and_unsigned_epoch_changes(inbox):
    store, scope, apply, _ = inbox
    apply()
    apply(conv(row(1)))
    sid, _ = only(store, scope)
    old_token = store.snapshot(sid, **scope, epoch="A-first")
    unbound_token = store.snapshot(sid, **scope)
    before = tables(store.database_path)
    # The API supplies its current epoch after A -> B -> A, even if the client
    # replaces the old request header with the current account epoch.
    for current_epoch in ("B", "A-returned", None):
        with pytest.raises(ValueError, match="snapshot"):
            store.mark_read(sid, old_token, **scope, epoch=current_epoch)
    with pytest.raises(ValueError, match="snapshot"):
        store.mark_read(sid, unbound_token, **scope, epoch="A-returned")
    raw = base64.urlsafe_b64decode(old_token)
    payload = json.loads(raw[32:])
    payload[-2] = "A-returned"
    forged = base64.urlsafe_b64encode(raw[:32] + json.dumps(payload).encode()).decode()
    with pytest.raises(ValueError, match="snapshot"):
        store.mark_read(sid, forged, **scope, epoch="A-returned")
    assert tables(store.database_path) == before
    new_token = store.snapshot(sid, **scope, epoch="A-returned")
    assert store.mark_read(sid, new_token, **scope, epoch="A-returned")["unread_count"] == 0


def test_source_order_ties_late_buyers_and_strict_later_human_reply(inbox):
    store, scope, apply, _ = inbox
    apply(conv(row(1, at=T - 10000000)))
    apply(conv(row(2, at=T), row(3, at=T + 1), row(4, sender="seller", at=T)))
    sid, state = only(store, scope, now=T + 86400)
    assert state["pending_since"] == T and state["is_overdue"]
    assert state["uncertain"] and state["unread_count"] == 2
    apply(conv(row(5, sender="seller", at=T + 1)))
    assert only(store, scope)[1]["reply_state"] == "needs_reply"
    assert only(store, scope)[1]["pending_since"] == T + 1
    apply(conv(row(6, sender="seller", at=T + 2)))
    state = store.states(**scope)[sid]
    assert state["reply_state"] == "waiting_customer"
    assert state["pending_since"] is None and not state["is_overdue"]
    assert state["unread_count"] == 2
    apply(conv(row(7, at=T - 10)))
    state = only(store, scope)[1]
    assert state["unread_count"] == 3 and state["reply_state"] == "waiting_customer"


def test_card_direction_system_auto_and_unknown_sender(inbox):
    store, scope, apply, _ = inbox
    apply()
    apply(conv(row(1, kind=10010), row(2, sender="seller", at=T + 1, auto=True),
               row(3, sender="seller", at=T + 2, system=True), row(4, at=T + 3, auto=True)))
    state = only(store, scope)[1]
    assert state["reply_state"] == "needs_reply" and state["unread_count"] == 1
    apply(conv(row(5, sender="seller", at=T + 4, kind=10010)))
    assert only(store, scope)[1]["reply_state"] == "waiting_customer"
    apply(conv(row(6, sender="stranger", at=T + 5)))
    state = only(store, scope)[1]
    assert state["reply_state"] == "unknown" and state["uncertain"]
    assert state["unread_count"] == 1
    apply(conv(row(7, sender="seller", at=T + 6)))
    assert only(store, scope)[1]["reply_state"] == "waiting_customer"
    apply(conv(row(8, sender=None, at=T + 6)))
    assert only(store, scope)[1]["reply_state"] == "unknown"


def test_only_system_auto_or_unknown_messages_and_empty_snapshot(inbox):
    store, scope, apply, _ = inbox
    apply()
    assert store.metadata(**scope)["baseline_complete"]
    assert store.states(**scope) == {}
    apply(conv(row(1, system=True), row(2, auto=True)),
          conv(row(3, sender=None), contact="second"))
    states = list(store.states(**scope).values())
    assert {state["reply_state"] for state in states} == {"none", "unknown"}
    assert all(state["unread_count"] == 0 and not state["is_overdue"] for state in states)
    assert all(state["due_at"] is None for state in states)


def test_concurrent_sync_and_read_watermarks_are_monotonic(inbox):
    store, scope, apply, adapter = inbox
    apply()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(apply, conv(row(n))) for n in (1, 2)]
        for future in futures:
            future.result(timeout=10)
    sid, state = only(store, scope)
    assert state["unread_count"] == state["snapshot_seq"] == 2
    token = store.snapshot(sid, **scope)
    apply(conv(row(3)))
    newer = InboxStore(store.database_path).snapshot(sid, **scope)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(store.mark_read, sid, value, **scope) for value in (newer, token)]
        for future in futures:
            future.result(timeout=10)
    assert only(store, scope)[1]["read_seq"] == 3
    assert only(store, scope)[1]["unread_count"] == 0
    with Session(adapter.engine) as session:
        assert len(session.execute(inbox_store.inbox_message.select()).all()) == 3


@pytest.mark.parametrize("invalid", [None, "not-a-time", 0, -1, True, 10**25])
def test_invalid_time_uses_first_seen_with_uncertainty(inbox, monkeypatch, invalid):
    store, scope, apply, _ = inbox
    apply()
    monkeypatch.setattr(inbox_store.time, "time", lambda: T)
    apply(conv(row(1, at=invalid)))
    state = only(store, scope, now=T + 86400)[1]
    assert state["pending_since"] == T and state["due_at"] == T + 86400
    assert state["reply_state"] == "needs_reply" and state["uncertain"] and state["is_overdue"]
    apply(conv(row(2, sender="seller", at=T + 1)))
    assert only(store, scope)[1]["reply_state"] == "waiting_customer"
    assert only(store, scope)[1]["uncertain"]


@pytest.mark.parametrize("source_at", [T + 300, T + 301, 4070908800])
def test_future_time_boundary_and_repeat_sync_use_fixed_first_seen(inbox, monkeypatch, source_at):
    store, scope, apply, _ = inbox
    monkeypatch.setattr(inbox_store.time, "time", lambda: T)
    apply()
    apply(conv(row(1, at=source_at)))
    invalid = source_at > T + 300
    state = only(store, scope, now=T + 86400)[1]
    expected_time = T if invalid else source_at
    assert state["pending_since"] == expected_time
    assert state["due_at"] == expected_time + 86400
    assert state["is_overdue"] is invalid
    assert state["uncertain"] is invalid
    assert state["unread_count"] == 1
    before = tables(store.database_path)["app_inbox_message"]
    metadata = store.metadata(**scope)
    for later_now in (source_at + 86400, T - 86400):
        monkeypatch.setattr(inbox_store.time, "time", lambda: later_now)
        apply(conv(row(1, at=source_at)))
        assert tables(store.database_path)["app_inbox_message"] == before
        assert store.metadata(**scope) == metadata
        assert only(store, scope, now=T + 86400)[1] == state


def test_future_outbound_does_not_hide_new_buyer_and_corrections_use_original_first_seen(inbox, monkeypatch):
    store, scope, apply, _ = inbox
    monkeypatch.setattr(inbox_store.time, "time", lambda: T)
    apply(conv(row(1, sender="seller", at=4070908800)))
    apply(conv(row(2, at=T + 1)))
    sid, state = only(store, scope)
    assert state["reply_state"] == "needs_reply" and state["uncertain"]
    assert state["pending_since"] == T + 1
    store.mark_read(sid, store.snapshot(sid, **scope), **scope)
    metadata = store.metadata(**scope)
    # This corrected time is past relative to the new wall clock, but still
    # invalid relative to this message's original observation.
    monkeypatch.setattr(inbox_store.time, "time", lambda: T + 10000)
    apply(conv(row(1, sender="seller", at=T + 301)))
    assert store.metadata(**scope) == metadata
    assert only(store, scope)[1]["reply_state"] == "needs_reply"
    apply(conv(row(1, sender="seller", at=T + 2)))
    state = only(store, scope)[1]
    assert state["reply_state"] == "waiting_customer" and not state["uncertain"]
    assert state["read_seq"] == state["snapshot_seq"] == 2
    assert state["unread_count"] == 0
    assert store.metadata(**scope)["inbox_revision"] == metadata["inbox_revision"] + 1


def test_future_archive_history_has_no_unread_or_timeout(inbox, monkeypatch):
    store, scope, apply, _ = inbox
    monkeypatch.setattr(inbox_store.time, "time", lambda: T)
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply(conv(row(1, at=4070908800)))
    store.initialize(**scope)
    state = only(store, scope, now=T + 86400)[1]
    assert state["reply_state"] == "history_pending" and state["uncertain"]
    assert state["unread_count"] == 0 and not state["is_overdue"]
    assert state["pending_since"] is None and state["due_at"] is None


def test_timeout_is_derived_from_clock_and_scoped_config(inbox):
    store, scope, apply, _ = inbox
    apply(conv(row(1, at=T - 10000000)))
    apply(conv(row(2, at=T)))
    state = only(store, scope, now=T + 86399)[1]
    assert not state["is_overdue"] and state["due_at"] == T + 86400
    revision = store.metadata(**scope)["inbox_revision"]
    assert only(store, scope, now=T + 86400)[1]["is_overdue"]
    assert store.metadata(**scope)["inbox_revision"] == revision
    metadata = store.set_timeout(60, **scope)
    assert metadata["inbox_revision"] == revision + 1
    assert store.set_timeout(60, **scope) == metadata
    assert only(store, scope, now=T + 60)[1]["is_overdue"]
    assert not only(store, scope, now=T + 59)[1]["is_overdue"]
    for invalid in (0, -1, True, 1.2, "60", None, 2**31):
        with pytest.raises(ValueError):
            store.set_timeout(invalid, **scope)
    assert store.metadata(**scope) == metadata


def test_seller_and_source_directory_isolation_with_same_external_message_id(inbox):
    store, scope, apply, _ = inbox
    a, b = scope, {**scope, "data_dir": scope["data_dir"] + "-other"}
    other_seller = {**scope, "seller": "other"}
    apply()
    apply(conv(row(1)))
    sid, _ = only(store, a)
    apply(conv(row(1, sender="seller")), data_dir=b["data_dir"])
    apply(conv(row(1)), seller="other")
    assert only(store, a)[1]["unread_count"] == 1
    assert only(store, b)[1]["unread_count"] == 0
    assert only(store, b)[1]["reply_state"] == "waiting_customer"
    assert only(store, other_seller)[1]["reply_state"] == "history_pending"
    assert store.states(**other_seller, sids=[sid]) == {}
    apply(conv(row(2)), data_dir=b["data_dir"])
    assert only(store, b)[1]["unread_count"] == 1
    store.mark_read(sid, store.snapshot(sid, **a), **a)
    assert only(store, a)[1]["unread_count"] == 0
    assert only(store, b)[1]["unread_count"] == 1
    store.set_timeout(300, **a)
    assert store.metadata(**b)["timeout_seconds"] == 86400
    assert store.metadata(**other_seller)["timeout_seconds"] == 86400
    alias = {**a, "data_dir": a["data_dir"] + "/../client/."}
    assert store.states(**alias) == store.states(**a)
    assert store.metadata(**alias)["data_dir"] == canonical_source_dir(a["data_dir"])
    assert store.metadata(**alias) == store.metadata(**a)


@pytest.mark.parametrize("existing", [False, True])
def test_sync_failure_rolls_back_inbox_messages_metadata_and_ddl(inbox, monkeypatch, existing):
    store, scope, apply, _ = inbox
    if existing:
        apply(conv(row(1)))
    before = tables(store.database_path)
    metadata = store.metadata(**scope)
    sync_before = read_sync_state(scope["seller"], scope["data_dir"], store.database_path)
    original = sync.write_sync_state

    def fail(*args):
        original(*args)
        assert store.metadata(**scope) == metadata
        raise RuntimeError("after inbox and CRM metadata")

    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_sync_state", fail)
        with pytest.raises(RuntimeError, match="after inbox"):
            apply(conv(row(1), row(2)))
    assert tables(store.database_path) == before
    assert read_sync_state(scope["seller"], scope["data_dir"], store.database_path) == sync_before
    apply(conv(row(1), row(2)))
    state = only(store, scope)[1]
    assert state["snapshot_seq"] == 2
    assert state["unread_count"] == (1 if existing else 0)


def test_archive_initialization_failure_rolls_back_ddl_and_all_rows(inbox, monkeypatch):
    store, scope, apply, _ = inbox
    with monkeypatch.context() as patch:
        patch.setattr(sync, "write_inbox", lambda *args: None)
        apply(conv(row(1)))
    before = tables(store.database_path)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO app_inbox_scope"):
            raise RuntimeError("seed failed")

    original_create = inbox_store.create_engine

    def engine_with_failure(*args, **kwargs):
        engine = original_create(*args, **kwargs)
        event.listen(engine, "before_cursor_execute", fail)
        return engine

    with monkeypatch.context() as patch:
        patch.setattr(inbox_store, "create_engine", engine_with_failure)
        with pytest.raises(RuntimeError, match="seed failed"):
            store.initialize(**scope)
    assert tables(store.database_path) == before
    assert store.initialize(**scope)["last_seq"] == 1


def test_readonly_missing_store_and_missing_schema(tmp_path):
    path = tmp_path / "missing" / "crm.sqlite"
    store = InboxStore(path)
    scope = {"seller": "seller", "data_dir": str(tmp_path / "client")}
    assert store.states(**scope) == {}
    assert not store.metadata(**scope)["baseline_complete"]
    with pytest.raises(FileNotFoundError):
        store.initialize(**scope)
    assert not path.parent.exists()
    path = tmp_path / "unrelated.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
        conn.execute("INSERT INTO unrelated VALUES ('preserve')")
        conn.commit()
    store = InboxStore(path)
    before = path.read_bytes()
    assert store.states(**scope) == {}
    assert store.metadata(**scope)["inbox_revision"] == 0
    assert path.read_bytes() == before
