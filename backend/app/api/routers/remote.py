"""Provides read-only client frames and lightweight GUI activity observations."""

import io
import time

from fastapi import APIRouter, Header, Response
from PIL import Image

from backend.app.api.account_scope import OutboxObservationRoute, outbox_context
from backend.app.api.envelope import ok
from backend.app.shared.backend.client_preview import capture_preview
from backend.app.shared.backend.gui_session import observe_gui_activity
from backend.app.shared.backend.outbox_service import observe_active_send

router = APIRouter(route_class=OutboxObservationRoute)


@router.get("/api/remote/activity")
def get_activity() -> dict:
    context = outbox_context.get()
    return ok({"sending": observe_active_send(context),
               "navigating": observe_gui_activity(context) == "goto-contact"})


@router.get("/api/remote/frame")
def get_frame(if_none_match: str | None = Header(default=None)) -> Response:
    frame = capture_preview()
    headers = {"Cache-Control": "no-store", "ETag": f'"{frame.sha256}"',
               "X-Frame-Width": str(frame.width), "X-Frame-Height": str(frame.height)}
    if if_none_match and if_none_match.strip('"') == frame.sha256:
        return Response(status_code=304, headers=headers)
    with Image.open(io.BytesIO(frame.png_bytes)) as image:
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=75)
    return Response(output.getvalue(), media_type="image/jpeg", headers=headers)


@router.get("/api/remote/download")
def download_frame() -> Response:
    frame = capture_preview()
    return Response(frame.png_bytes, media_type="image/png", headers={
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="client-{time.strftime("%y%m%d-%H%M%S")}.png"',
        "X-Frame-Width": str(frame.width), "X-Frame-Height": str(frame.height),
    })
