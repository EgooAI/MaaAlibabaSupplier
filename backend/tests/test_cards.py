import base64
import json

import pytest

from backend.app.shared.cards import (
    card_reference,
    card_view,
    parse_card,
    product_ids,
)
from backend.app.shared.crm.views import CrmMessage
from backend.app.shared.mitm.pool import ProductCard, get_product_card_pool


def _blob(payload: dict) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return b"\x82\x01e\x03\x85\x01\xcd'\x1a\x02\xda\x00" + raw + b"\x03\xa0\x04\xa0\x05\xa0"


def _message(content: bytes | None, *, mid: str = "m1", label: str = "") -> CrmMessage:
    return CrmMessage(
        table_name="msg_table",
        cid="10001-20002",
        mid=mid,
        sender_id="20002@icbu",
        created_at=1756720000,
        user_content_type=10010,
        content_label=label,
        content=content,
    )


_ORDER_PARAMS = base64.b64encode(json.dumps({
    "paymentAmountCurrency": "USD",
    "orderAmount": 276.0,
    "orderAmountCurrency": "USD",
    "statusMessageKey": "buyer.message.status.unpay.full",
    "paymentAmount": 276.0,
}, separators=(",", ":")).encode()).decode()

PAYLOADS: dict[int, dict] = {
    3: {"cardType": 3, "params": {
        "appKey": "21574050", "appVersion": "263200", "country": "MU",
        "ctime": "1789581592677", "fromUid": "133461196495",
        "id": "1601713005360", "toUid": "2500001168191"}},
    2000: {"cardType": 2000, "params": {
        "type": "2000", "ids": "1601721403075,1601779714884",
        "ctime": "1789538076048", "country": "US"}},
    2028: {"cardType": 2028, "params": {
        "toUid": "2500001168191", "fromUid": "4500109566941",
        "sign": "30FC0BE8AF5018DA5957015E7480B57752D536B3",
        "ctime": "1789477956947", "id": "1601725011454"}},
    2098: {"cardType": 2098, "params": {
        "toUid": "2500001168191", "fromUid": "4500087022729",
        "quoteProductId": "1601725292931",
        "sign": "71E389DFCE4A99132B7CF0D5806A20D838389C6A",
        "quoId": "1264919538", "ctime": "1779923894263"}},
    6: {"cardType": 6, "params": {
        "sign": "D5E510B9C07702259DC0007AC6FC6ADBBFEDA02C",
        "encryFeedbackId": "MC1IDX1ZDuPLUx-CatqdqhhyP9_HNXAL_3DS0l4yC6g6zuCtmNe7ByjK80do06DG1tYEu9r",
        "ctime": "1789648495652", "from": "mu1336302814cuak", "to": "bjygzn",
        "source": "ANDROID_BUYER",
        "encryTradeId": "MC1IDX1vq0Vh3tFKINQqoX9_TKVHcPxYe586ZOZ59_cc3mBLpRCmze0BFwKoLRaHtClQRLk",
        "fbType": "1", "version": "1", "marketType": "0"}},
    9: {"cardType": 9, "params": {
        "orderId": "311700687001027112", "bizCode": "1020401",
        "contractId": "8717350515",
        "sign": "DDC8CBDBD8F95DDBDD6841207CCF92A9BE9DE9A5",
        "ctime": "1787191838372", "from": "bjygzn", "to": "ca29093168022bzqc",
        "id": "311700687001027112", "params": _ORDER_PARAMS}},
    12: {"cardType": 12, "params": {
        "type": "12", "ctime": "1787835241134", "version": "1",
        "extensionType": "pdf", "id": "4921276026", "parentId": "4921276023",
        "md5": "e6a594380efe7e43a42554d72f1565a6",
        "url": "https://clouddisk.alibaba.com/file/redirectFileUrl.htm?id=4921276026",
        "name": "产品目录.pdf", "size": "2024243",
        "thumbnailUrl": "https://clouddisk.alibaba.com/file/videoThumb.htm?id=4921276026",
        "downloadUrl": "https://clouddisk.alibaba.com/file/downloadFile.htm?id=4921276026"}},
    2013: {"cardType": 2013, "params": {
        "address": "24131 Blue Crest Drive", "addressId": "650036411853",
        "cityName": "Porter", "companyAddress": "24131 Blue Crest Drive, Porter, Texas",
        "companyTel": "8327220207", "contactName": "renwick Keith franklin",
        "countryCode": "US", "countryName": "United States of America",
        "ctime": "1789591326738", "zipCode": "77365-5990"}},
    2086: {"cardType": 2086, "params": {
        "toUid": "2500001168191",
        "activeAccountIdEncrypt": "MC1IDX1xd3FdMUlne4DK2kKjwtdlGsPKUOgPWDN7zmajIjZfB",
        "sign": "0132B4BD0A57B927EE8B4685341D5A7B3C62FF32",
        "buyerName": "Sanjar Sadatbekov", "scene": "notFollowRemind",
        "activeAccountId": "1576056456", "chatToken": "YVVGWk5saDVkakpCUW5oTk1uRnRZ",
        "fromUid": "1576056456", "ctime": "1780680180539"}},
    2106: {"cardType": 2106, "params": {
        "toUid": "2500001168191",
        "remindBuyers": json.dumps([
            {"activeAccountId": "1520991207", "aliId": 1507554360788, "questionType": "7dNotPay"},
            {"activeAccountId": "15015950617", "aliId": 5500016074158, "questionType": "7dNotPay"},
        ], ensure_ascii=False),
        "fromUid": "2500001168191", "sign": "1E6F83C159EAFFBC31A69A8D71094D63425CC403",
        "ctime": "1779719174287"}},
    1: {"cardType": 1, "params": {
        "type": "1", "showCertification": "true",
        "showCompanyName": "true", "showEmailAddress": "true"}},
    23: {"cardType": 23, "params": {
        "showCertifications": "true", "showEmailAddress": "true",
        "showCompanyName": "true", "from": "bjygzn", "to": "bj29021422701wbqj"}},
    2008: {"cardType": 2008, "params": {"type": "2008", "catalogId": "27840070", "ctime": "1779089304863"}},
    2111: {"cardType": 2111, "params": {
        "type": "2111", "ids": "2111", "productId": "1601728693892",
        "skuSelectList": json.dumps([{"skuId": 107829922084, "amount": 10}]),
        "totalPrice": "25", "country": "US"}},
}

