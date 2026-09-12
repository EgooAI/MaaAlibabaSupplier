import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from backend.app.api.main import app
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
        os.environ["MAA_CRM_DB_PATH"] = str(Path(self.temp_dir.name) / "crm.sqlite")
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        try:
            ChatHistoryManager().engine.dispose()
        finally:
            self.temp_dir.cleanup()
            os.environ.pop("MAA_CRM_DB_PATH", None)

    def test_console_shape(self) -> None:
        body = self.client.get("/api/agent/console").json()
        self.assertEqual(body["code"], 0)
        for key in ("llmLevels", "agents", "agentPresets", "history"):
            self.assertIn(key, body["data"])

    def test_system_definitions(self) -> None:
        body = self.client.get("/api/agent/system").json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(len(body["data"]), 4)
        self.assertTrue(all(item["apid"] for item in body["data"]))

    def test_llm_config_roundtrip(self) -> None:
        payload = {
            "level": 4, "base_url": "https://llm.example", "api_key": "k",
            "model_name": "m", "system_prompt": "", "context": 8000, "max_tool_rounds": 3,
        }
        saved = self.client.put("/api/agent/llm-config", json=payload).json()
        self.assertEqual(saved["code"], 0)
        self.assertEqual(saved["data"]["model_name"], "m")
        console = self.client.get("/api/agent/console").json()["data"]
        self.assertTrue(any(level["level"] == 4 for level in console["llmLevels"]))

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
