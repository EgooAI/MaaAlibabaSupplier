from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
import sqlite3
from threading import Barrier

import pytest

from backend.app.api.envelope import AppError
from backend.app.shared.crm.outbox_store import OutboxConflict, OutboxStore, PENDING_STATUSES


@pytest.fixture
def store(tmp_path):
    return OutboxStore(tmp_path / "crm.sqlite")


@pytest.fixture
def scope(tmp_path):
    return {"seller": "seller-1", "data_dir": tmp_path / "source"}


def create(store, scope, key="key-1", **changes):
    return store.create(**{
        **scope, "conversation_id": 1, "contact_ali_id": "buyer-1", "login_id": "buyer-login",
        "content": "  exact\r\nsubmitted \u5185\u5bb9  ", "action": "send",
        "idempotency_key": key, "draft_version": 8, **changes,
    })


def move(store, scope, task, status, **changes):
    return store.transition(task["id"], task["status"], task["version"], **scope, status=status, **changes)


def advance(store, scope, task, target):
    for status in ("navigating", "awaiting_confirmation", "queued_send", "running", "verifying"):
        if task["status"] == target:
            break
        changes = {}
        if status == "awaiting_confirmation":
            changes = {"screenshot_id": "artifact-1", "screenshot_at": 123.5, "screenshot_digest": "sha256:abc"}
        if status == "verifying":
            changes = {"may_have_sent": True, "baseline": {"message_ids": ["old"], "revision": 7}}
        task = move(store, scope, task, status, **changes)
    assert task["status"] == target
    return task


def race(*calls):
    gate = Barrier(len(calls))

    def run(call):
        gate.wait(timeout=5)
        try:
            return call()
        except OutboxConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(run, call) for call in calls]
        return [future.result(timeout=15) for future in futures]


def test_reads_do_not_create_database_directories_or_schema(tmp_path, scope):
    store = OutboxStore(tmp_path / "absent" / "crm.sqlite")
    assert store.get("missing", **scope) is None
    assert store.list(1, **scope) == []
    assert store.list_pending(**scope) == []
    assert store.events("missing", **scope) == []
    assert store.recover() == []
    assert not store.database_path.parent.exists()

    existing = OutboxStore(tmp_path / "existing.sqlite")
    with closing(sqlite3.connect(existing.database_path)) as conn:
        conn.execute("CREATE TABLE crm_sentinel (content TEXT)")
        conn.execute("INSERT INTO crm_sentinel VALUES ('preserved')")
        conn.commit()
    before = existing.database_path.read_bytes()
    assert existing.get("missing", **scope) is None
    assert existing.list(1, **scope) == []
    assert existing.list_pending(**scope) == []
    assert existing.events("missing", **scope) == []
    assert existing.recover() == []
    assert existing.database_path.read_bytes() == before
    existing.initialize()
    existing.initialize()
    with closing(sqlite3.connect(existing.database_path)) as conn:
        assert {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "crm_sentinel", "app_outbox", "app_outbox_events",
        }
        assert conn.execute("SELECT * FROM crm_sentinel").fetchall() == [("preserved",)]


def test_default_database_path_uses_isolated_crm_configuration(monkeypatch, tmp_path, scope):
    path = tmp_path / "configured-crm.sqlite"
    monkeypatch.setenv("MAA_CRM_DB_PATH", str(path))
    store = OutboxStore()
    assert store.database_path == path.resolve()
    assert not path.exists()
    task, created = create(store, scope)
    assert created and store.get(task["id"], **scope) == task


