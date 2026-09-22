"""Windows containment is established before the launcher starts any children.

The job is created and joined at startup, before application children, so every
descendant stays contained. The job allows breakaway children: one single-use
updater broker is started outside containment when the user confirms an
installation. A dead broker only fails the attempt it belongs to; the next
attempt starts a new broker. The broker is never a member of the job it
terminates.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from backend.app.updater import BUILD_KEYS, UpdateError, atomic_json, read_json

JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_HELPER_ACCEPT_SECONDS = 30.0


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsUpdateRuntime:
    def __init__(self, install: Path, base: Path, powershell: str) -> None:
        self.install = install
        self.base = base
        self.powershell = powershell
        self.stage: Path | None = None
        self.process: subprocess.Popen | None = None
        self.nonce: str | None = None
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self.job_name = "Local\\MaaUpdater-" + uuid.uuid4().hex
        self.job = kernel.CreateJobObjectW(None, self.job_name)
        if not self.job:
            raise UpdateError("Cannot establish updater process containment.")
        # Only the broker is spawned with CREATE_BREAKAWAY_FROM_JOB; every other
        # child inherits containment. The job never kills on handle close.
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_BREAKAWAY_OK
        if not kernel.SetInformationJobObject(self.job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                              ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel.CloseHandle(self.job)
            raise UpdateError("Cannot configure updater process containment.")
        if not kernel.AssignProcessToJobObject(self.job, kernel.GetCurrentProcess()):
            kernel.CloseHandle(self.job)
            raise UpdateError("The launcher cannot join a Windows job.")

    def reset(self) -> None:
        """Forget a finished attempt so its process is not seen as current."""
        self.stage = self.process = self.nonce = None

    def start(self, installer: Path, prepared: dict) -> None:
        """Start this attempt's single-use broker and wait until it accepts.

        A previous attempt's exited process is not reused. Nothing is armed
        until the application writes the commit file after its response.
        """
        if self.process is not None and self.process.poll() is None:
            raise UpdateError("An updater helper is already running.")
        self.reset()
        stage = self.base / uuid.uuid4().hex
        stage.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(Path(__file__).with_name("update-helper.ps1"), stage / "update-helper.ps1")
        created, exited, cpu_kernel, cpu_user = (wintypes.FILETIME() for _ in range(4))
        if not self._kernel.GetProcessTimes(self._kernel.GetCurrentProcess(), ctypes.byref(created),
                                            ctypes.byref(exited), ctypes.byref(cpu_kernel), ctypes.byref(cpu_user)):
            raise UpdateError("Cannot identify the application process.")
        atomic_json(stage / "bootstrap.json", {
            "job": self.job_name, "root_pid": os.getpid(),
            "root_created": str((created.dwHighDateTime << 32) | created.dwLowDateTime),
            "root_path": str(Path(sys.executable).resolve()), "install": str(self.install),
            "result": str(self.base / "last-result.json"),
        })
        nonce = uuid.uuid4().hex
        atomic_json(stage / "request.json", {
            "nonce": nonce, "installer": str(installer),
            "installer_sha256": prepared["installer_sha256"],
            "expected": {key: prepared[key] for key in BUILD_KEYS},
        })
        env = os.environ.copy()
        # Preserve MAA_* launch configuration for the restarted backend. Discard
        # unrelated GitHub credentials and the previous internal MITM token.
        for key in tuple(env):
            if key in {"MAA_MITM_INTERNAL_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"}:
                env.pop(key)
        try:
            # Keep PowerShell's own error text locally; diagnostics never exports it.
            with (stage / "helper.log").open("ab") as helper_log:
                process = subprocess.Popen(
                    [self.powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(stage / "update-helper.ps1"), "-Stage", str(stage)],
                    cwd=str(stage), env=env, stdin=subprocess.DEVNULL,
                    stdout=helper_log, stderr=subprocess.STDOUT, close_fds=True,
                    creationflags=CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
        except OSError:
            raise UpdateError("The updater helper could not be started.") from None
        self.stage, self.process, self.nonce = stage, process, nonce
        try:
            self._await_accept()
        except BaseException:
            if process.poll() is None:
                process.kill()
            self.stage, self.process, self.nonce = None, None, None
            raise

    def _await_accept(self) -> None:
        deadline = time.monotonic() + _HELPER_ACCEPT_SECONDS
        while time.monotonic() < deadline:
            try:
                accepted = read_json(self.stage / "accepted.json")
            except (OSError, ValueError, UpdateError):
                accepted = None
            if accepted is not None and accepted.get("nonce") == self.nonce:
                if accepted.get("status") == "accepted":
                    return
                raise UpdateError("The updater helper rejected the installation.")
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        raise UpdateError("The updater helper did not accept the installation.")

    def arm(self) -> None:
        if (self.process is None or self.process.poll() is not None
                or self.stage is None or not self.nonce):
            raise UpdateError("Updater helper is unavailable.")
        atomic_json(self.stage / "go.json", {"nonce": self.nonce})

    def cancel(self) -> None:
        if self.stage is not None and self.nonce:
            atomic_json(self.stage / "cancel.json", {"nonce": self.nonce})

    def failed(self) -> bool:
        if self.process is not None and self.process.poll() is not None:
            return True
        if self.stage is not None and self.nonce:
            try:
                accepted = read_json(self.stage / "accepted.json")
            except (OSError, ValueError, UpdateError):
                return False
            return accepted.get("nonce") == self.nonce and accepted.get("status") == "error"
        return False

    def result(self) -> dict | None:
        """Only terminal results. An interrupted attempt is ignored by the app."""
        try:
            result = read_json(self.base / "last-result.json")
            if (result.get("status") in {"installed", "error", "reboot_required"}
                    and isinstance(result.get("message"), str)
                    and (result.get("version") is None or isinstance(result.get("version"), str))):
                return {key: result.get(key) for key in ("status", "message", "version")}
        except (OSError, ValueError, UpdateError):
            pass
        return None
