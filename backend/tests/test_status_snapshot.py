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
        for key in ("updatedAt", "modules", "tasks", "userStatus", "proxyStatus", "receiverStatus", "nodeResult", "taskSnapshots"):
            self.assertIn(key, snapshot)
        module_ids = [module["id"] for module in snapshot["modules"]]
        self.assertEqual(
            module_ids, ["health-identity", "health-proxy", "health-receiver", "health-node"]
        )
        for module in snapshot["modules"]:
            for key in ("id", "name", "status", "latency", "description"):
                self.assertIn(key, module)
        for task in snapshot["tasks"]:
            for key in ("id", "type", "status", "createdAt", "duration", "message"):
                self.assertIn(key, task)

    def test_refresh_matches_status(self) -> None:
        status_ids = [m["id"] for m in self.client.get("/api/status").json()["data"]["modules"]]
        refresh_ids = [m["id"] for m in self.client.post("/api/status/refresh").json()["data"]["modules"]]
        self.assertEqual(status_ids, refresh_ids)


if __name__ == "__main__":
    unittest.main()