def test_legacy_schema_adds_nullable_origins_without_changing_payload_or_audit(store, scope):
    old, _ = create(store, scope)
    events = store.events(old["id"], **scope)
    with closing(sqlite3.connect(store.database_path)) as conn, conn:
        conn.execute("ALTER TABLE app_outbox DROP COLUMN origin_request_id")
        conn.execute("ALTER TABLE app_outbox DROP COLUMN origin_account_epoch")
    before = store.database_path.read_bytes()
    assert store.get(old["id"], **scope)["origin_request_id"] is None
    assert store.database_path.read_bytes() == before
    stores = [OutboxStore(store.database_path) for _ in range(3)]
    assert race(*(current.initialize for current in stores)) == [None] * 3
    assert store.get(old["id"], **scope) == old
    assert store.events(old["id"], **scope) == events
    task, _ = create(store, scope, key="new", origin_request_id="request-a", origin_account_epoch="epoch-a")
    repeated, created = create(store, scope, key="new", origin_request_id="request-b", origin_account_epoch="epoch-b")
    assert not created and repeated == task
    failed = move(store, scope, task, "failed")
    retry = store.retry(task["id"], failed["version"], **scope)
    assert retry["origin_request_id"] == "request-a"
    assert retry["origin_account_epoch"] == "epoch-a"
    assert retry["attempt"] == 2


def test_concurrent_idempotency_across_store_instances(store, scope):
    stores = [OutboxStore(store.database_path) for _ in range(8)]
    results = race(*(lambda current=current: create(current, scope) for current in stores))
    assert sum(created for _, created in results) == 1
    task = results[0][0]
    assert all(result == task for result, _ in results)
    assert len(store.events(task["id"], **scope)) == 1
    assert len(store.list(1, **scope)) == 1
    assert task["content"] == "  exact\r\nsubmitted \u5185\u5bb9  "
    assert task["may_have_sent"] is False
    assert task["version"] == task["attempt"] == 1
    alias = {**scope, "data_dir": scope["data_dir"] / "child" / ".."}
    assert create(store, alias) == (task, False)
    if os.name == "nt":
        assert create(store, {**scope, "data_dir": str(scope["data_dir"]).upper()}) == (task, False)
    changed = move(store, scope, task, "navigating")
    assert create(store, scope) == (changed, False)


@pytest.mark.parametrize("changes", [
    {"content": "exact\r\nsubmitted \u5185\u5bb9"}, {"conversation_id": 2},
    {"contact_ali_id": "buyer-2"}, {"login_id": "new-login"}, {"action": "test"},
    {"draft_version": 9}, {"draft_version": None},
])
def test_payload_mismatch_is_409_without_mutation(store, scope, changes):
    task, _ = create(store, scope)
    with pytest.raises(OutboxConflict) as error:
        create(store, scope, **changes)
    assert isinstance(error.value, AppError)
    assert error.value.status_code == 409
    assert store.get(task["id"], **scope) == task
    assert len(store.events(task["id"], **scope)) == 1


def test_concurrent_different_payload_cannot_reuse_key(store, scope):
    results = race(lambda: create(store, scope, content="first"), lambda: create(store, scope, content="second"))
    assert sum(isinstance(result, OutboxConflict) for result in results) == 1
    assert len(store.list(1, **scope)) == 1


@pytest.mark.parametrize("changed_scope", ["seller", "data_dir"])
def test_scope_ownership_for_all_task_reads_and_writes(store, scope, tmp_path, changed_scope):
    task, _ = create(store, scope)
    other = {**scope, changed_scope: "seller-2" if changed_scope == "seller" else tmp_path / "other-source"}
    assert store.get(task["id"], **other) is None
    assert store.list(1, **other) == []
    assert store.list_pending(**other) == []
    assert store.events(task["id"], **other) == []
    for write in (
        lambda: move(store, other, task, "navigating"),
        lambda: store.update_fields(task["id"], "queued", **other, reason="wrong scope"),
        lambda: store.retry(task["id"], **other),
        lambda: store.cancel(task["id"], **other),
    ):
        with pytest.raises(OutboxConflict):
            write()
    other_task, created = create(store, other)
    assert created and other_task["id"] != task["id"]
    assert store.get(task["id"], **scope) == task


