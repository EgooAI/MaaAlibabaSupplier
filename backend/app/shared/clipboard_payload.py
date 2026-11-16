"""Builds clipboard flavors and readback contracts without accessing Windows."""

from collections.abc import Callable
from dataclasses import dataclass

from backend.app.shared.message_content import newlines, validate_content


CF_UNICODETEXT = 13


@dataclass(frozen=True)
class ClipboardReadback:
    format: int | str
    max_bytes: int
    matches: Callable[[bytes], bool]


@dataclass(frozen=True)
class ClipboardPayload:
    formats: tuple[tuple[int | str, bytes], ...]
    readbacks: tuple[ClipboardReadback, ...]


def read_unicode(data: bytes) -> str:
    if len(data) % 2:
        raise ValueError("Invalid Unicode clipboard size")
    end = next((index for index in range(0, len(data), 2) if data[index:index + 2] == b"\0\0"), None)
    if end is None:
        raise ValueError("Unterminated Unicode clipboard data")
    return data[:end].decode("utf-16-le")


def text_payload(content: str, content_format: str = "text") -> ClipboardPayload:
    """Packages literal Unicode text with exact readback verification."""
    validate_content(content, content_format)
    expected = newlines(content)
    data = (expected.replace("\n", "\r\n") + "\0").encode("utf-16-le")
    text_readback = ClipboardReadback(CF_UNICODETEXT, len(data) + 4096,
                                      lambda raw: newlines(read_unicode(raw)) == expected)
    return ClipboardPayload(((CF_UNICODETEXT, data),), (text_readback,))
