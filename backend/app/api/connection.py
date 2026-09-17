"""Connection observations shared by settings and chat reads (no live retry)."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from backend.app.api.envelope import AppError, user_message
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.backend.gui_session import get_client_status
from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.crm.identities import PLATFORM_PID
from backend.app.shared.crm.sdk import LLMApiConfig
from backend.app.shared.mitm.pool import SelfInfo
from backend.app.shared.utils.settings import CRM_DB_RELATIVE_DEFAULT, resolve_backend_root


def _observe_crm(query: str, tables: set[str], parameters: tuple = ()) -> list[dict]:
    # SDK managers create tables and CRMAdapter runs migrations on construction.
    # Polling must leave those writes to explicit setup/synchronization paths.
    path = Path(os.environ.get("MAA_CRM_DB_PATH", CRM_DB_RELATIVE_DEFAULT))
    if not path.is_absolute():
        path = resolve_backend_root() / path
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
            return SelfInfo.model_validate(extra).ali_id == self_ali_id
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
    client = dict(get_client_status())
    client["detail"] = user_message(client["detail"])
    model_error = ""
    try:
        configured = model_configured()
    except (sqlite3.Error, OSError):
        configured = False
        model_error = "无法从应用数据库读取模型配置。"
    key = source["key_validation"]
    key_detail = {"unverified": "尚未验证", "valid": "验证通过", "invalid": "验证失败", "unavailable": "不可用"}[key]
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
            "operate_client": bool(context.self_ali_id and client["connected"] and client["confirmed"]),
        },
        "model": {"configured": configured, "verified": False},
        "steps": [
            {"id": "data_dir", "state": "ready" if directory["state"] == "ok" else ("error" if directory["state"] == "invalid" else "pending"), "detail": directory["detail"] or directory["path"]},
            {"id": "identity", "state": "ready" if context.self_ali_id else "pending", "detail": f"已选择卖家账号：{context.self_ali_id}" if context.self_ali_id else "请在当前数据目录中选择卖家账号。"},
            {"id": "key", "state": "ready" if key == "valid" else ("error" if key in {"invalid", "unavailable"} else "pending"), "detail": f"密钥{key_detail}。点击重试可验证密钥并同步聊天。"},
            {"id": "crm", "state": "error" if archive_error or source["phase"] == "error" else ("ready" if source["ready"] else "pending"), "detail": archive_error or source["last_error"] or ("当前卖家的存档可读，但数据源尚未就绪，存档可能过期。" if source["stale"] and archive else ("聊天同步已就绪。" if source["ready"] else "点击重试以创建或更新卖家聊天存档。"))},
            {"id": "client", "state": "ready" if client["connected"] and client["confirmed"] else "pending", "detail": client["detail"]},
            {"id": "model", "state": "error" if model_error else ("ready" if configured else "pending"), "detail": model_error or ("模型配置可用，尚未验证实际调用。" if configured else "请配置模型接口地址、API 密钥和模型名称；实际调用尚未验证。")},
        ],
    }
