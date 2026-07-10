"""Batch chat tab - lets the user select multiple contacts."""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import ui

from app.shared.crm.views import CrmConversation
from app.shared.crm.views import format_created_at
from app.web.chat_presenter import ConversationGroup
from app.web.chat_presenter import contact_display_name
from app.web.components.ui_helpers import empty_state


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


def render(ctx: dict) -> None:
    """Render the batch customer selection tab content."""
    time_groups: list[ConversationGroup] = ctx["conversation_groups"]
    conversations: list[CrmConversation] = [
        conv for group in time_groups for conv in group.conversations
    ]
    group_state = ctx.setdefault("batch_group_state", {"mode": "updated_at"})
    selected_contacts: set[str] = ctx.setdefault("batch_selected_contacts", set())
    checkboxes: dict[str, ui.checkbox] = {}
    action_buttons: list[ui.button] = []
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

    def _update_count() -> None:
        count = len(selected_contacts)
        count_label.text = f"已选 {count} / {total} 个客户"
        action_count_label.text = f"已选中 {count} 个客户"
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
                        "操作一",
                        icon="outgoing_mail",
                        on_click=lambda: _placeholder_action("操作一"),
                    ).props("unelevated dense color=primary")
                )
                action_buttons.append(
                    ui.button(
                        "操作二",
                        icon="label",
                        on_click=lambda: _placeholder_action("操作二"),
                    ).props("outline dense color=primary")
                )

        _update_count()

        if total == 0:
            empty_state("groups", "暂无可选择客户")
            return

        @ui.refreshable
        def customer_list() -> None:
            checkboxes.clear()
            with ui.scroll_area().classes("w-full flex-grow"):
                for group in _groups():
                    if not group.conversations:
                        continue
                    label = f"{group.label} ({len(group.conversations)})"
                    with ui.expansion(label, value=group.expanded).classes("w-full"):
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
