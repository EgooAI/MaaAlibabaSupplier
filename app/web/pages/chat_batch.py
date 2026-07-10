"""Batch chat tab - lets the user select multiple contacts."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

from nicegui import ui

from app.shared.crm import get_user_info as get_crm_user_info
from app.shared.crm.views import CrmConversation
from app.shared.crm.views import format_created_at
from app.web.chat_presenter import ConversationGroup
from app.web.chat_presenter import contact_display_name
from app.web.chat_presenter import message_datetime, message_text
from app.web.components.ui_helpers import empty_state


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class BatchGroup:
    label: str
    conversations: list[CrmConversation]
    expanded: bool


def _dialogue_count(conv: CrmConversation) -> int:
    return sum(1 for message in conv.messages if not message.is_system)


def _group_by_dialogue_count(conversations: list[CrmConversation]) -> list[BatchGroup]:
    long_convs: list[CrmConversation] = []
    medium_convs: list[CrmConversation] = []
    short_convs: list[CrmConversation] = []

    for conv in conversations:
        count = _dialogue_count(conv)
        if count >= 16:
            long_convs.append(conv)
        elif count >= 4:
            medium_convs.append(conv)
        else:
            short_convs.append(conv)

    return [
        BatchGroup("长会话：16条及以上", long_convs, True),
        BatchGroup("中等长度会话：4-15条", medium_convs, True),
        BatchGroup("短会话：1-3条", short_convs, False),
    ]


def _clean_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip().strip(".")
    return (cleaned or "unknown")[:120]


def _front_matter_value(value: object) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "").replace("\r", " ").replace("\n", " ")


def _customer_info_lines(contact: str) -> list[str]:
    info = get_crm_user_info(contact)
    if info is None:
        return [f"客户ID: {_front_matter_value(contact)}"]

    name = f"{info.first_name} {info.last_name}".strip()
    register_date = ""
    if info.register_date:
        register_date = datetime.fromtimestamp(info.register_date, tz=timezone.utc).strftime("%Y-%m-%d")

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
    return [f"{label}: {_front_matter_value(value)}" for label, value in fields]


def _message_speaker(conv: CrmConversation, resolver, message) -> str:
    if message.is_system:
        return "系统"
    if resolver.is_self(message.sender_id):
        return "我的机器人" if message.is_auto_reply else "我"
    return contact_display_name(conv.contact_ali_id)


def _conversation_text(conv: CrmConversation, resolver) -> str:
    lines = ["---", *_customer_info_lines(conv.contact_ali_id), "---"]
    for message in conv.messages:
        speaker = _message_speaker(conv, resolver, message)
        timestamp = message_datetime(message).strftime("%Y-%m-%d %H:%M:%S")
        text = message_text(message).replace("\r\n", "\n").replace("\r", "\n")
        lines.append(f"{speaker} ({timestamp}): {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_export_zip(conversations: list[CrmConversation], resolver) -> tuple[bytes, str]:
    created_at = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_name = f"Chats-Export-{created_at}.zip"
    used_names: dict[str, int] = {}
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for conv in conversations:
            base_name = _clean_filename(contact_display_name(conv.contact_ali_id))
            index = used_names.get(base_name, 0) + 1
            used_names[base_name] = index
            file_name = f"{base_name}.txt" if index == 1 else f"{base_name}_{index}.txt"
            archive.writestr(file_name, _conversation_text(conv, resolver).encode("utf-8"))

    return buffer.getvalue(), archive_name


def render(ctx: dict) -> None:
    """Render the batch customer selection tab content."""
    time_groups: list[ConversationGroup] = ctx["conversation_groups"]
    conversations: list[CrmConversation] = [
        conv for group in time_groups for conv in group.conversations
    ]
    group_state = ctx.setdefault("batch_group_state", {"mode": "updated_at"})
    selected_contacts: set[str] = ctx.setdefault("batch_selected_contacts", set())
    resolver = ctx["resolver"]
    checkboxes: dict[str, ui.checkbox] = {}
    action_buttons: list[ui.button] = []
    group_count_labels: list[tuple[ui.label, list[str]]] = []
    total = len(conversations)

    def _groups() -> list[ConversationGroup] | list[BatchGroup]:
        if group_state["mode"] == "dialogue_count":
            return _group_by_dialogue_count(conversations)
        return time_groups

    def _set_all(value: bool) -> None:
        for conv in conversations:
            if value:
                selected_contacts.add(conv.contact_ali_id)
            else:
                selected_contacts.discard(conv.contact_ali_id)
        for contact, checkbox in checkboxes.items():
            checkbox.value = contact in selected_contacts
        _update_count()

    def _set_group(contacts: list[str], value: bool) -> None:
        for contact in contacts:
            if value:
                selected_contacts.add(contact)
            else:
                selected_contacts.discard(contact)
            checkbox = checkboxes.get(contact)
            if checkbox is not None:
                checkbox.value = contact in selected_contacts
        _update_count()

    def _update_count() -> None:
        count = len(selected_contacts)
        count_label.text = f"已选 {count} / {total} 个客户"
        action_count_label.text = f"已选中 {count} 个客户"
        for label, contacts in group_count_labels:
            group_count = sum(1 for contact in contacts if contact in selected_contacts)
            label.text = f"已选 {group_count} / {len(contacts)}"
        for button in action_buttons:
            if count:
                button.enable()
            else:
                button.disable()

    def _toggle_contact(contact: str, value: bool) -> None:
        if value:
            selected_contacts.add(contact)
        else:
            selected_contacts.discard(contact)
        _update_count()

    def _change_group_mode(value: str) -> None:
        group_state["mode"] = value
        customer_list.refresh()

    def _placeholder_action(name: str) -> None:
        ui.notify(f"{name}功能待接入", type="info")

    def _export_selected_chats() -> None:
        selected_convs = [conv for conv in conversations if conv.contact_ali_id in selected_contacts]
        if not selected_convs:
            ui.notify("请先选择客户", type="warning")
            return
        content, filename = _build_export_zip(selected_convs, resolver)
        ui.download.content(content, filename)

    with ui.column().classes("w-full h-full p-4 gap-4"):
        with ui.row().classes("w-full items-center justify-between gap-3"):
            with ui.column().classes("gap-0"):
                ui.label("批量客户管理").classes("text-lg font-bold")
                count_label = ui.label().classes("text-sm text-gray-500")
            with ui.row().classes("items-center gap-3"):
                ui.toggle(
                    {"updated_at": "按更新时间", "dialogue_count": "按对话数"},
                    value=group_state["mode"],
                    on_change=lambda e: _change_group_mode(e.value),
                ).props("dense unelevated toggle-color=primary")
                with ui.row().classes("gap-2"):
                    ui.button("全选", icon="select_all", on_click=lambda: _set_all(True)).props(
                        "flat dense color=primary"
                    )
                    ui.button("清空", icon="clear", on_click=lambda: _set_all(False)).props(
                        "flat dense color=grey"
                    )

        with ui.row().classes(
            "w-full items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3"
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("task_alt").classes("text-primary")
                action_count_label = ui.label().classes("font-medium")
            with ui.row().classes("gap-2"):
                action_buttons.append(
                    ui.button(
                        "群发消息",
                        icon="outgoing_mail",
                        on_click=lambda: _placeholder_action("群发消息"),
                    ).props("unelevated dense color=primary")
                )
                action_buttons.append(
                    ui.button(
                        "导出聊天",
                        icon="download",
                        on_click=_export_selected_chats,
                    ).props("outline dense color=primary")
                )

        _update_count()

        if total == 0:
            empty_state("groups", "暂无可选择客户")
            return

        @ui.refreshable
        def customer_list() -> None:
            checkboxes.clear()
            group_count_labels.clear()
            with ui.scroll_area().classes("w-full flex-grow"):
                for group in _groups():
                    if not group.conversations:
                        continue
                    group_contacts = [conv.contact_ali_id for conv in group.conversations]
                    with ui.expansion(value=group.expanded).classes("w-full") as expansion:
                        with expansion.add_slot("header"):
                            with ui.row().classes("w-full items-center justify-between gap-3 pr-2"):
                                with ui.column().classes("gap-0"):
                                    ui.label(f"{group.label} ({len(group.conversations)})").classes("font-medium")
                                    group_count_label = ui.label().classes("text-xs text-gray-500")
                                    group_count_labels.append((group_count_label, group_contacts))
                                with ui.row().classes("items-center gap-1"):
                                    ui.button(
                                        "选中本组",
                                        icon="done_all",
                                        on_click=lambda contacts=group_contacts: _set_group(contacts, True),
                                    ).props("flat dense size=sm color=primary")
                                    ui.button(
                                        "取消本组",
                                        icon="remove_done",
                                        on_click=lambda contacts=group_contacts: _set_group(contacts, False),
                                    ).props("flat dense size=sm color=grey")
                        with ui.list().props("dense separator").classes("w-full"):
                            for conv in group.conversations:
                                contact = conv.contact_ali_id
                                title = contact_display_name(contact)
                                ts = format_created_at(conv.last_created_at)
                                subtitle = ts[:16] if len(ts) >= 16 else ts
                                count = _dialogue_count(conv)
                                with ui.item().classes("w-full"):
                                    with ui.item_section().props("avatar"):
                                        checkbox = ui.checkbox(
                                            value=contact in selected_contacts,
                                            on_change=lambda e, c=contact: _toggle_contact(c, bool(e.value)),
                                        )
                                        checkboxes[contact] = checkbox
                                    with ui.item_section():
                                        ui.item_label(title)
                                        ui.item_label(f"{subtitle} · 对话 {count} 条").props("caption")
                                    with ui.item_section().props("side"):
                                        ui.label(contact).classes("text-xs text-gray-400")

        customer_list()
        _update_count()
