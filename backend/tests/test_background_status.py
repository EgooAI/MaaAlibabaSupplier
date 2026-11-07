"""Status observations preserve business evidence and workspace ownership."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from unittest.mock import Mock
import json
import sqlite3

from backend.app import updater
from backend.app.api.routers import status
from backend.app.shared.agent.experiment_runs import RunRegistry
from backend.app.shared.backend.account_context import AccountContext
from backend.app.shared.crm.outbox_store import OutboxStore
from backend.app.shared.utils.log_context import bind_log_context
from backend.app.task_queue import TaskQueue
from backend.tests.test_outbox_store import create, advance, move


def test_background_snapshot_is_passive_and_does_not_create_work_or_storage(monkeypatch, tmp_path):
    from backend.app.background import get_background_runtime

    context = AccountContext("seller", str(tmp_path / "source"), "epoch")
    store = OutboxStore(tmp_path / "absent.sqlite")
    monkeypatch.setattr(status, "OutboxStore", lambda: store)
    monkeypatch.setattr(updater, "_instance", None)
    before = get_background_runtime().observation()
    for _ in range(3):
        observed = status._background_snapshot(context)
        assert observed["queueTasks"] == observed["experiments"] == []
        assert observed["outbox"] == {"counts": {}, "recent": []}
        assert observed["update"] is None and observed["errors"] == {}
    assert not store.database_path.exists()
    assert not TaskQueue._instances
    assert updater._instance is None
    assert get_background_runtime().observation()["resources"] == before["resources"]


def test_queue_observation_keeps_running_pending_failed_and_filters_old_epochs(monkeypatch, tmp_path):
    context = AccountContext("seller", str(tmp_path), "current")
    queue = TaskQueue("translation")
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        assert release.wait(5)
        return False, "partial translation failure"

    with bind_log_context(account_epoch="old"):
        queue.enqueue(lambda: (True, "old result"), description="private old task")
    with bind_log_context(account_epoch=context.epoch):
        running = queue.enqueue(blocked, description="Translation texts")
        pending = queue.enqueue(lambda: (True, "done"), description="Translation texts")
    try:
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=1) as reader:
            snapshot = reader.submit(status._background_snapshot, context).result(2)
        tasks = {task["id"]: task for task in snapshot["queueTasks"]}
        assert set(tasks) == {running.task_id, pending.task_id}
        assert tasks[running.task_id]["status"] == "running"
        assert tasks[pending.task_id]["status"] == "queued"
        assert {task["queue"] for task in tasks.values()} == {"translation"}
        assert "private old task" not in json.dumps(snapshot)
    finally:
        release.set()
        queue.shutdown()
    # A stopped queue handle can still serve its bounded history while registered.
    monkeypatch.setitem(TaskQueue._instances, "translation", queue)
    tasks = {task["id"]: task for task in status._background_snapshot(context)["queueTasks"]}
    assert tasks[running.task_id]["status"] == "failed"
    assert tasks[running.task_id]["resultSuccess"] is False
    assert tasks[pending.task_id]["status"] == "succeeded"
    assert status._background_snapshot(replace(context, epoch="next"))["queueTasks"] == []


def test_queue_limit_keeps_running_work_and_counts_all_scoped_requests(tmp_path):
    context = AccountContext("seller", str(tmp_path), "epoch")
    queue = TaskQueue()
    entered, release = Event(), Event()

    def block():
        entered.set()
        assert release.wait(5)
        return True, "done"

    try:
        with bind_log_context(account_epoch=context.epoch):
            running = queue.enqueue(block, description="diagnostic")
            assert entered.wait(2)
            for _ in range(101):
                queue.enqueue(lambda: (True, "done"), description="diagnostic")
            for _ in range(101):
                queue.enqueue(lambda: (True, "done"), description="Outbox excluded")
        snapshot = status._background_snapshot(context)
        assert len(snapshot["queueTasks"]) == 100
        assert snapshot["queueTasks"][0]["id"] == running.task_id
        assert snapshot["queueCounts"]["maafw"] == {"pending": 101, "running": 1, "succeeded": 0, "failed": 0}
        assert status._observed_tasks(context.epoch)[0].task_id == running.task_id
    finally:
        release.set()
        queue.shutdown()


def test_first_sweep_failures_are_visible_for_the_current_workspace(monkeypatch, tmp_path):
    from backend.app.api.envelope import AppError
    from backend.app.shared.backend import card_sweep_service as sweep

    context = AccountContext("seller", str(tmp_path), "epoch")
    service = sweep.CardSweepService()
    monkeypatch.setattr(sweep, "_service", service)
    monkeypatch.setattr(sweep, "get_account_context", lambda: context)
    monkeypatch.setattr(sweep, "outbox_busy", lambda _: False)
    monkeypatch.setattr(sweep, "CRMAdapter", Mock())
    monkeypatch.setattr(sweep, "get_product_card_pool", Mock())
    monkeypatch.setattr(sweep, "select_targets", Mock(side_effect=OSError("selection failed")))
    service._run_once()
    observed = status._scoped_workers(context)["card-sweep"]
    assert observed["context"] == context.to_dict()
    assert observed["last_error"] == "sweep_failed"
    monkeypatch.setattr(sweep, "select_targets", lambda *args: [sweep.SweepTarget(1, "contact", ("product",))])
    monkeypatch.setattr(sweep.gui_session, "capture_gui_session", Mock(side_effect=AppError("client offline", status_code=503)))
    service._run_once()
    observed = status._scoped_workers(context)["card-sweep"]
    assert observed["targets"] == 1 and observed["last_error"] == "client offline"
    hidden = status._scoped_workers(replace(context, epoch="new"))["card-sweep"]
    assert hidden["targets"] is None and hidden["last_error"] is None


def test_experiment_observation_keeps_cancel_request_distinct_and_hides_other_workspaces(monkeypatch, tmp_path):
    from backend.app.background import get_background_runtime

    registry = RunRegistry()
    monkeypatch.setattr(status, "runs", registry)
    owner = AccountContext("seller", str(tmp_path), "epoch")
    visible = registry.start(1, 0, ["a", "b"])
    registry.bind_owner(visible.run_id, owner)
    visible.update("a", status="completed")
    visible.update("b", status="running")
    visible.token.cancel()
    for other in (replace(owner, self_ali_id="other"), replace(owner, data_dir="other")):
        run = registry.start(2 if other.self_ali_id == "other" else 3, 0, ["private"])
        registry.bind_owner(run.run_id, other)
    worker = get_background_runtime().worker("experiment-run-private", lambda stop: None).start()
    assert worker.join(2)
    observed = status._background_snapshot(owner)
    assert len(observed["experiments"]) == 1
    run = observed["experiments"][0]
    assert run["runId"] == visible.run_id
    assert run["status"] == "running" and run["cancelRequested"]
    assert [variant["status"] for variant in run["variants"]] == ["completed", "running"]
    assert "experiment-run-private" not in json.dumps(observed)
    visible.update("b", status="cancelled")
    visible.finish()
    assert status._background_snapshot(owner)["experiments"][0]["status"] == "cancelled"


def test_outbox_summary_reads_persisted_states_without_payloads_or_recovery(tmp_path):
    store = OutboxStore(tmp_path / "crm.sqlite")
    scope = {"seller": "seller", "data_dir": tmp_path / "source"}
    store.initialize()
    awaiting = advance(store, scope, create(store, scope, "confirm")[0], "awaiting_confirmation")
    verifying = advance(store, scope, create(store, scope, "verify")[0], "verifying")
    unknown = move(store, scope, verifying, "unknown")
    observed = advance(store, scope, create(store, scope, "observed")[0], "verifying")
    observed = move(store, scope, observed, "observed", matched_message_id="match", evidence={"sensitive": "evidence"})
    create(store, {**scope, "seller": "other"}, "private", content="PRIVATE_BODY")
    create(store, {**scope, "data_dir": tmp_path / "other"}, "private-dir", content="PRIVATE_DIR_BODY")
    before = store.events(awaiting["id"], **scope)
    snapshot = OutboxStore(store.database_path).observation(**scope)
    assert snapshot["counts"] == {"awaiting_confirmation": 1, "unknown": 1, "observed": 1}
    assert {task["id"] for task in snapshot["recent"]} == {awaiting["id"], unknown["id"], observed["id"]}
    assert all(set(task) == {"id", "status", "action", "conversationId", "createdAt", "updatedAt", "mayHaveSent"}
               for task in snapshot["recent"])
    assert store.events(awaiting["id"], **scope) == before
    assert "PRIVATE" not in json.dumps(snapshot) and "content" not in json.dumps(snapshot)
    assert len(store.observation(**scope, limit=1)["recent"]) == 1


def test_partial_observation_failure_does_not_claim_empty_success_or_probe_updater(monkeypatch, tmp_path):
    instance = updater.Updater(tmp_path)
    instance.runtime = Mock()
    instance.state.update(supported=True, phase="downloading", downloaded_bytes=50, total_bytes=100, auto=True)
    instance.handoff.set()
    monkeypatch.setattr(updater, "_instance", instance)
    store = OutboxStore(tmp_path / "crm.sqlite")
    store.initialize()
    monkeypatch.setattr(status, "OutboxStore", lambda: store)
    context = AccountContext("seller", str(tmp_path), "epoch")
    observed = status._background_snapshot(context)
    assert observed["update"]["phase"] == "downloading"
    assert observed["update"]["downloaded_bytes"] == 50
    assert instance.runtime.mock_calls == []
    with sqlite3.connect(store.database_path, isolation_level=None) as conn:
        conn.execute("BEGIN EXCLUSIVE")
        with instance.lock, ThreadPoolExecutor(max_workers=1) as reader:
            failed = reader.submit(status._background_snapshot, context).result(2)
        conn.rollback()
    assert failed["errors"] == {"outbox": "OperationalError", "update": "BlockingIOError"}
    assert failed["update"] is None
    assert status._background_snapshot(context)["errors"] == {}
