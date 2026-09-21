from concurrent.futures import ThreadPoolExecutor
import os
import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from backend.app.api.routers import conversations
from backend.app.shared.backend import account_context, outbox_service, source_messages
from backend.app.shared.backend.gui_evidence import frame_from_image
from backend.app.shared.crm.outbox_store import OutboxStore
from backend.app.shared.crm.identities import self_sender_id
from backend.app.shared.utils import app_config
from backend.tests.test_connection_api import chat, client, connect, sdk
from backend.tests.test_outbox_service import DeferredQueue


@pytest.fixture
def outbox(client, chat, monkeypatch, tmp_path):
    queue = DeferredQueue()
    service = outbox_service.OutboxService(OutboxStore(tmp_path / "outbox.sqlite"), queue)
    frame = frame_from_image(np.zeros((4, 5, 3), dtype=np.uint8))
    monkeypatch.setattr(outbox_service, "_service", service)
    monkeypatch.setattr(outbox_service, "RECONCILE_INTERVAL", 3600)
    monkeypatch.setattr(outbox_service.runner, "capture_client_frame", lambda: frame)
    context = account_context.get_account_context()
    source = {
        "valid": True, "origin": {"seller": context.self_ali_id, "data_dir": os.path.normcase(context.data_dir),
                                  "source_path": "private-source"},
        "checked_at": time.time(), "messages": [{"id": "private-baseline-id"}],
    }
    monkeypatch.setattr(source_messages, "read_source_messages", lambda *args: source)
    connect(client)
    yield SimpleNamespace(service=service, queue=queue, frame=frame, source=source)
    service.stop()


def submit(client, **changes):
    return client.post("/api/conversations/1/messages", json={
        "content": "  hello\r\nworld  ", "action": "send", "idempotency_key": "request-1",
        **changes,
    })


def prepared(client, outbox, **changes):
    response = submit(client, **changes)
    assert response.status_code == 200, response.text
    task = response.json()["data"]["outbox"]
    assert outbox.queue.run()[0]
    return client.get(f"/api/outbox/{task['id']}").json()["data"]


def confirm(client, task):
    return client.post(f"/api/outbox/{task['id']}/confirm", json={
        "version": task["version"], "screenshot_id": task["screenshot_id"],
    })


@pytest.mark.parametrize("action,final", [("send", "verifying"), ("test", "filled")])
def test_two_step_contract_png_and_immutable_content(client, outbox, sdk, action, final):
    response = submit(client, action=action)
    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0
    assert set(response.json()["data"]) == {"outbox"}
    task = response.json()["data"]["outbox"]
    assert task["status"] == "queued" and task["content"] == "hello\r\nworld"
    assert task["idempotency_key"] == "request-1"
    assert set(task) == {
        "id", "conversation_id", "contact_ali_id", "login_id", "content", "action",
        "idempotency_key", "status", "version", "attempt", "may_have_sent",
        "reason", "created_at", "updated_at", "screenshot_id", "screenshot_at",
        "matched_message_id", "evidence",
    }
    assert sdk.calls == []
    assert outbox.queue.run()[0]
    task = client.get(f"/api/outbox/{task['id']}").json()["data"]
    assert task["status"] == "awaiting_confirmation"
    assert [entry for entry, _ in sdk.calls] == ["ContactSearch"]
    assert sdk.calls[0][1]["ContactSearch_InputText"]["action"]["param"]["input_text"] == "buyer-login"
    image = client.get(task["screenshot_url"])
    assert image.status_code == 200
    assert image.content == outbox.frame.png_bytes
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.headers["Content-Type"] == "image/png"
    assert image.headers["Cache-Control"] == "no-store"
    assert image.headers["X-Account-Epoch"] == client.headers["X-Account-Epoch"]
    response = client.post(f"/api/outbox/{task['id']}/confirm", json={
        "version": task["version"], "screenshot_id": task["screenshot_id"], "content": "changed",
    })
    assert response.status_code == 422
    accepted = confirm(client, task)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "queued_send"
    assert confirm(client, task).status_code == 409
    assert len(outbox.queue.jobs) == 1
    assert client.get(task["screenshot_url"]).status_code == 409
    assert outbox.queue.run()[0]
    result = client.get(f"/api/outbox/{task['id']}").json()["data"]
    assert result["status"] == final
    assert result["may_have_sent"] is (action == "send")
    assert confirm(client, task).status_code == 409
    assert result["content"] == "hello\r\nworld"
    entries = [entry for entry, _ in sdk.calls]
    assert entries == ["ContactSearch", "ChatInput"] + (["ChatInput_SendOnly"] if action == "send" else [])
    assert sdk.calls[1][1]["ChatInput_SendMessage"]["enabled"] is False
    assert "private-baseline-id" not in str(result)
    assert "private-source" not in str(result)


