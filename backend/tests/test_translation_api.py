"""Async translation job API: submission, polling, cache query and contract."""

import time
from unittest.mock import Mock

import pytest

from backend.app.api.routers import messages
from backend.app import translation_jobs
from backend.app.task_queue import TaskStatus, get_translation_queue
from backend.app.translation_jobs import submit_translation_job, translation_job
from backend.tests.test_connection_api import client as base_client, connect, sdk


@pytest.fixture
def client(base_client):
    connect(base_client, confirm=True)
    return base_client


def _wait_terminal(task_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = translation_job(task_id)
        if snapshot is not None and snapshot.completed_at is not None:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"translation job {task_id} did not finish in time")


def test_translation_job_lifecycle_and_dedupe(client, monkeypatch):
    calls = []
    monkeypatch.setattr(translation_jobs, "request_translations",
                        lambda texts, force=False: calls.append((texts, force)) or 1)
    response = client.post("/api/messages/translations", json={"texts": ["hello", " hello ", "", "hello"]})
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["status"] in {"pending", "running"}
    snapshot = _wait_terminal(payload["task_id"])
    assert snapshot.status == TaskStatus.SUCCEEDED
    assert calls == [(["hello"], False)]

    job = client.get(f"/api/messages/translations/jobs/{payload['task_id']}")
    assert job.status_code == 200
    assert job.json()["data"]["status"] == "succeeded"


def test_translation_force_flag_is_forwarded(client, monkeypatch):
    calls = []
    monkeypatch.setattr(translation_jobs, "request_translations",
                        lambda texts, force=False: calls.append((texts, force)) or 1)
    response = client.post("/api/messages/translations", json={"texts": ["a", "b"], "force": True})
    snapshot = _wait_terminal(response.json()["data"]["task_id"])
    assert snapshot.status == TaskStatus.SUCCEEDED
    assert calls == [(["a", "b"], True)]


def test_empty_submission_completes_immediately(client, monkeypatch):
    run = Mock()
    monkeypatch.setattr(translation_jobs, "request_translations", run)
    response = client.post("/api/messages/translations", json={"texts": ["", "   "]})
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["task_id"] == "" and payload["status"] == "succeeded"
    run.assert_not_called()


def test_chunks_of_fifty_and_partial_failure(client, monkeypatch):
    calls = []

    def flaky(texts, force=False):
        calls.append(list(texts))
        if len(texts) <= 5:
            raise RuntimeError("chunk boom")
        return len(texts)

    monkeypatch.setattr(translation_jobs, "request_translations", flaky)
    texts = [f"t{i}" for i in range(55)]
    response = client.post("/api/messages/translations", json={"texts": texts})
    snapshot = _wait_terminal(response.json()["data"]["task_id"])
    assert [len(chunk) for chunk in calls] == [50, 5]
    assert snapshot.status == TaskStatus.FAILED
    assert snapshot.message == "5/55 条翻译失败，可重试"


def test_job_aborts_when_account_epoch_changed(client):
    snap = submit_translation_job(["x"], force=False, expected_epoch="definitely-not-current")
    snapshot = _wait_terminal(snap.task_id)
    assert snapshot.status == TaskStatus.FAILED
    assert "账号已切换" in snapshot.message


def test_query_returns_cached_translations(client, monkeypatch):
    monkeypatch.setattr(messages, "get_translation", lambda text: f"译:{text}" if text == "hello" else None)
    response = client.post("/api/messages/translations/query", json={"texts": ["hello", "world", " hello "]})
    assert response.status_code == 200
    assert response.json()["data"]["translations"] == {"hello": "译:hello", "world": None}


def test_unknown_job_returns_404(client):
    assert client.get("/api/messages/translations/jobs/missing").status_code == 404


def test_submission_rejects_oversized_texts(client):
    assert client.post("/api/messages/translations", json={"texts": ["x"] * 501}).status_code == 422


def test_single_translate_uses_text_field(client, monkeypatch):
    request = Mock(return_value=1)
    monkeypatch.setattr(messages, "request_translations", request)
    monkeypatch.setattr(messages, "get_translation", Mock(return_value="你好"))
    response = client.post("/api/messages/translate", json={"messageId": "m1", "text": "hello"})
    assert response.status_code == 200
    assert response.json()["data"] == {"messageId": "m1", "translatedContent": "你好"}
    request.assert_called_once_with(["hello"], force=False)


def test_single_translate_without_text_returns_null(client, monkeypatch):
    request = Mock()
    monkeypatch.setattr(messages, "request_translations", request)
    response = client.post("/api/messages/translate", json={"messageId": "m1"})
    assert response.status_code == 200
    assert response.json()["data"] == {"messageId": "m1", "translatedContent": None}
    request.assert_not_called()
