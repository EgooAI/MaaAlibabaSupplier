from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.api.envelope import AppError, api_error, ok
from backend.app.api.account_scope import SettingsRoute, require_epoch
from backend.app.api.connection import connection_snapshot
from backend.app.shared.backend.gui_session import connect_client, confirm_client
from backend.app.shared.backend.im_db_middleware import (
    IMDBMiddleware,
    STATE_OK,
    find_data_dir_candidates,
    get_im_db_middleware,
)
from backend.app.shared.crm.account_keys import (
    KeyFormatError,
    delete_key,
    get_key_hex,
    get_key_source,
    mask_key_preview,
    parse_and_verify_key,
    save_key,
)
from backend.app.shared.utils.app_config import get_configured_self_ali_id

router = APIRouter(route_class=SettingsRoute)


class ConnectionInput(BaseModel):
    epoch: str = Field(min_length=1)


class ConfirmConnectionInput(ConnectionInput):
    window_generation: str = Field(min_length=1)


@router.get("/api/settings/connection")
def get_connection() -> dict:
    return ok(connection_snapshot())


@router.post("/api/settings/connection/connect")
def connect_connection(body: ConnectionInput) -> dict:
    require_epoch(body.epoch)
    connect_client()
    return ok(connection_snapshot())


@router.post("/api/settings/connection/confirm")
def confirm_connection(body: ConfirmConnectionInput) -> dict:
    require_epoch(body.epoch)
    confirm_client(body.epoch, body.window_generation)
    return ok(connection_snapshot())


@router.post("/api/settings/connection/retry")
def retry_connection(body: ConnectionInput) -> dict:
    require_epoch(body.epoch)
    get_im_db_middleware().retry_connection(wait=False)
    return ok(connection_snapshot())


class DataDirInput(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class AliIdInput(BaseModel):
    ali_id: str = Field(default="", max_length=64)


class AliKeyInput(BaseModel):
    ali_id: str = Field(min_length=1, max_length=64)
    aes_key_hex: str = Field(min_length=1, max_length=128)


@router.get("/api/settings/alibaba-data-dir")
def get_data_dir() -> dict:
    return ok(get_im_db_middleware().data_dir_status())


@router.put("/api/settings/alibaba-data-dir")
def save_data_dir(body: DataDirInput) -> dict:
    raw = (body.path or "").strip()
    if not raw:
        raise AppError("数据目录路径不能为空", status_code=422)
    candidate = Path(raw).resolve()
    if not candidate.exists():
        raise AppError(f"目录不存在: {raw}", status_code=422)
    if not IMDBMiddleware.looks_like_data_dir(candidate):
        raise AppError("目录下未找到 IMServiceDir/MessageSDK IM 数据库结构", status_code=422)
    try:
        get_im_db_middleware().set_data_dir(str(candidate))
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(get_im_db_middleware().data_dir_status())


@router.get("/api/settings/alibaba-data-dir/candidates")
def list_data_dir_candidates() -> dict:
    try:
        candidates = find_data_dir_candidates()
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok({"candidates": candidates})


def _require_data_dir_ok() -> IMDBMiddleware:
    mw = get_im_db_middleware()
    if mw.data_dir_status()["state"] != STATE_OK:
        raise AppError("数据目录尚未配置或无效，请先配置数据目录", status_code=422)
    return mw


def _ali_id_rows(mw: IMDBMiddleware) -> list[dict]:
    selected = get_configured_self_ali_id()
    rows = []
    for account in mw.scan_ali_ids():
        ali_id = account["ali_id"]
        record_hex = get_key_hex(ali_id)
        rows.append(
            {
                "ali_id": ali_id,
                "db_size": account["db_size"],
                "last_modified": account["last_modified"],
                "has_key": record_hex is not None,
                "key_preview": mask_key_preview(record_hex.hex()) if record_hex is not None else "",
                "key_source": get_key_source(ali_id),
                "is_active": ali_id == selected,
            }
        )
    return rows


@router.get("/api/settings/ali-ids")
def list_ali_ids() -> dict:
    mw = _require_data_dir_ok()
    try:
        rows = _ali_id_rows(mw)
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok({"accounts": rows, "selected": get_configured_self_ali_id()})


@router.put("/api/settings/ali-id")
def save_ali_id(body: AliIdInput) -> dict:
    mw = _require_data_dir_ok()
    ali_id = (body.ali_id or "").strip()
    if ali_id and ali_id not in {row["ali_id"] for row in mw.scan_ali_ids()}:
        raise AppError(f"数据目录下未发现该账号: {ali_id}", status_code=422)
    try:
        mw.set_self_ali_id(ali_id)
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok({"accounts": _ali_id_rows(mw), "selected": ali_id})


@router.put("/api/settings/ali-keys")
def save_ali_key(body: AliKeyInput) -> dict:
    mw = _require_data_dir_ok()
    ali_id = body.ali_id.strip()
    db_path = mw.resolve_encrypted_db_path(ali_id)
    if db_path is None:
        raise AppError(f"数据目录下未发现该账号: {ali_id}", status_code=422)
    try:
        key = parse_and_verify_key(body.aes_key_hex, db_path)
    except KeyFormatError as exc:
        raise AppError(str(exc), status_code=422) from exc
    try:
        save_key(ali_id, key, "manual")
        mw.drop_cached_key()
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok({"accounts": _ali_id_rows(mw), "selected": get_configured_self_ali_id()})


@router.delete("/api/settings/ali-keys/{ali_id}")
def clear_ali_key(ali_id: str) -> dict:
    mw = _require_data_dir_ok()
    try:
        removed = delete_key(ali_id)
        mw.drop_cached_key()
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    if not removed:
        raise AppError(f"该账号没有已存 Key: {ali_id}", status_code=422)
    return ok({"accounts": _ali_id_rows(mw), "selected": get_configured_self_ali_id()})