@pytest.mark.parametrize("start,target", [("queued", "navigating"), ("queued_send", "running")])
def test_concurrent_cancel_and_worker_claim_have_exactly_one_winner(store, scope, start, target):
    task, _ = create(store, scope)
    if start != "queued":
        task = advance(store, scope, task, start)
    other = OutboxStore(store.database_path)
    results = race(
        lambda: store.cancel(task["id"], task["version"], **scope),
        lambda: move(other, scope, task, target),
    )
    assert sum(isinstance(result, OutboxConflict) for result in results) == 1
    current = store.get(task["id"], **scope)
    assert current["status"] in ("cancelled", target)
    assert current["version"] == task["version"] + 1
    assert len(store.events(task["id"], **scope)) == current["version"]


@pytest.mark.parametrize("state", PENDING_STATUSES)
def test_cancel_is_limited_to_safe_queued_or_confirmation_states(store, scope, state):
    task, _ = create(store, scope)
    if state != "queued":
        task = advance(store, scope, task, state)
    if state in ("queued", "awaiting_confirmation", "queued_send"):
        cancelled = store.cancel(task["id"], task["version"], **scope)
        assert cancelled["status"] == "cancelled"
        assert cancelled["reason"] == "cancelled_by_user"
        with pytest.raises(OutboxConflict):
            store.retry(task["id"], **scope)
    else:
        with pytest.raises(OutboxConflict):
            store.cancel(task["id"], task["version"], **scope)
        assert store.get(task["id"], **scope) == task


def test_version_cas_protects_confirmation_snapshot_and_retry_aba(store, scope):
    task, _ = create(store, scope)
    failed = move(store, scope, task, "failed", reason="safe_failure")
    retried = store.retry(task["id"], failed["version"], **scope)
    with pytest.raises(OutboxConflict):
        move(store, scope, task, "navigating")
    current = advance(store, scope, retried, "awaiting_confirmation")
    updated = store.update_fields(current["id"], current["status"], current["version"], **scope, reason="changed")
    with pytest.raises(OutboxConflict):
        move(store, scope, current, "queued_send")
    with pytest.raises(OutboxConflict):
        store.update_fields(current["id"], current["status"], current["version"], **scope, reason="stale")
    assert store.get(current["id"], **scope) == updated
    assert move(store, scope, updated, "queued_send")["status"] == "queued_send"


@pytest.mark.parametrize("expired", [False, True])
def test_fresh_confirmation_invalidates_old_confirmation_and_worker_versions(store, scope, expired):
    task, _ = create(store, scope)
    original = advance(store, scope, task, "awaiting_confirmation")
    queued = move(store, scope, original, "queued_send")
    refreshed = move(
        store, scope, queued, "awaiting_confirmation",
        screenshot_id="artifact-2", screenshot_at=original["screenshot_at"] + (120 if expired else 1),
        screenshot_digest=original["screenshot_digest"] if expired else "sha256:changed",
        reason="screenshot_expired_or_changed",
    )
    assert refreshed["status"] == refreshed["phase"] == "awaiting_confirmation"
    assert refreshed["version"] == queued["version"] + 1
    assert refreshed["attempt"] == original["attempt"]
    assert refreshed["may_have_sent"] is False
    assert refreshed["screenshot_id"] != original["screenshot_id"]
    assert store.events(task["id"], **scope)[-1]["previous"] == queued
    assert store.events(task["id"], **scope)[-1]["current"] == refreshed
    with pytest.raises(OutboxConflict):
        move(store, scope, original, "queued_send")
    with pytest.raises(OutboxConflict):
        move(store, scope, queued, "running", phase="input")
    requeued = move(store, scope, refreshed, "queued_send")
    # The same status recurs, but an old callback still cannot start input.
    with pytest.raises(OutboxConflict):
        move(store, scope, queued, "running", phase="input")
    with pytest.raises(OutboxConflict):
        move(store, scope, requeued, "awaiting_confirmation", may_have_sent=True)
    assert store.get(task["id"], **scope) == requeued
    running = move(store, scope, requeued, "running", phase="input")
    assert running["version"] == requeued["version"] + 1
    assert len(store.events(task["id"], **scope)) == running["version"]


