import json

from backend.app.mitm import proxy
from backend.app.shared.mitm.pool import get_product_card_pool

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
