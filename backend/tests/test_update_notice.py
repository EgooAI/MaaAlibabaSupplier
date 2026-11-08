"""Checks shared acknowledgment persistence without installing any executable."""

import pytest
from fastapi.testclient import TestClient

from backend.app import updater as updates
from backend.app.api.envelope import AppError
from backend.app.api.main import create_app
from backend.tests.auth_client import authenticate
from backend.tests.test_updater import manager


def publish(manager, *, installation_id="a" * 32, status="installed"):
    base = updates.staging_base(manager.install)
    base.mkdir(parents=True, exist_ok=True)
    value = {"status": status, "message": "helper result", "version": "v1.2.3",
             "installation_id": installation_id, "executed_at": 1794000000,
             "completed_at": 1794000120, "sha": "a" * 40}
    updates.atomic_json(base / "installation-notice.json", value)
    return value


def test_acknowledgment_is_shared_by_devices_and_survives_backend_restart(manager):
    value = publish(manager)
    with TestClient(create_app()) as first, TestClient(create_app()) as second:
        authenticate(first)
        second.headers["Authorization"] = "Bearer " + second.app.state.auth_store.issue()
        notice = first.get("/api/app/update/notice").json()["data"]
        assert notice == {"id": value["installation_id"], "version": value["version"],
                          "executed_at": value["executed_at"], "completed_at": value["completed_at"]}
        assert second.get("/api/app/update/notice").json()["data"] == notice
        response = first.post("/api/app/update/notice/ack", json={"installation_id": notice["id"]})
        assert response.status_code == 200 and response.json()["data"] is None
        assert second.get("/api/app/update/notice").json()["data"] is None
    restarted = updates.Updater(manager.install)
    assert restarted.installation_notice() is None
    manager.runtime.start.assert_not_called()


def test_old_device_ack_cannot_dismiss_a_new_installation_and_failures_do_not_clear_notice(manager):
    first = publish(manager)
    manager.acknowledge_installation(first["installation_id"])
    publish(manager, installation_id="b" * 32)
    newer = manager.installation_notice()
    assert manager.acknowledge_installation(first["installation_id"]) == newer
    updates.atomic_json(updates.staging_base(manager.install) / "last-result.json",
                        {"status": "error", "message": "later attempt failed", "version": "v2"})
    assert updates.Updater(manager.install).installation_notice() == newer
    manager.acknowledge_installation(newer["id"])
    assert manager.installation_notice() is None


def test_failed_ack_persistence_keeps_notice_visible(manager, monkeypatch):
    publish(manager)
    before = manager.installation_notice()

    def failed(*args):
        raise PermissionError("locked")

    monkeypatch.setattr(updates, "atomic_json", failed)
    with pytest.raises(AppError) as exc:
        manager.acknowledge_installation(before["id"])
    assert exc.value.status_code == 503
    assert manager.installation_notice() == before


@pytest.mark.parametrize("change", [
    {"status": "error"}, {"executed_at": None},
    {"completed_at": 1}, {"installation_id": "bad"}, {"sha": None},
])
def test_only_timestamped_successful_installs_create_a_notice(manager, change):
    value = publish(manager)
    value.update(change)
    updates.atomic_json(updates.staging_base(manager.install) / "installation-notice.json", value)
    assert manager.installation_notice() is None


def test_notice_reads_are_passive(manager):
    base = updates.staging_base(manager.install)
    assert manager.installation_notice() is None
    assert not base.exists()
