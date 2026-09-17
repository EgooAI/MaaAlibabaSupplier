"""In-memory full-frame evidence; storage, binding and expiry belong to the service."""

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


@dataclass(frozen=True)
class FrameComparison:
    needs_reconfirm: bool
    frame: ClientFrame


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


def compare_frame(expected_sha256: str, fresh_frame: ClientFrame) -> FrameComparison:
    """Any full-frame change requires confirmation before input; never guess a ROI.

    The digest covers the deterministic lossless PNG, including its dimensions.
    The caller must obtain fresh_frame inside the same run_guarded as any input.
    """
    return FrameComparison(fresh_frame.sha256 != expected_sha256, fresh_frame)
