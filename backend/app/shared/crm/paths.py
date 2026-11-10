"""Resolve the CRM database path from fixed configuration, without creating it."""

from pathlib import Path


def default_crm_database_path() -> Path:
    from backend.app.shared.utils import paths
    from backend.app.shared.utils.env import get_env_str
    from backend.app.shared.utils.settings import CRM_DB_RELATIVE_DEFAULT

    return paths.resolve_backend_relative(get_env_str("MAA_CRM_DB_PATH", CRM_DB_RELATIVE_DEFAULT))