@pytest.mark.parametrize("already_confirmed", [False, True])
def test_reconnect_same_window_invalidates_screenshot_session(client, outbox, sdk, already_confirmed):
    task = prepared(client, outbox)
    if already_confirmed:
        assert confirm(client, task).status_code == 200
    connect(client)
    assert len(sdk.controllers) == 1
    if already_confirmed:
        assert not outbox.queue.run()[0]
        result = client.get(f"/api/outbox/{task['id']}").json()["data"]
        assert result["status"] == "failed" and not result["may_have_sent"]
        assert result["reason"] == "客户端操作会话已失效，请重新连接后重试。"
    else:
        response = confirm(client, task)
        assert response.status_code == 409
        assert response.json()["msg"] == "原客户端操作授权已失效，请显式重试任务。"
    assert outbox.queue.jobs == []
    assert [entry for entry, _ in sdk.calls] == ["ContactSearch"]


def test_concurrent_duplicate_and_recovery_with_stale_gui(client, outbox):
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = [future.result(timeout=5) for future in [pool.submit(submit, client) for _ in range(2)]]
    assert all(response.status_code in (200, 409) for response in replies)
    accepted = [response.json()["data"]["outbox"] for response in replies if response.status_code == 200]
    assert accepted and len({task["id"] for task in accepted}) == 1
    task = accepted[0]
    assert submit(client).json()["data"]["outbox"]["id"] == task["id"]
    assert len(outbox.queue.jobs) == 1
    connect(client)  # Same epoch, but the window generation has changed.
    assert submit(client).json()["data"]["outbox"] == task
    assert len(outbox.queue.jobs) == 1
    for changes in ({"content": "changed"}, {"action": "test"}):
        assert submit(client, **changes).status_code == 409
    assert outbox.service.get(account_context.get_account_context(), task["id"])["content"] == "hello\r\nworld"


@pytest.mark.parametrize("payload", [
    {}, {"content": "hello"}, {"content": "hello", "action": "send"},
    {"content": "hello", "idempotency_key": "key"},
    {"content": " ", "action": "send", "idempotency_key": "key"},
    {"content": "hello", "action": "send", "idempotency_key": " "},
    {"content": "hello", "action": "send", "idempotency_key": "x" * 129},
])
def test_submit_validation_never_queues(client, outbox, payload):
    response = client.post("/api/conversations/1/messages", json=payload)
    assert response.status_code == 422
    assert outbox.queue.jobs == []
    assert not outbox.service.store.database_path.exists()


def test_missing_conversation_or_login_never_queues(client, outbox, monkeypatch):
    adapter_cls = conversations.CRMAdapter
    monkeypatch.setattr(adapter_cls, "get_conversation_detail", lambda *args: None)
    assert submit(client).status_code == 404
    monkeypatch.setattr(adapter_cls, "get_conversation_detail", lambda *args: SimpleNamespace(contact_ali_id=""))
    assert submit(client).status_code == 503
    monkeypatch.setattr(adapter_cls, "get_conversation_detail", lambda *args: SimpleNamespace(contact_ali_id="buyer"))
    monkeypatch.setattr(adapter_cls, "get_user_info", lambda *args: SimpleNamespace(login_id="", alias="unsafe"))
    assert submit(client).status_code == 503
    assert outbox.queue.jobs == []


