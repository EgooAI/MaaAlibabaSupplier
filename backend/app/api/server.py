from __future__ import annotations

import os
from typing import cast

import uvicorn
from loguru import logger

from backend.app.shared.utils.env import load_workdir_env

server: uvicorn.Server | None = None


def run() -> None:
    load_workdir_env()
    host = os.environ.get("MAA_API_HOST", "127.0.0.1")
    port = int(os.environ.get("MAA_API_PORT", "8000"))
    # log_config=None keeps uvicorn off the console; std logging propagates to
    # the root InterceptHandler and lands in data/logs/api.log (pythonw-safe).
    global server
    server = uvicorn.Server(uvicorn.Config("backend.app.api.main:app", host=host, port=port, reload=False, log_config=None))
    server.run()


def request_shutdown() -> bool:
    """Ask the running uvicorn server to exit gracefully.

    Returns False when no server instance is tracked (e.g. app imported
    outside `python -m backend.app.main`). On graceful exit, the startup
    script's finally-block terminates MaaPiCli and the Yak MITM proxy.
    """
    if server is None or server.should_exit:
        return False
    logger.info("Shutdown requested via API; stopping uvicorn server")
    server.should_exit = True
    return True
