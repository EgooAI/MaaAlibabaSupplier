"""Connection observations shared by settings and chat reads (no live retry)."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from backend.app.api.envelope import AppError, user_message
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.backend.gui_session import get_client_status
from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.crm.identities import PLATFORM_PID
from backend.app.shared.crm.paths import default_crm_database_path
from backend.app.shared.crm.sdk import LLMApiConfig

# Sync internals surfaced only by the diagnostics endpoint, never by the UI.
_INTERNAL_SOURCE_FIELDS = frozenset({"source_mtime", "cache_time", "wal_frames_applied", "last_refresh_ms", "wal_pipeline"})


def _observe_crm(query: str, tables: set[str], parameters: tuple = ()) -> list[dict]:
    # SDK managers create tables and CRMAdapter runs migrations on construction.
    # Polling must leave those writes to explicit setup/synchronization paths.
    path = default_crm_database_path()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        database.row_factory = sqlite3.Row
        existing = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables <= existing:
            return []
        return [dict(row) for row in database.execute(query, parameters)]


def has_selected_archive(self_ali_id: str) -> bool:
    if not self_ali_id:
        return False
    rows = _observe_crm(
        "SELECT a.extra FROM account a JOIN accountmapping m ON a.aid=m.aid "
        "WHERE m.type='ali_id' AND m.key=? AND a.pid=?",
        {"account", "accountmapping"}, (self_ali_id, PLATFORM_PID),
    )
    for row in rows:
        extra = json.loads(row["extra"] or "null")
        if isinstance(extra, dict) and extra.get("is_self") is True and extra.get("ali_id") == self_ali_id:
            return True
    return False


def safe_has_selected_archive(self_ali_id: str) -> bool:
    """Read-only polling variant: unavailable or malformed CRM data is not ready."""
    try:
        return has_selected_archive(self_ali_id)
    except (sqlite3.Error, OSError, ValueError):
        return False


def model_configured() -> bool:
    for row in _observe_crm("SELECT * FROM llm_api_config", {"llm_api_config"}):
        try:
            config = LLMApiConfig.model_validate(row)
            url = urlsplit(config.base_url.strip())
            if url.scheme in {"http", "https"} and url.hostname and config.api_key.strip() and config.model_name.strip():
                return True
        except ValueError:
            continue
    return False


def connection_snapshot() -> dict:
    context = get_account_context()
    mw = get_im_db_middleware()
    directory = dict(mw.data_dir_status())
    directory["path"] = str(Path(directory["path"]).resolve()) if directory["path"] else ""
    account = context.to_dict()
    account["data_dir"] = str(Path(context.data_dir).resolve()) if context.data_dir else ""
    source = dict(mw.sync_status())
    source["error_code"] = source.get("error_code") or None
    source["last_error"] = user_message(source["last_error"]) if source.get("last_error") else None
    archive_error = ""
    try:
        archive = has_selected_archive(context.self_ali_id)
    except (sqlite3.Error, OSError, ValueError):
        archive = False
        archive_error = "无法读取所选卖家的 CRM 存档，请检查数据库配置后重试。"
    readable = bool(not archive_error and context.self_ali_id and (source["ready"] or archive))
    source["stale"] = bool(source.get("stale") or (archive and not source["ready"]))
    # Internal sync diagnostics are not part of the UI contract; keep the
    # snapshot to the fields the settings and chat pages actually consume.
    source = {k: v for k, v in source.items() if k not in _INTERNAL_SOURCE_FIELDS}
    client = dict(get_client_status())
    client["detail"] = user_message(client["detail"])
    try:
        configured = model_configured()
    except (sqlite3.Error, OSError):
        configured = False
    # Each observation uses short locks. Reject a mixed snapshot instead of
    # holding the account lock while a queued GUI operation is running.
    if get_account_context() != context or source["epoch"] != context.epoch or source["self_ali_id"] != context.self_ali_id:
        raise AppError("读取接入状态期间账号已切换，请刷新后重试。", status_code=409)
    return {
        "account": account,
        "data_dir": directory,
        "source": source,
        "client": client,
        "capabilities": {
            "read_chat": readable,
            "use_ai": readable and configured,
            "operate_client": bool(context.self_ali_id and client["connected"]),
        },
        "model": {"configured": configured},
    }
