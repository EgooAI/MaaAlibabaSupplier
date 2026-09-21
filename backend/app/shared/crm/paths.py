"""Resolve the CRM database path from fixed configuration, without creating it."""

from pathlib import Path


def default_crm_database_path() -> Path:
    from backend.app.shared.utils.env import get_env_str
    from backend.app.shared.utils.settings import CRM_DB_RELATIVE_DEFAULT, resolve_backend_root

    raw = get_env_str("MAA_CRM_DB_PATH", CRM_DB_RELATIVE_DEFAULT)
    path = Path(raw)
    if not path.is_absolute():
        path = resolve_backend_root() / path
    return path
