from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from types import SimpleNamespace
import os
import sqlite3
import time

import numpy as np
import pytest

from backend.app.api.envelope import AppError
from backend.app.shared.backend import outbox_service as module, source_messages, send_verification
from backend.app.shared.backend.account_context import AccountContext
from backend.app.shared.backend.gui_evidence import frame_from_image
from backend.app.shared.backend.gui_session import GuiSessionToken
from backend.app.shared.crm.outbox_store import OutboxConflict, OutboxStore


class DeferredQueue:
    def __init__(self):
        self.jobs = []
        self.full = False

    def enqueue(self, fn, *, description):
        if self.full:
            raise OverflowError("queue full")
        self.jobs.append(fn)

    def run(self):
        return self.jobs.pop(0)()


@pytest.fixture
def env(monkeypatch, tmp_path):
    context = AccountContext("seller", str(tmp_path / "source"), "epoch")
    token = GuiSessionToken(context.self_ali_id, context.data_dir, context.epoch, "window")
    queue = DeferredQueue()
    service = module.OutboxService(OutboxStore(tmp_path / "crm.sqlite"), queue)
    state = SimpleNamespace(
        context=context, token=token, queue=queue, service=service, calls=[], now=1000.0,
        send_job=object(),
        frame=frame_from_image(np.zeros((4, 5, 3), dtype=np.uint8)),
    )
    state.source = {
        "valid": True, "origin": {"seller": "seller", "data_dir": os.path.normcase(context.data_dir), "source_path": "source"},
        "checked_at": 999.0, "messages": [{"id": "message:old"}],
    }
    monkeypatch.setattr(module, "RECONCILE_INTERVAL", 3600)
    monkeypatch.setattr(module, "get_account_context", lambda: state.context)
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: state.now, monotonic=time.monotonic))
    monkeypatch.setattr(module.gui_session, "capture_gui_session", lambda epoch: state.token)

    def guard(token, fn):
        state.calls.append("guard")
        if token != state.token:
            return False, "session expired"
        try:
            return fn()
        except Exception as exc:
            return False, str(exc)

    monkeypatch.setattr(module.gui_session, "run_guarded", guard)
    monkeypatch.setattr(module.runner, "goto_contact", lambda target: (state.calls.append("goto") is None, "located"))
    monkeypatch.setattr(module.runner, "capture_client_frame", lambda: state.calls.append("frame") or state.frame)
    monkeypatch.setattr(module.runner, "chat_input", lambda text: (state.calls.append("input") is None, "filled"))
    monkeypatch.setattr(module.runner, "submit_send", lambda: state.calls.append("click") or state.send_job)
    monkeypatch.setattr(module.runner, "wait_send", lambda job: (True, "clicked"))
    monkeypatch.setattr(source_messages, "read_source_messages", lambda context, contact: state.calls.append("baseline") or state.source)
    yield state
    service.stop()


def submit(env, **changes):
    return env.service.submit(env.context, **{
        "conversation_id": 1, "contact_ali_id": "buyer", "login_id": "buyer-login",
        "content": " exact\r\ntext ", "action": "send", "idempotency_key": "key", **changes,
    })


def prepared(env, **changes):
    task = submit(env, **changes)
    assert env.queue.run()[0]
    return env.service.get(env.context, task["id"])


def confirm(env, task):
    return env.service.confirm(
        env.context, task["id"], screenshot_id=task["screenshot_id"], version=task["version"],
        expected_epoch=env.context.epoch,
    )


def race(*calls):
    barrier = Barrier(len(calls))

    def run(call):
        barrier.wait(5)
        try:
            return call()
        except AppError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(run, call) for call in calls]
        return [future.result(10) for future in futures]


def test_observers_do_not_initialize_store_or_queue(env):
    assert env.service.get(env.context, "missing") is None
    assert env.service.list(env.context, 1) == []
    assert env.service.reconcile_once() == []
    assert not env.service.store.database_path.exists()
    assert not env.service._screenshot_dir.exists()
    assert env.queue.jobs == []


