"""Lossless client frames shared by remote preview and PNG downloads."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class ClientFrame:
    png_bytes: bytes
    sha256: str
    width: int
    height: int


def frame_from_image(image: np.ndarray) -> ClientFrame:
    """Encode the complete MaaFW BGR frame losslessly, without cropping or resizing."""
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim != 3
        or image.shape[2] != 3
        or image.size == 0
    ):
        raise ValueError("Screenshot did not return a nonempty uint8 BGR frame.")
    output = BytesIO()
    Image.fromarray(image[:, :, ::-1]).save(output, format="PNG")
    png = output.getvalue()
    height, width = image.shape[:2]
    return ClientFrame(png, sha256(png).hexdigest(), width, height)