@pytest.mark.parametrize("scope", ["seller", "directory"])
def test_account_ownership_and_stale_epochs(client, outbox, scope):
    task = prepared(client, outbox)
    old_epoch = client.headers["X-Account-Epoch"]
    config = app_config.read_app_config()
    config["self_ali_id" if scope == "seller" else "alibaba_data_dir"] += "-other"
    app_config.write_app_config(config)
    current = account_context.get_account_context()
    for path in (f"/api/outbox/{task['id']}", "/api/conversations/1/outbox", task["screenshot_url"]):
        assert client.get(path).status_code == 409
    for operation in ("confirm", "cancel", "retry"):
        assert client.post(f"/api/outbox/{task['id']}/{operation}", json={"version": task["version"]}).status_code == 409
    client.headers["X-Account-Epoch"] = current.epoch
    assert current.epoch != old_epoch
    assert client.get(f"/api/outbox/{task['id']}").status_code == 404
    assert client.get(task["screenshot_url"]).status_code == 404
    assert client.get("/api/conversations/1/outbox").json()["data"] == []
    assert client.post(f"/api/outbox/{task['id']}/cancel", json={"version": task["version"]}).status_code == 409
    client.headers.pop("X-Account-Epoch")
    assert client.get(task["screenshot_url"]).status_code == 404
    assert client.post(f"/api/outbox/{task['id']}/cancel", json={"version": task["version"]}).status_code == 409


def test_screenshot_id_version_expiry_and_optional_read_header(client, outbox, monkeypatch):
    task = prepared(client, outbox)
    other = prepared(client, outbox, idempotency_key="other")
    path = f"/api/outbox/{task['id']}/screenshot/"
    assert client.get(path + task["screenshot_id"]).status_code == 422
    for invalid in ("not-a-uuid", "C:%5Cprivate.png"):
        assert client.get(path + invalid, params={"version": task["version"]}).status_code == 404
    assert client.get(path + other["screenshot_id"], params={"version": task["version"]}).status_code == 409
    client.headers.pop("X-Account-Epoch")
    assert client.get(task["screenshot_url"]).status_code == 200
    monkeypatch.setattr(outbox_service, "time", SimpleNamespace(time=lambda: task["screenshot_at"] + 121, monotonic=time.monotonic))
    assert client.get(task["screenshot_url"]).status_code == 409


def test_background_observation_is_not_delivery_confirmation(client, outbox):
    task = prepared(client, outbox)
    assert confirm(client, task).status_code == 200
    assert outbox.queue.run()[0]
    context = account_context.get_account_context()
    outbox.source["checked_at"] = time.time()
    outbox.source["messages"] = [{
        "id": "matched-local-id", "sender_id": self_sender_id(context.self_ali_id),
        "cid": f"{context.self_ali_id}-buyer", "type": 0, "text": task["content"],
        "created_at": time.time(), "is_system": False, "is_auto_reply": False, "extension_valid": True,
    }]
    assert client.get(f"/api/outbox/{task['id']}").json()["data"]["status"] == "verifying"
    assert len(outbox.service.reconcile_once()) == 1
    result = client.get(f"/api/outbox/{task['id']}").json()["data"]
    assert result["status"] == "observed"
    assert result["matched_message_id"] == "matched-local-id"
    assert result["reason"] == "local_message_observed"
    assert result["evidence"]["kind"] == "local_source_message"
    assert "private-source" not in str(result)
    assert "message" not in result["evidence"]
    assert "candidate_ids" not in result["evidence"]


