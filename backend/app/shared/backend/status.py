"""System status checks — pure data, no UI dependencies."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.utils.app_config import get_configured_self_ali_id
from backend.app.shared.utils.env import get_env_int, get_env_str, load_workdir_env
from backend.app.shared.utils.settings import (
    MITM_PROXY_HOST_DEFAULT,
    MITM_PROXY_PORT_DEFAULT,
    MITM_RECEIVER_HOST_DEFAULT,
    MITM_RECEIVER_PORT_DEFAULT,
    MITM_CHECK_TIMEOUT_S,
)


@dataclass(frozen=True)
class KeyStatus:
    has_key: bool
    source: str  # "live" | "cached" | "none"
    ali_id: str
    db_exists: bool


@dataclass(frozen=True)
class NetworkStatus:
    reachable: bool
    host: str
    port: int
    latency_ms: float | None
    error: str | None


def check_user_status() -> KeyStatus:
    load_workdir_env()
    ali_id = get_configured_self_ali_id()

    mw = get_im_db_middleware()
    has_key, source = mw.key_status()

    db_exists = mw.resolve_encrypted_db_path(ali_id) is not None if ali_id else False

    return KeyStatus(has_key=has_key, source=source, ali_id=ali_id, db_exists=db_exists)


def check_data_dir_status() -> dict:
    """Return the middleware data-dir status dict for the status snapshot."""
    return get_im_db_middleware().data_dir_status()


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
