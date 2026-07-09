"""System status checks — pure data, no UI dependencies."""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from app.shared.backend.im_db_middleware import get_im_db_middleware
from app.shared.mitm.pool import get_self_info_pool
from app.shared.utils.env import get_env_str


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
    info = get_self_info_pool().get()
    ali_id = info.ali_id if info and info.ali_id else ""

    mw = get_im_db_middleware()
    has_key, source = mw.key_status()

    db_exists = False
    if ali_id:
        data_dir = get_env_str("ALIBABA_DATA_DIR")
        if data_dir:
            db_path = (
                Path(data_dir)
                / "IMServiceDir"
                / "MessageSDK"
                / f"{ali_id}@icbu"
                / "database"
                / "im.sqlite"
            )
            db_exists = db_path.exists()

    return KeyStatus(has_key=has_key, source=source, ali_id=ali_id, db_exists=db_exists)


def _check_port(host: str, port: int) -> NetworkStatus:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=2.0):
            latency = (time.perf_counter() - start) * 1000
            return NetworkStatus(
                reachable=True, host=host, port=port, latency_ms=round(latency, 1), error=None
            )
    except OSError as exc:
        return NetworkStatus(
            reachable=False, host=host, port=port, latency_ms=None, error=str(exc)
        )


def check_mitm_proxy() -> NetworkStatus:
    host = os.environ.get("MITM_PROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("MITM_PROXY_PORT", "8084"))
    return _check_port(host, port)


def check_mitm_receiver() -> NetworkStatus:
    host = os.environ.get("MITM_RECEIVER_HOST", "127.0.0.1")
    port = int(os.environ.get("MITM_RECEIVER_PORT", "8085"))
    return _check_port(host, port)
