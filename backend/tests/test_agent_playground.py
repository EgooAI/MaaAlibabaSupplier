"""Playground forks: frozen prefix fidelity, pinned versions and account isolation."""

import os
from unittest import mock

import pytest

from backend.app.api.routers import agent as agent_router
from backend.app.shared.agent.chat_history_content import build_history_content
from backend.app.shared.agent.inputs import build_reply_suggestion_input
from backend.app.shared.agent.reply_tool import TOOL_SPEC, reply_customer
from backend.app.shared.agent.runner import ChatAgentTurn
from backend.app.shared.agent.system_agents import CHAT_REPLY_SUGGESTION_AGENT_APID
from backend.app.shared.backend import account_context
from backend.app.shared.crm import sync
from backend.app.shared.crm.message_markdown import AGENT_OPTIONS, transcript_markdown
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager, ChatHistory, ChatHistoryManager
from backend.app.shared.crm.views import CrmResolver
from backend.tests.test_connection_api import client, sdk  # noqa: F401 - fixtures
from backend.tests.test_inbox_api import archive, row  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def business_tools():
    from agent_pipeline import register_tool

    register_tool("reply_customer", reply_customer, spec=TOOL_SPEC)
    _seed_preset()
    yield


def _seed_preset(prompt: str = "你是建议 Agent") -> None:
    AgentPresetManager().upsert_agent_preset(AgentPreset(
        apid=CHAT_REPLY_SUGGESTION_AGENT_APID, name="建议", description="d", prompt=prompt,
        llm_level=0, tools=["reply_customer"], requires_tool=True,
    ))


def _fork(client, sid: int, index: int, **extra):
    payload = {"agentId": CHAT_REPLY_SUGGESTION_AGENT_APID, "conversationId": sid,
               "messageIndex": index, **extra}
    return client.post("/api/agent/fork", json=payload)


def _captured_run(monkeypatch):
    calls: list[dict] = []

    def fake_run(apid, user_input, **kwargs):
        calls.append({"apid": apid, "user_input": user_input, **kwargs})
        return ChatAgentTurn(
            text="理由", apid=apid, agent_name="建议", mode=kwargs.get("mode", "live"),
            intents=[{
                "intentId": None, "kind": "reply_customer", "status": "simulated",
                "reply": "Sure", "zh": "好的", "tone": "formal",
            }],
        )

    monkeypatch.setattr(agent_router, "run_chat_agent", fake_run)
    return calls


def test_two_forks_from_one_message_receive_identical_frozen_input(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [row(1, text="first"), row(2, text="second", at=1_700_000_100)]))
    listing = client.get("/api/conversations").json()["data"]["items"]
    sid = listing[0]["sid"]

    first = _fork(client, sid, 1)
    second = _fork(client, sid, 1)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_session = first.json()["data"]
    second_session = second.json()["data"]
    assert first_session["type"] == "fork"
    assert first_session["source"]["messageIndex"] == 1
    assert first_session["source"]["conversationId"] == sid

    calls = _captured_run(monkeypatch)
    for session in (first_session, second_session):
        response = client.post("/api/agent/test", json={
            "agentId": CHAT_REPLY_SUGGESTION_AGENT_APID,
            "sessionId": session["id"], "content": "买家追问交期", "inputMode": "buyer",
        })
        assert response.status_code == 200, response.text
        stored = response.json()["data"]["session"]
        assert stored["type"] == "fork", stored
        assert stored["messages"][-1]["mode"] == calls[-1]["mode"], (stored["messages"], calls)
        assert stored["messages"][-1]["mode"] == "simulated"

    assert len(calls) == 2
    assert calls[0]["user_input"] == calls[1]["user_input"]
    assert calls[0]["mode"] == calls[1]["mode"] == "simulated"

    # The frozen prefix is exactly the live projection of the conversation up to
    # the chosen message; the session only appends the new turn after it.
    context = account_context.get_account_context()
    conv = sync.CRMAdapter().get_conversation_detail(context.self_ali_id, sid)
    index = first_session["source"]["messageIndex"]
    frozen = transcript_markdown(
        conv.messages[: index + 1],
        resolver=CrmResolver(context.self_ali_id), options=AGENT_OPTIONS, limit=None,
    )
    expected = build_reply_suggestion_input(
        f"{frozen}\n\n## 继续对话\n\n**User**\n\n买家追问交期"
    )
    assert calls[0]["user_input"] == expected


