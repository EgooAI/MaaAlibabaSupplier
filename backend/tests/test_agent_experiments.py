"""Experiments: creation from the three starting points, runs, and promotion."""

import time

import pytest

from backend.tests.test_connection_api import client, sdk  # noqa: F401 - fixtures
from backend.app.api.routers import agent as agent_router
from backend.app.shared.agent.experiment_runs import runs
from backend.app.shared.agent.runner import ChatAgentTurn
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager, ChatHistoryManager

AGENT = "agent-experiment-test"


@pytest.fixture(autouse=True)
def preset():
    from agent_pipeline.registry import register_tool

    from backend.app.shared.agent.reply_tool import TOOL_SPEC, reply_customer

    register_tool("reply_customer", reply_customer, spec=TOOL_SPEC)
    AgentPresetManager().upsert_agent_preset(AgentPreset(
        apid=AGENT, name="实验 Agent", description="d", prompt="基准提示词",
        llm_level=0, tools=["reply_customer"], requires_tool=False,
    ))
    yield
    # The tool registry is process-global and other suites assert its exact
    # contents, so a registration here must not outlive the module.
    from agent_pipeline.registry import tool_registry

    tool_registry.clear()


def _create(client, **body):
    payload = {"agentId": AGENT, "source": {"kind": "text", "text": "买家：在吗"},
               "variants": [{"label": "A"}], **body}
    return client.post("/api/agent/experiments", json=payload)


def _captured_run(monkeypatch, *, text="回答", wait=None):
    calls: list[dict] = []

    def fake_run(apid, user_input, **kwargs):
        calls.append({"apid": apid, "user_input": user_input, **kwargs})
        if wait is not None:
            wait.wait(5)
        preset = kwargs.get("preset_override")
        return ChatAgentTurn(
            text=f"{text}:{(preset.prompt if preset else '')}", apid=apid,
            agent_name="实验 Agent", mode=kwargs.get("mode", "live"),
        )

    monkeypatch.setattr(agent_router, "run_chat_agent", fake_run)
    return calls


def _wait_for_run(client, run_id: str, *, expected: str = "completed") -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/agent/runs/{run_id}")
        assert response.status_code == 200, response.text
        state = response.json()["data"]
        if state["status"] != "running":
            assert state["status"] == expected, state
            return state
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_create_from_text_keeps_the_starting_point_and_variants(client):
    response = _create(client, name="我的对比", variants=[
        {"label": "保存版"},
        {"label": "草稿版", "versionKind": "draft", "draft": {
            "prompt": "草稿提示词", "level": 0, "tools": ["reply_customer"],
        }},
    ])
    assert response.status_code == 200, response.text
    experiment = response.json()["data"]
    assert experiment["name"] == "我的对比"
    assert experiment["frozenPrefix"] == "买家：在吗"
    assert experiment["source"] == {"kind": "text"}
    assert [variant["label"] for variant in experiment["variants"]] == ["保存版", "草稿版"]
    assert [variant["title"] for variant in experiment["variants"]] == [
        "实验 Agent · 保存版", "实验 Agent · 草稿版",
    ]
    saved, draft = experiment["variants"]
    assert saved["pinned"]["kind"] == "preset"
    assert saved["type"] == "playground"
    assert draft["pinned"]["kind"] == "draft"
    assert draft["pinned"]["prompt"] == "草稿提示词"
    # Every variant owns a real session row attached to the experiment.
    for variant in experiment["variants"]:
        hist = ChatHistoryManager().get_chat_history(int(variant["id"]))
        assert hist is not None and hist.experiment_id == int(experiment["id"])

    listing = client.get("/api/agent/experiments", params={"agentId": AGENT}).json()["data"]
    assert [item["id"] for item in listing] == [experiment["id"]]
    assert listing[0]["variants"][0]["turnCount"] == 0
    assert "messages" not in listing[0]["variants"][0]
    assert client.get("/api/agent/experiments", params={"agentId": "agent-other"}).json()["data"] == []


def test_create_rejects_bad_sources_and_too_many_variants(client):
    assert _create(client, source={"kind": "text"}, variants=[]).status_code == 422
    assert _create(client, source={"kind": "conversation"}).status_code == 422
    assert _create(client, source={"kind": "session"}).status_code == 422
    assert _create(client, source={"kind": "nope", "text": "x"}).status_code == 422
    assert _create(client, agentId="agent-missing").status_code == 404
    assert _create(client, variants=[{"label": str(index)} for index in range(5)]).status_code == 422
    assert _create(client, variants=[{"label": "d", "versionKind": "draft"}]).status_code == 422