TYPED_CASES = [(3, "产品卡片", "product"), (2000, "产品批量", "product"),
               (2028, "报价", "product"), (2098, "报价提醒", "product"),
               (6, "询盘/反馈", "inquiry"), (9, "订单/交易", "generic"),
               (2013, "收货地址", "generic"),
               (2086, "关注提醒", "generic"), (2106, "关注提醒", "generic"),
               (1, "资质认证", "generic"), (23, "资质认证", "generic"),
               (2008, "商品目录", "generic"), (2111, "询价", "generic")]


@pytest.mark.parametrize("card_type,name,category", TYPED_CASES)
def test_every_observed_card_type_gets_a_typed_view(card_type, name, category):
    view = card_view(_message(_blob(PAYLOADS[card_type])))
    assert view["title"] == name
    assert view["type"] == category
    assert view["tags"][0] == name
    assert view["id"]


def test_params_before_card_type_and_binary_padding():
    content = b'\x82\x01e\x03{"params":{"id":"1601713005360","name":""},"cardType":3}\x03\xa0\x04'
    card = parse_card(content)
    assert card.card_type == 3
    assert card.params["id"] == "1601713005360"
    assert card_view(_message(content))["summary"] == "商品 ID：1601713005360"


def test_product_pool_enriches_product_cards():
    get_product_card_pool().put(ProductCard(
        card_id="1601713005360", title="Camping Wagon", price="$12.90-16.80",
        display_price="$12.90-16.80", product_image="https://img.example/x.png",
        moq="1", moq_unit="Unit", product_id="1601713005360",
        product_url="https://chinese.alibaba.com/product-detail/x.html",
    ))
    view = card_view(_message(_blob(PAYLOADS[3])))
    assert view["title"] == "Camping Wagon"
    assert view["summary"] == "$12.90-16.80 · MOQ 1Unit"
    details = {item["label"]: item["value"] for item in view["details"]}
    assert details["商品 ID"] == "1601713005360"
    assert details["链接"] == "https://chinese.alibaba.com/product-detail/x.html"


