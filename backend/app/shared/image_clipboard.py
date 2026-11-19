"""Encodes PNG/DIBV5 clipboard images and checks exact decoded pixels."""

from io import BytesIO
import struct

from PIL import Image

from backend.app.shared.clipboard_payload import ClipboardPayload, ClipboardReadback, read_unicode
from backend.app.shared.image_content import decoded_identity, image_identity, MAX_IMAGE_PIXELS

CF_DIB, CF_HDROP, CF_DIBV5 = 8, 15, 17


def single_image_selection(text: bytes | None, files: bytes | None) -> bool:
    """Requires single-object evidence and rejects conflicting selection formats."""
    if text is not None and read_unicode(text) != "\ufffc":
        return False
    if files is not None:
        # Qt can export images as a Unicode DROPFILES list without any text.
        # Only inspects the list; clipboard paths never become filesystem reads.
        if len(files) < 24:
            raise ValueError("Invalid file-drop header")
        offset, _, _, _, wide = struct.unpack_from("<IiiII", files)
        if wide != 1 or offset < 20 or offset % 2 or offset > len(files) - 4:
            raise ValueError("Invalid Unicode file-drop layout")
        end = next((i for i in range(offset, len(files) - 3, 2) if files[i:i + 4] == b"\0" * 4), None)
        if end is None:
            raise ValueError("Unterminated file-drop list")
        names = files[offset:end].decode("utf-16-le").split("\0")
        if len(names) != 1 or not names[0]:
            return False
    return text is not None or files is not None


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
         ClipboardReadback("image/png", 20_000_000, lambda data: decoded_identity(data) == expected),
         ClipboardReadback(CF_DIBV5, bound, lambda data: dib_identity(data) == expected),
         ClipboardReadback(CF_DIB, bound, lambda data: dib_identity(data) == expected)),
    )
