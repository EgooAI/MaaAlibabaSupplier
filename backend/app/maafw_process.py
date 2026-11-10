from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from loguru import logger

from backend.app.shared.utils import paths
from backend.app.shared.utils.log_context import log_event
from backend.app.shared.utils.process_logs import attach_process_log, colorless_child_env, finish_process_log

# Console-subsystem children (MaaPiCli.exe) pop their own console window when
# the parent is pythonw (no console); hide it on Windows.
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class MaaFWProcessError(Exception):
    pass


class MaaFWProcess:
    def __init__(self, backend_root: Path | None = None) -> None:
        from backend.app.shared.utils.settings import resolve_backend_root

        self.backend_root = backend_root or resolve_backend_root()
        self.workdir = paths.assets_dir(self.backend_root)
        self.executable = paths.maafw_cli_path(self.backend_root)
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        if not self.executable.exists():
            raise MaaFWProcessError(
                f"MaaPiCli not found at {self.executable}; run `python tools/install_3_maafw.py` first."
            )
        if not self.workdir.is_dir():
            raise MaaFWProcessError(f"MaaFW workdir not found at {self.workdir}.")

        command = [str(self.executable)]
        env = colorless_child_env()
        env["PATH"] = str(paths.deps_bin_dir(self.backend_root)) + os.pathsep + env.get("PATH", "")
        log_path = paths.maafw_cli_log_path(self.backend_root)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(self.workdir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=CREATE_NO_WINDOW,
                env=env,
            )
            attach_process_log(self.process, log_path, source="maafw_cli")
        except OSError as exc:
            raise MaaFWProcessError(f"MaaPiCli process creation failed ({type(exc).__name__})") from None
        process = self.process
        code = process.poll()
        if code is not None:
            finish_process_log(process)
            raise MaaFWProcessError(f"MaaPiCli exited during startup (code={code}); readiness was not verified")
        # The CLI owns no readiness contract and normally exits once its stdin
        # closes; only its start and an explicit stop are worth recording.
        log_event("process.started", component="maafw_cli", pid=process.pid)

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning("MaaPiCli termination not confirmed")
        finally:
            if not finish_process_log(process):
                logger.warning("MaaPiCli output drain is still pending")

    def __enter__(self) -> MaaFWProcess:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