def test_attempt_context_retains_origin_across_confirmation_and_changes_on_explicit_retry(env, monkeypatch):
    from backend.app.shared.utils.log_context import bind_log_context, capture_log_context

    contexts = []
    monkeypatch.setattr(module.runner, "goto_contact", lambda target: contexts.append(capture_log_context()) or (True, "located"))
    monkeypatch.setattr(module.runner, "chat_input", lambda text: contexts.append(capture_log_context()) or (True, "filled"))
    with bind_log_context(request_id="submit-request"):
        task = prepared(env, action="test")
    assert task["origin_request_id"] == "submit-request"
    assert task["origin_account_epoch"] == env.context.epoch
    with bind_log_context(request_id="confirm-request", account_epoch="unrelated"):
        confirm(env, task)
        env.queue.run()
    assert all(c["request_id"] == "submit-request" and c["account_epoch"] == env.context.epoch for c in contexts)
    assert all(c["outbox_id"] == task["id"] and c["attempt"] == 1 for c in contexts)

    with bind_log_context(request_id="second-submit"):
        failed = submit(env, idempotency_key="retry-key")
    failed = env.service.store.transition(failed["id"], "queued", failed["version"],
                                         **env.service._scope(env.context), status="failed")
    with bind_log_context(request_id="retry-request"):
        retry = env.service.retry(env.context, failed["id"], version=failed["version"])
    while env.queue.jobs:
        env.queue.run()
    assert contexts[-1]["request_id"] == "retry-request"
    assert contexts[-1]["origin_request_id"] == retry["origin_request_id"] == "second-submit"
    assert contexts[-1]["attempt"] == 2


def test_navigation_only_then_explicit_confirmation_and_durable_send_barrier(env, monkeypatch):
    task = prepared(env)
    assert task["status"] == "awaiting_confirmation"
    assert task["may_have_sent"] is False
    assert env.calls == ["guard", "goto", "frame"]
    assert env.service.get_screenshot(env.context, task["id"], task["screenshot_id"]) == env.frame.png_bytes
    assert (env.service._screenshot_dir / f"{task['screenshot_id']}.png").is_file()

    def input_text(text):
        running = env.service.get(env.context, task["id"])
        assert running["status"] == "running" and running["phase"] == "input"
        assert not running["may_have_sent"]
        assert running["baseline"] == {
            "origin": env.source["origin"], "ids": ["message:old"],
            "checked_at": 999.0, "send_started_at": env.now, "sent_at": env.now,
        }
        assert text == " exact\r\ntext "
        env.calls.append("input")
        return True, "filled"

    def click():
        running = env.service.get(env.context, task["id"])
        assert running["phase"] == "click" and running["may_have_sent"] is True
        env.calls.append("click")
        return env.send_job

    monkeypatch.setattr(module.runner, "chat_input", input_text)
    monkeypatch.setattr(module.runner, "submit_send", click)
    queued = confirm(env, task)
    assert queued["status"] == "queued_send"
    assert env.calls == ["guard", "goto", "frame"]
    assert env.queue.run()[0]
    result = env.service.get(env.context, task["id"])
    assert result["status"] == "verifying"
    assert result["may_have_sent"] is True
    assert env.calls == ["guard", "goto", "frame", "baseline", "guard", "frame", "input", "click"]
    assert result["screenshot_id"] is None


def test_concurrent_idempotency_enqueues_once_and_payload_conflicts(env):
    results = race(*(lambda: submit(env) for _ in range(8)))
    assert len({record["id"] for record in results}) == 1
    assert len(env.queue.jobs) == 1
    with pytest.raises(OutboxConflict):
        submit(env, content="different")
    env.queue.run()
    assert submit(env)["status"] == "awaiting_confirmation"
    assert env.queue.jobs == []
    assert env.calls.count("goto") == 1


def test_concurrent_confirmation_enqueues_once(env):
    task = prepared(env)
    results = race(*(lambda: confirm(env, task) for _ in range(6)))
    assert sum(isinstance(result, OutboxConflict) for result in results) == 5
    assert len(env.queue.jobs) == 1
    env.queue.run()
    assert env.calls.count("input") == env.calls.count("click") == 1


@pytest.mark.parametrize("state", ["queued", "awaiting_confirmation", "queued_send"])
def test_cancellation_leaves_queued_callback_noop(env, state):
    task = submit(env)
    if state != "queued":
        env.queue.run()
        task = env.service.get(env.context, task["id"])
    if state == "queued_send":
        task = confirm(env, task)
    result = env.service.cancel(env.context, task["id"], version=task["version"])
    assert result["status"] == "cancelled"
    while env.queue.jobs:
        assert not env.queue.run()[0]
    assert "input" not in env.calls and "click" not in env.calls


def test_confirmation_cancel_race_cannot_send_cancelled_task(env):
    task = prepared(env)
    results = race(lambda: confirm(env, task), lambda: env.service.cancel(env.context, task["id"], version=task["version"]))
    assert sum(isinstance(result, OutboxConflict) for result in results) == 1
    result = env.service.get(env.context, task["id"])
    if result["status"] == "queued_send":
        env.service.cancel(env.context, task["id"])
    while env.queue.jobs:
        env.queue.run()
    assert "input" not in env.calls


