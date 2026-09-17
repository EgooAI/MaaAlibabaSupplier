from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app.api.main import app
from backend.app.api.routers import conversations, status
from backend.app.shared.backend.account_context import get_account_context
from backend.app.task_queue import TaskQueue, TaskStatus


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(status, "_last_node_result", None)
    monkeypatch.setattr(status, "get_client_status", lambda: {"connected": True, "window_generation": "fake", "detail": "test"})
    monkeypatch.setattr(conversations, "capture_gui_session", lambda epoch: epoch)
    monkeypatch.setattr(conversations, "run_guarded", lambda token, fn: fn())
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers["X-Account-Epoch"] = get_account_context().epoch
        yield client


@pytest.fixture
def send_setup(monkeypatch):
    calls = []
    monkeypatch.setattr(conversations, "_ready", lambda: "seller-test")
    monkeypatch.setattr(conversations, "crm_get_conversation_detail", lambda *args: SimpleNamespace(contact_ali_id="buyer-test"))
    monkeypatch.setattr(conversations, "crm_get_user_info", lambda *args: SimpleNamespace(login_id="buyer-login"))
    monkeypatch.setattr(conversations, "CRMAdapter", lambda: object())
    monkeypatch.setattr(conversations, "_build_aggregate", lambda *args: {"sid": 1})
    monkeypatch.setattr(conversations, "goto_contact", lambda target: (calls.append(("goto", target)) is None, "located"))
    monkeypatch.setattr(conversations, "chat_input", lambda text: (calls.append(("input", text)) is None, "filled"))
    monkeypatch.setattr(conversations, "chat_send", lambda text: (calls.append(("send", text)) is None, "GUI completed"))
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


@pytest.mark.parametrize("action, expected", [("send", "send"), ("test", "input")])
def test_send_acceptance_is_pending_and_queryable(client, send_setup, blocked_queue, action, expected):
    queue, release = blocked_queue
    response = client.post("/api/conversations/1/messages", json={"content": "hello", "action": action})
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["conversation"] == {"sid": 1}
    assert result["execution"]["success"] is None
    snapshot = result["execution"]["task_snapshot"]
    assert snapshot["status"] == "pending"
    assert snapshot["result"] is None
    assert send_setup == []
    tasks = client.get("/api/status/tasks").json()["data"]
    assert any(task["task_id"] == snapshot["task_id"] for task in tasks)

    release.set()
    queue.shutdown()
    assert send_setup == [("goto", "buyer-login"), (expected, "hello")]
    assert queue.get(snapshot["task_id"]).status == TaskStatus.SUCCEEDED


def test_response_preparation_failure_cannot_enqueue_send(client, send_setup, monkeypatch):
    queue = TaskQueue()
    monkeypatch.setattr(conversations, "_build_aggregate", Mock(side_effect=RuntimeError("aggregate failed")))
    response = client.post("/api/conversations/1/messages", json={"content": "hello"})
    assert response.status_code == 500
    assert queue.all_snapshots() == []
    assert send_setup == []


def test_failed_navigation_never_inputs_or_sends(client, send_setup, monkeypatch):
    queue = TaskQueue()
    monkeypatch.setattr(conversations, "goto_contact", lambda target: (False, "not found"))
    response = client.post("/api/conversations/1/messages", json={"content": "hello"})
    snapshot = response.json()["data"]["execution"]["task_snapshot"]
    queue.shutdown()
    assert send_setup == []
    assert queue.get(snapshot["task_id"]).status == TaskStatus.FAILED


def test_diagnostics_cannot_interrupt_navigation_and_send(client, send_setup, monkeypatch):
    queue = TaskQueue()
    navigating, release = Event(), Event()

    def goto(target):
        send_setup.append(("goto", target))
        navigating.set()
        if not release.wait(5):
            raise TimeoutError("test gate not released")
        return True, "located"

    def diagnose(entry):
        send_setup.append(("diagnostic", entry))
        return True, "matched"

    monkeypatch.setattr(conversations, "goto_contact", goto)
    monkeypatch.setattr(status, "run_node", diagnose)
    try:
        client.post("/api/conversations/1/messages", json={"content": "hello"})
        assert navigating.wait(2)
        response = client.post("/api/status/node-test", json={"entry": "ContactSearch_GoToSearch"})
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["success"] is None
        assert result["task_snapshot"]["status"] == "pending"
        assert status._last_node_result is None
        assert send_setup == [("goto", "buyer-login")]
    finally:
        release.set()
        queue.shutdown()
    assert send_setup == [
        ("goto", "buyer-login"), ("send", "hello"),
        ("diagnostic", "Diagnostics_ContactSearch"),
    ]
    assert status._last_node_result["success"] is True


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
    assert TaskQueue._instance is None
    run.assert_not_called()


def test_queue_full_rejects_request_without_gui_action(client, send_setup, blocked_queue, monkeypatch):
    from backend.app import task_queue

    queue, _ = blocked_queue
    monkeypatch.setattr(task_queue, "TASK_QUEUE_MAXSIZE", 1)
    first = client.post("/api/conversations/1/messages", json={"content": "first"})
    second = client.post("/api/conversations/1/messages", json={"content": "second"})
    assert first.status_code == 200
    assert second.status_code == 429
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
    assert TaskQueue._instance is None
    with pytest.raises(RuntimeError):
        queue.enqueue(lambda: (True, "unexpected"), description="late")