def test_product_views_without_pool_keep_product_ids():
    view = card_view(_message(_blob(PAYLOADS[2000])))
    assert view["summary"] == "商品 ID：1601721403075, 1601779714884"
    assert [item["value"] for item in view["details"]] == ["1601721403075", "1601779714884"]
    assert product_ids(parse_card(_blob(PAYLOADS[2000]))) == ["1601721403075", "1601779714884"]


def test_order_card_decodes_amount_and_status():
    view = card_view(_message(_blob(PAYLOADS[9])))
    assert "USD 276.0" in view["summary"] and "待付款" in view["summary"]
    details = {item["label"]: item["value"] for item in view["details"]}
    assert details["订单号"] == "311700687001027112"
    assert details["合同号"] == "8717350515"
    assert details["应付金额"] == "276.0"
    assert details["状态"] == "待付款"


def test_file_card_exposes_download_link_and_human_size():
    view = card_view(_message(_blob(PAYLOADS[12])))
    assert view["title"] == "产品目录.pdf"
    assert view["tags"] == ["文件附件"]
    assert view["summary"] == "PDF · 1.9 MB"
    assert view["link"]["href"].endswith("id=4921276026")
    assert card_reference(parse_card(_blob(PAYLOADS[12]))) == "4921276026"


def test_reminder_and_address_cards_use_buyer_visible_fields():
    reminder = card_view(_message(_blob(PAYLOADS[2086])))
    assert reminder["summary"] == "Sanjar Sadatbekov"
    batch = card_view(_message(_blob(PAYLOADS[2106])))
    assert batch["summary"] == "2 位买家待跟进"
    address = card_view(_message(_blob(PAYLOADS[2013])))
    assert address["summary"].startswith("24131 Blue Crest Drive")


def test_inquiry_request_card_shows_quantity_and_total():
    view = card_view(_message(_blob(PAYLOADS[2111])))
    assert view["summary"] == "1 项 · 总价 25"
    assert [item["label"] for item in view["details"]] == ["商品ID", "总价"]


def test_inquiry_card_has_no_displayable_content():
    view = card_view(_message(_blob(PAYLOADS[6])))
    assert view["summary"] == "买家询盘/反馈，请在客户端查看"
    assert view["details"] == []


def test_sensitive_tokens_never_reach_the_view():
    for card_type in (6, 2086, 2106):
        text = json.dumps(card_view(_message(_blob(PAYLOADS[card_type]))), ensure_ascii=False)
        for secret in ("chatToken", "sign", "encryFeedbackId", "encryTradeId", "activeAccountIdEncrypt"):
            assert secret not in text


def test_unknown_and_malformed_payloads_do_not_crash():
    assert parse_card(None) is None
    assert parse_card(b"") is None
    assert parse_card(b"\x82\x01not json at all") is None
    assert parse_card(b'{"cardType": 9}') is None
    view = card_view(_message(_blob({"cardType": 2017, "params": {"cardId": "240647143", "subType": "seller"}})))
    assert view["title"] == "类型 2017"
    assert view["id"] == "2017:m1"
    assert card_view(_message(b"garbage")) is None
