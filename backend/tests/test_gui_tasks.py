from threading import Event
from unittest.mock import Mock

import pytest
from backend.app.api.routers import conversations, status
from backend.app.shared.backend import outbox_service
from backend.app.task_queue import DEFAULT_QUEUE_NAME, TaskQueue, TaskStatus
from backend.tests.test_connection_api import client as base_client, chat, connect, sdk
from backend.tests.test_outbox_api import outbox


@pytest.fixture
def client(base_client, monkeypatch):
    connect(base_client)
    monkeypatch.setattr(status, "_last_node_result", None)
    return base_client


@pytest.fixture
def send_setup(outbox, monkeypatch):
    calls = []
    outbox.service._queue = TaskQueue()
    monkeypatch.setattr(outbox_service.runner, "goto_contact", lambda target: (calls.append(("goto", target)) is None, "located"))
    monkeypatch.setattr(outbox_service.runner, "chat_input", lambda text: (calls.append(("input", text)) is None, "filled"))
    monkeypatch.setattr(outbox_service.runner, "submit_send", lambda: calls.append(("send", None)))
    monkeypatch.setattr(outbox_service.runner, "wait_send", lambda job: (True, "clicked"))
    return calls


@pytest.fixture
def blocked_queue():
    queue = TaskQueue()
    entered, release = Event(), Event()

    def block():
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test gate not released")
        return True, "released"

    queue.enqueue(block, description="test gate")
    assert entered.wait(2)
    try:
        yield queue, release
    finally:
        release.set()
        queue.shutdown()


@pytest.mark.parametrize("action", ["send", "test"])
def test_send_acceptance_is_pending_and_queryable(client, send_setup, blocked_queue, monkeypatch, action):
    aggregate = Mock(side_effect=AssertionError("legacy aggregate must not be built"))
    monkeypatch.setattr(conversations, "_build_aggregate", aggregate)
    queue, release = blocked_queue
    response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": action, "idempotency_key": "key"})
    assert response.status_code == 200
    result = response.json()["data"]
    assert set(result) == {"outbox"}
    snapshot = result["outbox"]
    assert snapshot["status"] == "queued" and not snapshot["may_have_sent"]
    assert send_setup == []
    aggregate.assert_not_called()
    tasks = client.get("/api/conversations/1/outbox").json()["data"]
    assert any(task["id"] == snapshot["id"] for task in tasks)

    release.set()
    queue.shutdown()
    assert send_setup == [("goto", "buyer-login")]
    assert client.get(f"/api/outbox/{snapshot['id']}").json()["data"]["status"] == "awaiting_confirmation"


def test_failed_navigation_never_inputs_or_sends(client, send_setup, monkeypatch):
    queue = TaskQueue()
    monkeypatch.setattr(outbox_service.runner, "goto_contact", lambda target: (False, "not found"))
    response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": "send", "idempotency_key": "key"})
    snapshot = response.json()["data"]["outbox"]
    queue.shutdown()
    assert send_setup == []
    assert client.get(f"/api/outbox/{snapshot['id']}").json()["data"]["status"] == "unknown"


def test_diagnostics_share_queue_without_bypassing_recipient_confirmation(client, send_setup, blocked_queue, monkeypatch):
    queue, release = blocked_queue

    def diagnose(entry):
        send_setup.append(("diagnostic", entry))
        return True, "matched"

    monkeypatch.setattr(status, "run_node", diagnose)
    try:
        response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": "send", "idempotency_key": "key"})
        task_id = response.json()["data"]["outbox"]["id"]
        response = client.post("/api/status/node-test", json={"entry": "ContactSearch_GoToSearch"})
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["success"] is None
        assert result["task_snapshot"]["status"] == "pending"
        assert status._last_node_result is None
        assert send_setup == []
    finally:
        release.set()
        queue.shutdown()
    assert send_setup == [
        ("goto", "buyer-login"),
        ("diagnostic", "Diagnostics_ContactSearch"),
    ]
    assert status._last_node_result["success"] is True
    assert client.get(f"/api/outbox/{task_id}").json()["data"]["status"] == "awaiting_confirmation"


def test_diagnostic_failure_updates_last_result(client, monkeypatch):
    queue = TaskQueue()
    monkeypatch.setattr(status, "run_node", Mock(side_effect=RuntimeError("failed")))
    response = client.post("/api/status/node-test", json={})
    task_id = response.json()["data"]["task_snapshot"]["task_id"]
    queue.shutdown()
    assert queue.get(task_id).status == TaskStatus.FAILED
    assert status._last_node_result["success"] is False


@pytest.mark.parametrize("entry", ["ChatInput", "ChatSend", "GotoContact", "ChatInput_SendMessage", ""])
def test_diagnostic_rejects_action_entries(client, monkeypatch, entry):
    run = Mock()
    monkeypatch.setattr(status, "run_node", run)
    response = client.post("/api/status/node-test", json={"entry": entry})
    assert response.status_code == 422
    assert TaskQueue._instances.get(DEFAULT_QUEUE_NAME) is None
    run.assert_not_called()


def test_queue_full_persists_failed_outbox_without_gui_action(client, send_setup, blocked_queue, monkeypatch):
    from backend.app import task_queue

    queue, _ = blocked_queue
    monkeypatch.setattr(task_queue, "TASK_QUEUE_MAXSIZE", 1)
    first = client.post("/api/conversations/1/messages", json={"content": "first", "action": "send", "idempotency_key": "first"})
    second = client.post("/api/conversations/1/messages", json={"content": "second", "action": "send", "idempotency_key": "second"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["outbox"]["status"] == "failed"
    assert len(queue.all_snapshots()) == 2
    assert send_setup == []


def test_shutdown_drains_work_rejects_new_work_and_releases_worker():
    queue = TaskQueue()
    calls = []
    queue.enqueue(lambda: (calls.append("first") is None, "done"), description="first")
    queue.enqueue(lambda: (calls.append("second") is None, "done"), description="second")
    queue.shutdown()
    assert calls == ["first", "second"]
    assert not queue._worker.is_alive()
    assert TaskQueue._instances.get(DEFAULT_QUEUE_NAME) is None
    with pytest.raises(RuntimeError):
        queue.enqueue(lambda: (True, "unexpected"), description="late")
