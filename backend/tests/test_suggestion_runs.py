"""Exercises persisted previews with a real pipeline and isolated CRM databases."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_pipeline import LLMConfig, LLMResponse, LLMToolCall, register_llm
from agent_pipeline.llm import StaticLLMClient

from backend.app.shared.agent import suggestion_runs as module
from backend.app.shared.agent.system_agents import CHAT_REPLY_SUGGESTION_AGENT_APID
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.crm.intent_store import IntentStore
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager
from backend.app.shared.crm.suggestion_store import SuggestionStore
from backend.app.shared.utils import app_config
from backend.tests.test_connection_api import client, sdk
from backend.tests.test_inbox_api import archive, data, row


class DeferredPool:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append(lambda: fn(*args))

    def run(self):
        self.jobs.pop(0)()


@pytest.fixture
def preview(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [row(1, text="Need 500 tents")]))
    sid = data(client)["items"][0]["sid"]
    manager = AgentPresetManager()
    for apid in ("custom-reply", CHAT_REPLY_SUGGESTION_AGENT_APID):
        manager.upsert_agent_preset(AgentPreset(apid=apid, name=apid, description="", prompt="Ask about delivery", llm_level=0,
                                               tools=["reply_customer"], enabled=True, requires_tool=True))
    register_llm(0, LLMConfig(base_url="https://example.invalid/v1", api_key="synthetic", model_name="fixture"))
    clients = []

    def factory(*args, **kwargs):
        llm = StaticLLMClient([
            LLMResponse(text="", needs_tool=True, tool_call=LLMToolCall(name="reply_customer", call_id="one",
                        tool_input={"reply": "When do you need them?", "zh": "您何时需要？", "tone": "friendly"})),
            LLMResponse(text="", needs_tool=True, tool_call=LLMToolCall(name="reply_customer", call_id="two",
                        tool_input={"reply": "Please confirm your delivery date.", "zh": "请确认交期。", "tone": "formal"})),
            LLMResponse(text="The quantity is known; ask about delivery."),
        ])
        clients.append(llm)
        return llm

    monkeypatch.setattr("agent_pipeline.llm.OpenAICompatibleLLMClient", factory)
    pool = DeferredPool()
    service = module.SuggestionRuns(pool=pool)
    monkeypatch.setattr(module, "_service", service)
    return SimpleNamespace(sid=sid, apply=apply, service=service, pool=pool, clients=clients)


def request(**changes):
    return {"runId": uuid4().hex, "agentId": "custom-reply", "draft": None, "contextRunId": None, **changes}


def submit(client, preview, payload):
    response = client.post(f"/api/conversations/{preview.sid}/suggestion-runs", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def observe(client, preview, run_id):
    return data(client, f"/api/conversations/{preview.sid}/suggestion-runs/{run_id}")


def test_receipt_recovery_pins_prompt_and_preserves_results_without_reexecution(client, preview, sdk):
    payload = request()
    queued = submit(client, preview, payload)
    assert queued["status"] == "queued" and not preview.clients
    saved = data(client, "/api/agent/suggestion-agents")["agents"]
    preset = next(item for item in saved if item["apid"] == "custom-reply")
    assert client.post("/api/agent/presets", json={**preset, "prompt": "A later edit"}).status_code == 200
    assert submit(client, preview, payload)["id"] == queued["id"]
    assert len(preview.pool.jobs) == 1
    preview.pool.run()
    result = observe(client, preview, queued["id"])
    assert result["status"] == "completed"
    assert result["pinned"]["prompt"] == "Ask about delivery"
    assert "Ask about delivery" in preview.clients[0].requests[0].system_prompt
    assert "Need 500 tents" in preview.clients[0].requests[0].user_input
    assert len(result["result"]["items"]) == 2
    assert result["result"]["items"][0]["intentId"] is None
    assert not {"owner", "seller", "data_dir", "snapshot"}.intersection(result)
    context = get_account_context()
    assert IntentStore().list_conversation(preview.sid, seller=context.self_ali_id, data_dir=context.data_dir) == []
    assert submit(client, preview, payload) == result
    assert not preview.pool.jobs and len(preview.clients) == 1 and not sdk.calls
    changed = client.post(f"/api/conversations/{preview.sid}/suggestion-runs", json={**payload, "agentId": CHAT_REPLY_SUGGESTION_AGENT_APID})
    assert changed.status_code == 409


def test_draft_retests_reuse_context_without_blacklisting_previous_candidates(client, preview):
    first = submit(client, preview, request())
    preview.pool.run()
    preview.apply(("buyer", [row(2, text="Deliver in December", at=1756720100)]))
    draft = {"prompt": "Be brief", "level": 0, "tools": ["reply_customer"], "requires_tool": True}
    repeated = submit(client, preview, request(draft=draft, contextRunId=first["id"]))
    draft["prompt"] = "Edit made after submission"
    preview.pool.run()
    result = observe(client, preview, repeated["id"])
    assert result["status"] == "completed" and result["pinned"]["prompt"] == "Be brief"
    assert result["pinned"]["kind"] == "draft"
    assert result["context"] == first["context"]
    assert preview.clients[0].requests[0].user_input == preview.clients[1].requests[0].user_input
    assert "When do you need them?" not in preview.clients[1].requests[0].user_input
    assert result["result"]["items"][0]["content"] == "When do you need them?"
    assert AgentPresetManager().get_agent_preset("custom-reply").prompt == "Ask about delivery"
    fresh = submit(client, preview, request())
    preview.pool.run()
    assert fresh["context"]["lastMessageId"] != first["context"]["lastMessageId"]
    assert "Deliver in December" in preview.clients[2].requests[0].user_input


def test_adoption_is_idempotent_and_batch_acceptance_survives_later_inserts(client, preview):
    run = submit(client, preview, request())
    preview.pool.run()
    url = f"/api/conversations/{preview.sid}/suggestion-runs/{run['id']}/adopt"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result(5) for future in [pool.submit(client.post, url, json={"index": 0}) for _ in range(2)]]
    assert all(response.status_code == 200 for response in responses)
    first = responses[0].json()["data"]
    assert first == responses[1].json()["data"]
    context = get_account_context()
    store = IntentStore()
    scope = dict(seller=context.self_ali_id, data_dir=context.data_dir)
    records = store.list_batch(run["id"], **scope)
    assert len(records) == 2
    assert all(record["apid"] == "custom-reply" for record in records)
    store.transition(first["intentId"], "accepted", **scope)
    assert client.post(url, json={"index": 1}).status_code == 409
    assert len(store.list_batch(run["id"], **scope)) == 2


@pytest.mark.parametrize("phase", ["queued", "running"])
def test_account_switch_never_moves_old_work_to_new_account(client, preview, monkeypatch, phase):
    original = get_account_context()
    run = submit(client, preview, request())
    entered, release = Event(), Event()
    observed_accounts = []
    real_generate = module.generate_reply_suggestions

    def blocked_generate(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        observed_accounts.append(get_account_context())
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(module, "generate_reply_suggestions", blocked_generate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(preview.pool.run) if phase == "running" else None
        try:
            if future:
                assert entered.wait(5)
            config = app_config.read_app_config()
            app_config.write_app_config({**config, "self_ali_id": "seller-b"})
            client.headers["X-Account-Epoch"] = get_account_context().epoch
            assert client.get(f"/api/conversations/{preview.sid}/suggestion-runs/{run['id']}").status_code == 404
            assert client.post(f"/api/conversations/{preview.sid}/suggestion-runs/{run['id']}/adopt", json={"index": 0}).status_code == 404
        finally:
            release.set()
        if future:
            future.result(5)
        else:
            preview.pool.run()
    saved = preview.service.store.get(original, preview.sid, run["id"])
    assert saved["status"] == ("completed" if phase == "running" else "failed")
    assert observed_accounts == ([original] if phase == "running" else [])


def test_restart_observes_interrupted_work_and_never_replays_it(client, preview):
    run = submit(client, preview, request())
    restarted = SuggestionStore(preview.service.store.database_path, owner="new-process")
    record = restarted.get(get_account_context(), preview.sid, run["id"])
    assert record["status"] == "failed" and "重启" in record["error"]
    assert preview.clients == []


def test_regular_agent_keeps_partial_candidates_at_tool_limit(client, preview):
    register_llm(0, LLMConfig(base_url="https://example.invalid/v1", api_key="synthetic", model_name="fixture", max_tool_rounds=1))
    run = submit(client, preview, request())
    preview.pool.run()
    result = observe(client, preview, run["id"])
    assert result["status"] == "completed" and result["result"]["restricted"]
    assert result["result"]["items"][0]["content"] == "When do you need them?"


def test_prompt_save_rejects_stale_base_and_retains_history(client, preview):
    preset = next(item for item in data(client, "/api/agent/suggestion-agents")["agents"] if item["apid"] == "custom-reply")
    first = client.post("/api/agent/presets", json={**preset, "prompt": "P2", "expected_fingerprint": preset["fingerprint"]})
    assert first.status_code == 200
    stale = client.post("/api/agent/presets", json={**preset, "prompt": "P3", "expected_fingerprint": preset["fingerprint"]})
    assert stale.status_code == 409
    assert AgentPresetManager().get_agent_preset("custom-reply").prompt == "P2"
    assert data(client, "/api/agent/presets/custom-reply/revisions")[0]["prompt"] == "P2"


@pytest.mark.parametrize("change", [{"enabled": False}, {"tools": []}, {"tools": ["unregistered"]}])
def test_unavailable_agents_cannot_start_model_work(client, preview, change):
    preset = AgentPresetManager().get_agent_preset("custom-reply")
    for key, value in change.items():
        setattr(preset, key, value)
    AgentPresetManager().upsert_agent_preset(preset)
    response = client.post(f"/api/conversations/{preview.sid}/suggestion-runs", json=request())
    assert response.status_code == 409
    assert preview.pool.jobs == [] and preview.clients == []


def test_context_cannot_be_reused_by_a_different_conversation(client, preview):
    original = submit(client, preview, request())
    response = client.post(f"/api/conversations/{preview.sid + 1}/suggestion-runs",
                           json=request(contextRunId=original["id"]))
    assert response.status_code == 404
    assert len(preview.pool.jobs) == 1


def test_inserted_preview_submission_links_outbox_to_the_exact_run(client, preview, monkeypatch, sdk):
    from backend.app.api.routers import conversations
    from backend.app.shared.backend import outbox_service
    from backend.app.shared.backend.gui_session import GuiSessionToken
    from backend.app.shared.crm.outbox_store import OutboxStore
    from backend.tests.test_outbox_service import DeferredQueue

    run = submit(client, preview, request(draft={
        "prompt": "Tested prompt", "level": 0, "tools": ["reply_customer"], "requires_tool": True,
    }))
    preview.pool.run()
    url = f"/api/conversations/{preview.sid}/suggestion-runs/{run['id']}/adopt"
    item = client.post(url, json={"index": 0}).json()["data"]
    context = get_account_context()
    queue = DeferredQueue()
    service = outbox_service.OutboxService(OutboxStore(), queue)
    monkeypatch.setattr(outbox_service, "_service", service)
    monkeypatch.setattr(outbox_service, "RECONCILE_INTERVAL", 3600)
    monkeypatch.setattr(outbox_service.gui_session, "capture_gui_session", lambda epoch:
                        GuiSessionToken(context.self_ali_id, context.data_dir, context.epoch, "synthetic-window"))
    monkeypatch.setattr(conversations, "_ready", lambda: context.self_ali_id)
    monkeypatch.setattr(conversations.CRMAdapter, "get_user_info", lambda *args: SimpleNamespace(login_id="buyer-login"))
    payload = {"content": item["content"], "action": "send", "idempotency_key": "preview-send", "intent_id": item["intentId"]}
    try:
        response = client.post(f"/api/conversations/{preview.sid}/messages", json=payload)
        assert response.status_code == 200, response.text
        task = response.json()["data"]["outbox"]
        assert task["status"] == "queued" and len(queue.jobs) == 1
        saved = service.get(context, task["id"])
        assert saved["origin_request_id"] == item["intentId"]
        intent = IntentStore().get(item["intentId"], seller=context.self_ali_id, data_dir=context.data_dir)
        assert intent["status"] == "accepted" and intent["batch_id"] == run["id"]
        assert preview.service.require(context, preview.sid, intent["batch_id"])["snapshot"]["pinned"]["prompt"] == "Tested prompt"
        assert client.post(url, json={"index": 1}).status_code == 409
        assert client.post(f"/api/conversations/{preview.sid}/messages", json=payload).json()["data"]["outbox"] == task
        assert len(queue.jobs) == 1 and sdk.calls == []
    finally:
        service.stop()
