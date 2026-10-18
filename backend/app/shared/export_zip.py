"""Batch chat export: per-contact Markdown files bundled into a ZIP archive.

Ported from the legacy NiceGUI batch tab; has no UI dependencies so both the
HTTP API and (until its removal) the web UI can share it. Message bodies and the
transcript come from ``crm.message_markdown`` so the export and the LLM inputs
structure cards and speakers the same way.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone

from backend.app.shared.crm.message_markdown import EXPORT_OPTIONS, transcript_markdown
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.crm.views import CrmConversation, CrmResolver, coerce_epoch

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def dialogue_count(conv: CrmConversation) -> int:
    return sum(1 for message in conv.messages if not message.is_system)


def contact_display_name(contact_ali_id: str) -> str:
    info = CRMAdapter().get_user_info(contact_ali_id)
    if info is None:
        return contact_ali_id
    name = f"{info.first_name} {info.last_name}".strip()
    return name or info.login_id or contact_ali_id


def format_register_date(register_date) -> str:
    if not register_date:
        return ""
    return datetime.fromtimestamp(coerce_epoch(register_date), tz=timezone.utc).strftime("%Y-%m-%d")


def clean_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip().strip(".")
    return (cleaned or "unknown")[:120]


def front_matter_value(value: object) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "").replace("\r", " ").replace("\n", " ")


def customer_info_lines(contact: str) -> list[str]:
    info = CRMAdapter().get_user_info(contact)
    if info is None:
        return [f"客户ID: {front_matter_value(contact)}"]

    name = f"{info.first_name} {info.last_name}".strip()
    register_date = format_register_date(info.register_date)

    fields: list[tuple[str, object]] = [
        ("显示名", contact_display_name(contact)),
        ("Ali ID", info.ali_id),
        ("会员 ID", info.ali_member_id),
        ("登录 ID", info.login_id),
        ("加密 ID", info.encrypt_account_id),
        ("姓名", name),
        ("国家", info.country_code),
        ("公司", info.company_name),
        ("注册时间", register_date),
        ("邮箱", info.email),
        ("手机", info.mobile_number),
        ("电话", info.phone_number),
        ("商品浏览", info.product_view_count),
        ("有效询盘", info.valid_inquiry_count),
        ("已回复询盘", info.replied_inquiry_count),
        ("有效 RFQ", info.valid_rfq_count),
        ("登录天数", info.login_days),
        ("垃圾询盘", info.spam_inquiry_count),
        ("拉黑次数", info.blacklisted_count),
        ("质量等级", info.high_quality_level_tag),
        ("成长等级", info.growth_level),
        ("偏好行业", info.preferred_industries),
        ("状态", "可用" if info.available else "不可用"),
        ("加入年限", info.joining_years),
        ("潜力分", info.potential_score),
        ("近期联系", info.recent_contact),
        ("邮箱验证", info.email_validated),
    ]
    return [f"{label}: {front_matter_value(value)}" for label, value in fields]


def conversation_markdown(conv: CrmConversation, resolver: CrmResolver, *, display_name: str = "") -> str:
    name = display_name or conv.contact_ali_id
    lines = [
        f"# {name}",
        "",
        *(f"- {line}" for line in customer_info_lines(conv.contact_ali_id)),
        "",
        "---",
        "",
        "## 会话记录",
        "",
    ]
    transcript = transcript_markdown(
        conv.messages, resolver=resolver, options=EXPORT_OPTIONS, limit=None, display_name=name
    )
    if transcript:
        lines.append(transcript)
    return "\n".join(lines).rstrip() + "\n"


def build_export_zip(conversations: list[CrmConversation], resolver: CrmResolver) -> tuple[bytes, str]:
    created_at = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_name = f"Chats-Export-{created_at}.zip"
    used_names: dict[str, int] = {}
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for conv in conversations:
            display_name = contact_display_name(conv.contact_ali_id)
            base_name = clean_filename(display_name)
            index = used_names.get(base_name, 0) + 1
            used_names[base_name] = index
            file_name = f"{base_name}.md" if index == 1 else f"{base_name}_{index}.md"
            archive.writestr(file_name, conversation_markdown(conv, resolver, display_name=display_name).encode("utf-8"))

    return buffer.getvalue(), archive_name