def test_observation_only_bounded_list_and_sanitized_evidence(client, outbox, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("GET must only observe"))
    monkeypatch.setattr(outbox.service, "start", forbidden)
    monkeypatch.setattr(outbox.service, "reconcile_once", forbidden)
    monkeypatch.setattr(source_messages, "read_source_messages", forbidden)
    assert client.get("/api/conversations/1/outbox").json()["data"] == []
    assert client.get("/api/outbox/missing").status_code == 404
    assert not outbox.service.store.database_path.exists()
    store = outbox.service.store
    context = account_context.get_account_context()
    for index in range(3):
        monkeypatch.setattr("backend.app.shared.crm.outbox_store.time",
                            SimpleNamespace(time=lambda index=index: float(index)))
        task, _ = store.create(seller=context.self_ali_id, data_dir=context.data_dir,
                              conversation_id=1, contact_ali_id="buyer", login_id="buyer-login",
                              content="hello", action="send", idempotency_key=str(index))
    store.update_fields(task["id"], "queued", task["version"], seller=context.self_ali_id, data_dir=context.data_dir,
                        reason="Failure at C:\\private\\source; token=secret-token",
                        baseline={"ids": ["private-mid"]}, evidence={
                            "kind": "local_source_message", "checked_at": 2.0,
                            "origin": {"source_path": "secret-path"}, "candidate_ids": ["private-mid"],
                            "message": {"token": "secret-token"}, "source_revision": "secret-token",
                        })
    for limit in (0, 101, "bad"):
        assert client.get("/api/conversations/1/outbox", params={"limit": limit}).status_code == 422
    rows = client.get("/api/conversations/1/outbox", params={"limit": 2}).json()["data"]
    assert len(rows) == 2 and rows[0]["id"] == task["id"]
    assert rows[0]["evidence"] == {"kind": "local_source_message", "checked_at": 2.0}
    assert rows[0]["reason"] == "outbox_operation_failed"
    assert all(secret not in str(rows) for secret in ("private-mid", "secret-path", "secret-token", "C:\\private"))
    forbidden.assert_not_called()


