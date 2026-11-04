import json

import pytest

from backend.app.api.routers import conversations
from backend.app.shared.backend import image_fetch
from backend.app.shared.backend.image_fetch import ImageFetchError, ImageUnavailable, Response, fetch_image
from backend.app.shared.backend.im_chat_db import MessageRow
from backend.app.shared.crm.identities import message_external_id
from backend.app.shared.crm.views import CrmMessage, message_display_text, normalize_message_type
from backend.app.shared.images import parse_image
from backend.tests.test_connection_api import client, sdk  # noqa: F401
from backend.tests.test_inbox_api import archive, data, row  # noqa: F401

SIGNED_URL = "https://clouddisk.alibaba.com/file/redirectFileUrl.htm?id=1&secOperateAliId=SECRET"
CDN_URL = "https://s.alicdn.com/@chat-files/hz/chat/a.jpg?Expires=1&Signature=x"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def blob(payload: dict) -> bytes:
    return b"\x82\x01e\x03\x85\x01\x07\x02\xda\x02\x1e" + json.dumps(payload, ensure_ascii=False).encode() + b"\x03\xa5image\x04\xa5image\x05\xa0"


def image_row(mid, *, kind=7, payload=None, sender="buyer", at=1756720000):
    payload = {"fileId": "a.jpg", "url": SIGNED_URL, "width": "1179", "height": "1158", "size": "9"} if payload is None else payload
    return MessageRow(
        table_name="messages", cid="seller-a-buyer", mid=str(mid), sender_id=f"{sender}@icbu",
        created_at=at, user_content_type=kind, content_label="", content=blob(payload),
    )


def crm_message(content, kind=7) -> CrmMessage:
    return CrmMessage("messages", "c", "m", "buyer@icbu", 1, kind, "", content)


@pytest.mark.parametrize("content, expected", [
    (blob({"url": SIGNED_URL, "width": "1179", "height": 1158}), (SIGNED_URL, 1179, 1158)),
    (blob({"url": SIGNED_URL, "width": "x", "height": None}), (SIGNED_URL, 0, 0)),
    (b"\x01{broken" + blob({"url": SIGNED_URL}), (SIGNED_URL, 0, 0)),
    (blob({"fileId": "a.jpg"}), None),
    (b"no json at all", None),
    (None, None),
])
def test_parse_image_tolerates_binary_framing_and_missing_fields(content, expected):
    image = parse_image(crm_message(content))
    assert (image and (image.url, image.width, image.height)) == expected


def test_only_image_messages_are_parsed_and_labelled():
    other = crm_message(blob({"url": SIGNED_URL}), kind=9)
    assert parse_image(other) is None
    assert (normalize_message_type(other), message_display_text(other)) == ("video", "[视频]")
    image = crm_message(blob({"url": SIGNED_URL}))
    assert (normalize_message_type(image), message_display_text(image)) == ("image", "[图片]")


def scripted(*responses):
    calls = []
    queue = list(responses)

    def opener(url):
        calls.append(url)
        return queue.pop(0)

    return opener, calls


def redirect(location):
    return Response(302, {"location": location}, b"")


def test_fetch_follows_signed_redirect_to_image():
    opener, calls = scripted(redirect(CDN_URL), Response(200, {"content-type": "image/png"}, PNG))
    image = fetch_image(SIGNED_URL, opener=opener)
    assert (image.content_type, image.body, calls) == ("image/png", PNG, [SIGNED_URL, CDN_URL])


@pytest.mark.parametrize("responses, error", [
    ([redirect("https://hz-icbuim-file.oss-cn-beijing.aliyuncs.com/expiredFile.png?x=1")], ImageUnavailable),
    ([Response(200, {}, b"")], ImageUnavailable),
    ([redirect("https://evil.example/a.png")], ImageFetchError),
    ([redirect("http://s.alicdn.com/a.png")], ImageFetchError),
    ([redirect(CDN_URL), Response(200, {"content-type": "text/html"}, b"<html>")], ImageFetchError),
    ([redirect(CDN_URL), Response(403, {}, b"")], ImageFetchError),
    ([redirect(CDN_URL)] * 3, ImageFetchError),
])
def test_fetch_rejects_unusable_upstream_answers(responses, error):
    opener, _ = scripted(*responses)
    with pytest.raises(error):
        fetch_image(SIGNED_URL, opener=opener)


