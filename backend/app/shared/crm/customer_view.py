"""Projects a resolved contact account into the shared customer profile."""

from __future__ import annotations

from typing import Any

from backend.app.shared.crm.customer_portrait_store import public_portrait_fields
from backend.app.shared.crm.views import format_created_at
from backend.app.shared.mitm.pool import UserInfo


def user_info_from_extra(extra: Any) -> UserInfo | None:
    """Validates stored profile data without resolving aliases again."""
    if not isinstance(extra, dict):
        return None
    try:
        return UserInfo.model_validate(extra)
    except ValueError:
        return None


def customer_view(
    contact_ali_id: str, account: dict | None, customer: dict | None, portrait: dict | None,
) -> dict:
    """Builds profile and portrait fields from the same resolved contact."""
    account = account or {}
    customer = customer or {}
    cid = account.get("cid")
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    user = user_info_from_extra(account.get("extra"))
    first_name = user.first_name if user else ""
    last_name = user.last_name if user else ""
    name = (
        f"{first_name} {last_name}".strip() or user.company_name or user.login_id or contact_ali_id
        if user else str(extra.get("account") or account.get("nickname") or contact_ali_id)
    )
    quality_tag = user.high_quality_level_tag if user else ""
    growth_level = user.growth_level if user else ""
    return {
        "id": str(cid or contact_ali_id),
        "ali_id": (user.ali_id if user else None) or str(extra.get("ali_id") or "") or None,
        "login_id": (user.login_id if user else None) or str(extra.get("login_id") or "") or None,
        "encrypt_account_id": (user.encrypt_account_id if user else "") or None,
        "member_id": (user.ali_member_id if user else "") or None,
        "name": name,
        "avatar_url": account.get("avatar") or (user.avatar_url if user else None) or None,
        "first_name": first_name or None,
        "last_name": last_name or None,
        "company": user.company_name if user else "",
        "country": (user.country_code if user else "") or str(customer.get("region") or ""),
        "register_date": format_created_at(user.register_date) if user and user.register_date else None,
        "email": user.email if user else "",
        "mobile": (user.mobile_number if user else "") or None,
        "phone": user.phone_number if user else "",
        "stage": "unknown",
        "tags": [tag for tag in (quality_tag, growth_level) if tag],
        "quality_tag": quality_tag or None,
        "growth_level": growth_level or None,
        "industries": list(user.preferred_industries) if user else [],
        "availability": ("可用" if user.available else "不可用") if user else "",
        "joining_years": (user.joining_years or None) if user else None,
        "potential_score": (user.potential_score or None) if user else None,
        "recent_contact": bool(user.recent_contact) if user else None,
        "email_validated": bool(user.email_validated) if user else None,
        "behavior": [],
        "d90": {
            "product_views": user.product_view_count,
            "valid_inquiries": user.valid_inquiry_count,
            "replied_inquiries": user.replied_inquiry_count,
            "valid_rfqs": user.valid_rfq_count,
            "login_days": user.login_days,
            "spam_inquiries": user.spam_inquiry_count,
            "blacklisted": user.blacklisted_count,
        } if user else {},
        **public_portrait_fields(cid, portrait),
    }