def test_create_from_session_inherits_its_turns(client, monkeypatch):
    _captured_run(monkeypatch)
    session = client.post("/api/agent/test", json={
        "agentId": AGENT, "content": "买家第一条",
    }).json()["data"]["session"]

    response = _create(client, source={"kind": "session", "sessionId": session["id"]})
    assert response.status_code == 200, response.text
    experiment = response.json()["data"]
    assert experiment["source"]["kind"] == "session"
    assert experiment["source"]["sessionId"] == session["id"]
    variant = experiment["variants"][0]
    assert [message["role"] for message in variant["messages"]] == ["user", "assistant"]
    assert variant["messages"][0]["content"] == "买家第一条"

    other = client.post("/api/agent/test", json={"agentId": AGENT, "content": "x"}).json()["data"]["session"]
    assert _create(client, source={"kind": "session", "sessionId": other["id"]},
                   agentId="agent-other").status_code == 404


def test_run_replays_one_step_against_each_pinned_version(client, monkeypatch):
    response = _create(client, variants=[
        {"label": "保存版"},
        {"label": "草稿版", "versionKind": "draft", "draft": {
            "prompt": "草稿提示词", "level": 0, "tools": ["reply_customer"],
        }},
    ])
    experiment = response.json()["data"]
    added = client.post(f"/api/agent/experiments/{experiment['id']}/steps", json={
        "mode": "buyer", "text": "买家：能便宜吗", "label": "砍价",
    })
    assert added.status_code == 200, added.text
    assert added.json()["data"]["steps"] == [
        {"index": 0, "mode": "buyer", "text": "买家：能便宜吗", "label": "砍价"}
    ]
    assert client.post(f"/api/agent/experiments/{experiment['id']}/steps", json={
        "mode": "buyer", "text": "  ",
    }).status_code == 422
    assert client.post(f"/api/agent/experiments/{experiment['id']}/steps", json={
        "mode": "nope", "text": "x",
    }).status_code == 422

    calls = _captured_run(monkeypatch)
    started = client.post(f"/api/agent/experiments/{experiment['id']}/runs", json={"stepIndex": 0})
    assert started.status_code == 200, started.text
    state = _wait_for_run(client, started.json()["data"]["runId"])
    assert [variant["status"] for variant in state["variants"]] == ["completed", "completed"]
    assert all(variant["durationMs"] > 0 for variant in state["variants"])

    # Each variant ran its own pinned version, in simulated mode.
    assert sorted(call["preset_override"].prompt for call in calls) == ["基准提示词", "草稿提示词"]
    assert {call["mode"] for call in calls} == {"simulated"}
    assert all("买家：能便宜吗" in call["user_input"] for call in calls)

    detail = client.get(f"/api/agent/experiments/{experiment['id']}").json()["data"]
    for variant, prompt in zip(detail["variants"], ("基准提示词", "草稿提示词")):
        assert variant["messages"][-1]["content"] == f"回答:{prompt}"
        assert variant["messages"][-2]["content"] == "买家：能便宜吗"


def test_run_conflict_returns_the_active_run_id_and_cancel_reaches_the_token(client, monkeypatch):
    from threading import Event

    experiment = _create(client).json()["data"]
    client.post(f"/api/agent/experiments/{experiment['id']}/steps",
                json={"mode": "buyer", "text": "第一句"})
    release = Event()
    _captured_run(monkeypatch, wait=release)
    try:
        first = client.post(f"/api/agent/experiments/{experiment['id']}/runs", json={"stepIndex": 0})
        assert first.status_code == 200, first.text
        run_id = first.json()["data"]["runId"]
        second = client.post(f"/api/agent/experiments/{experiment['id']}/runs", json={"stepIndex": 0})
        assert second.status_code == 409
        assert second.json()["data"]["runId"] == run_id

        cancelled = client.delete(f"/api/agent/runs/{run_id}")
        assert cancelled.status_code == 200
        assert runs.get(run_id).token.cancelled
    finally:
        release.set()
    # A variant already in flight finishes before the run observes the cancel,
    # so either outcome is correct here; the run must still reach a terminal state.
    state = _wait_for_run(client, run_id)
    assert state["status"] in ("cancelled", "completed")


