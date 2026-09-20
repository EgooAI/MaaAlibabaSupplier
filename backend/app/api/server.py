from __future__ import annotations

import uvicorn
from loguru import logger

from backend.app.shared.utils.env import get_env_int, get_env_str, load_workdir_env
from backend.app.shared.utils.settings import MAA_API_HOST_DEFAULT, MAA_API_PORT_DEFAULT

server: uvicorn.Server | None = None


def run() -> None:
    load_workdir_env()
    host = get_env_str("MAA_API_HOST", MAA_API_HOST_DEFAULT)
    port = get_env_int("MAA_API_PORT", MAA_API_PORT_DEFAULT)
    # log_config=None keeps uvicorn off the console; std logging propagates to
    # the root InterceptHandler and lands in data/logs/api.log (pythonw-safe).
    global server
    server = uvicorn.Server(uvicorn.Config("backend.app.api.main:app", host=host, port=port, reload=False, log_config=None, access_log=False))
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
