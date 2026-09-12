from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.envelope import ok
from backend.app.shared.crm import get_self_info, refresh_chat_data
from backend.app.shared.mitm.pool import (
    get_generic_card_pool,
    get_inquiry_card_pool,
    get_product_card_pool,
    get_self_info_pool,
    get_user_info_pool,
)

router = APIRouter()


@router.get("/api/self-info")
def self_info() -> dict:
    info = get_self_info()
    if info is None:
        return ok(None)
    return ok(info.model_dump())


@router.post("/api/cache/reset")
def reset_cache() -> dict:
    get_user_info_pool().clear()
    get_self_info_pool().clear()
    get_product_card_pool().clear()
    get_inquiry_card_pool().clear()
    get_generic_card_pool().clear()
    return ok(None)


@router.get("/api/sync-state")
def sync_state() -> dict:
    state = refresh_chat_data(wait=False)
    return ok({"ready": state.ready, "self_ali_id": state.self_ali_id, "reason": state.reason})
