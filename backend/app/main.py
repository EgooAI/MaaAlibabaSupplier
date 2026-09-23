from __future__ import annotations

import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from loguru import logger

from backend.app.shared.utils.env import get_env_int, get_env_str
from backend.app.shared.utils.settings import (
    MAA_API_HOST_DEFAULT,
    MITM_PROXY_PORT_DEFAULT,
    MITM_RECEIVER_HOST_DEFAULT,
    MITM_RECEIVER_PORT_DEFAULT,
    YAK_STARTUP_GRACE_S,
    resolve_backend_root,
    resolve_repo_root,
)

# Console-subsystem children (yak.exe) pop their own console window when the
# parent is pythonw (no console); hide it on Windows.
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

from backend.app.maafw_process import MaaFWProcess, MaaFWProcessError
from backend.app.mitm.proxy import ReusableThreadingHTTPServer, create_receiver, validate_receiver_config
from backend.app.shared.agent.system_presets import ensure_system_agents_seeded
from backend.app.shared.agent.default_llm_levels import ensure_default_llm_levels_seeded
from backend.app.api.server import run as run_api
from backend.app.api.auth import validate_auth_config
from backend.app.shared.utils.env import load_workdir_env
from backend.app.shared.utils.log_context import log_event
from backend.app.shared.utils.logging import configure_logging
from backend.app.shared.utils.process_logs import (
    attach_process_log, colorless_child_env, finish_process_log, report_startup_failure,
)
from backend.app.shared.backend.card_sweep_service import start_card_sweep_service, stop_card_sweep_service
from backend.app.shared.backend.sync_coordinator import start_sync_service, stop_sync_service
from backend.app.shared.backend.outbox_service import start_outbox_service, stop_outbox_service
from backend.app.updater import register_update_runtime


def _register_agent_runtime() -> None:
    """Register LLM levels, built-in tools and output normalizers once at startup.

    注册动作只需要执行一次（幂等覆盖），不要在每次调用工具 Agent 时重复注册。
    """
    from agent_pipeline.llm_api import register_default_llms
    from agent_tools import register_builtin_tools
    from backend.app.shared.agent.output_normalizers import register_system_output_normalizers

    register_default_llms()
    register_builtin_tools()
    register_system_output_normalizers()


def _start_mitm_receiver(*, host: str, port: int, internal_token: str) -> ReusableThreadingHTTPServer:
    server = create_receiver(host, port, internal_token=internal_token)

    def _run() -> None:
        try:
            server.serve_forever()
        finally:
            server.server_close()

    thread = threading.Thread(target=_run, daemon=True, name="mitm-receiver")
    try:
        thread.start()
    except BaseException:
        server.server_close()
        raise
    logger.info("MITM receiver thread started on {}:{}", host, port)
    return server


def _resolve_yak_executable(backend_root: Path) -> str:
    """Yak discovery order: YAK_EXECUTABLE -> PATH -> portable runtime installed by tools/install_4_yak.py."""
    configured = get_env_str("YAK_EXECUTABLE", "")
    if configured:
        return configured
    on_path = shutil.which("yak")
    if on_path:
        return on_path
    return str(backend_root / ".portable" / "yak" / "yak.exe")


