"""Keeps plain-text and HTML send representations distinct throughout verification."""

from html import escape


def newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def validate_content(content: str, content_format: str) -> None:
    if content_format != "text":
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
    # Historical HTML tasks retain their original evidence contract.
    return content_format == "html" and actual == expected
