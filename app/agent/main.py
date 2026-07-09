import signal
import sys
import threading
from pathlib import Path

from loguru import logger

# Ensure the `app` package can be imported when launching with
# `python ./../app/agent/main.py <socket_id>` from the assets directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from maa.agent.agent_server import AgentServer

import app.agent.nodes.customer.goto_contact
import app.agent.nodes.auxi.chat_send
import app.agent.nodes.auxi.copy_text
import app.agent.nodes.auxi.read_clipboard
import app.agent.nodes.auxi.send_message
from app.shared.utils.env import load_workdir_env
from app.shared.utils.logging import configure_logging


def main():
    load_workdir_env()
    configure_logging()

    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)

    socket_id = sys.argv[-1]
    shutdown_requested = threading.Event()
    shutdown_lock = threading.Lock()

    def _shutdown_agent_server() -> None:
        with shutdown_lock:
            if shutdown_requested.is_set():
                return
            shutdown_requested.set()
        print("[agent] stopping Maa AgentServer...")
        try:
            AgentServer.shut_down()
        except Exception:
            logger.exception("Failed to shut down Maa AgentServer")

    def _request_shutdown(signum: int, _frame: object) -> None:
        if shutdown_requested.is_set():
            raise KeyboardInterrupt
        print(f"\n[agent] received signal {signum}; shutting down...")
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
        print("\n[agent] interrupted; shutting down...")
    finally:
        _shutdown_agent_server()
        if join_thread is not None and join_thread.is_alive():
            join_thread.join(timeout=5.0)
            if join_thread.is_alive():
                print("[agent] AgentServer.join() still blocking after shutdown; forcing exit")


if __name__ == "__main__":
    main()
