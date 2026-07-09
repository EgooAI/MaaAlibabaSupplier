"""Parsers for intercepted Alibaba API responses."""

from __future__ import annotations

import base64
import json
import re

from loguru import logger

from .pool import GenericCard, InquiryCard, InquiryProduct, ProductCard, SelfInfo, UserInfo


def _try_decode_base64(body: bytes) -> bytes:
    """If body looks like base64, decode it. Otherwise return as-is."""
    text = body.decode("utf-8", errors="replace").strip()
    # Quick heuristic: pure base64 text (no braces, no spaces) that decodes to text containing {
    if "{" not in text and len(text) > 100:
        try:
            decoded = base64.b64decode(text)
            if b"{" in decoded:
                return decoded
        except Exception:
            pass
    return body


def _unwrap_jsonp(body: bytes) -> dict | None:
    """Strip JSONP callback wrapper and parse the inner JSON object.

    Handles both classic JSONP (``jsonp_123({"success":true, ...})``)
    and guard-check JSONP (``/**/ typeof jsonp_xxx === 'function' && jsonp_xxx({...})``).
    Also handles base64-encoded bodies.
    """
    body = _try_decode_base64(body)
    text = body.decode("utf-8", errors="replace").strip()
    # Find the JSON object: look for ({" which marks the callback invocation
    paren_brace = text.find('({"')
    if paren_brace >= 0:
        start = paren_brace + 1  # position of {
    else:
        start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.debug("JSONP unwrap: no JSON found, text starts with: {!r}", text[:200])
        return None
    extracted = text[start : end + 1]
    try:
        return json.loads(extracted)
    except json.JSONDecodeError as e:
        logger.debug("JSONP unwrap: JSON parse failed at char {}: {}. extracted={!r}", e.pos, e.msg, extracted[:200])
        return None


def _parse_json(body: bytes) -> dict | None:
    body = _try_decode_base64(body)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        logger.opt(exception=True).debug("JSON parse failed")
        return None


def _is_success_ret(data: dict) -> bool:
    ret = data.get("ret") or []
    return any("SUCCESS" in str(item) for item in ret)


# ---------------------------------------------------------------------------
# queryCustomerInfo  (JSONP, alicrm.alibaba.com)
# ---------------------------------------------------------------------------


def parse_query_customer_info(body: bytes, url_buyer_login_id: str = "") -> UserInfo | None:
    data = _unwrap_jsonp(body)
    if data is None:
        logger.warning("queryCustomerInfo: JSONP unwrap returned None (body_len={})", len(body))
        return None
    if not data.get("success"):
        logger.warning("queryCustomerInfo: success={!r} keys={}", data.get("success"), list(data.keys())[:10])
        return None

    inner = (data.get("data") or {}).get("data") or {}
    buyer = inner.get("buyerInfo") or {}
    crm = inner.get("alicrmCustomerInfo") or {}

    if not buyer:
        logger.warning("queryCustomerInfo: buyerInfo is empty")
        return None

    contact = buyer.get("buyerContactInfo") or {}
    growth = buyer.get("growthLevelInfo") or {}

    login_id = (
        buyer.get("buyerLoginId")
        or crm.get("buyerLoginId")
        or url_buyer_login_id
        or ""
    )

    if not login_id:
        logger.warning("queryCustomerInfo: no login_id found (buyer={}, crm={})", bool(buyer), bool(crm))

    logger.info(
        "queryCustomerInfo parsed: login_id={} encrypt_id={} name={} {} country={} company={}",
        login_id,
        buyer.get("encryptAccountId") or "",
        buyer.get("firstName") or "",
        buyer.get("lastName") or "",
        buyer.get("country") or "",
        buyer.get("companyName") or "",
    )

    return UserInfo(
        # ali_id is NOT available from queryCustomerInfo (CRM API).
        # Leave it empty — pool.put() will key by login_id when ali_id is absent,
        # and later im.id.get will supply the real numeric ali_id.
        ali_id="",
        login_id=login_id,
        encrypt_account_id=buyer.get("encryptAccountId") or "",
        # Profile
        first_name=buyer.get("firstName") or "",
        last_name=buyer.get("lastName") or "",
        country_code=buyer.get("country") or "",
        company_name=buyer.get("companyName") or "",
        register_date=buyer.get("registerDate") or 0,
        # Contact
        email=contact.get("email") or "",
        mobile_number=contact.get("mobileNumber") or "",
        phone_number=contact.get("phoneNumber") or "",
        # Behavior
        product_view_count=buyer.get("productViewCount") or 0,
        valid_inquiry_count=buyer.get("validInquiryCount") or 0,
        replied_inquiry_count=buyer.get("repliedInquiryCount") or 0,
        valid_rfq_count=buyer.get("validRfqCount") or 0,
        login_days=buyer.get("loginDays") or 0,
        spam_inquiry_count=buyer.get("spamInquiryMarkedBySupplierCount") or 0,
        blacklisted_count=buyer.get("addedToBlacklistCount") or 0,
        # Tags
        high_quality_level_tag=buyer.get("highQualityLevelTag") or "",
        growth_level=growth.get("growthLevel") or "",
        preferred_industries=buyer.get("preferredIndustries") or [],
    )


