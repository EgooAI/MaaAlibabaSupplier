import unittest
import time
from dataclasses import replace
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.api.main import create_app
from backend.tests.auth_client import authenticate
from backend.app.api.routers import status as status_router
from backend.app.shared.backend import status as status_mod


class StatusSnapshotTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = authenticate(TestClient(create_app()))
        self.addCleanup(self.client.close)
        queue = mock.Mock()
        queue.all_snapshots.return_value = []
        patchers = [
            mock.patch.object(status_router, "get_task_queue", return_value=queue),
            mock.patch.object(status_router, "_last_node_result", None),
            mock.patch.object(status_router, "get_client_status", return_value={"connected": False, "window_generation": "", "detail": "test disconnected"}),
            mock.patch.object(
                status_mod, "_check_port",
                side_effect=lambda host, port: status_mod.NetworkStatus(False, host, port, None, "test offline"),
            ),
        ]
        for patcher in patchers:
            self.addCleanup(patcher.stop)
            patcher.start()

    def test_system_snapshot_matches_frontend_contract(self) -> None:
        body = self.client.get("/api/status").json()
        self.assertEqual(body["code"], 0)
        snapshot = body["data"]
        for key in ("updatedAt", "modules", "tasks"):
            self.assertIn(key, snapshot)
        module_ids = [module["id"] for module in snapshot["modules"]]
        self.assertEqual(
            module_ids, ["health-identity", "health-proxy", "health-receiver", "health-node", "health-datadir"]
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

    def test_observation_does_not_create_queue_or_claim_tcp_business_readiness(self) -> None:
        from backend.app.shared.backend import card_sweep_service, sync_coordinator, outbox_service

        with mock.patch.dict(status_router.TaskQueue._instances, {}, clear=True), mock.patch.object(
            status_router, "get_task_queue", side_effect=AssertionError("must not create worker")
        ), mock.patch.object(status_mod, "_check_port", side_effect=lambda host, port: status_mod.NetworkStatus(True, host, port, 1, None)), mock.patch.object(
            sync_coordinator, "_service", None
        ), mock.patch.object(outbox_service, "_service", None), mock.patch.object(
            card_sweep_service, "_service", None
        ), mock.patch.object(
            status_router.IMDBMiddleware, "__new__", side_effect=AssertionError("must not initialize middleware")
        ):
            snapshot = self.client.get("/api/status").json()["data"]
            self.assertEqual(snapshot["tasks"], [])
            self.assertEqual(self.client.get("/api/status/tasks").json()["data"], [])
            self.assertEqual(status_router.TaskQueue._instances, {})
            self.assertEqual(
                snapshot["workers"],
                {"im-source-check": None, "outbox-verifier": None, "card-sweep": None},
            )
            self.assertFalse(snapshot["queues"]["maafw"]["initialized"])
            self.assertFalse(snapshot["queues"]["translation"]["initialized"])
            self.assertEqual(snapshot["queues"]["maafw"]["pending"], 0)
        for module in snapshot["modules"]:
            if module["id"] in ("health-proxy", "health-receiver"):
                self.assertEqual(module["status"], "uncertain")
                self.assertEqual(module["evidence"], "tcp_connect")
                self.assertLessEqual(module["observedAt"], snapshot["observedAt"])

    def test_diagnostic_completion_keeps_original_account_window_and_time(self) -> None:
        context = status_router.get_account_context()
        client = {"connected": True, "window_generation": "window-a", "detail": "connected"}
        queue = mock.Mock()
        queue.enqueue.side_effect = lambda fn, **kwargs: (fn(), mock.Mock(
            task_id="diagnostic", description="diagnostic", status="succeeded", message="ok",
            result=(True, "ok"), created_at=time.time(), started_at=time.time(), completed_at=time.time(),
        ))[1]
        token = status_router.request_epoch.set(context.epoch)
        try:
            with mock.patch.object(status_router, "get_client_status", return_value=client), mock.patch.object(
                status_router, "get_task_queue", return_value=queue
            ), mock.patch.object(status_router, "run_node", return_value=(True, "ok")):
                before = time.time()
                status_router.node_test(status_router.NodeTestInput())
                snapshot = status_router._build_system_snapshot()
                diagnostic = snapshot["lastDiagnostic"]
                self.assertTrue(diagnostic["currentContext"])
                self.assertEqual(diagnostic["context"], context.to_dict())
                self.assertEqual(diagnostic["window_generation"], "window-a")
                self.assertGreaterEqual(diagnostic["completed_at"], before)
                client["window_generation"] = "window-b"
                changed = status_router._build_system_snapshot()
                self.assertFalse(changed["lastDiagnostic"]["currentContext"])
                node = next(module for module in changed["modules"] if module["id"] == "health-node")
                self.assertEqual(node["status"], "uncertain")
                self.assertEqual(changed["lastDiagnostic"]["completed_at"], diagnostic["completed_at"])
                client["window_generation"] = "window-a"
                with mock.patch.object(status_router, "get_account_context", return_value=replace(context, epoch="other-account-selection")):
                    changed_account = status_router._build_system_snapshot()
                self.assertFalse(changed_account["lastDiagnostic"]["currentContext"])
                self.assertEqual(changed_account["lastDiagnostic"]["context"], context.to_dict())
        finally:
            status_router.request_epoch.reset(token)


if __name__ == "__main__":
    unittest.main()
