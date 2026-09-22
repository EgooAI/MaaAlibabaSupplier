from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from loguru import logger

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
        self.executable = self.backend_root / "deps" / "bin" / "MaaPiCli.exe"
        self.workdir = self.backend_root / "assets"
        self.process: subprocess.Popen | None = None
        self._stopping = threading.Event()

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
        log_path = self.backend_root / "data" / "logs" / "maafw_cli.log"
        self._stopping = threading.Event()
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(self.workdir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=CREATE_NO_WINDOW,
                env=colorless_child_env(),
            )
            attach_process_log(self.process, log_path, source="maafw_cli")
        except OSError as exc:
            raise MaaFWProcessError(f"MaaPiCli process creation failed ({type(exc).__name__})") from None
        process = self.process
        code = process.poll()
        if code is not None:
            finish_process_log(process)
            raise MaaFWProcessError(f"MaaPiCli exited during startup (code={code}); readiness was not verified")
        logger.info("MaaPiCli process created (pid={}); CLI readiness is not verified", process.pid)
        stopping = self._stopping

        def observe_exit() -> None:
            try:
                code = process.wait()
                if not stopping.is_set():
                    logger.warning("MaaPiCli process exited (code={}); CLI readiness was not verified", code)
            except Exception as exc:
                logger.warning("MaaPiCli exit observation failed ({})", type(exc).__name__)

        try:
            threading.Thread(target=observe_exit, daemon=True, name="maafw-cli-exit").start()
        except RuntimeError:
            logger.warning("MaaPiCli exit observation unavailable; CLI readiness is not verified")

    def stop(self) -> None:
        process = self.process
        self.process = None
        self._stopping.set()
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
