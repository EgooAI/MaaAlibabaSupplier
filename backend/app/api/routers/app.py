from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.envelope import ok
from backend.app.api.server import request_shutdown

router = APIRouter()


@router.post("/api/app/shutdown")
def shutdown() -> dict:
    return ok({"accepted": request_shutdown()})
