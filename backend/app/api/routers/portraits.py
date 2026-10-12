"""Customer portrait maintenance endpoints.

Portraits are workspace-scoped business state on top of the existing
conversation list. The batch endpoint resolves SIDs to customers, validates
visibility and versions, and commits portraits, tags, records and revisions in
one transaction; the client must refresh and retry on 409.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.app.api.account_scope import AccountRoute
from backend.app.api.envelope import AppError, ok
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.backend.customer_portrait_service import export_customer_portraits
from backend.app.shared.crm.customer_portrait_store import (
    LOST_NOTE_MAX,
    MAX_BATCH_TARGETS,
    MAX_EXPORT_TARGETS,
    TAG_DESCRIPTION_MAX,
    TAG_NAME_MAX,
    CustomerPortraitStore,
    PortraitConflict,
    PortraitNotFound,
    PortraitNotReady,
)
from backend.app.shared.crm.inbox_store import InboxStore

router = APIRouter(route_class=AccountRoute)

_PORTRAIT_ERRORS = (PortraitConflict, PortraitNotFound, PortraitNotReady, ValueError)


class TagCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=TAG_NAME_MAX)
    description: str = Field(default="", max_length=TAG_DESCRIPTION_MAX)


class TagUpdateInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=TAG_NAME_MAX)
    description: str | None = Field(default=None, max_length=TAG_DESCRIPTION_MAX)
    archived: bool | None = None


class PortraitTargetInput(BaseModel):
    sid: int = Field(ge=1)
    expected_cid: int = Field(ge=1)
    expected_version: int = Field(ge=0)


class PortraitChangesInput(BaseModel):
    stage: str | None = None
    lost_reason: str | None = None
    lost_note: str | None = Field(default=None, max_length=LOST_NOTE_MAX)
    add_tag_ids: list[str] = Field(default_factory=list, max_length=20)
    remove_tag_ids: list[str] = Field(default_factory=list, max_length=20)


class PortraitBatchInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"\S")
    targets: list[PortraitTargetInput] = Field(min_length=1, max_length=MAX_BATCH_TARGETS)
    changes: PortraitChangesInput


class PortraitExportInput(BaseModel):
    conversationIds: list[int] = Field(min_length=1, max_length=MAX_EXPORT_TARGETS)


def _portrait_store() -> tuple[CustomerPortraitStore, str, str]:
    context = get_account_context()
    if not context.self_ali_id:
        raise AppError("尚未选择阿里账号身份，请前往设置页选择后重试", status_code=503)
    if not context.data_dir:
        raise AppError("请先在设置页选择数据目录。", status_code=503)
    return CustomerPortraitStore(), context.self_ali_id, context.data_dir


def _translate(error: Exception) -> AppError:
    if isinstance(error, PortraitConflict):
        return AppError(str(error), status_code=409)
    if isinstance(error, PortraitNotFound):
        return AppError(str(error), status_code=404)
    if isinstance(error, PortraitNotReady):
        return AppError(str(error), status_code=503)
    return AppError(str(error) or "画像操作无效", status_code=422)


@router.get("/api/portraits/tags")
def list_portrait_tags() -> dict:
    store, seller, data_dir = _portrait_store()
    return ok({"tags": store.tag_catalog(seller, data_dir)})


@router.post("/api/portraits/tags")
def create_portrait_tag(body: TagCreateInput) -> dict:
    store, _, _ = _portrait_store()
    try:
        tag = store.create_tag(name=body.name, description=body.description)
    except _PORTRAIT_ERRORS as error:
        raise _translate(error) from error
    return ok({"tag": tag})


@router.patch("/api/portraits/tags/{tag_id}")
def update_portrait_tag(tag_id: str, body: TagUpdateInput) -> dict:
    store, seller, data_dir = _portrait_store()
    if body.name is None and body.description is None and body.archived is None:
        raise AppError("没有可应用的标签变更", status_code=422)
    try:
        tag = store.update_tag(
            seller=seller, data_dir=data_dir, tag_id=tag_id,
            name=body.name, description=body.description, archived=body.archived,
        )
    except _PORTRAIT_ERRORS as error:
        raise _translate(error) from error
    return ok({"tag": tag})


@router.post("/api/portraits/batch")
def apply_portrait_batch(body: PortraitBatchInput) -> dict:
    store, seller, data_dir = _portrait_store()
    changes = body.changes.model_dump(exclude_unset=True)
    try:
        result = store.apply(
            seller=seller, data_dir=data_dir, request_id=body.request_id,
            targets=[target.model_dump() for target in body.targets], changes=changes,
        )
    except _PORTRAIT_ERRORS as error:
        raise _translate(error) from error
    return ok(result)


@router.post("/api/portraits/export")
def export_portraits(body: PortraitExportInput) -> dict:
    store, seller, data_dir = _portrait_store()
    try:
        result = export_customer_portraits(
            InboxStore(), seller=seller, data_dir=data_dir, sids=body.conversationIds,
        )
    except _PORTRAIT_ERRORS as error:
        raise _translate(error) from error
    return ok(result)


@router.get("/api/portraits/operations")
def list_portrait_operations(
    cid: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    store, seller, data_dir = _portrait_store()
    try:
        operations = store.operations(seller, data_dir, cid, limit)
    except ValueError as error:
        raise AppError(str(error), status_code=422) from error
    return ok({"operations": operations})
