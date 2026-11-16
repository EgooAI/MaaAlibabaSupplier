"""Keeps plain-text and HTML send representations distinct throughout verification."""

from html import escape
from html.parser import HTMLParser


_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK = frozenset("p div li ul ol pre blockquote h1 h2 h3 h4 h5 h6 table tr".split())


def newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


class _Fragment(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.plain = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK and self.plain and not self.plain[-1].endswith("\n"):
            self.plain.append("\n")
        if tag in ("br", "hr"):
            self.plain.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK and self.plain and not self.plain[-1].endswith("\n"):
            self.plain.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_data(self, value):
        if not value:
            return
        self.plain.append(value)


def html_plain_text(value: str) -> str:
    """Provides companion text for clipboard input checks, not post-send matching."""
    parser = _Fragment()
    parser.feed(newlines(value).strip())
    parser.close()
    return "".join(parser.plain).strip("\n")


def validate_content(content: str, content_format: str) -> None:
    if content_format not in ("text", "html"):
        raise ValueError("Unsupported message format")
    if not isinstance(content, str) or not content.strip() or "\0" in content:
        raise ValueError("Invalid message content")


def content_matches(content: str, content_format: str, source: object) -> bool:
    """Compares text with text serialization, or HTML with its retained structure."""
    if not isinstance(source, str) or not isinstance(content, str):
        return False
    expected, actual = newlines(content).strip(), newlines(source).strip()
    if content_format == "text":
        # Plain messages have two observed client storage representations. Encode
        # the submitted literal, rather than interpreting it as HTML on a mismatch.
        encoded = escape(expected, quote=False).replace('"', "&quot;")
        encoded = encoded.replace(" ", "&nbsp;").replace("\n", "<br>")
        if any(character in expected for character in '<>&"'):
            return actual == encoded
        return actual in (expected, encoded)
    return content_format == "html" and actual == expected
