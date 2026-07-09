"""Index page — displays the current logged-in user's own profile."""

from __future__ import annotations

from nicegui import ui

from app.shared.mitm.pool import (
    SelfInfo,
    get_generic_card_pool,
    get_inquiry_card_pool,
    get_product_card_pool,
    get_self_info_pool,
    get_user_info_pool,
)
from app.web.components.nav import nav
from app.web.components.ui_helpers import empty_state, info_row


async def _clear_captured_data() -> None:
    get_user_info_pool().clear()
    get_self_info_pool().clear()
    get_product_card_pool().clear()
    get_inquiry_card_pool().clear()
    get_generic_card_pool().clear()
    ui.navigate.to("/")


def create() -> None:
    """Register the / page on the current NiceGUI app."""

    @ui.page("/")
    def index_page() -> None:
        ui.add_head_html(
            "<style>@media(min-width:993px){.drawer-toggle{display:none!important}}</style>"
        )
        drawer = ui.left_drawer(top_corner=True, bottom_corner=True).props("bordered")
        with drawer:
            nav("/")

        pool = get_self_info_pool()
        info: SelfInfo | None = pool.get()

        with ui.column().classes("w-full max-w-md mx-auto p-6 gap-4"):
            with ui.row().classes("items-center gap-2"):
                ui.button(icon="menu", on_click=drawer.toggle).props(
                    "flat dense round"
                ).classes("drawer-toggle")
                ui.label("自身信息").classes("text-xl font-bold")

            if info is None:
                with ui.card().classes("w-full p-6"):
                    empty_state("cloud_off", "暂无数据", subtitle="请先启动 MITM 代理并登录阿里卖家客户端")
                return

            with ui.card().classes("w-full p-6 gap-4"):
                # Avatar + name header
                with ui.row().classes("items-center gap-4"):
                    if info.avatar_url:
                        ui.avatar(size="xl").props("round").classes("shadow-md").style(
                            f"background-image: url('{info.avatar_url}'); background-size: cover;"
                        )
                    else:
                        ui.avatar(icon="person", color="primary", size="xl")
                    with ui.column().classes("gap-0"):
                        name = f"{info.first_name} {info.last_name}".strip()
                        ui.label(name or info.login_id).classes("text-lg font-bold")
                        with ui.row().classes("items-center gap-1"):
                            ui.icon("flag").classes("text-gray-400 text-sm")
                            ui.label(info.country or "—").classes(
                                "text-sm text-gray-500"
                            )

                ui.separator()

                # Account status badge
                with ui.row().classes("items-center gap-2"):
                    ui.label("账号状态:").classes("text-sm text-gray-500")
                    status = info.account_status or "unknown"
                    color = "positive" if status == "enabled" else "negative"
                    ui.badge(status, color=color).props("outline")

                ui.separator()

                # Info rows
                info_row("fingerprint", "Ali ID", info.ali_id, label_width="w-24")
                info_row("login", "登录 ID", info.login_id, label_width="w-24")
                info_row("lock", "加密 ID", info.encrypt_account_id, label_width="w-24")
                info_row("apartment", "公司", info.company_name, label_width="w-24")

            # Quick actions
            with ui.row().classes("w-full gap-2"):
                ui.button("查看聊天记录", icon="chat", on_click=lambda: ui.navigate.to("/chat")).classes(
                    "flex-grow"
                ).props("color=primary outline")
                ui.button("查看卡片", icon="inventory_2", on_click=lambda: ui.navigate.to("/card")).classes(
                    "flex-grow"
                ).props("color=primary outline")

            with ui.row().classes("w-full"):
                ui.button("清除缓存数据", icon="delete", on_click=_clear_captured_data).classes(
                    "w-full"
                ).props("color=red outline")
