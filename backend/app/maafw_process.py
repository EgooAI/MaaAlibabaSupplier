from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


class MaaFWProcessError(Exception):
    pass


class MaaFWProcess:
    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[1]
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        executable = os.environ.get("MAAFW_EXECUTABLE", "").strip()
        if not executable:
            raise MaaFWProcessError("Missing MAAFW_EXECUTABLE; set it to the MaaFW executable path before starting service.")

        executable_path = Path(executable)
        if not executable_path.exists():
            raise MaaFWProcessError(f"MAAFW_EXECUTABLE does not exist: {executable}")
        if not executable_path.is_file():
            raise MaaFWProcessError(f"MAAFW_EXECUTABLE must point to an executable file, not a directory: {executable}")

        workdir = Path(os.environ.get("MAAFW_WORKDIR", str(self.repo_root / "assets"))).resolve()
        if not workdir.exists():
            raise MaaFWProcessError(f"MAAFW_WORKDIR does not exist: {workdir}")

        args = shlex.split(os.environ.get("MAAFW_ARGS", ""), posix=False)
        command = [str(executable_path), *args]
        log_path = self.repo_root / "debug" / "maafw.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("ab")
        self.process = subprocess.Popen(
            command,
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        log_file.close()

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def __enter__(self) -> MaaFWProcess:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
