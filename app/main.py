from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from loguru import logger

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.maafw_process import MaaFWProcess, MaaFWProcessError
from app.mitm.proxy import run_receiver
from app.web.server import run as run_web
from app.shared.utils.env import load_workdir_env
from app.shared.utils.logging import configure_logging


def _start_mitm_receiver() -> threading.Thread:
    host = os.environ.get("MITM_RECEIVER_HOST", "127.0.0.1")
    port = int(os.environ.get("MITM_RECEIVER_PORT", "8085"))

    def _run() -> None:
        run_receiver(host=host, port=port)

    thread = threading.Thread(target=_run, daemon=True, name="mitm-receiver")
    thread.start()
    logger.info("MITM receiver thread started on {}:{}", host, port)
    return thread


def _start_yak_mitm(repo_root: Path) -> subprocess.Popen | None:
    """Start the Yak MITM proxy via ``yak yak_mitm.yak`` in a subprocess."""
    yak_exe = os.environ.get("YAK_EXECUTABLE", "yak")
    yak_script = repo_root / "yak_mitm.yak"

    if not yak_script.exists():
        logger.warning("yak_mitm.yak not found at {}, skipping", yak_script)
        return None

    try:
        proc = subprocess.Popen(
            [yak_exe, str(yak_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.error("'{}' not found — is Yak installed?", yak_exe)
        return None

    # Give the MITM server a moment to bind the port.
    time.sleep(1.5)
    logger.info("Yak MITM proxy started (pid={})", proc.pid)
    return proc


def main() -> None:
    load_workdir_env()
    configure_logging()

    repo_root = Path(__file__).resolve().parents[1]
    maafw = MaaFWProcess(repo_root)
    yak_proc: subprocess.Popen | None = None

    try:
        maafw.start()
        logger.info("MaaFW process started")
        _start_mitm_receiver()
        yak_proc = _start_yak_mitm(repo_root)
        run_web()
    except MaaFWProcessError:
        logger.exception("Failed to start MaaFW")
        raise
    finally:
        maafw.stop()
        if yak_proc and yak_proc.poll() is None:
            yak_proc.terminate()
            logger.info("Yak MITM proxy terminated")


if __name__ == "__main__":
    main()
