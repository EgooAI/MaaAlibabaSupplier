"""Chat page — tabbed interface with messages and customer info."""

from __future__ import annotations

from nicegui import ui

from app.task_queue import get_task_queue
from app.shared.backend.im_chat_db import MsgTableResolver, build_conversations, format_created_at
from app.shared.mitm.pool import get_input_pending_pool, get_self_info_pool, get_translation_cache
from app.web.chat_presenter import contact_display_name, group_conversations
from app.web.components.nav import nav
from app.web.pages import chat_info, chat_msg


def _get_self_ali_id() -> str:
    info = get_self_info_pool().get()
    return info.ali_id if info else ""


def create(mw) -> None:
    """Register the /chat page on the current NiceGUI app."""

    @ui.page("/chat")
    def chat_page() -> None:
        ui.add_head_html(
            "<style>"
            ".nicegui-content{padding:0;gap:0}"
            ".compact-label .q-message-label{margin:4px 0}"
            "@media(min-width:993px){.drawer-toggle{display:none!important}}"
            "</style>"
        )
        conn = mw.get_connection()
        if conn is None:
            with ui.column().classes("items-center justify-center w-full h-screen"):
                ui.spinner(size="lg", color="primary")
                ui.label("等待数据库就绪…").classes("text-gray-500 mt-4")
                ui.label("请确保已登录阿里卖家客户端").classes("text-xs text-gray-400")
            return

        self_ali_id = _get_self_ali_id()
        convs = build_conversations(conn, self_ali_id)
        resolver = MsgTableResolver(self_ali_id)

        if not convs:
            with ui.column().classes("items-center justify-center w-full h-screen"):
                ui.icon("forum", size="4rem").classes("text-grey-3")
                ui.label("暂无会话").classes("text-grey-5")
            return

        selected: dict[str, str | None] = {"contact": convs[0].contact_ali_id}
        suggestion_state: dict[str, object] = {
            "loading": False,
            "items": [],
            "buyer_language": "",
            "error": None,
        }
        translation_state: dict[str, object] = {
            "loading": False,
            "error": None,
            "done": False,
        }
        send_state: dict[str, object] = {
            "task_id": None,
        }

        conv_map = {c.contact_ali_id: c for c in convs}
        all_groups = [g for g in group_conversations(convs) if g.conversations]
        cache = get_translation_cache()
        pending_pool = get_input_pending_pool()
        task_queue = get_task_queue()

        # Shared context passed to tab modules
        ctx: dict = {
            "selected": selected,
            "conv_map": conv_map,
            "resolver": resolver,
            "cache": cache,
            "pending_pool": pending_pool,
            "suggestion_state": suggestion_state,
            "translation_state": translation_state,
            "send_state": send_state,
            "task_queue": task_queue,
        }

        # Default no-op; overwritten by chat_info.render once the tab renders
        ctx["refresh_info"] = lambda: None

        # --------------------------------------------------------------
        # Left drawer
        # --------------------------------------------------------------

        drawer = ui.left_drawer(top_corner=True, bottom_corner=True).props("bordered")
        with drawer:
            nav("/chat")

            def _render_conv_item(conv) -> None:
                title = contact_display_name(conv.contact_ali_id)
                ts = format_created_at(conv.last_created_at)
                subtitle = ts[:16] if len(ts) >= 16 else ts
                is_selected = selected["contact"] == conv.contact_ali_id

                def _select(c: str = conv.contact_ali_id) -> None:
                    prev = selected.get("contact")
                    if prev:
                        pending_pool.put(prev, ctx["msg_input"].value or "")
                    selected["contact"] = c
                    ctx["msg_input"].value = pending_pool.get(c)
                    suggestion_state["loading"] = False
                    suggestion_state["items"] = []
                    suggestion_state["buyer_language"] = ""
                    suggestion_state["error"] = None
                    translation_state["loading"] = False
                    translation_state["error"] = None
                    translation_state["done"] = False
                    send_state["task_id"] = None
                    ctx["refresh_messages"]()
                    ctx["refresh_translate"]()
                    ctx["refresh_send_status"]()
                    ctx["refresh_info"]()
                    conv_list.refresh()

                item_classes = "rounded-lg" if is_selected else ""
                with ui.item(on_click=_select).classes(item_classes):
                    with ui.item_section().props("avatar"):
                        ui.avatar(
                            icon="chat",
                            color="primary" if is_selected else "grey-4",
                            size="sm",
                        )
                    with ui.item_section():
                        ui.item_label(title).classes(
                            "font-bold" if is_selected else ""
                        )
                        ui.item_label(subtitle).props("caption")

            @ui.refreshable
            def conv_list() -> None:
                with ui.scroll_area().classes("h-full"):
                    for group in all_groups:
                        label = f"{group.label} ({len(group.conversations)})"
                        with ui.expansion(label, value=group.expanded).classes("w-full"):
                            with ui.list().props("dense separator"):
                                for conv in group.conversations:
                                    _render_conv_item(conv)

            conv_list()

        # --------------------------------------------------------------
        # Main content area
        # --------------------------------------------------------------

        with ui.column().classes("w-full h-screen"):
            with ui.row().classes("w-full items-center"):
                ui.button(icon="menu", on_click=drawer.toggle).props(
                    "flat dense round"
                ).classes("drawer-toggle")
                with ui.tabs().classes("flex-grow") as tabs:
                    tab_msg = ui.tab("聊天消息", icon="chat")
                    tab_info = ui.tab("客户信息", icon="person")

            with ui.tab_panels(tabs, value=tab_msg).classes("w-full flex-grow flex flex-col"):
                with ui.tab_panel(tab_msg).classes("flex flex-col flex-grow p-0"):
                    chat_msg.render(ctx)

                with ui.tab_panel(tab_info).classes("flex flex-col flex-grow p-0"):
                    ctx["refresh_info"] = chat_info.render(ctx)