# ---------------------------------------------------------------------------
# getuserinfobyparams  (JSON, acs.m.alibaba.com)
# ---------------------------------------------------------------------------


def parse_get_user_info_by_params(body: bytes) -> list[UserInfo]:
    data = _parse_json(body)
    if not data:
        return []

    if not _is_success_ret(data):
        return []

    objects = (data.get("data") or {}).get("object") or []
    results: list[UserInfo] = []
    for obj in objects:
        ali_id = str(obj.get("aliId") or "")
        if not ali_id:
            continue
        results.append(
            UserInfo(
                ali_id=ali_id,
                login_id=obj.get("loginId") or "",
                country_code=obj.get("countryCode") or "",
                available=obj.get("available", True),
                joining_years=obj.get("joiningYears") or 0,
                potential_score=obj.get("potentialScore") or 0,
                recent_contact=obj.get("recentContact") or False,
                email_validated=obj.get("emailValidation") or False,
            )
        )
    return results


# ---------------------------------------------------------------------------
# im.id.get  (JSON, acs.m.alibaba.com)
# ---------------------------------------------------------------------------


def parse_im_id_get(body: bytes) -> list[UserInfo]:
    data = _parse_json(body)
    if not data:
        return []

    if not _is_success_ret(data):
        return []

    objects = (data.get("data") or {}).get("object") or []
    results: list[UserInfo] = []
    for obj in objects:
        ali_id = str(obj.get("aliId") or "")
        if not ali_id:
            continue
        results.append(
            UserInfo(
                ali_id=ali_id,
                ali_member_id=str(obj.get("aliMemberId") or ""),
                login_id=obj.get("loginId") or "",
            )
        )
    return results


# ---------------------------------------------------------------------------
# fetchcard  (JSON, acs.m.alibaba.com)
# ---------------------------------------------------------------------------


def parse_fetch_card(body: bytes) -> ProductCard | None:
    data = _parse_json(body)
    if not data:
        return None

    if not _is_success_ret(data):
        return None

    card_list = (data.get("data") or {}).get("fbCardList") or []
    if not card_list:
        logger.info("fetchcard: fbCardList empty, top keys={}", list(data.keys())[:10])
        return None

    card_data = card_list[0].get("data") or {}
    if not card_data:
        logger.info("fetchcard: card[0].data empty, card[0] keys={}", list(card_list[0].keys())[:10])
        return None

    # fbCardList may contain different card types (product, RFQ, etc.)
    # Product cards have productIdTitle; some nest actual fields under a 'data' sub-key
    if "productIdTitle" not in card_data:
        nested = card_data.get("data")
        if isinstance(nested, dict) and "productIdTitle" in nested:
            card_data = nested
        else:
            # Not a product card (e.g. RFQ card) — skip
            return None

    # Extract numeric product ID from productIdTitle ("产品 ID: 1234567890") or hsfId fallback
    raw_product_id = ""
    title_val = card_data.get("productIdTitle") or ""
    m_pid = re.search(r"\d+", title_val)
    if m_pid:
        raw_product_id = m_pid.group(0)
    else:
        raw_product_id = str(card_data.get("hsfId") or "")

    # Extract product URL from action
    action = card_data.get("productAction") or {}
    action_params = action.get("actionParams") or {}
    product_url = action_params.get("url") or ""

    return ProductCard(
        card_id=raw_product_id,
        title=card_data.get("title") or "",
        price=card_data.get("price") or "",
        display_price=card_data.get("displayPrice") or "",
        product_image=card_data.get("productImage") or card_data.get("hsfImg") or "",
        moq=str(card_data.get("moq") or ""),
        moq_unit=card_data.get("moqUnit") or "",
        product_id=raw_product_id,
        product_url=product_url,
        expired=bool(card_data.get("expiredTitle")),
    )


# ---------------------------------------------------------------------------
# contact.extinfo.get  (JSON, acs.m.alibaba.com)
# ---------------------------------------------------------------------------


