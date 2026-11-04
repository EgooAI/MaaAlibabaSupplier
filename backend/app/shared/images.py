"""Image-message payload parsing.

The chat database stores every image message (``user_content_type = 7``) as a
binary blob with one embedded JSON object. This module is the single place that
turns that blob into the remote file URL and the display size. The URL carries
account-bound signature parameters, so it is used server-side only and never
appears in an API response.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.app.shared.crm.views import IMAGE_CONTENT_TYPE, CrmMessage


@dataclass(frozen=True)
class ImagePayload:
    url: str
    width: int
    height: int


def parse_image(message: CrmMessage) -> ImagePayload | None:
    """Extract the embedded image JSON from a message blob, tolerating binary padding."""
    if message.user_content_type != IMAGE_CONTENT_TYPE or not message.content:
        return None
    text = bytes(message.content).decode("latin1")
    decoder = json.JSONDecoder()
    position = 0
    while True:
        index = text.find("{", position)
        if index < 0:
            return None
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            position = index + 1
            continue
        if isinstance(value, dict) and isinstance(value.get("url"), str) and value["url"]:
            return ImagePayload(value["url"], _dimension(value.get("width")), _dimension(value.get("height")))
        position = end if isinstance(value, dict) else index + 1


def image_view(message: CrmMessage) -> dict | None:
    """Display size of an image message; None when the blob has no usable image."""
    image = parse_image(message)
    return None if image is None else {"width": image.width, "height": image.height}


def _dimension(raw: Any) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


__all__ = ["ImagePayload", "image_view", "parse_image"]
