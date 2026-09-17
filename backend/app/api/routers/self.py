from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.envelope import ok
from backend.app.api.account_scope import AccountRoute
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.crm import get_self_info
from backend.app.shared.mitm.pool import (
    get_generic_card_pool,
    get_inquiry_card_pool,
    get_product_card_pool,
    get_user_info_pool,
)

router = APIRouter(route_class=AccountRoute)


@router.get("/api/self-info")
def self_info() -> dict:
    info = get_self_info(get_account_context().self_ali_id)
    if info is None:
        return ok(None)
    return ok(info.model_dump())


@router.post("/api/cache/reset")
def reset_cache() -> dict:
    get_user_info_pool().clear()
    get_product_card_pool().clear()
    get_inquiry_card_pool().clear()
    get_generic_card_pool().clear()
    return ok(None)


@router.get("/api/sync-state")
def sync_state() -> dict:
    state = get_im_db_middleware().sync_status()
    return ok({**state, "reason": state.get("error_code") or ""})
