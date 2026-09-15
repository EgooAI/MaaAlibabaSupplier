from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Console-subsystem children (MaaPiCli.exe) pop their own console window when
# the parent is pythonw (no console); hide it on Windows.
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class MaaFWProcessError(Exception):
    pass


class MaaFWProcess:
    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[1]
        self.executable = self.repo_root / "deps" / "bin" / "MaaPiCli.exe"
        self.workdir = self.repo_root / "assets"
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        if not self.executable.exists():
            raise MaaFWProcessError(
                f"MaaPiCli not found at {self.executable}; run `python tools/install_3_maafw.py` first."
            )

        command = [str(self.executable)]
        log_path = self.repo_root / "debug" / "maafw.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("ab")
        self.process = subprocess.Popen(
            command,
            cwd=str(self.workdir),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
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
