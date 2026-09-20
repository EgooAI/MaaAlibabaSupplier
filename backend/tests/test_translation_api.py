"""Async translation job API: submission, polling, cache query and contract."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest

from backend.app import translation_jobs
from backend.app.api.routers import messages
from backend.app.shared.agent.translation import SHORT_HASH_LENGTH, TranslationOutcome
from backend.app.shared.crm.sdk import Translate, TranslateManager
from backend.app.shared.crm.translation_cache import md5_text_key
from backend.app.task_queue import TaskStatus
from backend.app.translation_jobs import submit_translation_job, translation_job
from backend.tests.test_connection_api import client as base_client, connect, sdk


@pytest.fixture
def client(base_client):
    connect(base_client)
    return base_client


def _wait_terminal(task_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = translation_job(task_id)
        if snapshot is not None and snapshot.completed_at is not None:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"translation job {task_id} did not finish in time")


def _recorder(calls):
    def fake(texts, force=False, conversation=None, annotate=None):
        calls.append({"texts": list(texts), "force": force, "conversation": conversation, "annotate": annotate})
        return TranslationOutcome(saved=len(texts), omitted=0)

    return fake


@pytest.mark.parametrize("force", [False, True])
def test_translation_job_lifecycle_and_dedupe(client, monkeypatch, force):
    calls = []
    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", _recorder(calls))
    response = client.post("/api/messages/translations", json={"texts": ["hello", " hello ", "", "hello"], "force": force})
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["status"] in {"pending", "running"}
    snapshot = _wait_terminal(payload["task_id"])
    assert snapshot.status == TaskStatus.SUCCEEDED
    assert [call["texts"] for call in calls] == [["hello"]]
    assert calls[0]["force"] is force
    assert calls[0]["conversation"] is None
    assert calls[0]["annotate"] is not None

    job = client.get(f"/api/messages/translations/jobs/{payload['task_id']}")
    assert job.status_code == 200
    assert job.json()["data"]["status"] == "succeeded"


def test_empty_submission_completes_immediately(client, monkeypatch):
    run = Mock()
    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", run)
    response = client.post("/api/messages/translations", json={"texts": ["", "   "]})
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["task_id"] == "" and payload["status"] == "succeeded"
    run.assert_not_called()


def test_all_cached_submission_completes_without_llm(client, monkeypatch):
    run = Mock()
    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", run)
    TranslateManager().upsert_translate(Translate(text_hash=md5_text_key("hello"), translation="你好"))
    response = client.post("/api/messages/translations", json={"texts": ["hello"]})
    assert response.status_code == 200
    snapshot = _wait_terminal(response.json()["data"]["task_id"])
    assert snapshot.status == TaskStatus.SUCCEEDED
    run.assert_not_called()


def test_chunks_of_fifty_and_partial_failure(client, monkeypatch):
    calls = []

    def flaky(texts, force=False, conversation=None, annotate=None):
        calls.append(list(texts))
        if len(texts) <= 5:
            raise RuntimeError("chunk boom")
        return TranslationOutcome(saved=len(texts), omitted=0)

    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", flaky)
    texts = [f"t{i}" for i in range(55)]
    response = client.post("/api/messages/translations", json={"texts": texts})
    snapshot = _wait_terminal(response.json()["data"]["task_id"])
    assert [len(chunk) for chunk in calls] == [50, 5]
    assert snapshot.status == TaskStatus.FAILED
    assert "条翻译失败" in snapshot.message


def test_agent_omissions_fail_the_job(client, monkeypatch):
    def omitting(texts, force=False, conversation=None, annotate=None):
        return TranslationOutcome(saved=len(texts) - 1, omitted=1)

    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", omitting)
    response = client.post("/api/messages/translations", json={"texts": ["a", "b"]})
    snapshot = _wait_terminal(response.json()["data"]["task_id"])
    assert snapshot.status == TaskStatus.FAILED
    assert "条翻译失败" in snapshot.message


def test_annotate_only_marks_uncached_texts(client, monkeypatch):
    calls = []
    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", _recorder(calls))
    TranslateManager().upsert_translate(Translate(text_hash=md5_text_key("cached"), translation="已译"))
    response = client.post("/api/messages/translations", json={"texts": ["cached", "fresh"]})
    snapshot = _wait_terminal(response.json()["data"]["task_id"])
    assert snapshot.status == TaskStatus.SUCCEEDED
    # 已缓存文本不进入待翻译列表，也不带上下文标记。
    assert calls[0]["texts"] == ["fresh"]
    assert set(calls[0]["annotate"]) == {"fresh"}


def test_conversation_id_loads_full_context_shared_across_chunks(client, monkeypatch):
    calls = []
    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", _recorder(calls))
    rows = [("10:00", "买家", "hello"), ("10:01", "买家", "cached")]
    loaded = []

    def fake_context(conversation_id):
        loaded.append(conversation_id)
        return rows

    monkeypatch.setattr(translation_jobs, "_conversation_context", fake_context)
    response = client.post(
        "/api/messages/translations",
        json={"texts": [f"t{i}" for i in range(55)], "conversationId": 17},
    )
    snapshot = _wait_terminal(response.json()["data"]["task_id"])
    assert snapshot.status == TaskStatus.SUCCEEDED
    assert loaded == [17]
    assert len(calls) == 2
    # 每个分片共享同一份全量上下文与同一张 job 级短哈希表（稳定前缀）。
    assert all(call["conversation"] is rows for call in calls)
    annotate = calls[0]["annotate"]
    assert annotate is not None and calls[1]["annotate"] == annotate
    assert set(annotate) == {f"t{i}" for i in range(55)}
    assert all(len(value) <= SHORT_HASH_LENGTH for value in annotate.values())


def test_context_load_failure_degrades_to_contextless_job(client, monkeypatch):
    calls = []
    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", _recorder(calls))
    monkeypatch.setattr(
        "backend.app.shared.crm.queries.get_conversation_detail",
        Mock(side_effect=RuntimeError("db boom")),
    )
    response = client.post("/api/messages/translations", json={"texts": ["hello"], "conversationId": 17})
    snapshot = _wait_terminal(response.json()["data"]["task_id"])
    assert snapshot.status == TaskStatus.SUCCEEDED
    assert calls[0]["conversation"] is None


def test_job_aborts_when_account_epoch_changed(client):
    snap = submit_translation_job(["x"], force=False, expected_epoch="definitely-not-current")
    snapshot = _wait_terminal(snap.task_id)
    assert snapshot.status == TaskStatus.FAILED
    assert "账号已切换" in snapshot.message


def test_translation_job_does_not_hold_account_lock(client, monkeypatch):
    entered, release = Event(), Event()

    def blocked(texts, force=False, conversation=None, annotate=None):
        entered.set()
        assert release.wait(5)
        return TranslationOutcome(saved=len(texts), omitted=0)

    monkeypatch.setattr(translation_jobs, "translate_texts_to_crm", blocked)
    response = client.post("/api/messages/translations", json={"texts": ["hello"]})
    task_id = response.json()["data"]["task_id"]
    assert entered.wait(2)
    try:
        # LLM 调用进行中，账号路径请求不得被阻塞。
        with ThreadPoolExecutor(max_workers=1) as executor:
            probe = executor.submit(client.get, "/api/self-info")
            assert probe.result(timeout=2).status_code == 200
    finally:
        release.set()
    assert _wait_terminal(task_id).status == TaskStatus.SUCCEEDED


def test_query_returns_cached_translations_and_no_need_sentinel(client, monkeypatch):
    monkeypatch.setattr(
        messages,
        "get_translation",
        lambda text: {"hello": "译:hello", "cached": ""}.get(text),
    )
    response = client.post("/api/messages/translations/query", json={"texts": ["hello", "world", " hello ", "cached"]})
    assert response.status_code == 200
    # null=未缓存；空串=NO_NEED 哨兵（已缓存、无需翻译）。
    assert response.json()["data"]["translations"] == {"hello": "译:hello", "world": None, "cached": ""}


def test_unknown_job_returns_404(client):
    assert client.get("/api/messages/translations/jobs/missing").status_code == 404


def test_fetch_translation_by_path(client, monkeypatch):
    monkeypatch.setattr(messages, "get_translation", lambda text: f"译:{text}")
    response = client.get("/api/messages/translations/hello")
    assert response.status_code == 200
    assert response.json()["data"] == "译:hello"


def test_submission_rejects_oversized_texts(client):
    assert client.post("/api/messages/translations", json={"texts": ["x"] * 501}).status_code == 422