def test_a_cancelled_token_marks_every_variant_cancelled(client, monkeypatch):
    import agent_pipeline.experiment_runner as sdk_runner

    experiment = _create(client).json()["data"]
    client.post(f"/api/agent/experiments/{experiment['id']}/steps",
                json={"mode": "buyer", "text": "第一句"})

    def cancelled_run(*, steps, variant_ids, execute, cancel_token=None, max_parallel=4):
        # Same contract as the SDK runner: a cancelled token stops before the
        # step and reports every unfinished variant as cancelled. The states are
        # written through the registry the caller polls, as the real runner's
        # execute callback would.
        state = runs.active_for(int(experiment["id"]))
        deadline = time.monotonic() + 10
        while not cancel_token.cancelled:
            assert time.monotonic() < deadline, "cancel never reached the run"
            time.sleep(0.01)
        for variant_id in variant_ids:
            state.update(variant_id, status="cancelled")
        return sdk_runner.ExperimentRun(cancelled=True, steps=[
            sdk_runner.StepRun(step=steps[0], variants=[
                sdk_runner.VariantRun(variant_id=variant_id, status="cancelled")
                for variant_id in variant_ids
            ])
        ])

    monkeypatch.setattr(sdk_runner, "run_experiment", cancelled_run)
    started = client.post(f"/api/agent/experiments/{experiment['id']}/runs", json={"stepIndex": 0})
    run_id = started.json()["data"]["runId"]
    assert client.delete(f"/api/agent/runs/{run_id}").status_code == 200
    state = _wait_for_run(client, run_id, expected="cancelled")
    assert state["status"] == "cancelled"
    assert [variant["status"] for variant in state["variants"]] == ["cancelled"]


def test_rejected_step_marks_the_variant_failed_with_its_reason(client, monkeypatch):
    from backend.app.shared.agent.runner import AgentRunError

    experiment = _create(client).json()["data"]
    client.post(f"/api/agent/experiments/{experiment['id']}/steps",
                json={"mode": "buyer", "text": "第一句"})

    def failing(apid, user_input, **kwargs):
        raise AgentRunError("LLM level 0 is not registered", status_code=409)

    monkeypatch.setattr(agent_router, "run_chat_agent", failing)
    started = client.post(f"/api/agent/experiments/{experiment['id']}/runs", json={"stepIndex": 0})
    state = _wait_for_run(client, started.json()["data"]["runId"], expected="failed")
    assert state["variants"][0]["status"] == "failed"
    assert "not registered" in state["variants"][0]["error"]


def test_variants_can_be_added_and_removed_after_a_step(client, monkeypatch):
    _captured_run(monkeypatch)
    experiment = _create(client).json()["data"]
    client.post(f"/api/agent/experiments/{experiment['id']}/steps",
                json={"mode": "buyer", "text": "第一句"})
    started = client.post(f"/api/agent/experiments/{experiment['id']}/runs", json={"stepIndex": 0})
    _wait_for_run(client, started.json()["data"]["runId"])

    added = client.post(f"/api/agent/experiments/{experiment['id']}/variants", json={"label": "后来者"})
    assert added.status_code == 200, added.text
    variants = added.json()["data"]["variants"]
    assert [variant["label"] for variant in variants] == ["A", "后来者"]
    # The newcomer joins the comparison at the same scripted input but answers
    # it itself: it inherits the buyer turns, never another variant's answers.
    assert [message["role"] for message in variants[1]["messages"]] == ["user"]
    assert variants[1]["messages"][0]["content"] == "第一句"

    removed = client.delete(
        f"/api/agent/experiments/{experiment['id']}/variants/{variants[1]['id']}"
    )
    assert removed.status_code == 200, removed.text
    assert [variant["label"] for variant in removed.json()["data"]["variants"]] == ["A"]
    assert ChatHistoryManager().get_chat_history(int(variants[1]["id"])) is None


def test_annotations_and_notes_are_replaced_wholesale(client):
    experiment = _create(client).json()["data"]
    variant_id = experiment["variants"][0]["id"]
    client.post(f"/api/agent/experiments/{experiment['id']}/steps",
                json={"mode": "buyer", "text": "第一句"})

    response = client.patch(f"/api/agent/experiments/{experiment['id']}", json={
        "name": "改名", "notes": "备注",
        "annotations": [{"step": 0, "variantId": variant_id, "verdict": "better", "note": "更自然"}],
    })
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["name"] == "改名"
    assert data["notes"] == "备注"
    assert data["annotations"] == [
        {"step": 0, "variantId": variant_id, "verdict": "better", "note": "更自然"}
    ]

    replaced = client.patch(f"/api/agent/experiments/{experiment['id']}", json={"annotations": []})
    assert replaced.json()["data"]["annotations"] == []
    for bad in (
        [{"step": 9, "variantId": variant_id, "verdict": ""}],
        [{"step": 0, "variantId": "999999", "verdict": ""}],
        [{"step": 0, "variantId": variant_id, "verdict": "maybe"}],
    ):
        assert client.patch(
            f"/api/agent/experiments/{experiment['id']}", json={"annotations": bad}
        ).status_code == 422


