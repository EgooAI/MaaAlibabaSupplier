from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.account_scope import AccountRoute
from backend.app.api.envelope import ok
from backend.app.shared.backend.card_sweep_service import get_card_sweep_service

router = APIRouter(route_class=AccountRoute)


@router.get("/api/cards/sweep")
def sweep_state() -> dict:
    return ok(get_card_sweep_service().observation())


@router.post("/api/cards/sweep")
def sweep_now() -> dict:
    service = get_card_sweep_service()
    service.trigger()
    return ok({"accepted": True, "state": service.observation()})
