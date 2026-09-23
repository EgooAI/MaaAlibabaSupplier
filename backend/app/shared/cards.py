"""Card-message payload parsing and the display view sent to the frontend.

The chat database stores every card message (``user_content_type = 10010``) as a
binary blob with one embedded JSON object. This module is the single place that
turns that blob into a displayable card; product cards additionally use the
MITM fetchcard pool to replace the bare product id with title, price and image.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from backend.app.shared.crm.views import CrmMessage
from backend.app.shared.mitm.pool import ProductCard, get_product_card_pool

PRODUCT_CARD_TYPES = frozenset({3, 2000, 2028, 2098})
INQUIRY_CARD_TYPE = 6
ORDER_CARD_TYPE = 9
FILE_CARD_TYPE = 12

TYPE_NAMES: dict[int, str] = {
    1: "资质认证",
    3: "产品卡片",
    6: "询盘/反馈",
    9: "订单/交易",
    12: "文件附件",
    23: "资质认证",
    2000: "产品批量",
    2008: "商品目录",
    2013: "收货地址",
    2028: "报价",
    2086: "关注提醒",
    2098: "报价提醒",
    2106: "关注提醒",
    2111: "询价",
}

_COVER_TONES = {"product": "#e6f4ff", "inquiry": "#fff7e6", "generic": "#f6ffed"}

_FIELD_LABELS = {
    "id": "ID",
    "productId": "商品ID",
    "quoteProductId": "商品ID",
    "orderId": "订单号",
    "contractId": "合同号",
    "bizCode": "业务码",
    "quoId": "报价ID",
    "catalogId": "目录ID",
    "cardId": "卡片ID",
    "addressId": "地址ID",
    "snapshotId": "地址ID",
    "name": "文件名",
    "extensionType": "文件类型",
    "size": "大小",
    "countryName": "国家",
    "provinceName": "省份",
    "cityName": "城市",
    "zipCode": "邮编",
    "address": "地址",
    "address2": "地址2",
    "companyAddress": "公司地址",
    "contactName": "联系人",
    "companyTel": "电话",
    "mobileNo": "手机",
    "buyerName": "买家",
    "questionType": "提醒类型",
    "subType": "身份",
    "totalPrice": "总价",
    "orderAmount": "订单金额",
    "paymentAmount": "应付金额",
    "orderAmountCurrency": "币种",
    "paymentAmountCurrency": "币种",
    "statusMessageKey": "状态",
}

# Never leave the server in a card view.
_DENIED_KEYS = frozenset({
    "sign", "chatToken", "activeAccountIdEncrypt", "loginIdEncrypt",
    "encryFeedbackId", "encryTradeId", "encryId",
})

# Transport and envelope fields that carry no display value.
_NOISE_KEYS = frozenset({
    "appKey", "appVersion", "cardType", "ctime", "version", "type", "country",
    "from", "to", "fromUid", "toUid", "source", "fbType", "marketType", "ids",
    "url", "downloadUrl", "thumbnailUrl", "md5", "parentId", "_shareCardId",
    "contryFlag", "processId", "targetAliId", "activeAccountId", "scene",
    "remindBuyers", "skuSelectList", "orderCreateTime", "params", "extension",
})

_REFERENCE_KEYS = (
    "id", "ids", "orderId", "cardId", "productId", "quoteProductId",
    "catalogId", "snapshotId", "addressId", "encryFeedbackId",
)

_ORDER_STATUS = {"unpay": "待付款"}

_MAX_DETAILS = 8


@dataclass(frozen=True)
class Card:
    card_type: int
    params: dict[str, Any]


def parse_card(content: bytes | None) -> Card | None:
    """Extract the embedded card JSON from a message blob, tolerating binary padding."""
    if not content:
        return None
    raw = bytes(content)
    text = raw.decode("latin1", errors="replace")
    decoder = json.JSONDecoder()
    position = 0
    while True:
        index = text.find("{", position)
        if index < 0:
            return None
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            position = index + 1
            continue
        if isinstance(value, dict) and "cardType" in value:
            value = _utf8_json(raw, index, end, value)
            try:
                card_type = int(value.get("cardType") or 0)
            except (TypeError, ValueError):
                return None
            params = value.get("params")
            return Card(card_type, params) if card_type and isinstance(params, dict) else None
        position = index + 1


def _utf8_json(raw: bytes, start: int, end: int, fallback: dict) -> dict:
    """Prefer a UTF-8 decode of the JSON span so non-ASCII text is not mojibake."""
    try:
        value = json.loads(raw[start:end].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return fallback
    return value if isinstance(value, dict) else fallback


def product_ids(card: Card) -> list[str]:
    raw = card.params.get("ids") or card.params.get("id") or card.params.get("quoteProductId") or ""
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def card_reference(card: Card) -> str:
    """First identifier of any card, for text exports and logs."""
    for key in _REFERENCE_KEYS:
        value = card.params.get(key)
        if value:
            return str(value).split(",")[0].strip()
    return ""


def card_view(message: CrmMessage) -> dict | None:
    card = parse_card(message.content)
    if card is None:
        return None
    if card.card_type in PRODUCT_CARD_TYPES:
        return _product_view(card)
    return _other_view(card, message.mid)


def _product_view(card: Card) -> dict:
    name = _type_name(card.card_type)
    ids = product_ids(card)
    product = _enriched_product(ids)
    if product is not None:
        price = product.display_price or product.price
        moq = f"{product.moq}{product.moq_unit}"
        return {
            "id": product.card_id or (ids[0] if ids else name),
            "title": product.title or name,
            "type": "product",
            "summary": f"{price} · MOQ {moq}",
            "tags": [name, *([product.product_id] if product.product_id else [])],
            "coverTone": _COVER_TONES["product"],
            "details": [
                {"label": "价格", "value": price},
                {"label": "MOQ", "value": moq},
                {"label": "商品 ID", "value": product.product_id},
                {"label": "链接", "value": product.product_url},
            ],
        }
    identifier = ids[0] if ids else f"{card.card_type}:{name}"
    return {
        "id": identifier,
        "title": name,
        "type": "product",
        "summary": f"商品 ID：{', '.join(ids)}" if ids else "请在客户端查看",
        "tags": [name, *ids[:3]],
        "coverTone": _COVER_TONES["product"],
        "details": [{"label": "商品 ID", "value": pid} for pid in ids],
    }


def _other_view(card: Card, mid: str) -> dict:
    type_name = _type_name(card.card_type)
    decoded = _order_params(card) if card.card_type == ORDER_CARD_TYPE else {}
    details = _display_fields(card, decoded)
    title = str(card.params.get("name") or "") if card.card_type == FILE_CARD_TYPE else ""
    view = {
        "id": f"{card.card_type}:{mid}",
        "title": title or type_name,
        "type": _category(card.card_type),
        "summary": _summary(card, decoded, details),
        "tags": [type_name],
        "coverTone": _COVER_TONES[_category(card.card_type)],
        "details": details,
    }
    if card.card_type == FILE_CARD_TYPE:
        href = str(card.params.get("downloadUrl") or card.params.get("url") or "")
        if href:
            view["link"] = {"label": "下载文件", "href": href}
    return view


def _category(card_type: int) -> str:
    if card_type == INQUIRY_CARD_TYPE:
        return "inquiry"
    return "generic"


def _type_name(card_type: int) -> str:
    return TYPE_NAMES.get(card_type) or f"类型 {card_type}"


def _enriched_product(ids: list[str]) -> ProductCard | None:
    if not ids:
        return None
    pool = get_product_card_pool()
    for product_id in ids:
        product = pool.find_by_product_id(product_id)
        if product is not None:
            return product
    return None


def _summary(card: Card, decoded: dict[str, Any], details: list[dict]) -> str:
    card_type, params = card.card_type, card.params
    if card_type == INQUIRY_CARD_TYPE:
        return "买家询盘/反馈，请在客户端查看"
    if card_type == ORDER_CARD_TYPE:
        amount = decoded.get("paymentAmount") or decoded.get("orderAmount")
        currency = decoded.get("orderAmountCurrency") or ""
        status = _order_status(decoded.get("statusMessageKey"))
        parts = [f"{currency} {amount}".strip() if amount else "", status]
        return " · ".join(part for part in parts if part) or "订单详情请在客户端查看"
    if card_type == FILE_CARD_TYPE:
        kind = str(params.get("extensionType") or "").upper()
        size = _human_size(params.get("size"))
        return " · ".join(part for part in (kind, size) if part) or "文件"
    if card_type == 2013:
        return str(params.get("companyAddress") or params.get("address") or "")
    if card_type == 2086:
        return str(params.get("buyerName") or "买家关注提醒")
    if card_type == 2106:
        count = _json_list_length(params.get("remindBuyers"))
        return f"{count} 位买家待跟进" if count else "买家关注提醒"
    if card_type in (1, 23):
        return "对方请求查看企业资质信息"
    if card_type == 2008:
        return f"目录 ID：{params.get('catalogId') or '未知'}"
    if card_type == 2111:
        return f"{_json_list_length(params.get('skuSelectList'))} 项 · 总价 {params.get('totalPrice') or ''}".strip()
    return str(details[0]["value"]) if details else ""


def _display_fields(card: Card, decoded: dict[str, Any]) -> list[dict]:
    fields: list[dict] = []
    labels: set[str] = set()
    for key, value in (*card.params.items(), *decoded.items()):
        if key in _DENIED_KEYS or key in _NOISE_KEYS:
            continue
        text = _field_value(key, value)
        label = _FIELD_LABELS.get(key, key)
        if not text or label in labels:
            continue
        labels.add(label)
        fields.append({"label": label, "value": text})
        if len(fields) >= _MAX_DETAILS:
            break
    return fields


def _field_value(key: str, value: Any) -> str:
    if isinstance(value, (dict, list)):
        return ""
    if key == "statusMessageKey":
        return _order_status(value)
    text = str(value).strip()
    if not text:
        return ""
    return _human_size(text) if key == "size" else text


def _order_params(card: Card) -> dict[str, Any]:
    encoded = card.params.get("params")
    if not isinstance(encoded, str) or not encoded:
        return {}
    try:
        payload = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        value = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(item, (str, int, float))}


def _order_status(key: Any) -> str:
    if not isinstance(key, str) or not key:
        return ""
    tail = key.rsplit(".", 2)[-2] if key.count(".") >= 2 else key
    return _ORDER_STATUS.get(tail, tail)


def _json_list_length(raw: Any) -> int:
    if not isinstance(raw, str) or not raw:
        return 0
    try:
        value = json.loads(raw)
    except ValueError:
        return 0
    return len(value) if isinstance(value, list) else 0


def _human_size(raw: Any) -> str:
    try:
        size = int(str(raw))
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


__all__ = [
    "Card",
    "PRODUCT_CARD_TYPES",
    "TYPE_NAMES",
    "card_reference",
    "card_view",
    "parse_card",
    "product_ids",
]