def test_promote_saves_the_variant_version_as_the_agent_prompt(client):
    experiment = _create(client, variants=[
        {"label": "草稿版", "versionKind": "draft", "draft": {
            "prompt": "更好的提示词", "level": 0, "tools": ["reply_customer"],
        }},
    ]).json()["data"]
    variant_id = experiment["variants"][0]["id"]

    response = client.post(
        f"/api/agent/experiments/{experiment['id']}/promote",
        json={"variantId": variant_id, "note": "实验胜出"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["prompt"] == "更好的提示词"
    assert AgentPresetManager().get_agent_preset(AGENT).prompt == "更好的提示词"

    revisions = client.get(f"/api/agent/presets/{AGENT}/revisions").json()["data"]
    assert revisions[0]["prompt"] == "更好的提示词"
    assert revisions[0]["note"] == "实验胜出"


def test_a_step_cannot_be_replayed_into_the_same_transcript(client, monkeypatch):
    _captured_run(monkeypatch)
    experiment = _create(client).json()["data"]
    for text in ("第一句", "第二句"):
        client.post(f"/api/agent/experiments/{experiment['id']}/steps",
                    json={"mode": "buyer", "text": text})
    base = f"/api/agent/experiments/{experiment['id']}/runs"

    # A later step needs its predecessor: the comparison must stay linear.
    ahead = client.post(base, json={"stepIndex": 1})
    assert ahead.status_code == 409
    assert "先运行上一步" in ahead.json()["msg"]

    first = client.post(base, json={"stepIndex": 0})
    _wait_for_run(client, first.json()["data"]["runId"])
    progress = client.get(f"/api/agent/experiments/{experiment['id']}").json()["data"]["progress"]
    assert progress["0"]["variants"] == [experiment["variants"][0]["id"]]
    assert progress["0"]["before"][experiment["variants"][0]["id"]] == 0

    replay = client.post(base, json={"stepIndex": 0})
    assert replay.status_code == 409, replay.text
    assert client.post(base, json={"stepIndex": 1}).status_code == 200
    # An explicit replay is allowed and truncates to the checkpoint, so the
    # transcript does not accumulate the same buyer message twice.
    forced = client.post(base, json={"stepIndex": 0, "force": True})
    assert forced.status_code == 200, forced.text
    _wait_for_run(client, forced.json()["data"]["runId"])
    progress = client.get(f"/api/agent/experiments/{experiment['id']}").json()["data"]["progress"]
    assert progress["0"]["variants"] == [experiment["variants"][0]["id"]]
    variant = client.get(f"/api/agent/experiments/{experiment['id']}").json()["data"]["variants"][0]
    assert [message["content"] for message in variant["messages"] if message["role"] == "user"] == ["第一句"]


def test_delete_experiment_removes_its_variants(client):
    experiment = _create(client, variants=[{"label": "A"}, {"label": "B"}]).json()["data"]
    variant_ids = [variant["id"] for variant in experiment["variants"]]

    assert client.delete(f"/api/agent/experiments/{experiment['id']}").status_code == 200
    assert client.get(f"/api/agent/experiments/{experiment['id']}").status_code == 404
    for variant_id in variant_ids:
        assert ChatHistoryManager().get_chat_history(int(variant_id)) is None
    assert client.get("/api/agent/test-history").json()["data"] == []


def test_experiments_are_invisible_to_another_seller(client):
    experiment = _create(client).json()["data"]

    client.put("/api/settings/ali-id", json={"ali_id": "seller-b"})
    from backend.app.shared.backend import account_context

    client.headers["X-Account-Epoch"] = account_context.get_account_context().epoch

    assert client.get(f"/api/agent/experiments/{experiment['id']}").status_code == 404
    assert client.get("/api/agent/experiments").json()["data"] == []


def test_failed_variant_reports_the_runner_error(client, monkeypatch):
    """A runner failure is reported per variant instead of failing the request."""
    experiment = _create(client).json()["data"]
    client.post(f"/api/agent/experiments/{experiment['id']}/steps",
                json={"mode": "agent", "text": ""})

    def boom(apid, user_input, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_router, "run_chat_agent", boom)
    started = client.post(
        f"/api/agent/experiments/{experiment['id']}/runs", json={"stepIndex": 0}
    )
    state = _wait_for_run(client, started.json()["data"]["runId"], expected="failed")
    assert state["variants"][0]["error"] == "boom"
    assert client.get("/api/agent/runs/unknown").status_code == 404