def test_running_cannot_be_cancelled_and_has_no_second_send(env, monkeypatch):
    task = prepared(env)
    confirm(env, task)
    entered, release = Event(), Event()

    def input_text(text):
        entered.set()
        assert release.wait(5)
        return True, "filled"

    monkeypatch.setattr(module.runner, "chat_input", input_text)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(env.queue.run)
        try:
            assert entered.wait(5)
            with pytest.raises(OutboxConflict):
                env.service.cancel(env.context, task["id"])
            with pytest.raises(OutboxConflict):
                confirm(env, task)
        finally:
            release.set()
        assert future.result(5)[0]
    assert env.calls.count("click") == 1


@pytest.mark.parametrize("kind", ["version", "screenshot", "epoch", "window"])
def test_stale_confirmation_never_enqueues_input(env, kind):
    task = prepared(env)
    if kind == "version":
        task["version"] -= 1
    elif kind == "screenshot":
        task["screenshot_id"] = "0" * 32
    elif kind == "epoch":
        env.context = replace(env.context, epoch="other")
        env.token = replace(env.token, epoch="other")
    else:
        env.token = replace(env.token, window_generation="other")
    with pytest.raises(OutboxConflict):
        confirm(env, task)
    assert env.queue.jobs == []
    assert "input" not in env.calls


@pytest.mark.parametrize("expired", [False, True])
def test_changed_or_expired_frame_requires_another_confirmation_without_input(env, expired):
    task = prepared(env)
    confirm(env, task)
    if expired:
        env.now += module.SCREENSHOT_TTL
    else:
        env.frame = frame_from_image(np.ones((4, 5, 3), dtype=np.uint8))
    ok, reason = env.queue.run()
    assert ok, reason
    refreshed = env.service.get(env.context, task["id"])
    assert refreshed["status"] == "awaiting_confirmation"
    assert refreshed["screenshot_id"] != task["screenshot_id"]
    assert refreshed["version"] > task["version"]
    assert refreshed["attempt"] == task["attempt"]
    assert env.calls[-3:] == ["baseline", "guard", "frame"]
    assert "input" not in env.calls and "click" not in env.calls
    with pytest.raises(AppError):
        env.service.get_screenshot(env.context, task["id"], task["screenshot_id"])
    with pytest.raises(OutboxConflict):
        confirm(env, task)
    confirm(env, refreshed)
    env.queue.run()
    assert env.calls.count("input") == env.calls.count("click") == 1


def test_cancel_during_source_read_wins_before_any_input(env, monkeypatch):
    task = prepared(env)
    confirm(env, task)
    entered, release = Event(), Event()

    def baseline(context, contact):
        entered.set()
        assert release.wait(5)
        return env.source

    monkeypatch.setattr(source_messages, "read_source_messages", baseline)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(env.queue.run)
        try:
            assert entered.wait(5)
            assert env.service.cancel(env.context, task["id"])["status"] == "cancelled"
        finally:
            release.set()
        assert not future.result(5)[0]
    assert "input" not in env.calls and "click" not in env.calls


def test_navigation_capture_failure_is_unknown_and_not_retryable(env, monkeypatch):
    task = submit(env)

    def failed_capture():
        raise RuntimeError("screenshot unavailable")

    monkeypatch.setattr(module.runner, "capture_client_frame", failed_capture)
    assert not env.queue.run()[0]
    result = env.service.get(env.context, task["id"])
    assert result["status"] == "unknown" and not result["may_have_sent"]
    with pytest.raises(OutboxConflict):
        env.service.retry(env.context, task["id"])


@pytest.mark.parametrize("kind", ["other_seller", "other_directory", "path", "uuid", "expired"])
def test_screenshot_is_scope_bound_current_uuid_and_unexpired(env, kind):
    task = prepared(env)
    context, screenshot_id = env.context, task["screenshot_id"]
    if kind == "other_seller":
        context = replace(context, self_ali_id="another")
    elif kind == "other_directory":
        context = replace(context, data_dir=context.data_dir + "other")
    elif kind == "path":
        screenshot_id = "../crm.sqlite"
    elif kind == "uuid":
        screenshot_id = "0" * 32
    else:
        env.now += 120
    with pytest.raises(AppError) as error:
        env.service.get_screenshot(context, task["id"], screenshot_id)
    assert error.value.status_code == 404


@pytest.mark.parametrize("at_confirm", [False, True])
def test_queue_full_returns_persisted_safe_failure_and_duplicate_never_requeues(env, at_confirm):
    if at_confirm:
        task = prepared(env)
        env.queue.full = True
        result = confirm(env, task)
    else:
        env.queue.full = True
        result = submit(env)
    assert result["status"] == "failed" and not result["may_have_sent"]
    assert result["screenshot_id"] is None
    env.queue.full = False
    assert submit(env) == result
    assert env.queue.jobs == []
    assert "input" not in env.calls


