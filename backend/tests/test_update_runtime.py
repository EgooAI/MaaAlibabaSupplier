import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.app import update_runtime as runtime
from backend.app import updater


@pytest.fixture
def native(monkeypatch):
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 100
    kernel.GetCurrentProcess.return_value = 200
    kernel.GetProcessTimes.return_value = True
    kernel.AssignProcessToJobObject.return_value = True
    monkeypatch.setattr(runtime.ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(runtime.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(runtime.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    process = Mock(pid=999, poll=Mock(return_value=None))
    spawn = Mock(return_value=process)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    return kernel, spawn


def test_broker_spawn_precedes_job_assignment_and_uses_external_runtime(native, monkeypatch, tmp_path):
    kernel, spawn = native
    order = []
    spawn.side_effect = lambda *a, **kw: (order.append("broker") or Mock(poll=Mock(return_value=None)))
    kernel.AssignProcessToJobObject.side_effect = lambda *a: (order.append("assign") or True)
    monkeypatch.setenv("MAA_MITM_INTERNAL_TOKEN", "old-private-token")
    monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", "test-digest")
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "external-pwsh.exe")
    assert order == ["broker", "assign"]
    command = spawn.call_args.args[0]
    assert command[0] == "external-pwsh.exe" and "-File" in command
    assert str(owner.stage / "update-helper.ps1") in command
    assert not owner.stage.is_relative_to(tmp_path / "install")
    assert "MAA_MITM_INTERNAL_TOKEN" not in spawn.call_args.kwargs["env"]
    assert spawn.call_args.kwargs["env"]["MAA_AUTH_SECRET_SHA256"] == "test-digest"
    assert spawn.call_args.kwargs["creationflags"] == 0x08000200
    assert (owner.stage / "attached.json").is_file()
    kernel.AssignProcessToJobObject.assert_called_once_with(100, 200)
    kernel.CloseHandle.assert_not_called()


def test_failed_containment_tells_broker_to_exit_without_killing_app(native, tmp_path):
    kernel, spawn = native
    kernel.AssignProcessToJobObject.return_value = False
    with pytest.raises(updater.UpdateError):
        runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "pwsh")
    stage = Path(spawn.call_args.kwargs["cwd"])
    assert (stage / "stop.json").is_file()
    assert not (stage / "attached.json").exists()
    kernel.CloseHandle.assert_called_once_with(100)
    spawn.return_value.kill.assert_not_called()


def broker(tmp_path):
    owner = object.__new__(runtime.WindowsUpdateRuntime)
    owner.stage = tmp_path
    owner.base = tmp_path
    owner.process = Mock(poll=Mock(return_value=None))
    owner.nonce = None
    owner.verified = None
    owner.pending_until = None
    return owner


def prepared():
    return {"schema_version": 1, "app_id": updater.APP_ID, "version": "v1", "repository": "org/repo",
            "sha": "a" * 40, "run_id": 3, "run_number": 2, "run_attempt": 1,
            "installer_sha256": "b" * 64}


def test_prepare_long_requires_nonce_verified_ack_and_install_only_acknowledges(monkeypatch, tmp_path):
    owner = broker(tmp_path)

    def acknowledge(delay):
        request = json.loads((tmp_path / "request.json").read_text())
        updater.atomic_json(tmp_path / "ready.json", {"nonce": request["nonce"], "status": "ready"})

    monkeypatch.setattr(runtime.time, "sleep", acknowledge)
    owner.prepare_long(tmp_path / "installer.exe", prepared())
    assert not (tmp_path / "arm.json").exists()
    assert not (tmp_path / "pending.json").exists()
    request = updater.read_json(tmp_path / "request.json")
    assert request["expected"] == {k: prepared()[k] for k in updater.BUILD_KEYS}
    assert request["installer_sha256"] == prepared()["installer_sha256"]
    owner.prepare(tmp_path / "installer.exe", prepared())
    assert updater.read_json(tmp_path / "pending.json")["nonce"] == owner.nonce
    owner.arm()
    assert updater.read_json(tmp_path / "arm.json")["nonce"] == owner.nonce
    owner.cancel()
    assert updater.read_json(tmp_path / "cancel.json")["nonce"] == owner.nonce


def test_long_hash_timeout_never_marks_ready_or_arms(monkeypatch, tmp_path):
    owner = broker(tmp_path)
    updater.atomic_json(tmp_path / "ready.json", {"nonce": "previous-request", "status": "ready"})
    clock = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(updater.UpdateError, match="timed out"):
        owner.prepare_long(tmp_path / "installer.exe", prepared())
    assert 900 <= clock[0] < 901
    assert owner.verified is None
    assert not (tmp_path / "arm.json").exists()


def test_dead_broker_and_failed_ack_prevent_handoff(tmp_path, monkeypatch):
    owner = broker(tmp_path)
    owner.process.poll.return_value = 1
    with pytest.raises(updater.UpdateError):
        owner.prepare_long(tmp_path / "installer.exe", prepared())
    assert not (tmp_path / "request.json").exists()
    owner.process.poll.return_value = None

    def reject(delay):
        updater.atomic_json(tmp_path / "ready.json", {"nonce": owner.nonce, "status": "error"})

    monkeypatch.setattr(runtime.time, "sleep", reject)
    with pytest.raises(updater.UpdateError, match="rejected"):
        owner.prepare_long(tmp_path / "installer.exe", prepared())
    assert owner.failed()
    assert not (tmp_path / "arm.json").exists()


def test_slow_hash_and_arbitrary_user_confirmation_delay_do_not_use_http_timeout(monkeypatch, tmp_path):
    owner = broker(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.time, "time", lambda: clock[0])

    def slow_hash(delay):
        clock[0] += delay
        if clock[0] >= 49.8:
            updater.atomic_json(tmp_path / "ready.json", {"nonce": owner.nonce, "status": "ready"})

    monkeypatch.setattr(runtime.time, "sleep", slow_hash)
    owner.prepare_long(tmp_path / "installer.exe", prepared())
    assert 49.8 <= clock[0] < 50
    assert not (tmp_path / "pending.json").exists()
    clock[0] += 86400
    started = clock[0]
    monkeypatch.setattr(runtime.time, "sleep", Mock(side_effect=AssertionError("HTTP install must not wait")))
    owner.prepare(tmp_path / "installer.exe", prepared())
    assert clock[0] == started
    assert updater.read_json(tmp_path / "pending.json")["expires_at"] == started + 15
    owner.arm()
    assert (tmp_path / "arm.json").is_file()


def test_expired_pending_lease_and_cancelled_verification_cannot_arm(monkeypatch, tmp_path):
    owner = broker(tmp_path)
    owner.nonce = "test-nonce"
    owner.verified = (tmp_path / "installer.exe", prepared())
    updater.atomic_json(tmp_path / "ready.json", {"nonce": owner.nonce, "status": "ready"})
    clock = [10.0]
    monkeypatch.setattr(runtime.time, "time", lambda: clock[0])
    owner.prepare(*owner.verified)
    clock[0] += 16
    with pytest.raises(updater.UpdateError):
        owner.arm()
    assert not (tmp_path / "arm.json").exists()
    owner.cancel()
    with pytest.raises(updater.UpdateError):
        owner.prepare(tmp_path / "installer.exe", prepared())
