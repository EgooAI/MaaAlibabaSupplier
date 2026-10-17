import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.api.main import create_app
from backend.tests.auth_client import authenticate
from backend.app.shared.agent.chat_history_content import build_history_content
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager, ChatHistory, ChatHistoryManager

TRANSLATION_APID = "agent-1bad27aabaac439da678f31d53855b5d"


def _seed_session(title: str = "seed") -> int:
    manager = ChatHistoryManager()
    hist = ChatHistory(
        name=title,
        content=build_history_content(
            apid="agent-test-x",
            agent_name="Test",
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        ),
    )
    manager.add_chat_history(hist)
    assert hist.id is not None
    return hist.id


class AgentConsoleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.enterContext(mock.patch.dict(os.environ, {
            "MAA_CRM_DB_PATH": str(Path(self.temp_dir.name) / "crm.sqlite"),
        }))
        self.client = authenticate(TestClient(create_app(), raise_server_exceptions=False))
        self.addCleanup(self.client.close)

    def tearDown(self) -> None:
        try:
            ChatHistoryManager().engine.dispose()
        finally:
            self.temp_dir.cleanup()

    def test_llm_config_roundtrip(self) -> None:
        payload = {
            "level": 4, "base_url": "https://llm.example", "api_key": "k",
            "model_name": "m", "system_prompt": "", "context": 8000, "max_tool_rounds": 3,
        }
        saved = self.client.put("/api/agent/llm-config", json=payload).json()
        self.assertEqual(saved["code"], 0)
        self.assertEqual(saved["data"]["model_name"], "m")
        body = self.client.get("/api/agent/console").json()
        self.assertEqual(body["code"], 0)
        console = body["data"]
        for key in ("llmLevels", "agents", "agentPresets", "history"):
            self.assertIn(key, console)
        self.assertTrue(any(level["level"] == 4 for level in console["llmLevels"]))

    def test_llm_config_rejects_blank_required_fields(self) -> None:
        base = {
            "level": 0, "base_url": "https://llm.example", "api_key": "k",
            "model_name": "m", "system_prompt": "", "context": 8000, "max_tool_rounds": 3,
        }
        for field in ("base_url", "api_key", "model_name"):
            payload = dict(base, **{field: "   "})
            resp = self.client.put("/api/agent/llm-config", json=payload)
            self.assertEqual(resp.status_code, 422)

    def test_preset_crud_and_system_guard(self) -> None:
        created = self.client.post("/api/agent/presets", json={
            "apid": "", "name": "N", "description": "D", "prompt": "P",
            "intelevel": 2, "tools": ["crm_query"], "enabled": True,
            "updated_at": "", "category": "regular",
        }).json()
        self.assertEqual(created["code"], 0)
        apid = created["data"]["apid"]
        self.assertTrue(apid.startswith("agent-"))
        deleted = self.client.delete(f"/api/agent/presets/{apid}").json()
        self.assertEqual(deleted, {"code": 0, "msg": "ok", "data": True})
        guard = self.client.delete(f"/api/agent/presets/{TRANSLATION_APID}").json()
        self.assertEqual(guard["data"], False)

    def test_preset_enabled_roundtrip_and_system_lock(self) -> None:
        created = self.client.post("/api/agent/presets", json={
            "apid": "", "name": "N", "description": "D", "prompt": "P",
            "intelevel": 0, "tools": [], "enabled": False,
        }).json()["data"]
        apid = created["apid"]
        self.assertFalse(created["enabled"])

        console = self.client.get("/api/agent/console").json()["data"]
        listed = next(agent for agent in console["agents"] if agent["id"] == apid)
        self.assertFalse(listed["enabled"])

        toggled = self.client.post("/api/agent/presets", json={
            "apid": apid, "name": "N", "description": "D", "prompt": "P",
            "intelevel": 0, "tools": [], "enabled": True,
        }).json()["data"]
        self.assertTrue(toggled["enabled"])
        console = self.client.get("/api/agent/console").json()["data"]
        listed = next(agent for agent in console["agents"] if agent["id"] == apid)
        self.assertTrue(listed["enabled"])

        locked = self.client.post("/api/agent/presets", json={
            "apid": TRANSLATION_APID, "name": "翻译", "description": "D", "prompt": "P",
            "intelevel": 0, "tools": [], "enabled": False,
        }).json()["data"]
        self.assertTrue(locked["enabled"])

    def test_preset_revisions_record_apply_and_dedupe(self) -> None:
        def save(prompt: str, **extra: object) -> dict:
            return self.client.post("/api/agent/presets", json={
                "apid": apid, "name": "N", "description": "D", "prompt": prompt,
                "intelevel": 0, "tools": [], "enabled": True, **extra,
            }).json()["data"]

        def revisions() -> list[dict]:
            body = self.client.get(f"/api/agent/presets/{apid}/revisions").json()
            self.assertEqual(body["code"], 0)
            return body["data"]

        apid = self.client.post("/api/agent/presets", json={
            "apid": "", "name": "N", "description": "D", "prompt": "P1",
            "intelevel": 0, "tools": [], "enabled": True,
        }).json()["data"]["apid"]
        self.assertEqual([item["prompt"] for item in revisions()], ["P1"])
        saved = save("P1")
        self.assertEqual(len(revisions()), 1)
        self.assertEqual(saved["updated_at"], revisions()[0]["created_at"])

        saved = save("P2", revision_source="manual", revision_note="按需改写")
        self.assertEqual([item["prompt"] for item in revisions()], ["P2", "P1"])
        head = revisions()[0]
        self.assertEqual(head["source"], "manual")
        self.assertEqual(head["note"], "按需改写")
        self.assertEqual(saved["updated_at"], head["created_at"])

        unknown_source = save("P3", revision_source="totally-made-up")
        self.assertEqual(unknown_source["updated_at"] != "", True)
        self.assertEqual(revisions()[0]["source"], "manual")

        invalid_limit = self.client.get(f"/api/agent/presets/{apid}/revisions?limit=0").json()
        self.assertEqual(invalid_limit["code"], 1)
        missing = self.client.get("/api/agent/presets/agent-nope/revisions").json()
        self.assertEqual(missing["code"], 0)
        self.assertEqual(missing["data"], [])

    def test_restore_system_preset(self) -> None:
        body = self.client.post(f"/api/agent/system/{TRANSLATION_APID}/restore").json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["category"], "system")
        missing = self.client.post("/api/agent/system/agent-nope/restore").json()
        self.assertEqual(missing["code"], 1)

    def test_run_validation_errors(self) -> None:
        self.assertEqual(self.client.post("/api/agent/test", json={}).json()["code"], 1)
        self.assertEqual(
            self.client.post("/api/agent/test", json={"agentId": "agent-nope", "content": "hi"}).json()["code"], 1
        )
        AgentPresetManager().upsert_agent_preset(AgentPreset(
            apid="agent-test-x", name="T", description="D", prompt="P", llm_level=0, tools=[]))
        no_content = self.client.post(
            "/api/agent/test", json={"agentId": "agent-test-x"}).json()
        self.assertEqual(no_content["code"], 1)

    def test_draft_test_run_snapshots_unsaved_prompt(self) -> None:
        AgentPresetManager().upsert_agent_preset(AgentPreset(
            apid="agent-draft", name="草稿测试", description="D",
            prompt="已保存提示词", llm_level=0, tools=[]))
        overrides: list[str] = []

        def fake_runner(_apid: str, _user_input: str, *, preset_override: object = None) -> str:
            overrides.append(preset_override.prompt if preset_override is not None else "(saved)")
            return "回复"

        with mock.patch(
            "backend.app.api.routers.agent.run_chat_tool_agent", side_effect=fake_runner,
        ):
            created = self.client.post("/api/agent/test", json={
                "agentId": "agent-draft", "content": "你好",
                "draft": {"prompt": "草稿提示词", "level": 0, "tools": []},
            }).json()["data"]
            self.assertTrue(created["session"]["draft"])
            session_id = created["session"]["id"]
            continued = self.client.post("/api/agent/test", json={
                "agentId": "agent-draft", "content": "继续", "sessionId": session_id,
                "draft": {"prompt": "另一个草稿", "level": 0, "tools": []},
            }).json()["data"]

        self.assertTrue(continued["session"]["draft"])
        self.assertEqual(overrides, ["草稿提示词", "草稿提示词"])

    def test_disabled_regular_agent_cannot_run(self) -> None:
        AgentPresetManager().upsert_agent_preset(AgentPreset(
            apid="agent-off", name="Off", description="D", prompt="P", llm_level=0, tools=[], enabled=False))
        body = self.client.post("/api/agent/test", json={
            "agentId": "agent-off", "content": "hi",
            "draft": {"prompt": "draw", "level": 0, "tools": []},
        }).json()
        self.assertEqual(body["code"], 1)

    def test_saved_preset_run_has_no_draft_snapshot(self) -> None:
        AgentPresetManager().upsert_agent_preset(AgentPreset(
            apid="agent-saved", name="S", description="D", prompt="P", llm_level=0, tools=[]))
        with mock.patch(
            "backend.app.api.routers.agent.run_chat_tool_agent", return_value="回复",
        ) as runner:
            created = self.client.post("/api/agent/test", json={
                "agentId": "agent-saved", "content": "你好",
                "draft": {"prompt": "P", "level": 0, "tools": []},
            }).json()["data"]
        self.assertFalse(created["session"]["draft"])
        self.assertIsNone(runner.call_args.kwargs["preset_override"])

    def test_history_undo_branch_delete(self) -> None:
        raw_id = _seed_session()
        history = self.client.get("/api/agent/test-history").json()
        self.assertEqual(history["code"], 0)
        self.assertTrue(any(session["id"] == str(raw_id) for session in history["data"]))

        undone = self.client.post(f"/api/agent/test-history/{raw_id}/undo").json()
        self.assertEqual(undone["code"], 0)
        self.assertEqual(undone["data"]["messages"], [])
        again = self.client.post(f"/api/agent/test-history/{raw_id}/undo").json()
        self.assertEqual(again["code"], 1)

        branched = self.client.post(f"/api/agent/test-history/{raw_id}/branch").json()
        self.assertEqual(branched["code"], 0)
        self.assertTrue(branched["data"]["title"].endswith("-分支"))
        self.assertNotEqual(branched["data"]["id"], str(raw_id))

        self.client.delete(f"/api/agent/test-history/{raw_id}")
        self.client.delete(f"/api/agent/test-history/{branched['data']['id']}")
        remaining = self.client.get("/api/agent/test-history").json()["data"]
        self.assertEqual(remaining, [])

    def test_invalid_session_ids(self) -> None:
        for method, path in (
            ("post", "/api/agent/test-history/abc/undo"),
            ("post", "/api/agent/test-history/abc/regenerate"),
            ("post", "/api/agent/test-history/abc/branch"),
            ("delete", "/api/agent/test-history/abc"),
        ):
            response = self.client.request(method, path).json()
            self.assertEqual(response["code"], 1, path)


if __name__ == "__main__":
    unittest.main()
