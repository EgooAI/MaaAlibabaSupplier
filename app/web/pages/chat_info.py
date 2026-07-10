"""Customer info tab — displays profile details for the selected contact."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

from nicegui import ui

from app.shared.backend.maafw_runner import goto_contact
from app.shared.crm import get_user_info as get_crm_user_info
from app.task_queue import TaskStatus, get_task_queue
from app.web.components.ui_helpers import empty_state, info_row, section_card


def render(ctx: dict) -> Callable[[], None]:
    """Render the customer info panel. Returns the refresh callable."""
    selected = ctx["selected"]

    @ui.refreshable
    def customer_info() -> None:
        contact = selected.get("contact")
        if not contact:
            empty_state("person", "选择一个会话查看客户信息")
            return

        info = get_crm_user_info(contact)
        if info is None:
            empty_state("person_off", "暂无客户信息")
            return

        with ui.scroll_area().classes("w-full flex-grow"):
            # Header (full width)
            with ui.row().classes("items-center gap-3 mb-4"):
                name = f"{info.first_name} {info.last_name}".strip() or info.login_id or info.ali_id
                ui.avatar(icon="person", color="primary", size="lg")
                with ui.column().classes("gap-0"):
                    ui.label(name).classes("text-lg font-bold")
                    if info.company_name:
                        ui.label(info.company_name).classes("text-sm text-gray-500")

            # Section cards in responsive grid
            with ui.element("div").classes("grid grid-cols-1 lg:grid-cols-2 gap-4"):
                # Identity
                with section_card("身份信息"):
                    info_row("fingerprint", "Ali ID", info.ali_id)
                    info_row("badge", "会员 ID", info.ali_member_id)
                    info_row("login", "登录 ID", info.login_id)
                    info_row("key", "加密 ID", info.encrypt_account_id[:40] + "…" if len(info.encrypt_account_id) > 40 else info.encrypt_account_id)
                    async def _goto_contact() -> None:
                        login_id = info.login_id
                        if not login_id:
                            ui.notify("缺少 login_id", type="warning")
                            return
                        tq = get_task_queue()
                        snapshot = tq.enqueue(
                            fn=lambda lid=login_id: goto_contact(lid),
                            description=f"跳转到联系人 {login_id}",
                        )
                        while True:
                            await asyncio.sleep(0.5)
                            snap = tq.get(snapshot.task_id)
                            if snap is None or snap.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
                                break
                        if snap and snap.result:
                            ok, msg = snap.result
                            ui.notify(msg, type="positive" if ok else "negative")

                    ui.button("在UI内跳转到该联系人", icon="open_in_new", on_click=_goto_contact).props(
                        "flat dense size=sm color=primary"
                    ).classes("mt-2")

                # Profile
                with section_card("个人资料"):
                    info_row("person", "姓名", f"{info.first_name} {info.last_name}".strip())
                    info_row("public", "国家", info.country_code)
                    info_row("business", "公司", info.company_name)
                    reg = ""
                    if info.register_date:
                        reg = datetime.fromtimestamp(info.register_date, tz=timezone.utc).strftime("%Y-%m-%d")
                    info_row("calendar_today", "注册时间", reg)

                # Contact
                with section_card("联系方式"):
                    info_row("email", "邮箱", info.email)
                    info_row("phone_android", "手机", info.mobile_number)
                    info_row("phone", "电话", info.phone_number)

                # Behavior (D90)
                with section_card("行为数据 (近 90 天)"):
                    info_row("visibility", "商品浏览", str(info.product_view_count))
                    info_row("question_answer", "有效询盘", str(info.valid_inquiry_count))
                    info_row("reply", "已回复询盘", str(info.replied_inquiry_count))
                    info_row("request_quote", "有效 RFQ", str(info.valid_rfq_count))
                    info_row("login", "登录天数", str(info.login_days))
                    info_row("report", "垃圾询盘", str(info.spam_inquiry_count))
                    info_row("block", "拉黑次数", str(info.blacklisted_count))

                # Tags
                with section_card("标签"):
                    info_row("label", "质量等级", info.high_quality_level_tag)
                    info_row("trending_up", "成长等级", info.growth_level)
                    info_row("category", "偏好行业", ", ".join(info.preferred_industries) if info.preferred_industries else "")

                # Availability
                with section_card("可用性"):
                    with ui.row().classes("items-center gap-3"):
                        ui.icon("circle").classes(
                            "text-green-500" if info.available else "text-red-500"
                        )
                        ui.label("状态:").classes("text-sm text-gray-500 w-28")
                        ui.badge("可用" if info.available else "不可用",
                                 color="positive" if info.available else "negative").props("outline")
                    info_row("schedule", "加入年限", str(info.joining_years))
                    info_row("star", "潜力分", str(info.potential_score))
                    with ui.row().classes("items-center gap-3"):
                        ui.icon("chat" if info.recent_contact else "chat_bubble_outline").classes("text-gray-400 text-lg")
                        ui.label("近期联系:").classes("text-sm text-gray-500 w-28")
                        ui.icon("check_circle" if info.recent_contact else "cancel",
                                color="green" if info.recent_contact else "grey")
                    with ui.row().classes("items-center gap-3"):
                        ui.icon("verified" if info.email_validated else "mark_email_unread").classes("text-gray-400 text-lg")
                        ui.label("邮箱验证:").classes("text-sm text-gray-500 w-28")
                        ui.icon("check_circle" if info.email_validated else "cancel",
                                color="green" if info.email_validated else "grey")

    customer_info()
    return customer_info.refresh