def test_fetch_rejects_oversized_body(monkeypatch):
    monkeypatch.setattr(image_fetch, "MAX_BYTES", 8)
    opener, _ = scripted(Response(200, {"content-type": "image/png"}, PNG))
    with pytest.raises(ImageFetchError):
        fetch_image(SIGNED_URL, opener=opener)


def image_url(client, sid, mid):
    return f"/api/conversations/{sid}/messages/{mid}/image"


def test_detail_exposes_image_size_but_never_the_signed_url(client, archive):  # noqa: F811
    _, apply = archive
    apply(("buyer", [row(1), image_row(2, at=1756720001), image_row(3, kind=9, at=1756720002)]))
    sid = data(client)["items"][0]["sid"]
    response = client.get(f"/api/conversations/{sid}")
    assert response.status_code == 200
    assert "SECRET" not in response.text and "clouddisk" not in response.text
    messages = {m["message"]["external_mid"]: m for m in response.json()["data"]["messages"]}
    image = messages[message_external_id("seller-a", "messages", "2")]["message"]
    assert (image["type"], image["content"]) == ("image", {"label": "[图片]", "image": {"width": 1179, "height": 1158}})
    video = messages[message_external_id("seller-a", "messages", "3")]["message"]
    assert (video["type"], video["content"]) == ("video", "[视频]")


def test_image_endpoint_serves_bytes_from_the_stored_url(client, archive, monkeypatch):  # noqa: F811
    _, apply = archive
    apply(("buyer", [image_row(2)]))
    sid = data(client)["items"][0]["sid"]
    seen = []

    def fake(url):
        seen.append(url)
        return image_fetch.FetchedImage("image/png", PNG)

    monkeypatch.setattr(conversations, "fetch_image", fake)
    response = client.get(image_url(client, sid, message_external_id("seller-a", "messages", "2")))
    assert (response.status_code, response.headers["content-type"], response.content) == (200, "image/png", PNG)
    assert seen == [SIGNED_URL]


@pytest.mark.parametrize("failure, status", [(ImageUnavailable("expired"), 404), (ImageFetchError("status_500"), 502)])
def test_image_endpoint_maps_upstream_failures(client, archive, monkeypatch, failure, status):  # noqa: F811
    _, apply = archive
    apply(("buyer", [image_row(2)]))
    sid = data(client)["items"][0]["sid"]

    def fake(url):
        raise failure

    monkeypatch.setattr(conversations, "fetch_image", fake)
    response = client.get(image_url(client, sid, message_external_id("seller-a", "messages", "2")))
    assert response.status_code == status
    assert "SECRET" not in response.text


def test_image_endpoint_is_scoped_to_conversation_and_message_kind(client, archive, monkeypatch):  # noqa: F811
    _, apply = archive
    apply(("buyer", [row(1), image_row(2)]), ("other", [image_row(3)]))
    sids = {item["name"].rsplit(":", 1)[-1]: item["sid"] for item in data(client)["items"]}
    assert set(sids) == {"buyer", "other"}
    monkeypatch.setattr(conversations, "fetch_image", lambda url: pytest.fail("must not fetch"))
    text = client.get(image_url(client, sids["buyer"], message_external_id("seller-a", "messages", "1")))
    unknown = client.get(image_url(client, sids["buyer"], "alibaba_icbu:seller-a:messages:999"))
    crossed = client.get(image_url(client, sids["other"], message_external_id("seller-a", "messages", "2")))
    assert (text.status_code, unknown.status_code, crossed.status_code) == (404, 404, 404)