def test_persistence_precedes_queue_acceptance(env, monkeypatch):
    original_enqueue = env.queue.enqueue

    def enqueue(fn, *, description):
        records = env.service.list(env.context, 1)
        assert len(records) == 1 and records[0]["status"] == "queued"
        return original_enqueue(fn, description=description)

    monkeypatch.setattr(env.queue, "enqueue", enqueue)
    task = submit(env)
    assert env.service.get(env.context, task["id"]) == task


def test_stale_queue_callback_cannot_run_new_retry_attempt(env):
    task = submit(env)
    stale = env.queue.jobs.pop()
    failed = env.service._move(task, "failed", reason="safe admission failure")
    env.service.retry(env.context, task["id"], version=failed["version"])
    assert not stale()[0]
    assert env.calls == []
    assert env.queue.run()[0]
    assert env.calls.count("goto") == 1


def test_initial_guard_capture_failure_retains_id_for_retry(env, monkeypatch):
    def reject(epoch):
        raise AppError("Client is not connected; connect first.", status_code=503)

    monkeypatch.setattr(module.gui_session, "capture_gui_session", reject)
    task = submit(env)
    assert task["status"] == "failed" and not task["may_have_sent"]
    assert submit(env)["id"] == task["id"]
    assert env.queue.jobs == []


@pytest.mark.parametrize("stage", ["navigation_guard", "send_guard", "baseline", "capture"])
def test_preinput_failures_are_safe_and_retry_requires_fresh_token(env, monkeypatch, stage):
    task = submit(env)
    if stage != "navigation_guard":
        env.queue.run()
        task = env.service.get(env.context, task["id"])
        confirm(env, task)
    if stage in ("navigation_guard", "send_guard"):
        env.token = replace(env.token, window_generation="fresh")
    elif stage == "baseline":
        env.source["valid"] = False
    else:
        monkeypatch.setattr(module.runner, "capture_client_frame", lambda: (_ for _ in ()).throw(RuntimeError("capture failed")))
    assert not env.queue.run()[0]
    failed = env.service.get(env.context, task["id"])
    assert failed["status"] == "failed" and not failed["may_have_sent"]
    assert failed["screenshot_id"] is None
    assert "input" not in env.calls and "click" not in env.calls
    env.token = replace(env.token, window_generation="new-attempt")
    retried = env.service.retry(env.context, task["id"], version=failed["version"])
    assert retried["id"] == task["id"] and retried["attempt"] == 2
    assert env.service._attempts[task["id"]].token == env.token


@pytest.mark.parametrize("stage,raises", [(stage, raises) for stage in ("navigation", "input", "click") for raises in (False, True)])
def test_gui_failure_is_unknown_without_automatic_retry(env, monkeypatch, stage, raises):
    def fail(*args):
        if raises:
            raise RuntimeError("uncertain failure")
        return False, "uncertain failure"

    if stage == "navigation":
        monkeypatch.setattr(module.runner, "goto_contact", fail)
        task = submit(env)
    else:
        task = prepared(env)
        confirm(env, task)
        monkeypatch.setattr(module.runner, "chat_input" if stage == "input" else "wait_send", fail)
    assert not env.queue.run()[0]
    result = env.service.get(env.context, task["id"])
    assert result["status"] == "unknown"
    assert result["may_have_sent"] is (stage == "click")
    assert result["evidence"]["detail"] == "uncertain failure"
    with pytest.raises(OutboxConflict):
        env.service.retry(env.context, task["id"])
    assert env.queue.jobs == []


def test_test_action_fills_without_click_or_reconciliation(env):
    task = prepared(env, action="test")
    confirm(env, task)
    env.queue.run()
    result = env.service.get(env.context, task["id"])
    assert result["status"] == "filled" and not result["may_have_sent"]
    assert "click" not in env.calls
    assert env.service.reconcile_once() == []


def test_reconciliation_claims_local_evidence_once_and_never_calls_gui(env, monkeypatch):
    first = prepared(env)
    confirm(env, first)
    env.queue.run()
    second = prepared(env, idempotency_key="second")
    confirm(env, second)
    env.queue.run()
    prior_calls = list(env.calls)
    proof = {"status": "observed", "reason": "local_message_observed", "matched_message_id": "message:new", "evidence": {"kind": "local", "source_revision": 2}}

    def matches(record, snapshot, pending):
        assert {record["id"] for record in pending} == {first["id"], second["id"]}
        return proof

    monkeypatch.setattr(send_verification, "match_outbox", matches)
    observed = env.service.reconcile_once()
    assert len(observed) == 1
    assert observed[0]["status"] == "observed"
    assert env.calls == prior_calls + ["baseline"]
    assert sorted(record["status"] for record in env.service.list(env.context, 1)) == ["observed", "verifying"]


