import signal
import sys
import threading
from pathlib import Path

from loguru import logger

# Ensure the `backend` package can be imported when launching with
# `python ./../app/agent/main.py <socket_id>` from the assets directory.
from backend.app.shared.utils.settings import resolve_repo_root

PROJECT_ROOT = resolve_repo_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from maa.agent.agent_server import AgentServer

import backend.app.agent.nodes.customer.goto_contact
import backend.app.agent.nodes.auxi.chat_send
import backend.app.agent.nodes.auxi.copy_text
import backend.app.agent.nodes.auxi.read_clipboard
import backend.app.agent.nodes.auxi.send_message
from backend.app.shared.utils.env import load_workdir_env
from backend.app.shared.utils.logging import configure_logging


def main() -> None:
    load_workdir_env()
    configure_logging()

    if len(sys.argv) < 2:
        logger.error("Usage: python main.py <socket_id>")
        sys.exit(1)

    socket_id = sys.argv[-1]
    shutdown_requested = threading.Event()
    shutdown_lock = threading.Lock()

    def _shutdown_agent_server() -> None:
        with shutdown_lock:
            if shutdown_requested.is_set():
                return
            shutdown_requested.set()
        logger.info("[agent] stopping Maa AgentServer...")
        try:
            AgentServer.shut_down()
        except Exception:
            logger.exception("Failed to shut down Maa AgentServer")

    def _request_shutdown(signum: int, _frame: object) -> None:
        if shutdown_requested.is_set():
            raise KeyboardInterrupt
        logger.info("[agent] received signal {}; shutting down...", signum)
        threading.Thread(target=_shutdown_agent_server, daemon=True, name="shutdown").start()

    signal.signal(signal.SIGINT, _request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_shutdown)

    join_thread: threading.Thread | None = None
    try:
        AgentServer.start_up(socket_id)
        join_thread = threading.Thread(target=AgentServer.join, name="maa-agent-server-join")
        join_thread.start()
        while join_thread.is_alive():
            join_thread.join(timeout=0.2)
    except KeyboardInterrupt:
        logger.info("[agent] interrupted; shutting down...")
    finally:
        _shutdown_agent_server()
        if join_thread is not None and join_thread.is_alive():
            join_thread.join(timeout=5.0)
            if join_thread.is_alive():
                logger.warning("[agent] AgentServer.join() still blocking after shutdown; forcing exit")


if __name__ == "__main__":
    main()
