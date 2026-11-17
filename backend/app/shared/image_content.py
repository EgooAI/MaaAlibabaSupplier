"""Normalizes single-image submissions and compares decoded pixels."""

import hashlib
from io import BytesIO
import struct

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 10_000_000
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_SIDE = 8192
IMAGE_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})


def image_identity(image: Image.Image) -> dict:
    """Hashes dimensions and RGBA pixels, retaining transparent pixel values."""
    width, height = image.size
    if not (0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE and width * height <= MAX_IMAGE_PIXELS):
        raise ValueError("图片最长边不能超过 8192 像素，总像素不能超过 2400 万。")
    with image.convert("RGBA") as rgba:
        digest = hashlib.sha256(struct.pack("<II", width, height) + rgba.tobytes()).hexdigest()
    return {"width": width, "height": height, "pixel_sha256": digest}


def decoded_identity(data: bytes) -> dict:
    """Decodes bounded static images for clipboard and source-message evidence."""
    if not data or len(data) > 20_000_000:
        raise ValueError("图片数据大小无效。")
    try:
        with Image.open(BytesIO(data)) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("仅支持静态单张图片。")
            return image_identity(image)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法解码。") from exc


def prepare_image(data: bytes) -> tuple[bytes, dict]:
    """Produces a canonical PNG and immutable file/pixel identities."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片文件不能超过 10 MB（10,000,000 字节）。")
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format not in IMAGE_FORMATS or getattr(original, "n_frames", 1) != 1:
                raise ValueError("仅支持静态 PNG、JPEG、WebP 图片。")
            image_identity(original)  # Checks allocation bounds before orientation/conversion.
            with ImageOps.exif_transpose(original) as oriented:
                with oriented.convert("RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB") as image:
                    identity = image_identity(image)
                    stream = BytesIO()
                    image.save(stream, format="PNG")
        png = stream.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法解码。") from exc
    if len(png) > MAX_IMAGE_BYTES:
        raise ValueError("转换后的发送图片超过 10 MB，请缩小图片后重试。")
    return png, {
        **identity, "bytes": len(png), "mime_type": "image/png", "normalization": "png-rgba-v1",
        "sha256": hashlib.sha256(png).hexdigest(), "md5": hashlib.md5(png).hexdigest(),
        "original_sha256": hashlib.sha256(data).hexdigest(),
    }