def test_queued_send_with_input_phase_cannot_return_to_confirmation(store, scope):
    task, _ = create(store, scope)
    task = advance(store, scope, task, "queued_send")
    task = store.update_fields(task["id"], "queued_send", task["version"], **scope, phase="input")
    with pytest.raises(OutboxConflict, match="before input"):
        move(store, scope, task, "awaiting_confirmation")
    assert store.get(task["id"], **scope) == task
    assert len(store.events(task["id"], **scope)) == task["version"]


@pytest.mark.parametrize("may_have_sent", [False, True])
def test_running_cannot_return_to_confirmation_even_if_changes_clear_risk(store, scope, may_have_sent):
    task, _ = create(store, scope)
    task = advance(store, scope, task, "running")
    task = store.update_fields(
        task["id"], "running", task["version"], **scope,
        phase="click" if may_have_sent else "input", may_have_sent=may_have_sent,
    )
    with pytest.raises(OutboxConflict):
        move(store, scope, task, "awaiting_confirmation", may_have_sent=False)
    assert store.get(task["id"], **scope) == task
    assert len(store.events(task["id"], **scope)) == task["version"]


@pytest.mark.parametrize("start,target", [
    ("queued", "running"), ("queued", "queued_send"), ("navigating", "running"),
    ("awaiting_confirmation", "running"), ("running", "queued"), ("verifying", "running"),
    ("verifying", "confirmed"), ("verifying", "delivered"), ("queued", "queued"),
])
def test_transition_graph_rejects_skipped_confirmation_and_replay(store, scope, start, target):
    task, _ = create(store, scope)
    if start != "queued":
        task = advance(store, scope, task, start)
    with pytest.raises(OutboxConflict):
        move(store, scope, task, target)
    assert store.get(task["id"], **scope) == task


def test_screenshot_and_action_constraints_and_monotonic_send_risk(store, scope):
    task, _ = create(store, scope)
    navigating = move(store, scope, task, "navigating")
    with pytest.raises(OutboxConflict, match="screenshot"):
        move(store, scope, navigating, "awaiting_confirmation")
    awaiting = move(store, scope, navigating, "awaiting_confirmation", screenshot_id="shot", screenshot_at=1)
    queued_send = move(store, scope, awaiting, "queued_send")
    running = move(store, scope, queued_send, "running")
    with pytest.raises(OutboxConflict):
        move(store, scope, running, "filled")
    with pytest.raises(OutboxConflict):
        move(store, scope, running, "verifying")
    marked = store.update_fields(running["id"], "running", running["version"], **scope, may_have_sent=True, phase="sending")
    with pytest.raises(OutboxConflict):
        store.update_fields(marked["id"], "running", **scope, may_have_sent=False)
    with pytest.raises(OutboxConflict):
        store.cancel(marked["id"], **scope)
    verifying = move(store, scope, marked, "verifying")
    with pytest.raises(OutboxConflict):
        move(store, scope, verifying, "observed", matched_message_id="local-1")
    with pytest.raises(OutboxConflict):
        move(store, scope, verifying, "observed", evidence={"source_revision": 8})

    test, _ = create(store, scope, key="test-action", action="test")
    test = advance(store, scope, test, "running")
    with pytest.raises(OutboxConflict):
        move(store, scope, test, "verifying", may_have_sent=True)
    filled = move(store, scope, test, "filled")
    assert filled["may_have_sent"] is False
    with pytest.raises(OutboxConflict):
        store.retry(filled["id"], **scope)


@pytest.mark.parametrize("changes", [
    {"content": "changed"}, {"attempt": 99}, {"version": 10},
    {"idempotency_key": "other"}, {"draft_version": 9}, {"status": "running"},
    {"matched_message_id": "local-1"}, {"may_have_sent": "false"},
    {"baseline": []}, {"evidence": {"bad": float("nan")}},
    {"screenshot_id": "missing timestamp"}, {"screenshot_at": float("inf")},
])
def test_field_update_whitelist_and_validation(store, scope, changes):
    task, _ = create(store, scope)
    with pytest.raises((ValueError, OutboxConflict)):
        store.update_fields(task["id"], "queued", **scope, **changes)
    assert store.get(task["id"], **scope) == task
    assert len(store.events(task["id"], **scope)) == 1


