"""Checks clipboard wire bytes independently of native memory and editor behavior."""

import re

import pytest

from backend.app.shared.clipboard_payload import HTML_FORMAT, read_clipboard_html, text_payload


def test_html_offsets_and_plain_flavor_preserve_multibyte_content():
    content = "<p>中文 🙂 &amp; &lt;b&gt;</p><p><b>next</b></p>"
    payload = text_payload(content, "html")
    formats = dict(payload.formats)
    raw = formats[HTML_FORMAT]
    offsets = {key.decode(): int(value) for key, value in re.findall(rb"(?m)^(\w+):(\d+)\r?$", raw)}
    fragment = raw[offsets["StartFragment"]:offsets["EndFragment"]]
    assert fragment == content.encode("utf-8")
    context = raw[offsets["StartHTML"]:offsets["EndHTML"]]
    assert context == b"<html><body><!--StartFragment-->" + fragment + b"<!--EndFragment--></body></html>"
    assert raw[offsets["EndHTML"]:] == b"\0"
    assert formats[13].decode("utf-16-le") == "中文 🙂 & <b>\r\nnext\0"
    assert payload.readback_format == HTML_FORMAT
    assert payload.matches(raw)
    assert not payload.matches(raw.replace(b"<b>next</b>", b"<i>next</i>"))


@pytest.mark.parametrize("header", [
    b"StartFragment:-1\r\nEndFragment:2\r\n",
    b"StartHTML:0\r\nEndHTML:99999\r\nStartFragment:1\r\nEndFragment:99999\r\n",
    b"StartHTML:0\r\nEndHTML:4\r\nStartFragment:3\r\nEndFragment:2\r\n",
])
def test_malformed_html_readback_cannot_be_used_as_evidence(header):
    with pytest.raises(ValueError):
        read_clipboard_html(header + b"<b>body</b>\0")
