"""Windows containment is established before the launcher starts any children.

The broker is created before assigning this process to a non-breakaway job.
All subsequent children inherit the job, including descendants born during
handoff. The external broker is never a member and holds exact process handles.
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


class WindowsUpdateRuntime:
    def __init__(self, install: Path, base: Path, powershell: str) -> None:
        self.base = base
        self.stage = base / uuid.uuid4().hex
        self.stage.mkdir(parents=True, exist_ok=False)
        self.nonce = None
        self.verified = None
        self.pending_until = None
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        job_name = "Local\\MaaUpdater-" + uuid.uuid4().hex
        # Default job limits prohibit breakaway and do not kill on handle close.
        self.job = kernel.CreateJobObjectW(None, job_name)
        if not self.job:
            raise UpdateError("Cannot establish updater process containment.")
        created, exited, cpu_kernel, cpu_user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(cpu_kernel), ctypes.byref(cpu_user)):
            kernel.CloseHandle(self.job)
            raise UpdateError("Cannot identify the application process.")
        script = self.stage / "update-helper.ps1"
        shutil.copyfile(Path(__file__).with_name("update-helper.ps1"), script)
        atomic_json(self.stage / "bootstrap.json", {
            "job": job_name, "root_pid": os.getpid(),
            "root_created": str((created.dwHighDateTime << 32) | created.dwLowDateTime),
            "root_path": str(Path(sys.executable).resolve()), "install": str(install),
            "result": str(base / "last-result.json"),
        })
        env = os.environ.copy()
        # Preserve MAA_* launch configuration for the restarted backend. Discard
        # unrelated GitHub credentials and the previous internal MITM token.
        for key in tuple(env):
            if key in {"MAA_MITM_INTERNAL_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"}:
                env.pop(key)
        try:
            self.process = subprocess.Popen(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script), "-Stage", str(self.stage)],
                cwd=str(self.stage), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            if not kernel.AssignProcessToJobObject(self.job, kernel.GetCurrentProcess()):
                raise UpdateError("The launcher cannot join a Windows job.")
            atomic_json(self.stage / "attached.json", {"attached": True})
        except Exception:
            atomic_json(self.stage / "stop.json", {"stop": True})
            kernel.CloseHandle(self.job)
            raise

    def prepare_long(self, installer: Path, prepared: dict) -> None:
        """Hash under the helper's retained file lock during async download."""
        self.cancel()
        if self.process.poll() is not None:
            raise UpdateError("The updater helper has exited.")
        self.nonce = uuid.uuid4().hex
        atomic_json(self.stage / "request.json", {
            "nonce": self.nonce, "installer": str(installer),
            "installer_sha256": prepared["installer_sha256"],
            "expected": {key: prepared[key] for key in BUILD_KEYS},
        })
        deadline = time.monotonic() + 900.0
        while time.monotonic() < deadline:
            try:
                ready = read_json(self.stage / "ready.json")
                if ready.get("nonce") == self.nonce:
                    if ready.get("status") == "ready":
                        self.verified = (installer, prepared)
                        return
                    raise UpdateError("The updater helper rejected the installation.")
            except (FileNotFoundError, ValueError):
                pass
            if self.process.poll() is not None:
                break
            time.sleep(0.1)
        raise UpdateError("Updater helper readiness timed out.")

    def prepare(self, installer: Path, prepared: dict) -> None:
        # The expensive verification already finished. This HTTP-path check
        # performs only local metadata I/O and never waits for hashing or tasks.
        if (self.verified != (installer, prepared) or not self.nonce
                or self.process.poll() is not None):
            raise UpdateError("The verified updater helper is unavailable.")
        ready = read_json(self.stage / "ready.json")
        if ready.get("nonce") != self.nonce or ready.get("status") != "ready":
            raise UpdateError("The updater helper is no longer ready.")
        self.pending_until = time.time() + 15.0
        atomic_json(self.stage / "pending.json", {"nonce": self.nonce, "expires_at": self.pending_until})

    def cancel(self) -> None:
        self.verified = None
        self.pending_until = None
        if self.nonce:
            atomic_json(self.stage / "cancel.json", {"nonce": self.nonce})

    def arm(self) -> None:
        if (not self.nonce or self.process.poll() is not None
                or self.pending_until is None or time.time() >= self.pending_until or self.failed()):
            raise UpdateError("Updater helper is unavailable.")
        atomic_json(self.stage / "arm.json", {"nonce": self.nonce})

    def failed(self) -> bool:
        if self.process.poll() is not None:
            return True
        if self.nonce:
            try:
                ready = read_json(self.stage / "ready.json")
                return ready.get("nonce") == self.nonce and ready.get("status") in {"error", "cancelled"}
            except (OSError, ValueError, UpdateError):
                pass
        return False

    def result(self) -> dict | None:
        try:
            result = read_json(self.base / "last-result.json")
            if (result.get("status") in {"installed", "error", "installing", "reboot_required"}
                    and isinstance(result.get("message"), str)
                    and (result.get("version") is None or isinstance(result.get("version"), str))):
                return {key: result.get(key) for key in ("status", "message", "version")}
        except (OSError, ValueError, UpdateError):
            pass
        return None
