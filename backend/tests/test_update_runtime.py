import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.app import update_runtime as runtime
from backend.app import updater


@pytest.fixture
def native(monkeypatch):
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 100
    kernel.SetInformationJobObject.return_value = True
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


def prepared():
    return {"schema_version": 1, "app_id": updater.APP_ID, "version": "v1", "repository": "org/repo",
            "sha": "a" * 40, "run_id": 3, "run_number": 2, "run_attempt": 1,
            "installer_sha256": "b" * 64}


def accepting(spawn):
    def popen(command, **kwargs):
        stage = Path(kwargs["cwd"])
        request = json.loads((stage / "request.json").read_text(encoding="utf-8"))
        updater.atomic_json(stage / "accepted.json", {"nonce": request["nonce"], "status": "accepted"})
        return Mock(pid=999, poll=Mock(return_value=None))

    spawn.side_effect = popen


def test_startup_builds_breakaway_containment_before_children(native, tmp_path):
    kernel, spawn = native
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "external-pwsh.exe")
    spawn.assert_not_called()
    assert owner.stage is None and owner.process is None and owner.nonce is None
    kernel.CreateJobObjectW.assert_called_once()
    assert kernel.SetInformationJobObject.call_args.args[0] == 100
    assert kernel.SetInformationJobObject.call_args.args[1] == runtime.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION
    limits = kernel.SetInformationJobObject.call_args.args[2]._obj
    assert limits.BasicLimitInformation.LimitFlags & runtime.JOB_OBJECT_LIMIT_BREAKAWAY_OK
    kernel.AssignProcessToJobObject.assert_called_once_with(100, 200)
    kernel.CloseHandle.assert_not_called()


def test_containment_failures_close_job_without_children(native, tmp_path):
    kernel, spawn = native
    kernel.SetInformationJobObject.return_value = False
    with pytest.raises(updater.UpdateError):
        runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "pwsh")
    kernel.CloseHandle.assert_called_once_with(100)
    kernel.AssignProcessToJobObject.assert_not_called()
    spawn.assert_not_called()


def test_join_failure_closes_job_without_children(native, tmp_path):
    kernel, spawn = native
    kernel.AssignProcessToJobObject.return_value = False
    with pytest.raises(updater.UpdateError):
        runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "pwsh")
    kernel.CloseHandle.assert_called_once_with(100)
    spawn.assert_not_called()


def test_start_spawns_single_use_breakaway_broker(native, monkeypatch, tmp_path):
    kernel, spawn = native
    accepting(spawn)
    monkeypatch.setenv("MAA_MITM_INTERNAL_TOKEN", "old-private-token")
    monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", "test-digest")
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "external-pwsh.exe")
    installer = tmp_path / "installer.exe"
    owner.start(installer, prepared())
    command = spawn.call_args.args[0]
    assert command[0] == "external-pwsh.exe" and "-File" in command
    assert str(owner.stage / "update-helper.ps1") in command
    assert owner.stage.is_relative_to(tmp_path / "external")
    assert spawn.call_args.kwargs["creationflags"] == runtime.CREATE_BREAKAWAY_FROM_JOB | 0x08000200
    assert "MAA_MITM_INTERNAL_TOKEN" not in spawn.call_args.kwargs["env"]
    assert spawn.call_args.kwargs["env"]["MAA_AUTH_SECRET_SHA256"] == "test-digest"
    bootstrap = updater.read_json(owner.stage / "bootstrap.json")
    assert bootstrap["job"] == owner.job_name and bootstrap["root_pid"] == os.getpid()
    request = updater.read_json(owner.stage / "request.json")
    assert request["nonce"] == owner.nonce
    assert request["installer"] == str(installer)
    assert request["installer_sha256"] == prepared()["installer_sha256"]
    assert request["expected"] == {key: prepared()[key] for key in updater.BUILD_KEYS}


def test_each_attempt_starts_a_fresh_broker(native, tmp_path):
    kernel, spawn = native
    accepting(spawn)
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "pwsh")
    owner.start(tmp_path / "installer.exe", prepared())
    first = owner.stage
    owner.process.poll.return_value = 1
    owner.start(tmp_path / "installer.exe", prepared())
    assert owner.stage != first and owner.stage.is_dir()


def test_exited_or_rejecting_broker_never_becomes_ready(native, tmp_path):
    kernel, spawn = native
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "pwsh")

    def reject(command, **kwargs):
        stage = Path(kwargs["cwd"])
        request = json.loads((stage / "request.json").read_text(encoding="utf-8"))
        updater.atomic_json(stage / "accepted.json", {"nonce": request["nonce"], "status": "error"})
        return Mock(pid=999, poll=Mock(return_value=None))

    spawn.side_effect = reject
    with pytest.raises(updater.UpdateError, match="rejected"):
        owner.start(tmp_path / "installer.exe", prepared())
    assert owner.process is None and owner.stage is None and owner.nonce is None

    spawn.side_effect = None
    spawn.return_value = Mock(pid=999, poll=Mock(return_value=1))
    with pytest.raises(updater.UpdateError, match="did not accept"):
        owner.start(tmp_path / "installer.exe", prepared())
    assert owner.process is None and owner.stage is None


def test_accept_wait_is_bounded(native, monkeypatch, tmp_path):
    kernel, spawn = native
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "pwsh")
    clock = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(updater.UpdateError, match="did not accept"):
        owner.start(tmp_path / "installer.exe", prepared())
    assert runtime._HELPER_ACCEPT_SECONDS <= clock[0] < runtime._HELPER_ACCEPT_SECONDS + 1


def test_arm_cancel_and_failure_detection(native, tmp_path):
    kernel, spawn = native
    accepting(spawn)
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external", "pwsh")
    owner.start(tmp_path / "installer.exe", prepared())
    owner.arm()
    assert updater.read_json(owner.stage / "go.json")["nonce"] == owner.nonce
    owner.cancel()
    assert updater.read_json(owner.stage / "cancel.json")["nonce"] == owner.nonce
    assert not owner.failed()

    updater.atomic_json(owner.stage / "accepted.json", {"nonce": owner.nonce, "status": "error"})
    assert owner.failed()
    owner.process.poll.return_value = 1
    assert owner.failed()
    with pytest.raises(updater.UpdateError):
        owner.arm()


def test_result_reports_only_terminal_outcomes(tmp_path):
    owner = object.__new__(runtime.WindowsUpdateRuntime)
    owner.base = tmp_path
    owner.stage = None
    owner.process = None
    owner.nonce = None
    updater.atomic_json(tmp_path / "last-result.json", {"status": "installing", "message": "pending", "version": "v1"})
    assert owner.result() is None
    updater.atomic_json(tmp_path / "last-result.json", {"status": "reboot_required", "message": "restart", "version": "v1"})
    assert owner.result() == {"status": "reboot_required", "message": "restart", "version": "v1"}
