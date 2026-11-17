"""Encodes PNG/DIBV5 clipboard images and checks exact decoded pixels."""

from io import BytesIO
import struct

from PIL import Image

from backend.app.shared.clipboard_payload import ClipboardPayload, ClipboardReadback
from backend.app.shared.image_content import decoded_identity, image_identity, MAX_IMAGE_PIXELS

CF_DIB, CF_DIBV5 = 8, 17


def dib_identity(data: bytes) -> dict:
    """Reads a bounded DIB without treating a GDI bitmap handle as HGLOBAL data."""
    if len(data) < 40 or len(data) > MAX_IMAGE_PIXELS * 4 + 65536:
        raise ValueError("Invalid DIB size")
    header, width, height, planes, bits, compression = struct.unpack_from("<IiiHHI", data)
    if header not in (40, 108, 124) or planes != 1 or bits not in (24, 32) or compression not in (0, 3):
        raise ValueError("Unsupported DIB layout")
    masks = 12 if header == 40 and compression == 3 else 0
    offset = header + masks
    bmp = struct.pack("<2sIHHI", b"BM", 14 + len(data), 0, 0, 14 + offset) + data
    with Image.open(BytesIO(bmp)) as image:
        return image_identity(image)


def image_payload(png: bytes) -> ClipboardPayload:
    expected = decoded_identity(png)
    with Image.open(BytesIO(png)) as original, original.convert("RGBA") as image:
        header = bytearray(124)
        struct.pack_into("<IiiHHII", header, 0, 124, image.width, -image.height, 1, 32, 3, image.width * image.height * 4)
        struct.pack_into("<IIIII", header, 40, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000, 0x73524742)
        dib = bytes(header) + image.tobytes("raw", "BGRA")
    bound = expected["width"] * expected["height"] * 4 + 65536
    return ClipboardPayload(
        (("PNG", png), (CF_DIBV5, dib)),
        (ClipboardReadback("PNG", 20_000_000, lambda data: decoded_identity(data) == expected),
         ClipboardReadback(CF_DIBV5, bound, lambda data: dib_identity(data) == expected),
         ClipboardReadback(CF_DIB, bound, lambda data: dib_identity(data) == expected)),
    )