@pytest.mark.parametrize("changed", ["seller", "directory", "during_read"])
def test_reconciliation_never_uses_unselected_scope(env, monkeypatch, changed):
    task = prepared(env)
    confirm(env, task)
    env.queue.run()
    original_context = env.context
    calls = list(env.calls)

    def matches(record, snapshot, pending):
        return {"status": "observed", "reason": "local_message_observed", "matched_message_id": "message:new", "evidence": {"kind": "local"}}

    monkeypatch.setattr(send_verification, "match_outbox", matches)
    if changed == "seller":
        env.context = replace(env.context, self_ali_id="different")
    elif changed == "directory":
        env.context = replace(env.context, data_dir=env.context.data_dir + "other")
    else:
        def switch(context, contact):
            env.context = replace(env.context, epoch="switched")
            return env.source

        monkeypatch.setattr(source_messages, "read_source_messages", switch)
    assert env.service.reconcile_once() == []
    assert env.service.get(original_context, task["id"])["status"] == "verifying"
    assert env.calls == calls


def test_unknown_postclick_can_be_observed_but_test_or_preclick_cannot(env, monkeypatch):
    task = prepared(env)
    confirm(env, task)
    monkeypatch.setattr(module.runner, "wait_send", lambda job: (False, "uncertain"))
    env.queue.run()
    assert env.service.get(env.context, task["id"])["status"] == "unknown"
    test = prepared(env, action="test", idempotency_key="test")
    confirm(env, test)
    monkeypatch.setattr(module.runner, "chat_input", lambda text: (False, "uncertain"))
    env.queue.run()

    def matches(record, snapshot, pending):
        assert record["id"] == task["id"]
        return {"status": "observed", "reason": "local_message_observed", "matched_message_id": "message:late", "evidence": {"kind": "local"}}

    monkeypatch.setattr(send_verification, "match_outbox", matches)
    assert env.service.reconcile_once()[0]["status"] == "observed"
    assert env.service.get(env.context, test["id"])["status"] == "unknown"


@pytest.mark.parametrize("ambiguous", [False, True])
def test_real_matching_observes_unique_message_and_rejects_ambiguous_attempts(env, ambiguous):
    task = prepared(env)
    confirm(env, task)
    env.queue.run()
    if ambiguous:
        other = prepared(env, idempotency_key="other")
        confirm(env, other)
        env.queue.run()
    env.source["checked_at"] = env.now + 1
    env.source["messages"] = [{
        "id": "message:new", "sender_id": "seller@icbu", "cid": "seller-buyer",
        "created_at": env.now, "type": 0, "text": task["content"],
        "is_system": False, "is_auto_reply": False, "extension_valid": True,
    }]
    observed = env.service.reconcile_once()
    if ambiguous:
        assert observed == []
        assert {record["status"] for record in env.service.list(env.context, 1)} == {"unknown"}
        assert {record["reason"] for record in env.service.list(env.context, 1)} == {"ambiguous_send_attempts"}
    else:
        assert len(observed) == 1 and observed[0]["status"] == "observed"
        assert observed[0]["evidence"]["kind"] == "local_source_message"
        assert env.service.reconcile_once() == []


def test_real_matching_can_observe_late_ingestion_after_verification_timeout(env):
    task = prepared(env)
    confirm(env, task)
    env.queue.run()
    env.source["messages"] = []
    env.source["checked_at"] = env.now + 121
    assert env.service.reconcile_once() == []
    assert env.service.get(env.context, task["id"])["status"] == "unknown"
    env.source["messages"] = [{
        "id": "message:late", "sender_id": "seller@icbu", "cid": "seller-buyer",
        "created_at": env.now + 1, "type": 0, "text": task["content"],
        "is_system": False, "is_auto_reply": False, "extension_valid": True,
    }]
    assert env.service.reconcile_once()[0]["status"] == "observed"
    assert env.calls.count("click") == 1


def test_new_possible_send_during_source_read_is_included_in_ambiguity_check(env, monkeypatch):
    task = prepared(env)
    confirm(env, task)
    env.queue.run()
    other = prepared(env, idempotency_key="other")
    queued = confirm(env, other)

    def read(context, contact):
        running = env.service._move(queued, "running", baseline={
            "origin": env.source["origin"], "ids": [], "checked_at": 999.0, "sent_at": env.now,
        })
        env.service._update(running, may_have_sent=True, phase="click")
        return {**env.source, "checked_at": env.now + 1, "messages": [{
            "id": "message:new", "sender_id": "seller@icbu", "cid": "seller-buyer",
            "created_at": env.now, "type": 0, "text": task["content"],
            "is_system": False, "is_auto_reply": False, "extension_valid": True,
        }]}

    monkeypatch.setattr(source_messages, "read_source_messages", read)
    assert env.service.reconcile_once() == []
    result = env.service.get(env.context, task["id"])
    assert result["status"] == "unknown" and result["reason"] == "ambiguous_send_attempts"