def test_pinned_revision_changes_behaviour_but_keeps_the_frozen_prefix(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [row(1, text="hello")]))
    sid = client.get("/api/conversations").json()["data"]["items"][0]["sid"]
    # Two revisions: the fork pins the older prompt, the live preset keeps the newer.
    from backend.app.shared.agent.preset_history import list_preset_revisions, record_preset_revision

    older_preset = AgentPreset(
        apid=CHAT_REPLY_SUGGESTION_AGENT_APID, name="建议", description="d",
        prompt="旧提示词", llm_level=0, tools=["reply_customer"], requires_tool=True,
    )
    AgentPresetManager().upsert_agent_preset(older_preset)
    record_preset_revision(older_preset)
    older = list_preset_revisions(CHAT_REPLY_SUGGESTION_AGENT_APID)[0]
    AgentPresetManager().upsert_agent_preset(AgentPreset(
        apid=CHAT_REPLY_SUGGESTION_AGENT_APID, name="建议", description="d",
        prompt="新提示词", llm_level=0, tools=["reply_customer"], requires_tool=True,
    ))
    record_preset_revision(AgentPreset(
        apid=CHAT_REPLY_SUGGESTION_AGENT_APID, name="建议", description="d",
        prompt="新提示词", llm_level=0, tools=["reply_customer"], requires_tool=True,
    ))

    response = _fork(client, sid, 0, revisionId=older.id)
    assert response.status_code == 200, response.text
    session = response.json()["data"]
    assert session["pinned"]["revision_id"] == older.id
    assert session["pinned"]["prompt"] == "旧提示词"

    calls = _captured_run(monkeypatch)
    run = client.post("/api/agent/test", json={
        "agentId": CHAT_REPLY_SUGGESTION_AGENT_APID, "sessionId": session["id"],
        "content": "hi", "inputMode": "buyer",
    })
    assert run.status_code == 200, run.text
    assert calls[0]["preset_override"].prompt == "旧提示词"


def test_forked_sessions_are_invisible_to_another_seller(client, archive):
    _, apply = archive
    apply(("buyer", [row(1, text="hello")]))
    sid = client.get("/api/conversations").json()["data"]["items"][0]["sid"]
    session = _fork(client, sid, 0).json()["data"]

    client.put("/api/settings/ali-id", json={"ali_id": "seller-b"})
    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch

    for method, path in (
        ("get", f"/api/agent/sessions/{session['id']}"),
        ("post", f"/api/agent/test-history/{session['id']}/undo"),
        ("post", f"/api/agent/test-history/{session['id']}/branch"),
        ("delete", f"/api/agent/test-history/{session['id']}"),
    ):
        response = client.request(method, path)
        assert response.status_code == 404, (method, path, response.text)
    run = client.post("/api/agent/test", json={
        "agentId": CHAT_REPLY_SUGGESTION_AGENT_APID, "sessionId": session["id"], "content": "hi",
    })
    assert run.status_code == 404
    assert client.get("/api/agent/test-history").json()["data"] == []


def test_agent_input_mode_does_not_append_a_buyer_message(client, archive, monkeypatch):
    _, apply = archive
    apply(("buyer", [row(1, text="hello")]))
    sid = client.get("/api/conversations").json()["data"]["items"][0]["sid"]
    session = _fork(client, sid, 0).json()["data"]
    calls = _captured_run(monkeypatch)

    response = client.post("/api/agent/test", json={
        "agentId": CHAT_REPLY_SUGGESTION_AGENT_APID, "sessionId": session["id"],
        "content": "", "inputMode": "agent",
    })
    assert response.status_code == 200, response.text
    messages = response.json()["data"]["session"]["messages"]
    assert [message["role"] for message in messages] == ["assistant"]
    assert calls[0]["user_input"].count("hello") == 1


def test_fork_rejects_unknown_message_and_unscoped_agent(client, archive):
    _, apply = archive
    apply(("buyer", [row(1, text="hello")]))
    sid = client.get("/api/conversations").json()["data"]["items"][0]["sid"]
    assert _fork(client, sid, 99).status_code == 404
    assert _fork(client, 9999, 0).status_code == 404
    assert _fork(client, sid, 0, agentId="agent-missing").status_code == 404


def test_playground_session_endpoint_rejects_regular_test_session(client):
    hist = ChatHistory(name="regular", content=build_history_content(
        apid="agent-test", agent_name="Test", messages=[], session_type="test",
    ))
    ChatHistoryManager().add_chat_history(hist)

    response = client.get(f"/api/agent/sessions/{hist.id}")

    assert response.status_code == 404


def test_legacy_unscoped_session_is_adopted_on_first_write(client, archive, monkeypatch):
    AgentPresetManager().upsert_agent_preset(AgentPreset(
        apid="agent-legacy", name="L", description="d", prompt="p", llm_level=0, tools=[]))
    hist = ChatHistory(name="legacy", content=build_history_content(
        apid="agent-legacy", agent_name="L",
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
    ))
    ChatHistoryManager().add_chat_history(hist)
    listing = client.get("/api/agent/test-history").json()["data"]
    assert [session["id"] for session in listing] == [str(hist.id)]

    calls = _captured_run(monkeypatch)
    response = client.post("/api/agent/test", json={
        "agentId": "agent-legacy", "sessionId": str(hist.id), "content": "next",
    })
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    stored = ChatHistoryManager().get_chat_history(hist.id)
    assert stored is not None
    from backend.app.shared.agent.chat_history_content import load_history_content

    scope = load_history_content(stored.content).scope
    assert scope is not None and scope.seller
