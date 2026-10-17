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
    monkeypatch.setattr(runtime, "resolve_powershell", Mock(return_value="external-pwsh.exe"))
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


def rejecting(spawn):
    def popen(command, **kwargs):
        stage = Path(kwargs["cwd"])
        request = json.loads((stage / "request.json").read_text(encoding="utf-8"))
        updater.atomic_json(stage / "accepted.json", {"nonce": request["nonce"], "status": "error"})
        return Mock(pid=999, poll=Mock(return_value=None))

    spawn.side_effect = popen


def test_startup_builds_breakaway_containment_before_children(native, tmp_path):
    kernel, spawn = native
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
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
        runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
    kernel.CloseHandle.assert_called_once_with(100)
    kernel.AssignProcessToJobObject.assert_not_called()
    spawn.assert_not_called()


def test_join_failure_closes_job_without_children(native, tmp_path):
    kernel, spawn = native
    kernel.AssignProcessToJobObject.return_value = False
    with pytest.raises(updater.UpdateError):
        runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
    kernel.CloseHandle.assert_called_once_with(100)
    spawn.assert_not_called()


def test_start_spawns_single_use_breakaway_broker(native, monkeypatch, tmp_path):
    kernel, spawn = native
    accepting(spawn)
    monkeypatch.setenv("MAA_MITM_INTERNAL_TOKEN", "old-private-token")
    monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", "test-digest")
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
    installer = tmp_path / "installer.exe"
    owner.start(installer, prepared())
    command = spawn.call_args.args[0]
    assert command[0] == "external-pwsh.exe" and "-File" in command
    assert str(owner.stage / "update-helper.ps1") in command
    assert owner.stage.is_relative_to(tmp_path / "external")
    assert spawn.call_args.kwargs["creationflags"] == runtime.CREATE_BREAKAWAY_FROM_JOB | 0x08000200
    assert spawn.call_args.kwargs["stderr"] is runtime.subprocess.STDOUT
    assert (owner.stage / "helper.log").is_file()
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
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
    owner.start(tmp_path / "installer.exe", prepared())
    first = owner.stage
    owner.process.poll.return_value = 1
    owner.start(tmp_path / "installer.exe", prepared())
    assert owner.stage != first and owner.stage.is_dir()


def test_each_attempt_re_resolves_the_shell_before_spawning(native, monkeypatch, tmp_path):
    kernel, spawn = native
    accepting(spawn)
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
    resolutions = ["fresh-1.exe", "fresh-2.exe"]
    monkeypatch.setattr(runtime, "resolve_powershell", lambda install: resolutions.pop(0))
    owner.start(tmp_path / "installer.exe", prepared())
    assert spawn.call_args.args[0][0] == "fresh-1.exe"
    owner.process.poll.return_value = 1
    owner.start(tmp_path / "installer.exe", prepared())
    assert spawn.call_args.args[0][0] == "fresh-2.exe"


def test_start_fails_closed_without_usable_shell(native, monkeypatch, tmp_path):
    kernel, spawn = native

    def missing(install):
        raise updater.UpdateError("An external PowerShell runtime is required.")

    monkeypatch.setattr(runtime, "resolve_powershell", missing)
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
    with pytest.raises(updater.UpdateError, match="runtime is required"):
        owner.start(tmp_path / "installer.exe", prepared())
    spawn.assert_not_called()
    assert owner.stage is None and owner.process is None and owner.nonce is None
    base = tmp_path / "external"
    assert not base.exists() or not any(base.iterdir())


def test_failed_attempt_cleanup_on_each_failure_kind(native, monkeypatch, tmp_path):
    kernel, spawn = native
    monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", "test-digest")
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
    base = tmp_path / "external"

    spawn.side_effect = OSError("spawn was blocked")
    with pytest.raises(updater.UpdateError, match="could not be started"):
        owner.start(tmp_path / "installer.exe", prepared())
    # The helper never started, so its stage holds no diagnostics at all.
    assert owner.stage is None and not any(base.iterdir())

    rejecting(spawn)
    with pytest.raises(updater.UpdateError, match="rejected"):
        owner.start(tmp_path / "installer.exe", prepared())
    # The helper did start but rejected: the stage stays for local inspection.
    assert owner.stage is None and owner.nonce is None
    stages = [entry for entry in base.iterdir() if entry.is_dir()]
    assert len(stages) == 1 and (stages[0] / "helper.log").is_file()
    assert updater.read_json(stages[0] / "accepted.json")["status"] == "error"

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    spawn.reset_mock()
    with pytest.raises(updater.UpdateError, match="staging area could not be prepared"):
        runtime.WindowsUpdateRuntime(tmp_path / "install", blocked).start(tmp_path / "installer.exe", prepared())
    spawn.assert_not_called()


def test_exited_or_rejecting_broker_never_becomes_ready(native, tmp_path):
    kernel, spawn = native
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")

    rejecting(spawn)
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
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
    clock = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(updater.UpdateError, match="did not accept"):
        owner.start(tmp_path / "installer.exe", prepared())
    assert runtime._HELPER_ACCEPT_SECONDS <= clock[0] < runtime._HELPER_ACCEPT_SECONDS + 1


def test_arm_cancel_and_failure_detection(native, tmp_path):
    kernel, spawn = native
    accepting(spawn)
    owner = runtime.WindowsUpdateRuntime(tmp_path / "install", tmp_path / "external")
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
