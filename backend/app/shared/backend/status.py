"""System status checks — pure data, no UI dependencies."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.shared.backend.im_db_middleware import IMDBMiddleware
from backend.app.shared.backend.im_layout import looks_like_data_dir
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.utils.env import get_env_int, get_env_str
from backend.app.shared.utils.settings import (
    MITM_PROXY_HOST_DEFAULT,
    MITM_PROXY_PORT_DEFAULT,
    MITM_RECEIVER_HOST_DEFAULT,
    MITM_RECEIVER_PORT_DEFAULT,
    MITM_CHECK_TIMEOUT_S,
)


@dataclass(frozen=True)
class NetworkStatus:
    reachable: bool
    host: str
    port: int
    latency_ms: float | None
    error: str | None
    observed_at: float = field(default_factory=time.time)
    evidence: str = "tcp_connect"
    business_ready: bool | None = None


def check_data_dir_status() -> dict:
    """Return the middleware data-dir status dict without initializing it."""
    middleware = IMDBMiddleware._instance
    if middleware is not None:
        return middleware.data_dir_status()
    path = get_account_context().data_dir
    return {
        "state": ("ok" if looks_like_data_dir(Path(path)) else "invalid") if path else "unconfigured",
        "path": path, "source": "file" if path else "none", "detail": "",
    }


def observe_workers() -> dict:
    from backend.app.shared.backend.card_sweep_service import observe_card_sweep
    from backend.app.shared.backend.sync_coordinator import observe_sync_service
    from backend.app.shared.backend.outbox_service import observe_outbox_verifier

    return {
        "im-source-check": observe_sync_service(),
        "outbox-verifier": observe_outbox_verifier(),
        "card-sweep": observe_card_sweep(),
    }


def _check_port(host: str, port: int) -> NetworkStatus:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=MITM_CHECK_TIMEOUT_S):
            latency = (time.perf_counter() - start) * 1000
            return NetworkStatus(
                reachable=True, host=host, port=port, latency_ms=round(latency, 1), error=None
            )
    except OSError as exc:
        return NetworkStatus(
            reachable=False, host=host, port=port, latency_ms=None, error=str(exc)
        )


def check_mitm_proxy() -> NetworkStatus:
    host = get_env_str("MITM_PROXY_HOST", MITM_PROXY_HOST_DEFAULT)
    port = get_env_int("MITM_PROXY_PORT", MITM_PROXY_PORT_DEFAULT)
    return _check_port(host, port)


def check_mitm_receiver() -> NetworkStatus:
    host = get_env_str("MITM_RECEIVER_HOST", MITM_RECEIVER_HOST_DEFAULT)
    port = get_env_int("MITM_RECEIVER_PORT", MITM_RECEIVER_PORT_DEFAULT)
    return _check_port(host, port)
