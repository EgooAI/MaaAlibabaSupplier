import json

from backend.app.mitm import proxy
from backend.app.shared.mitm.pool import get_inquiry_card_pool, get_product_card_pool

URL = "https://acs.m.alibaba.com/gw/mtop.alibaba.intl.mobile.interaction.fetchcard/1.1/"
BATCH_URL = "https://acs.m.alibaba.com/gw/mtop.alibaba.intl.mobile.interaction.getuserinfobyparams/1.1/"


def test_matched_route_logs_zero_count_and_body_size(monkeypatch):
    events = []
    monkeypatch.setattr(proxy, "log_event", lambda name, **fields: events.append((name, fields)))
    router = proxy.TrafficRouter()
    body = b'{"api":"mtop.alibaba.intl.mobile.interaction.getuserinfobyparams","ret":["FAIL_SYS_ILLEGAL_ACCESS::x"]}'

    router.process({"url": BATCH_URL, "response_body": body})
    router.process({"url": BATCH_URL, "response_body": b""})

    assert events == [
        ("mitm.user_info_batch", {"count": 0, "body_bytes": len(body)}),
        ("mitm.user_info_batch", {"count": 0, "body_bytes": 0}),
    ]


def test_fetchcard_events_are_logged_even_without_a_parse(monkeypatch):
    events = []
    monkeypatch.setattr(proxy, "log_event", lambda name, **fields: events.append((name, fields)))
    router = proxy.TrafficRouter()

    router.process({"url": URL, "response_body": b""})
    router.process({"url": URL, "response_body": b"not json"})

    assert events == [
        ("mitm.fetch_card", {"body_bytes": 0, "count": 0}),
        ("mitm.fetch_card", {"body_bytes": 8, "count": 0}),
    ]
    assert get_product_card_pool().get("1601713005360") is None


def test_fetchcard_product_body_fills_the_pool(monkeypatch):
    events = []
    monkeypatch.setattr(proxy, "log_event", lambda name, **fields: events.append((name, fields)))
    router = proxy.TrafficRouter()
    body = json.dumps({
        "ret": ["SUCCESS::调用成功"],
        "data": {"fbCardList": [{"data": {
            "productIdTitle": "产品 ID: 1601713005360",
            "title": "Camping Wagon",
            "productAction": {"actionParams": {"url": "https://chinese.alibaba.com/product-detail/x.html"}},
        }}]},
    }).encode()

    router.process({"url": URL, "response_body": body})

    assert events == [("mitm.fetch_card", {"body_bytes": len(body), "count": 1})]
    card = get_product_card_pool().get("1601713005360")
    assert card.title == "Camping Wagon"
    assert card.product_url.endswith("x.html")
    assert get_inquiry_card_pool().count() == 0


def test_fetchcard_inquiry_body_fills_the_inquiry_pool(monkeypatch):
    events = []
    monkeypatch.setattr(proxy, "log_event", lambda name, **fields: events.append((name, fields)))
    router = proxy.TrafficRouter()
    inquiry = {
        "summary": {"tag": "询盘", "content": "Inquiry from Product Details Page"},
        "data": {
            "inquiryID": 4387647439018,
            "inquiryContent": "Hi,I&rsquo;d like to begin discussions.",
            "attachmentCount": "1",
            "products": [{
                "productId": 160123456789, "productName": "Power Twister", "productUnitPrice": "US$2-3",
                "productMOQ": "2 ", "productUnit": "piece",
                "detailAction": {"actionParams": {"url": "https://chinese.alibaba.com/product-detail/t.html"}},
            }],
        },
    }
    other = {"summary": {"tag": "RFQ"}, "data": {"inquiryID": "1"}}
    body = json.dumps({"ret": ["SUCCESS::调用成功"], "data": {"fbCardList": [other, inquiry]}}).encode()

    router.process({"url": URL, "response_body": body})

    assert events == [("mitm.fetch_card", {"body_bytes": len(body), "count": 1})]
    stored = get_inquiry_card_pool().get("4387647439018")
    assert stored.inquiry_content == "Hi,I&rsquo;d like to begin discussions."
    assert stored.attachment_count == "1"
    assert [(p.product_id, p.product_name, p.product_moq) for p in stored.products] == [
        ("160123456789", "Power Twister", "2")]
    assert get_inquiry_card_pool().get("1") is None
    assert get_product_card_pool().count() == 0


def test_fetchcard_failed_inquiry_response_is_not_pooled(monkeypatch):
    monkeypatch.setattr(proxy, "log_event", lambda name, **fields: None)
    body = json.dumps({
        "ret": ["FAIL_SYS_SESSION_EXPIRED::x"],
        "data": {"fbCardList": [{"summary": {"tag": "询盘"}, "data": {"inquiryID": "5"}}]},
    }).encode()

    proxy.TrafficRouter().process({"url": URL, "response_body": body})

    assert get_inquiry_card_pool().get("5") is None
