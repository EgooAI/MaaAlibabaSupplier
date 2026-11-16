"""Checks literal Unicode wire bytes independently of native memory."""

import pytest

from backend.app.shared.clipboard_payload import CF_UNICODETEXT, read_unicode, text_payload


def test_unicode_payload_preserves_tags_entities_and_blank_lines():
    content = "<b>中文 🙂 &amp; &lt;b&gt;</b>\n\nnext"
    payload = text_payload(content)
    raw = "<b>中文 🙂 &amp; &lt;b&gt;</b>\r\n\r\nnext\0".encode("utf-16-le")
    assert payload.formats == ((CF_UNICODETEXT, raw),)
    assert payload.readbacks[0].matches(raw)
    assert not payload.readbacks[0].matches("中文 🙂 & <b>\r\n\r\nnext\0".encode("utf-16-le"))


@pytest.mark.parametrize("data", [b"a", b"a\0", b"\0\xd8\0\0"])
def test_malformed_unicode_readback_cannot_be_used_as_evidence(data):
    with pytest.raises(ValueError):
        read_unicode(data)
