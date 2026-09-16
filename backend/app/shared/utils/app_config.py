"""Per-machine application config persisted outside git.

Stores user-configurable settings that vary by installation (e.g. the Alibaba
client data directory) in ``backend/data/app_config.json``. Both ``data/`` and
``.env`` are gitignored, so this file survives code updates on the user's machine.

The config file is the single source for these settings; environment variables
are intentionally not consulted (configure via the Settings page instead).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from backend.app.shared.utils.settings import resolve_backend_root

CONFIG_FILENAME = "app_config.json"
CONFIG_KEY_ALIBABA_DATA_DIR = "alibaba_data_dir"
CONFIG_KEY_SELF_ALI_ID = "self_ali_id"

_lock = threading.Lock()


def config_file_path() -> Path:
    return resolve_backend_root() / "data" / CONFIG_FILENAME


def read_app_config() -> dict[str, Any]:
    """Read the config file; return {} when missing, unreadable, or corrupt."""
    path = config_file_path()
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        logger.warning("Ignoring corrupt app config {}: {}", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def write_app_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge *patch* into the config file and return the merged config."""
    with _lock:
        merged = read_app_config()
        merged.update(patch)
        path = config_file_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write app config {}: {}", path, exc)
            raise
        return merged


def get_configured_alibaba_data_dir() -> str:
    """Return the file-configured Alibaba data dir ("" when absent)."""
    value = read_app_config().get(CONFIG_KEY_ALIBABA_DATA_DIR, "")
    return value.strip() if isinstance(value, str) else ""


def get_configured_self_ali_id() -> str:
    """Return the manually selected identity — the single source of self ali_id."""
    value = read_app_config().get(CONFIG_KEY_SELF_ALI_ID, "")
    return value.strip() if isinstance(value, str) else ""


def set_configured_self_ali_id(ali_id: str) -> None:
    write_app_config({CONFIG_KEY_SELF_ALI_ID: ali_id})