def test_retry_clears_current_artifacts_but_preserves_complete_event_history(store, scope):
    task, _ = create(store, scope)
    task = advance(store, scope, task, "awaiting_confirmation")
    failed = move(store, scope, task, "failed", reason="session_expired", baseline={"revision": 8}, evidence={"detail": "safe"})
    with pytest.raises(OutboxConflict):
        move(store, scope, failed, "queued")
    queued = store.retry(failed["id"], failed["version"], **scope)
    assert queued["attempt"] == 2
    assert queued["status"] == queued["phase"] == "queued"
    for name in ("screenshot_id", "screenshot_at", "screenshot_digest", "baseline", "evidence", "reason", "matched_message_id"):
        assert queued[name] is None
    assert queued["content"] == task["content"]
    assert queued["draft_version"] == 8
    events = store.events(task["id"], **scope)
    assert [event["version"] for event in events] == list(range(1, queued["version"] + 1))
    assert events[0]["previous"] is None
    for previous, current in zip(events, events[1:]):
        assert previous["current"] == current["previous"]
    assert events[-1]["kind"] == "retry"
    assert events[-1]["previous"] == failed
    assert events[-1]["previous"]["screenshot_digest"] == "sha256:abc"
    assert events[-1]["current"] == queued
    with pytest.raises(OutboxConflict):
        store.retry(queued["id"], **scope)


def test_possibly_sent_failure_cannot_retry_or_clear_risk(store, scope):
    task, _ = create(store, scope)
    task = advance(store, scope, task, "verifying")
    failed = move(store, scope, task, "failed", reason="send_reported_failure")
    with pytest.raises(OutboxConflict):
        store.retry(failed["id"], **scope)
    with pytest.raises(OutboxConflict):
        store.update_fields(failed["id"], "failed", **scope, may_have_sent=False)


@pytest.mark.parametrize("state,action", [
    (state, action) for state in PENDING_STATUSES for action in ("send", "test")
    if (state, action) != ("verifying", "test")
])
def test_restart_invalidates_every_pending_state_without_replay(store, scope, state, action):
    task, _ = create(store, scope, action=action)
    if state != "queued":
        task = advance(store, scope, task, state)
    restarted = OutboxStore(store.database_path)
    recovered = restarted.recover()
    assert len(recovered) == 1
    current = recovered[0]
    assert current["status"] == ("unknown" if state in ("navigating", "running", "verifying") else "failed")
    assert current["may_have_sent"] == task["may_have_sent"]
    assert current["version"] == task["version"] + 1
    assert current["attempt"] == 1
    assert current["screenshot_id"] is current["screenshot_at"] is None
    if state == "awaiting_confirmation":
        assert current["reason"] == "restart_confirmation_lost"
    assert restarted.list_pending(**scope) == []
    assert restarted.recover() == []
    assert restarted.events(task["id"], **scope)[-1]["previous"] == task
    with pytest.raises(OutboxConflict):
        store.transition(task["id"], state, task["version"], **scope, status="failed")
    if current["status"] == "unknown":
        with pytest.raises(OutboxConflict):
            restarted.retry(current["id"], **scope)
    else:
        assert restarted.retry(current["id"], current["version"], **scope)["attempt"] == 2