@pytest.mark.parametrize("state", ["queued", "navigating", "awaiting_confirmation", "queued_send", "running", "click", "verifying"])
def test_restart_never_replays_and_preserves_pre_post_click_uncertainty(env, state):
    task, _ = env.service.store.create(
        seller=env.context.self_ali_id, data_dir=env.context.data_dir, conversation_id=1,
        contact_ali_id="buyer", login_id="buyer-login", content="text", action="send", idempotency_key="restart",
    )
    for target in ("navigating", "awaiting_confirmation", "queued_send", "running", "click", "verifying"):
        if task["phase"] == state:
            break
        if target == "click":
            task = env.service._update(task, phase="click", may_have_sent=True)
        else:
            changes = {"screenshot_id": "0" * 32, "screenshot_at": 1000, "screenshot_digest": "digest"} if target == "awaiting_confirmation" else {}
            task = env.service._move(task, target, **changes)
    env.service.start()
    recovered = env.service.get(env.context, task["id"])
    assert recovered["status"] == ("unknown" if state in ("navigating", "running", "click", "verifying") else "failed")
    assert recovered["may_have_sent"] is (state in ("click", "verifying"))
    assert recovered["screenshot_id"] is None
    assert env.queue.jobs == [] and env.calls == []
    if recovered["status"] == "failed":
        assert env.service.retry(env.context, task["id"])["attempt"] == 2
        assert len(env.queue.jobs) == 1


def test_shutdown_invalidates_pending_jobs_and_rejects_new_work(env):
    first = prepared(env)
    confirm(env, first)
    second = submit(env, idempotency_key="second")
    env.service.stop()
    while env.queue.jobs:
        assert not env.queue.run()[0]
    assert env.service.get(env.context, first["id"])["status"] == "failed"
    assert env.service.get(env.context, second["id"])["status"] == "failed"
    assert "input" not in env.calls
    with pytest.raises(AppError):
        submit(env, idempotency_key="late")
    assert len(env.service.list(env.context, 1)) == 2


def test_shutdown_timeout_marks_running_unknown_and_prevents_later_click(env, monkeypatch):
    task = prepared(env)
    confirm(env, task)
    entered, release = Event(), Event()

    def input_text(text):
        entered.set()
        assert release.wait(5)
        return True, "filled"

    monkeypatch.setattr(module.runner, "chat_input", input_text)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(env.queue.run)
        try:
            assert entered.wait(5)
            env.service.stop(timeout=0.01)
            assert env.service.get(env.context, task["id"])["status"] == "unknown"
        finally:
            release.set()
        assert not future.result(5)[0]
    assert "click" not in env.calls