def parse_contact_extinfo_get(body: bytes) -> list[SelfInfo]:
    """Parse contact.extinfo.get response. May contain multiple accounts."""
    data = _parse_json(body)
    if not data:
        return []

    if not _is_success_ret(data):
        return []

    account_list = ((data.get("data") or {}).get("data") or {}).get("accountInfoList") or []
    results: list[SelfInfo] = []
    for acct in account_list:
        ali_id = str(acct.get("aliId") or "")
        if not ali_id:
            continue
        results.append(
            SelfInfo(
                ali_id=ali_id,
                login_id=acct.get("loginId") or "",
                encrypt_account_id=acct.get("accountIdEncrypt") or "",
                first_name=acct.get("firstName") or "",
                last_name=acct.get("lastName") or "",
                country=acct.get("country") or "",
                company_name=acct.get("companyName") or "",
                avatar_url=acct.get("avatarUrl") or "",
                account_status=acct.get("accountStatus") or "",
            )
        )
    return results


# ---------------------------------------------------------------------------
# Generic card parser (non-product cards from fetchcard)
# ---------------------------------------------------------------------------

_CARD_ID_KEYS = ("id", "ids", "encryFeedbackId", "orderId", "encryId", "quoteProductId", "quoId")


# ---------------------------------------------------------------------------
# fetchcard — inquiry card parser  (询盘卡片)
# ---------------------------------------------------------------------------


def parse_inquiry_card(body: bytes) -> InquiryCard | None:
    data = _parse_json(body)
    if not data or not _is_success_ret(data):
        return None

    card_list = (data.get("data") or {}).get("fbCardList") or []
    for item in card_list:
        card_data = item.get("data") or {}
        if not card_data:
            continue

        # Skip product cards
        check = card_data
        if "productIdTitle" not in check and isinstance(check.get("data"), dict):
            check = check["data"]
        if "productIdTitle" in check:
            continue

        # Inquiry cards have summary.tag == "询盘"
        summary = item.get("summary") or {}
        if summary.get("tag") != "询盘":
            continue

        inner = card_data.get("data") if isinstance(card_data.get("data"), dict) else card_data

        inquiry_id = str(inner.get("inquiryID") or "")
        if not inquiry_id:
            continue

        products: list[InquiryProduct] = []
        for p in inner.get("products") or []:
            if not isinstance(p, dict):
                continue
            detail_action = p.get("detailAction") or {}
            action_params = detail_action.get("actionParams") or {}
            products.append(InquiryProduct(
                product_name=p.get("productName") or "",
                product_id=str(p.get("productId") or ""),
                product_unit_price=p.get("productUnitPrice") or "",
                product_moq=str(p.get("productMOQ") or "").strip(),
                product_unit=p.get("productUnit") or "",
                product_image=p.get("productImage") or "",
                discount_price=p.get("discountPrice") or "",
                product_url=action_params.get("url") or "",
            ))

        return InquiryCard(
            inquiry_id=inquiry_id,
            inquiry_content=inner.get("inquiryContent") or inner.get("content") or "",
            products=products,
            product_image=inner.get("productImage") or "",
            is_seller=bool(inner.get("isSeller")),
            attachment_count=str(inner.get("attachmentCount") or "0"),
        )

    return None


# ---------------------------------------------------------------------------
# fetchcard — generic card parser (non-product, non-inquiry)
# ---------------------------------------------------------------------------


def _extract_card_id(params: dict) -> str:
    """Extract the best available ID from card params."""
    for key in _CARD_ID_KEYS:
        val = params.get(key)
        if val:
            return str(val)
    return ""


def parse_generic_card(body: bytes, source_url: str = "") -> list[GenericCard]:
    """Parse fetchcard response into GenericCard list (non-product cards only)."""
    data = _parse_json(body)
    if not data or not _is_success_ret(data):
        return []

    card_list = (data.get("data") or {}).get("fbCardList") or []
    results: list[GenericCard] = []
    for item in card_list:
        card_data = item.get("data") or {}
        if not card_data:
            continue

        # Skip product cards (handled by parse_fetch_card)
        check = card_data
        if "productIdTitle" not in check and isinstance(check.get("data"), dict):
            check = check["data"]
        if "productIdTitle" in check:
            continue

        # Skip inquiry cards (handled by parse_inquiry_card)
        summary = item.get("summary") or {}
        if summary.get("tag") == "询盘":
            continue

        # Determine cardType
        try:
            card_type = int(card_data.get("cardType") or 0)
        except (TypeError, ValueError):
            card_type = 0
        if not card_type:
            continue

        params = card_data.get("params") or {}
        card_id = _extract_card_id(params) if isinstance(params, dict) else ""
        if not card_id:
            continue

        results.append(GenericCard(
            card_type=card_type,
            card_id=card_id,
            source_url=source_url,
            raw_json=json.dumps(card_data, ensure_ascii=False),
        ))
    return results