def _wait_for_port(host: str, port: int, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _start_yak_mitm(
    backend_root: Path, *, host: str, port: int, internal_token: str,
) -> subprocess.Popen | None:
    """Start the Yak MITM proxy via ``yak yak_mitm.yak`` in a subprocess."""
    host = validate_receiver_config(host, port, internal_token)
    child_env = colorless_child_env()
    child_env["MAA_MITM_INTERNAL_TOKEN"] = internal_token
    child_env["MITM_RECEIVER_HOST"] = host
    child_env["MITM_RECEIVER_PORT"] = str(port)
    yak_exe = _resolve_yak_executable(backend_root)
    yak_script = backend_root / "yak_mitm.yak"
    proxy_host = get_env_str("MITM_PROXY_HOST", MAA_API_HOST_DEFAULT)
    proxy_port = get_env_int("MITM_PROXY_PORT", MITM_PROXY_PORT_DEFAULT)
    log_dir = backend_root / "data" / "logs"
    log_path = log_dir / "yak_mitm.log"

    if not yak_script.exists():
        logger.warning("Yak MITM script not found; proxy was not created")
        return None

    try:
        proc = subprocess.Popen(
            [yak_exe, str(yak_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=CREATE_NO_WINDOW,
            env=child_env,
        )
        attach_process_log(proc, log_path, source="yak")
    except FileNotFoundError:
        logger.error("Yak executable not found; proxy was not created")
        return None
    except OSError as exc:
        logger.error("Failed to create Yak MITM proxy process ({})", type(exc).__name__)
        return None

    reachable = _wait_for_port(proxy_host, proxy_port, timeout_s=YAK_STARTUP_GRACE_S + 3.5)
    code = proc.poll()
    if code is not None:
        finish_process_log(proc)
        logger.error("Yak MITM proxy exited during startup (code={}); proxy readiness was not verified", code)
        return None
    # A slow listener shows up as tcp_reachable=false, not as a separate warning.
    log_event("process.started", component="yak", pid=proc.pid, tcp_reachable=reachable)
    return proc


def main() -> None:
    try:
        _main()
    except Exception as exc:
        report_startup_failure(exc)
        raise


def _main() -> None:
    load_workdir_env()
    validate_auth_config()
    internal_token = "mitm_" + secrets.token_urlsafe(32)
    receiver_port = get_env_int("MITM_RECEIVER_PORT", MITM_RECEIVER_PORT_DEFAULT)
    receiver_host = validate_receiver_config(
        get_env_str("MITM_RECEIVER_HOST", MITM_RECEIVER_HOST_DEFAULT), receiver_port, internal_token,
    )
    configure_logging()

    # Must precede every application child: the updater contains descendants in
    # a non-breakaway job while its independent PowerShell broker stays outside.
    register_update_runtime()

    backend_root = resolve_backend_root()
    ensure_system_agents_seeded()
    ensure_default_llm_levels_seeded()
    _register_agent_runtime()
    maafw = MaaFWProcess(backend_root)
    yak_proc: subprocess.Popen | None = None
    receiver = None

    try:
        receiver = _start_mitm_receiver(host=receiver_host, port=receiver_port, internal_token=internal_token)
        maafw.start()
        yak_proc = _start_yak_mitm(
            backend_root, host=receiver_host, port=receiver_port, internal_token=internal_token,
        )
        start_sync_service()
        start_outbox_service()
        start_card_sweep_service()
        run_api()
    except MaaFWProcessError:
        logger.exception("Failed to start MaaFW")
        raise
    finally:
        try:
            stop_card_sweep_service()
        except Exception:
            logger.exception("Card sweep shutdown failed")
        try:
            stop_outbox_service()
        except Exception:
            logger.exception("Outbox shutdown failed")
        finally:
            try:
                from backend.app.task_queue import shutdown_task_queue

                shutdown_task_queue(timeout=5.0)
            except Exception:
                logger.exception("GUI task queue shutdown failed")
            finally:
                try:
                    stop_sync_service()
                except Exception:
                    logger.exception("IM sync shutdown failed")
                finally:
                    try:
                        maafw.stop()
                    except Exception:
                        logger.exception("MaaFW shutdown failed")
                    finally:
                        try:
                            if yak_proc and yak_proc.poll() is None:
                                yak_proc.terminate()
                                try:
                                    yak_proc.wait(timeout=5.0)
                                except subprocess.TimeoutExpired:
                                    yak_proc.kill()
                                    yak_proc.wait(timeout=5.0)
                                log_event("process.stopped", component="yak")
                        except Exception:
                            logger.exception("Yak MITM shutdown failed")
                        finally:
                            if yak_proc is not None:
                                finish_process_log(yak_proc)
                            if receiver is not None:
                                try:
                                    receiver.shutdown()
                                except Exception:
                                    logger.exception("MITM receiver shutdown failed")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # main already emitted type/frames without exception values or locals.
        raise SystemExit(1) from None
