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
import backend.app.agent.nodes.auxi.paste_text
import backend.app.agent.nodes.auxi.read_clipboard
import backend.app.agent.nodes.auxi.send_message
from backend.app.shared.utils.env import load_workdir_env
from backend.app.shared.utils.log_context import log_event
from backend.app.shared.utils.logging import configure_logging
from backend.app.background import get_background_runtime, shutdown_background


def main() -> None:
    load_workdir_env()
    configure_logging(source="agent")

    if len(sys.argv) < 2:
        log_event("agent.usage_error")
        sys.exit(1)

    socket_id = sys.argv[-1]
    shutdown_requested = threading.Event()
    shutdown_lock = threading.Lock()
    runtime = get_background_runtime()

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
        log_event("agent.signal", signal=signum)
        runtime.worker("agent-shutdown", lambda stop: _shutdown_agent_server()).start()

    signal.signal(signal.SIGINT, _request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_shutdown)

    runtime.on_shutdown("agent-server", _shutdown_agent_server)
    try:
        AgentServer.start_up(socket_id)
        join_thread = runtime.worker("maa-agent-server-join", lambda stop: AgentServer.join(), resident=True).start()
        while join_thread.is_alive():
            join_thread.join(timeout=0.2)
    except KeyboardInterrupt:
        logger.info("[agent] interrupted; shutting down...")
    finally:
        try:
            shutdown_background()
        except ExceptionGroup:
            logger.exception("Agent background shutdown did not complete")


if __name__ == "__main__":
    main()