@pytest.mark.parametrize("operation", ["submit", "duplicate", "confirm", "retry"])
def test_busy_outbox_write_rejects_before_release_and_never_executes_later(client, outbox, sdk, monkeypatch, operation):
    target = None
    if operation == "confirm":
        target = prepared(client, outbox, idempotency_key="second")
    elif operation == "retry":
        outbox.queue.full = True
        target = submit(client, idempotency_key="second").json()["data"]["outbox"]
        assert target["status"] == "failed"
        outbox.queue.full = False

    running = prepared(client, outbox, idempotency_key="active")
    assert confirm(client, running).status_code == 200
    entered, release = Event(), Event()

    def input_text(text):
        entered.set()
        assert release.wait(10)
        return True, "filled"

    monkeypatch.setattr(outbox_service.runner, "chat_input", input_text)
    method = "submit" if operation == "duplicate" else operation
    service_call = Mock(wraps=getattr(outbox.service, method))
    monkeypatch.setattr(outbox.service, method, service_call)

    def request():
        if operation in ("submit", "duplicate"):
            return submit(client, idempotency_key="active" if operation == "duplicate" else "second")
        if operation == "confirm":
            return confirm(client, target)
        return client.post(f"/api/outbox/{target['id']}/retry", json={"version": target["version"]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(outbox.queue.run)
        try:
            assert entered.wait(2)
            calls_before = list(sdk.calls)
            context = account_context.get_account_context()
            tasks_before = outbox.service.list(context, 1)
            response = pool.submit(request).result(timeout=0.5)
            assert response.status_code == 409, response.text
            assert response.json()["msg"] == "\u5f53\u524d\u8d26\u53f7\u4ecd\u6709\u64cd\u4f5c\u6b63\u5728\u6267\u884c\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5"
            assert response.json()["data"] is None
            assert response.headers["X-Account-Epoch"] == client.headers["X-Account-Epoch"]
            assert not release.is_set()
            service_call.assert_not_called()
            assert outbox.queue.jobs == []
            assert sdk.calls == calls_before
            assert outbox.service.list(context, 1) == tasks_before
        finally:
            release.set()
        assert execution.result(timeout=2)[0]

    service_call.assert_not_called()
    assert outbox.queue.jobs == []
    calls_after = list(sdk.calls)
    response = request()
    assert response.status_code == 200, response.text
    if operation in ("submit", "duplicate"):
        recovered = response.json()["data"]["outbox"]
        assert request().json()["data"]["outbox"] == recovered
        if operation == "duplicate":
            assert recovered["id"] == running["id"] and recovered["status"] == "verifying"
        else:
            assert recovered["status"] == "queued"
    else:
        recovered = response.json()["data"]
        assert recovered["id"] == target["id"]
        assert recovered["status"] == ("queued_send" if operation == "confirm" else "queued")
        assert request().status_code == 409
    assert len(outbox.queue.jobs) == (0 if operation == "duplicate" else 1)
    assert sdk.calls == calls_after


def test_get_and_cancel_remain_responsive_during_real_gui_guard(client, outbox, monkeypatch):
    running = prepared(client, outbox)
    assert confirm(client, running).status_code == 200
    queued = submit(client, idempotency_key="cancel-me").json()["data"]["outbox"]
    entered, release = Event(), Event()

    def input_text(text):
        entered.set()
        assert release.wait(10)
        return True, "filled"

    monkeypatch.setattr(outbox_service.runner, "chat_input", input_text)
    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(outbox.queue.run)
        try:
            assert entered.wait(2)
            for path in (f"/api/outbox/{running['id']}", "/api/conversations/1/outbox"):
                response = pool.submit(client.get, path).result(timeout=2)
                assert response.status_code == 200, response.text
            response = pool.submit(client.post, f"/api/outbox/{running['id']}/cancel",
                                   json={"version": running["version"]}).result(timeout=2)
            assert response.status_code == 409
            response = pool.submit(client.post, f"/api/outbox/{queued['id']}/cancel",
                                   json={"version": queued["version"]}).result(timeout=2)
            assert response.status_code == 200 and response.json()["data"]["status"] == "cancelled"
            assert not release.is_set()
        finally:
            release.set()
        assert execution.result(timeout=2)[0]
    assert not outbox.queue.run()[0]


@pytest.mark.parametrize("operation", ["get", "list", "get_screenshot"])
def test_epoch_change_during_read_discards_response_and_binds_original_scope(client, outbox, monkeypatch, operation):
    task = prepared(client, outbox)
    context = account_context.get_account_context()
    original = getattr(outbox.service, operation)

    def changed(captured, *args, **kwargs):
        assert captured == context
        account_context.invalidate_account_context()
        return original(captured, *args, **kwargs)

    monkeypatch.setattr(outbox.service, operation, changed)
    path = {"get": f"/api/outbox/{task['id']}", "list": "/api/conversations/1/outbox",
            "get_screenshot": task["screenshot_url"]}[operation]
    response = client.get(path)
    assert response.status_code == 409 and response.json()["data"] is None
    monkeypatch.setattr(outbox.service, operation, original)


def test_screenshot_version_change_during_read_discards_image(client, outbox, monkeypatch):
    task = prepared(client, outbox)
    original = outbox.service.get_screenshot

    def changed(context, task_id, screenshot_id):
        image = original(context, task_id, screenshot_id)
        outbox.service.cancel(context, task_id, version=task["version"])
        return image

    monkeypatch.setattr(outbox.service, "get_screenshot", changed)
    response = client.get(task["screenshot_url"])
    assert response.status_code == 409 and response.json()["data"] is None


def test_cancel_and_retry_require_versions_and_never_edit_payload(client, outbox):
    outbox.queue.full = True
    task = submit(client).json()["data"]["outbox"]
    assert task["status"] == "failed"
    for operation in ("retry", "cancel", "confirm"):
        assert client.post(f"/api/outbox/{task['id']}/{operation}", json={}).status_code == 422
    outbox.queue.full = False
    response = client.post(f"/api/outbox/{task['id']}/retry", json={"version": task["version"]})
    assert response.status_code == 200, response.text
    retry = response.json()["data"]
    assert retry["id"] == task["id"] and retry["attempt"] == 2
    assert retry["content"] == task["content"]
    assert client.post(f"/api/outbox/{task['id']}/retry", json={"version": task["version"]}).status_code == 409
    assert client.post(f"/api/outbox/{task['id']}/cancel", json={"version": retry["version"]}).status_code == 200
    assert not outbox.queue.run()[0]
