"""Builds clipboard flavors and readback contracts without accessing Windows."""

from collections.abc import Callable
from dataclasses import dataclass
import re

from backend.app.shared.message_content import html_plain_text, newlines, validate_content


CF_UNICODETEXT = 13
HTML_FORMAT = "HTML Format"


@dataclass(frozen=True)
class ClipboardPayload:
    formats: tuple[tuple[int | str, bytes], ...]
    readback_format: int | str
    max_readback_bytes: int
    matches: Callable[[bytes], bool]


def clipboard_html(fragment: str) -> bytes:
    """Encodes CF_HTML offsets in UTF-8 bytes, including multibyte characters."""
    header = "Version:1.0\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\nStartFragment:{:010d}\r\nEndFragment:{:010d}\r\n"
    prefix = b"<html><body><!--StartFragment-->"
    suffix = b"<!--EndFragment--></body></html>"
    body = fragment.encode("utf-8")
    start = len(header.format(0, 0, 0, 0).encode("ascii"))
    fragment_start = start + len(prefix)
    fragment_end = fragment_start + len(body)
    return header.format(start, fragment_end + len(suffix), fragment_start, fragment_end).encode("ascii") + prefix + body + suffix + b"\0"


def read_clipboard_html(data: bytes) -> str:
    """Extracts the advertised fragment, excluding the clipboard's HTML context."""
    offsets = {}
    for key in (b"StartHTML", b"EndHTML", b"StartFragment", b"EndFragment"):
        match = re.search(rb"(?:^|[\r\n])" + key + rb":[ \t]*(-?\d+)[ \t]*(?:\r|\n)", data[:4096])
        if match is None:
            raise ValueError("Invalid HTML clipboard offsets")
        offsets[key] = int(match[1])
    start, end = offsets[b"StartFragment"], offsets[b"EndFragment"]
    if not 0 <= start < end <= len(data):
        raise ValueError("Invalid HTML clipboard fragment")
    html_start, html_end = offsets[b"StartHTML"], offsets[b"EndHTML"]
    if (html_start, html_end) != (-1, -1) and not 0 <= html_start <= start < end <= html_end <= len(data):
        raise ValueError("Invalid HTML clipboard context")
    return data[start:end].decode("utf-8")


def read_unicode(data: bytes) -> str:
    if len(data) % 2:
        raise ValueError("Invalid Unicode clipboard size")
    end = next((index for index in range(0, len(data), 2) if data[index:index + 2] == b"\0\0"), None)
    if end is None:
        raise ValueError("Unterminated Unicode clipboard data")
    return data[:end].decode("utf-16-le")


def text_payload(content: str, content_format: str = "text") -> ClipboardPayload:
    """Packages text or HTML with format-specific, exact readback verification."""
    validate_content(content, content_format)
    expected = newlines(content)
    plain = html_plain_text(expected) if content_format == "html" else expected
    data = (plain.replace("\n", "\r\n") + "\0").encode("utf-16-le")
    if content_format == "text":
        return ClipboardPayload(((CF_UNICODETEXT, data),), CF_UNICODETEXT, len(data) + 4096,
                                lambda raw: newlines(read_unicode(raw)) == expected)
    html_data = clipboard_html(expected)
    return ClipboardPayload(((HTML_FORMAT, html_data), (CF_UNICODETEXT, data)), HTML_FORMAT, len(html_data) + 65536,
                            lambda raw: newlines(read_clipboard_html(raw)) == expected)
