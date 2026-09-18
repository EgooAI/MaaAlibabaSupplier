from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from backend.app.api.envelope import AppError, ok


router = APIRouter(prefix="/api/auth")


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    secret_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$", min_length=64, max_length=64)


@router.post("/login")
async def login(body: LoginBody, request: Request) -> dict:
    store = request.app.state.auth_store
    if not secrets.compare_digest(bytes.fromhex(body.secret_sha256), store.config.secret_digest):
        raise AppError("Invalid credentials", status_code=401)
    try:
        token = await run_in_threadpool(store.issue)
    except Exception:
        raise AppError("Authentication unavailable", status_code=503) from None
    return ok({"token": token})


@router.get("/session")
async def session() -> dict:
    return ok({"authenticated": True})


@router.post("/logout")
async def logout(request: Request) -> dict:
    try:
        await run_in_threadpool(request.app.state.auth_store.revoke, request.state.auth_token_hash)
    except Exception:
        raise AppError("Authentication unavailable", status_code=503) from None
    return ok()
