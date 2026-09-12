"""Chat page - tabbed interface with messages, customer info, and batch tools."""

from __future__ import annotations

import asyncio

from nicegui import ui

from backend.app.shared.crm import (
    REASON_IM_DB_NOT_READY,
    REASON_SELF_IDENTITY_NOT_READY,
    CrmResolver,
    list_conversations,
    refresh_chat_data,
)
from backend.app.shared.mitm.pool import get_input_pending_pool
from backend.app.task_queue import get_task_queue
from backend.app.web.chat_presenter import contact_display_name, group_conversations, short_ts
from backend.app.web.components.nav import nav
from backend.app.web.pages import chat_batch, chat_info, chat_msg


_SYNC_MESSAGES = {
    REASON_IM_DB_NOT_READY: (
        "等待聊天数据库就绪…",
        "请从 app/main.py 启动，或确认 ALIBABA_DATA_DIR 指向当前登录账号的 Alibaba Supplier 数据目录。",
    ),
    REASON_SELF_IDENTITY_NOT_READY: (
        "等待当前登录账号识别…",
        "请确认 Alibaba Supplier 客户端已登录，并让主程序完成一次身份初始化。",
    ),
}


def _sync_reason_message(reason: str) -> tuple[str, str]:
    return _SYNC_MESSAGES.get(
        reason,
        ("等待聊天数据加载…", "聊天数据正在初始化，请稍后自动重试。"),
    )


def create() -> None:
    """Register the /chat page on the current NiceGUI app."""

    @ui.page("/chat")
    def chat_page() -> None:
        ui.add_head_html(
            "<style>"
            ".nicegui-content{padding:0;gap:0}"
            ".compact-label .q-message-label{margin:4px 0}"
            "</style>"
        )
        retry_timer = None

        async def _poll_sync_state() -> None:
            """Poll sync state off the event loop; stop once ready.

            解密整库是阻塞操作，不能在事件循环线程执行；轮询就绪后取消定时器，
            避免每 2 秒空转、每 5 秒重复解密。
            """
            state = await asyncio.to_thread(refresh_chat_data, wait=False)
            if state.ready and retry_timer is not None:
                retry_timer.cancel()
            content.refresh()

        @ui.refreshable
        def content() -> None:
            nonlocal retry_timer
            sync_state = refresh_chat_data(wait=False)
            if not sync_state.ready:
                title, subtitle = _sync_reason_message(sync_state.reason)
                with ui.column().classes("items-center justify-center w-full h-screen gap-3"):
                    ui.spinner(size="lg", color="primary")
                    ui.label(title).classes("text-gray-600")
                    ui.label(subtitle).classes("text-xs text-gray-400 text-center max-w-md")
                    ui.button("立即重试", icon="refresh", on_click=content.refresh).props("flat color=primary")
                return

            if retry_timer is not None:
                retry_timer.cancel()
                retry_timer = None

            self_ali_id = sync_state.self_ali_id
            convs = list_conversations(self_ali_id)
            resolver = CrmResolver(self_ali_id)

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
                "show_results": True,
            }
            send_state: dict[str, object] = {
                "task_id": None,
            }

            conv_map = {c.contact_ali_id: c for c in convs}
            all_groups = [g for g in group_conversations(convs) if g.conversations]
            pending_pool = get_input_pending_pool()
            task_queue = get_task_queue()

            ctx: dict = {
                "selected": selected,
                "conv_map": conv_map,
                "resolver": resolver,
                "pending_pool": pending_pool,
                "suggestion_state": suggestion_state,
                "translation_state": translation_state,
                "send_state": send_state,
                "task_queue": task_queue,
                "conversation_groups": all_groups,
                "batch_selected_contacts": set(),
            }

            def _render_conv_item(conv) -> None:
                title = contact_display_name(conv.contact_ali_id)
                subtitle = short_ts(conv.last_created_at)
                is_selected = selected["contact"] == conv.contact_ali_id

                def _select(c: str = conv.contact_ali_id) -> None:
                    prev = selected.get("contact")
                    if prev:
                        pending_pool.put(prev, ctx["msg_input"].value or "")
                    selected["contact"] = c
                    ctx["msg_input"].value = pending_pool.get(c)
                    suggestion_state.update(
                        loading=False, items=[], buyer_language="", error=None
                    )
                    translation_state.update(loading=False, error=None, done=False)
                    send_state["task_id"] = None
                    ctx["refresh_messages"]()
                    ctx["refresh_translate"]()
                    ctx["refresh_send_status"]()
                    refresh_info = ctx.get("refresh_info")
                    if refresh_info is not None:
                        refresh_info()
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
                with ui.scroll_area().classes("h-full w-full"):
                    for group in all_groups:
                        label = f"{group.label} ({len(group.conversations)})"
                        with ui.expansion(label, value=group.expanded).classes("w-full"):
                            with ui.list().props("dense separator"):
                                for conv in group.conversations:
                                    _render_conv_item(conv)

            with ui.row().classes("w-full h-screen gap-0 no-wrap"):
                with ui.column().classes("w-[22rem] h-full p-3 border-r bg-white shrink-0"):
                    nav("/chat")
                    conv_list()

                with ui.column().classes("w-full h-screen"):
                    with ui.row().classes("w-full items-center px-3 pt-2"):
                        with ui.tabs().classes("flex-grow") as tabs:
                            tab_msg = ui.tab("聊天消息", icon="chat")
                            tab_info = ui.tab("客户信息", icon="person")
                            tab_batch = ui.tab("批量客户管理", icon="groups")

                    with ui.tab_panels(tabs, value=tab_msg).classes("w-full flex-grow flex flex-col"):
                        with ui.tab_panel(tab_msg).classes("flex flex-col flex-grow p-0"):
                            chat_msg.render(ctx)

                        with ui.tab_panel(tab_info).classes("flex flex-col flex-grow p-0"):
                            ctx["refresh_info"] = chat_info.render(ctx)

                        with ui.tab_panel(tab_batch).classes("flex flex-col flex-grow p-0"):
                            chat_batch.render(ctx)

        content()
        retry_timer = ui.timer(2.0, _poll_sync_state)
        ui.context.client.on_disconnect(lambda _client: retry_timer.cancel())