def test_observed_claim_is_unique_across_tasks_and_conversations_in_source_scope(store, scope, tmp_path):
    first, _ = create(store, scope, key="first")
    second, _ = create(store, scope, key="second", conversation_id=2)
    first = advance(store, scope, first, "verifying")
    second = advance(store, scope, second, "verifying")
    proof = {"matched_message_id": "message-table:42", "evidence": {"source_revision": 9, "match": "local"}}
    results = race(
        lambda: move(store, scope, first, "observed", **proof),
        lambda: move(OutboxStore(store.database_path), scope, second, "observed", **proof),
    )
    assert sum(isinstance(result, OutboxConflict) for result in results) == 1
    winner = next(result for result in results if isinstance(result, dict))
    loser = second if winner["id"] == first["id"] else first
    assert store.get(loser["id"], **scope) == loser
    assert len(store.events(loser["id"], **scope)) == loser["version"]
    assert winner["status"] == "observed"
    for call in (
        lambda: store.retry(winner["id"], **scope), lambda: store.cancel(winner["id"], **scope),
        lambda: move(store, scope, winner, "unknown"),
        lambda: store.update_fields(winner["id"], "observed", **scope, evidence={"changed": True}),
    ):
        with pytest.raises(OutboxConflict):
            call()
    store.recover()
    assert store.get(winner["id"], **scope) == winner
    for other_scope in ({**scope, "seller": "seller-2"}, {**scope, "data_dir": tmp_path / "other-source"}):
        other, _ = create(store, other_scope)
        other = advance(store, other_scope, other, "verifying")
        assert move(store, other_scope, other, "observed", **proof)["matched_message_id"] == proof["matched_message_id"]


def test_unknown_possible_send_can_be_observed_later_but_never_replayed(store, scope):
    task, _ = create(store, scope)
    task = advance(store, scope, task, "verifying")
    unknown = store.recover()[0]
    assert store.list_pending(("verifying", "unknown"), **scope) == [unknown]
    observed = move(store, scope, unknown, "observed", matched_message_id="late", evidence={"revision": 10})
    assert observed["baseline"] == task["baseline"]
    assert store.recover() == []


def test_restart_preserves_send_marker_and_recovers_all_seller_scopes(store, scope):
    task, _ = create(store, scope)
    task = advance(store, scope, task, "running")
    marked = store.update_fields(task["id"], "running", task["version"], **scope, may_have_sent=True, phase="sending")
    other_scope = {**scope, "seller": "other-seller"}
    other, _ = create(store, other_scope)
    recovered = {record["id"]: record for record in OutboxStore(store.database_path).recover()}
    assert recovered[task["id"]]["status"] == "unknown"
    assert recovered[task["id"]]["may_have_sent"] is True
    assert recovered[other["id"]]["status"] == "failed"
    assert store.events(task["id"], **scope)[-1]["previous"] == marked
    with pytest.raises(OutboxConflict):
        store.retry(task["id"], **scope)


@pytest.mark.parametrize("changes", [{"data_dir": ""}, {"data_dir": None}, {"seller": ""}])
def test_invalid_scope_cannot_initialize_or_write_database(store, scope, changes):
    with pytest.raises(ValueError):
        create(store, {**scope, **changes})
    assert not store.database_path.exists()


def test_event_failure_rolls_back_task_and_message_claim(store, scope):
    task, _ = create(store, scope)
    task = advance(store, scope, task, "verifying")
    with closing(sqlite3.connect(store.database_path)) as conn:
        conn.execute("CREATE TRIGGER reject_event BEFORE INSERT ON app_outbox_events "
                     "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        move(store, scope, task, "observed", matched_message_id="local", evidence={"revision": 9})
    assert store.get(task["id"], **scope) == task
    assert len(store.events(task["id"], **scope)) == task["version"]


def test_list_filters_conversation_orders_and_limits(store, scope):
    first, _ = create(store, scope, key="first")
    create(store, scope, key="other-conversation", conversation_id=2)
    second, _ = create(store, scope, key="second")
    cancelled = store.cancel(second["id"], **scope)
    assert store.list(1, **scope) == [cancelled, first]
    assert store.list(1, **scope, limit=1) == [cancelled]
    assert store.list_pending("cancelled", **scope) == [cancelled]
    with pytest.raises(ValueError):
        store.list_pending("confirmed", **scope)
    assert store.recover()
    assert store.get(second["id"], **scope) == cancelled
