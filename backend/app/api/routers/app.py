from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.api.envelope import ok
from backend.app.api.server import request_shutdown
from backend.app.updater import get_updater

router = APIRouter()


class UpdateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateDownload(UpdateCheck):
    candidate_id: str = Field(min_length=1, max_length=100, strict=True)


class UpdateInstall(UpdateDownload):
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


@router.get("/api/app/update")
def update_state() -> dict:
    return ok(get_updater().snapshot())


@router.post("/api/app/update/check")
def check_update(body: UpdateCheck) -> dict:
    return ok(get_updater().check())


@router.post("/api/app/update/download")
def download_update(body: UpdateDownload) -> dict:
    return ok(get_updater().download(body.candidate_id))


@router.post("/api/app/update/install")
async def install_update(body: UpdateInstall, request: Request) -> dict:
    updater = get_updater()
    # No await between admission and registration: cancellation cannot strand a
    # pending lease in a worker after the outer ASGI finally block has run.
    operation_id = updater.install_update(body.candidate_id)
    request.state.update_handoff = (updater, operation_id)
    return ok({"accepted": True})


@router.post("/api/app/shutdown")
def shutdown() -> dict:
    return ok({"accepted": request_shutdown()})
