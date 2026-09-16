import unittest

from fastapi.testclient import TestClient

from backend.app.api.main import app


class StatusSnapshotTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_system_snapshot_matches_frontend_contract(self) -> None:
        body = self.client.get("/api/status").json()
        self.assertEqual(body["code"], 0)
        snapshot = body["data"]
        for key in ("updatedAt", "modules", "tasks", "userStatus", "proxyStatus", "receiverStatus", "dataDirStatus", "nodeResult", "taskSnapshots"):
            self.assertIn(key, snapshot)
        for key in ("state", "path", "source", "detail"):
            self.assertIn(key, snapshot["dataDirStatus"])
        module_ids = [module["id"] for module in snapshot["modules"]]
        self.assertEqual(
            module_ids, ["health-datadir", "health-identity", "health-proxy", "health-receiver", "health-node"]
        )
        for module in snapshot["modules"]:
            for key in ("id", "name", "status", "latency", "description"):
                self.assertIn(key, module)
        for task in snapshot["tasks"]:
            for key in ("id", "type", "status", "createdAt", "duration", "message"):
                self.assertIn(key, task)

    def test_refresh_endpoint_serves_same_snapshot(self) -> None:
        body = self.client.post("/api/status/refresh").json()
        self.assertEqual(body["code"], 0)
        self.assertIn("modules", body["data"])


if __name__ == "__main__":
    unittest.main()