@pytest.mark.parametrize("pause", ["during_commit", "after_commit", "before_post"])
def test_stop_cannot_return_before_send_admission_boundary_exits(env, monkeypatch, pause):
    task = prepared(env)
    confirm(env, task)
    paused, resume, finish_job, stop_returned = Event(), Event(), Event(), Event()
    update = env.service.store.update_fields

    def commit(*args, **kwargs):
        if kwargs.get("may_have_sent") and pause == "during_commit":
            paused.set()
            assert resume.wait(5)
        record = update(*args, **kwargs)
        if kwargs.get("may_have_sent") and pause == "after_commit":
            paused.set()
            assert resume.wait(5)
        return record

    def post():
        if pause == "before_post":
            paused.set()
            assert resume.wait(5)
        assert not stop_returned.is_set(), "New send posted after stop returned"
        env.calls.append("click")
        return env.send_job

    def wait_send(job):
        assert job is env.send_job
        assert finish_job.wait(5)
        return True, "already submitted job finished"

    def stop():
        env.service.stop(timeout=0.01)
        stop_returned.set()

    monkeypatch.setattr(env.service.store, "update_fields", commit)
    monkeypatch.setattr(module.runner, "submit_send", post)
    monkeypatch.setattr(module.runner, "wait_send", wait_send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(env.queue.run)
        try:
            assert paused.wait(5)
            stopping = pool.submit(stop)
            assert env.service._stopping.wait(5)
            assert not stop_returned.wait(0.1)
            assert "click" not in env.calls
            resume.set()
            stopping.result(5)
            assert stop_returned.is_set()
            result = env.service.get(env.context, task["id"])
            assert result["status"] == "unknown" and result["may_have_sent"]
            assert env.calls.count("click") == (1 if pause == "before_post" else 0)
            if pause == "before_post":
                assert not worker.done(), "stop must not wait for the submitted job"
            calls_at_stop = list(env.calls)
            finish_job.set()
            assert not worker.result(5)[0]
            assert env.calls == calls_at_stop
        finally:
            resume.set()
            finish_job.set()


def test_stop_after_post_returns_during_wait_without_another_post(env, monkeypatch):
    task = prepared(env)
    confirm(env, task)
    waiting, finish_job = Event(), Event()

    def wait_send(job):
        assert job is env.send_job
        waiting.set()
        assert finish_job.wait(5)
        return True, "completed"

    monkeypatch.setattr(module.runner, "wait_send", wait_send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(env.queue.run)
        try:
            assert waiting.wait(5)
            assert env.calls.count("click") == 1
            env.service.stop(timeout=0.01)
            assert not worker.done()
            result = env.service.get(env.context, task["id"])
            assert result["status"] == "unknown" and result["may_have_sent"]
            finish_job.set()
            assert not worker.result(5)[0]
        finally:
            finish_job.set()
    assert env.calls.count("click") == 1
    assert env.queue.jobs == []


def test_send_post_exception_is_unknown_and_never_retried(env, monkeypatch):
    task = prepared(env)
    confirm(env, task)

    def post():
        record = env.service.get(env.context, task["id"])
        assert record["may_have_sent"] and record["phase"] == "click"
        env.calls.append("post_failed")
        raise RuntimeError("native post outcome unavailable")

    def wait_send(job):
        pytest.fail("Cannot wait without a returned job")

    monkeypatch.setattr(module.runner, "submit_send", post)
    monkeypatch.setattr(module.runner, "wait_send", wait_send)
    assert not env.queue.run()[0]
    record = env.service.get(env.context, task["id"])
    assert record["status"] == "unknown" and record["may_have_sent"]
    assert env.calls.count("post_failed") == 1
    assert env.queue.jobs == []
    with pytest.raises(OutboxConflict):
        env.service.retry(env.context, task["id"])


@pytest.mark.parametrize("restart", [False, True])
def test_terminal_writes_retry_after_repeated_db_locks_without_replaying_gui(env, monkeypatch, restart):
    task = prepared(env)
    confirm(env, task)
    transition = env.service.store.transition
    failures = []

    def locked(*args, **kwargs):
        if env.calls.count("click") and kwargs.get("status") in ("verifying", "unknown") and len(failures) < 3:
            failures.append(kwargs["status"])
            raise sqlite3.OperationalError("database is locked")
        return transition(*args, **kwargs)

    monkeypatch.setattr(env.service.store, "transition", locked)
    ok, reason = env.queue.run()
    assert not ok and "locked" in reason
    assert failures == ["verifying", "unknown"]
    record = env.service.get(env.context, task["id"])
    assert record["status"] == "running" and record["phase"] == "click" and record["may_have_sent"]
    pending = env.service._pending_terminal_writes[task["id"]]
    assert pending == {name: record[name] for name in (
        "id", "seller", "data_dir", "attempt", "version", "status", "phase",
    )} | {"reason": "database is locked"}
    assert env.service._active == {} and env.service._attempts == {}
    assert record["screenshot_id"] == task["screenshot_id"]
    env.source["checked_at"] = env.now + 1
    env.source["messages"] = [{
        "id": "message:new", "sender_id": "seller@icbu", "cid": "seller-buyer",
        "created_at": env.now, "type": 0, "text": task["content"],
        "is_system": False, "is_auto_reply": False, "extension_valid": True,
    }]
    gui_calls = list(env.calls)
    if restart:
        env.service.stop()
        assert len(failures) == 3
        replacement = module.OutboxService(OutboxStore(env.service.store.database_path), DeferredQueue())
        try:
            replacement.start()
            recovered = replacement.get(env.context, task["id"])
            assert recovered["status"] == "unknown" and recovered["may_have_sent"]
            assert replacement.reconcile_once()[0]["status"] == "observed"
            assert replacement._queue.jobs == []
        finally:
            replacement.stop()
    else:
        assert env.service.reconcile_once() == []
        assert failures == ["verifying", "unknown", "unknown"]
        assert env.service._pending_terminal_writes[task["id"]] == pending
        observed = env.service.reconcile_once()
        assert len(observed) == 1 and observed[0]["status"] == "observed"
        assert env.service._pending_terminal_writes == {}
        assert env.service.reconcile_once() == []
    assert env.calls == gui_calls + ["baseline"]
    assert env.calls.count("input") == env.calls.count("click") == 1
    assert env.queue.jobs == []


def test_completed_snapshot_survives_failure_to_read_database_after_click(env, monkeypatch):
    task = prepared(env)
    confirm(env, task)
    transition, get = env.service.store.transition, env.service.store.get

    def locked_transition(*args, **kwargs):
        if kwargs.get("status") == "verifying":
            raise sqlite3.OperationalError("database is locked")
        return transition(*args, **kwargs)

    def locked_get(*args, **kwargs):
        if env.calls.count("click"):
            raise sqlite3.OperationalError("database read is locked")
        return get(*args, **kwargs)

    monkeypatch.setattr(env.service.store, "transition", locked_transition)
    monkeypatch.setattr(env.service.store, "get", locked_get)
    assert not env.queue.run()[0]
    pending = env.service._pending_terminal_writes[task["id"]]
    assert pending["phase"] == "click" and not env.service._active
    monkeypatch.setattr(env.service.store, "get", get)
    env.service.reconcile_once()
    assert env.service.get(env.context, task["id"])["status"] == "unknown"
    assert env.service._pending_terminal_writes == {}
    assert env.calls.count("click") == 1


@pytest.mark.parametrize("superseded", ["version", "attempt"])
def test_terminal_compensation_cannot_overwrite_newer_version_or_attempt(env, monkeypatch, superseded):
    task = prepared(env) if superseded == "version" else submit(env)
    if superseded == "version":
        confirm(env, task)
        monkeypatch.setattr(module.runner, "chat_input", lambda text: (False, "input uncertain"))
    else:
        env.token = replace(env.token, window_generation="revoked")
    transition = env.service.store.transition

    def locked(*args, **kwargs):
        if kwargs.get("status") in ("unknown", "failed"):
            raise sqlite3.OperationalError("database is locked")
        return transition(*args, **kwargs)

    monkeypatch.setattr(env.service.store, "transition", locked)
    assert not env.queue.run()[0]
    assert task["id"] in env.service._pending_terminal_writes
    monkeypatch.setattr(env.service.store, "transition", transition)
    record = env.service.get(env.context, task["id"])
    if superseded == "version":
        newer = env.service._update(record, reason="newer writer")
    else:
        failed = env.service._move(record, "failed")
        newer = env.service.store.retry(task["id"], failed["version"], **env.service._scope(env.context))
    env.service.reconcile_once()
    assert env.service.get(env.context, task["id"]) == newer
    assert env.service._pending_terminal_writes == {}
    assert "click" not in env.calls


@pytest.mark.parametrize("stop", [False, True])
def test_active_click_is_not_compensated_before_callback_completes(env, monkeypatch, stop):
    task = prepared(env)
    confirm(env, task)
    entered, release = Event(), Event()
    transition = env.service.store.transition

    def wait_send(job):
        assert job is env.send_job
        entered.set()
        assert release.wait(5)
        return True, "clicked"

    def locked(*args, **kwargs):
        if kwargs.get("status") in ("verifying", "unknown"):
            raise sqlite3.OperationalError("database is locked")
        return transition(*args, **kwargs)

    monkeypatch.setattr(module.runner, "wait_send", wait_send)
    monkeypatch.setattr(env.service.store, "transition", locked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(env.queue.run)
        try:
            assert entered.wait(5)
            assert env.service.reconcile_once() == []
            if stop:
                env.service.stop(timeout=0.01)
                assert not env.service._thread.is_alive()
            assert env.service.get(env.context, task["id"])["status"] == "running"
            assert env.service._active[task["id"]]["phase"] == "click"
            assert env.service._pending_terminal_writes == {}
        finally:
            release.set()
        assert not future.result(5)[0]
    assert env.service._active == {}
    assert env.service._pending_terminal_writes[task["id"]]["phase"] == "click"
    monkeypatch.setattr(env.service.store, "transition", transition)
    if stop:
        env.service.stop()
    else:
        env.service.reconcile_once()
    assert env.service.get(env.context, task["id"])["status"] == "unknown"
    assert env.service._pending_terminal_writes == {}
    assert env.calls.count("click") == 1


@pytest.mark.parametrize("operation", ["transition", "get"])
def test_shutdown_db_errors_still_signal_join_and_never_resume_pending_jobs(env, monkeypatch, operation):
    first = prepared(env)
    second = submit(env, idempotency_key="second")
    original = getattr(env.service.store, operation)

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(env.service.store, operation, locked)
    env.service.stop()
    assert env.service._stopping.is_set()
    assert not env.service._thread.is_alive()
    assert env.service._attempts == {}
    with pytest.raises(AppError):
        submit(env, idempotency_key="late")
    monkeypatch.setattr(env.service.store, operation, original)
    while env.queue.jobs:
        assert not env.queue.run()[0]
    assert "input" not in env.calls and "click" not in env.calls
    replacement = module.OutboxService(OutboxStore(env.service.store.database_path), DeferredQueue())
    try:
        replacement.start()
        for record in (first, second):
            assert replacement.get(env.context, record["id"])["status"] == "failed"
        assert replacement._queue.jobs == []
    finally:
        replacement.stop()
