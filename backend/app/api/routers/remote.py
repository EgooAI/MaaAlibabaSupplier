from __future__ import annotations

import io
import time
from threading import RLock
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image

from backend.app.api.account_scope import AccountRoute, request_epoch
from backend.app.api.envelope import AppError, ok
from backend.app.shared.backend import gui_session, maafw_runner

router = APIRouter(route_class=AccountRoute)
_SESSION_TTL = 300
_lock = RLock()
_session: dict | None = None


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    action: str = Field(pattern=r"^(click|long_press|drag|type)$")
    x: int | None = Field(default=None, ge=0, le=1280)
    y: int | None = Field(default=None, ge=0, le=1280)
    x1: int | None = Field(default=None, ge=0, le=1280)
    y1: int | None = Field(default=None, ge=0, le=1280)
    x2: int | None = Field(default=None, ge=0, le=1280)
    y2: int | None = Field(default=None, ge=0, le=1280)
    duration: int = Field(default=600, ge=100, le=5000)
    text: str = Field(default="", max_length=4000)


def _get_session(session_id: str) -> dict:
    global _session
    try:
        UUID(hex=session_id)
    except ValueError:
        raise AppError("远控会话不存在或已失效。", status_code=409) from None
    with _lock:
        if _session is None or _session["id"] != session_id or _session["expires_at"] <= time.time():
            _session = None
            raise AppError("远控会话不存在或已失效。", status_code=409)
        _session["expires_at"] = time.time() + _SESSION_TTL
        return _session


def _assert_active(state: dict) -> None:
    with _lock:
        if _session is not state or state["expires_at"] <= time.time():
            raise AppError("远控会话不存在或已失效。", status_code=409)


@router.post("/api/remote/session")
def create_session() -> dict:
    global _session
    token = gui_session.capture_gui_session(request_epoch.get() or "")
    with _lock:
        _session = {"id": uuid4().hex, "token": token, "expires_at": time.time() + _SESSION_TTL, "sha256": None}
        return ok({"session_id": _session["id"], "expires_in": _SESSION_TTL})


@router.get("/api/remote/session/{session_id}/frame")
def get_frame(session_id: str, if_none_match: str | None = Header(default=None)) -> Response:
    state = _get_session(session_id)
    def capture():
        _assert_active(state)
        return maafw_runner.capture_client_frame()

    result = gui_session.run_guarded(state["token"], capture)
    if not hasattr(result, "png_bytes"):
        raise AppError(result[1] if isinstance(result, tuple) else "截图获取失败。", status_code=503)
    if (
        state["sha256"] == result.sha256
        and if_none_match
        and if_none_match.strip('"') == state["sha256"]
    ):
        return Response(status_code=304, headers={"ETag": f'"{result.sha256}"', "Cache-Control": "no-store"})
    with Image.open(io.BytesIO(result.png_bytes)) as image:
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=75)
    state["sha256"] = result.sha256
    state["width"], state["height"] = result.width, result.height
    return Response(
        output.getvalue(), media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store", "ETag": f'"{result.sha256}"',
            "X-Frame-Width": str(result.width), "X-Frame-Height": str(result.height),
        },
    )


@router.get("/api/remote/session/{session_id}/download")
def download_frame(session_id: str) -> Response:
    state = _get_session(session_id)

    def capture():
        _assert_active(state)
        return maafw_runner.capture_client_frame()

    result = gui_session.run_guarded(state["token"], capture)
    if not hasattr(result, "png_bytes"):
        raise AppError(result[1] if isinstance(result, tuple) else "截图下载失败。", status_code=503)
    return Response(
        result.png_bytes, media_type="image/png",
        headers={
            "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="client-{time.strftime("%y%m%d-%H%M%S")}.png"',
            "X-Frame-Width": str(result.width), "X-Frame-Height": str(result.height),
        },
    )


@router.post("/api/remote/action")
def run_action(body: ActionInput) -> dict:
    state = _get_session(body.session_id)
    if body.action in ("click", "long_press") and (body.x is None or body.y is None):
        raise AppError("点击操作缺少坐标。", status_code=422)
    if body.action == "drag" and any(value is None for value in (body.x1, body.y1, body.x2, body.y2)):
        raise AppError("拖动操作缺少坐标。", status_code=422)
    if body.action == "type" and not body.text:
        raise AppError("请输入要键入的文本。", status_code=422)
    for x_name, y_name in (("x", "y"), ("x1", "y1"), ("x2", "y2")):
        x, y = getattr(body, x_name), getattr(body, y_name)
        if x is not None and y is not None and (
            state.get("width") is None or x >= state["width"] or y >= state["height"]
        ):
            raise AppError("操作坐标超出当前截图范围，请刷新截图后重试。", status_code=422)
    def act():
        _assert_active(state)
        return maafw_runner.run_remote_action(body.action, body.model_dump(exclude_none=True))

    result = gui_session.run_guarded(state["token"], act)
    if not result[0]:
        raise AppError(result[1], status_code=503)
    return ok({"completed": True})


@router.delete("/api/remote/session/{session_id}")
def close_session(session_id: str) -> dict:
    global _session
    with _lock:
        if _session is not None and _session["id"] == session_id:
            _session = None
    return ok({"closed": True})
