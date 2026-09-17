from __future__ import annotations

import math

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.account_scope import AccountRoute, OutboxObservationRoute, outbox_context, request_epoch
from backend.app.api.envelope import AppError, ok, user_message
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.backend.outbox_service import ScreenshotExpiredError, get_outbox_service

router = APIRouter(route_class=AccountRoute)
observations = APIRouter(route_class=OutboxObservationRoute)

_PUBLIC_FIELDS = (
    "id", "conversation_id", "contact_ali_id", "login_id", "content", "action",
    "idempotency_key", "status", "version", "attempt", "phase", "may_have_sent",
    "created_at", "updated_at", "screenshot_id", "screenshot_at", "matched_message_id",
)
_REASON_CODES = {
    "cancelled_by_user", "screenshot_expired_or_changed", "shutdown_before_execution",
    "shutdown_execution_uncertain", "restart_execution_uncertain", "restart_before_execution",
    "restart_confirmation_lost", "baseline_invalid", "task_not_verifiable", "snapshot_invalid",
    "snapshot_scope_or_time_mismatch", "message_not_observed", "multiple_candidate_messages",
    "message_already_claimed", "ambiguous_send_attempts", "local_message_observed",
}
_SAFE_REASONS = {
    "Manually confirm the selected account and client window first.",
    "GUI session expired; reconnect and manually confirm again.",
    "Account context changed; reload and manually confirm again.",
    "Client window changed; reconnect and manually confirm again.",
    "Client connection lost; reconnect and manually confirm again.",
    "Client is not connected; connect and manually confirm the selected account.",
    "Outbox service is stopping",
    "Source baseline unavailable; no text was entered",
}


def public_task(record: dict) -> dict:
    """Never serialize source baselines, arbitrary failure details or GUI tokens."""
    result = {name: record[name] for name in _PUBLIC_FIELDS}
    reason = record.get("reason")
    result["reason"] = (
        reason if reason is None or reason in _REASON_CODES else
        user_message(reason) if reason in _SAFE_REASONS else "outbox_operation_failed"
    )
    proof = record.get("evidence")
    result["evidence"] = None
    if isinstance(proof, dict) and proof.get("kind") == "local_source_message":
        evidence = {"kind": "local_source_message"}
        for name in ("source_revision", "checked_at", "baseline_checked_at", "sent_at"):
            value = proof.get(name)
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                evidence[name] = value
        result["evidence"] = evidence
    if record["screenshot_id"] and record["status"] in ("awaiting_confirmation", "queued_send"):
        result["screenshot_url"] = (
            f"/api/outbox/{record['id']}/screenshot/{record['screenshot_id']}?version={record['version']}"
        )
    return result


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, strict=True)


class ConfirmInput(VersionInput):
    screenshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@observations.get("/api/conversations/{conversation_id}/outbox")
def list_outbox(conversation_id: int, limit: int = Query(default=100, ge=1, le=100)) -> dict:
    tasks = get_outbox_service().list(outbox_context.get(), conversation_id, limit=limit)
    return ok([public_task(task) for task in tasks])


@observations.get("/api/outbox/{task_id}")
def get_outbox(task_id: str) -> dict:
    task = get_outbox_service().get(outbox_context.get(), task_id)
    if task is None:
        raise AppError("Outbox task not found", status_code=404)
    return ok(public_task(task))


@observations.get("/api/outbox/{task_id}/screenshot/{screenshot_id}")
def get_screenshot(task_id: str, screenshot_id: str, version: int = Query(ge=1)) -> Response:
    context = outbox_context.get()
    service = get_outbox_service()
    task = service.get(context, task_id)
    if task is None:
        raise AppError("Outbox task not found", status_code=404)
    if task["version"] != version:
        raise AppError("Screenshot or task version changed", status_code=409)
    try:
        image = service.get_screenshot(context, task_id, screenshot_id)
    except ScreenshotExpiredError as exc:
        raise AppError(exc.message, status_code=409) from None
    current = service.get(context, task_id)
    if current is None or current["version"] != version:
        raise AppError("Screenshot or task version changed", status_code=409)
    return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/api/outbox/{task_id}/confirm")
def confirm_outbox(task_id: str, body: ConfirmInput) -> dict:
    task = get_outbox_service().confirm(
        get_account_context(), task_id, version=body.version,
        screenshot_id=body.screenshot_id, expected_epoch=request_epoch.get(),
    )
    return ok(public_task(task))


@observations.post("/api/outbox/{task_id}/cancel")
def cancel_outbox(task_id: str, body: VersionInput) -> dict:
    task = get_outbox_service().cancel(outbox_context.get(), task_id, version=body.version)
    return ok(public_task(task))


@router.post("/api/outbox/{task_id}/retry")
def retry_outbox(task_id: str, body: VersionInput) -> dict:
    task = get_outbox_service().retry(get_account_context(), task_id, version=body.version)
    return ok(public_task(task))


router.include_router(observations)
