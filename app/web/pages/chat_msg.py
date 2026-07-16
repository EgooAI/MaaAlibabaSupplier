"""Chat message tab — displays messages, translation, send status, and input."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from loguru import logger

from nicegui import ui

from app.shared.agent.chat_tools import (
    CHAT_CUSTOMER_INTENT_AGENT_APID,
    CHAT_CUSTOMER_STAGE_AGENT_APID,
    build_analysis_input,
    run_chat_tool_agent,
)
from app.shared.backend.maafw_runner import chat_input, chat_send, goto_contact
from app.shared.crm import (
    get_translation,
    get_user_info as get_crm_user_info,
    request_translations,
    translation_cached,
)
from app.task_queue import TaskStatus, get_task_queue
from app.shared.crm.views import format_created_at
from app.web.chat_presenter import (
    contact_display_name,
    conversation_for_suggestions,
    generic_card_from_message,
    message_datetime,
    message_text,
    product_card_from_message,
)
from app.web.components.ai_suggestion import open_suggestion_dialog
from app.web.components.card import generic_card, product_card

_AVATAR_API = "https://ui-avatars.com/api/"


def _as_text_list(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _markdown_list(title: str, items: list[str]) -> str:
    if not items:
        return ""
    lines = [f"**{title}**"]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


def _format_analysis_result(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return "暂无分析结果。"

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text

    sections: list[str] = []
    if payload.get("intent"):
        sections.append(f"**客户意图**\n\n{str(payload['intent']).strip()}")
    if payload.get("stage"):
        sections.append(f"**客户阶段**\n\n{str(payload['stage']).strip()}")
    if payload.get("confidence"):
        sections.append(f"**置信度**\n\n{str(payload['confidence']).strip()}")

    for key, title in (
        ("evidence", "判断依据"),
        ("concerns", "客户顾虑"),
        ("next_actions", "下一步建议"),
    ):
        section = _markdown_list(title, _as_text_list(payload.get(key)))
        if section:
            sections.append(section)

    return "\n\n".join(sections) if sections else text


def _avatar_url(name: str, color: str) -> str:
    return f"{_AVATAR_API}?name={name}&background={color}&color=fff&size=32"


def _is_all_cached(messages, resolver) -> bool:
    """Return True when every buyer message in the conversation is already cached."""
    for m in messages:
        if resolver.is_self(m.sender_id) or m.is_system:
            continue
        text = message_text(m)
        if text and not translation_cached(text):
            return False
    return True


def _login_id_for_contact(contact_ali_id: str) -> str:
    info = _user_info_for_contact(contact_ali_id)
    if info and info.login_id:
        return info.login_id
    return contact_ali_id.removesuffix("@icbu")


def _user_info_for_contact(contact_ali_id: str):
    return get_crm_user_info(contact_ali_id)


def render(ctx: dict) -> None:
    """Render the chat message tab content inside the current container.

    Writes refresh handles and ``msg_input`` back into *ctx*.
    """
    selected = ctx["selected"]
    conv_map = ctx["conv_map"]
    resolver = ctx["resolver"]
    pending_pool = ctx["pending_pool"]
    suggestion_state = ctx["suggestion_state"]
    translation_state = ctx["translation_state"]
    send_state = ctx["send_state"]
    task_queue = ctx["task_queue"]

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    @ui.refreshable
    def messages() -> None:
        contact = selected.get("contact")
        if not contact or contact not in conv_map:
            with ui.column().classes("items-center justify-center flex-grow"):
                ui.icon("forum", size="4rem").classes("text-grey-3")
                ui.label("选择一个会话").classes("text-grey-5")
            return

        conv = conv_map[contact]
        _TIME_GAP = 600  # 10 minutes in seconds

        scroll = ui.scroll_area().classes("w-full flex-grow")
        with scroll:
            # Quick customer overview card
            info = _user_info_for_contact(contact)
            if info:
                with ui.card().classes("w-full max-w-sm mx-auto my-4").props("flat bordered"):
                    with ui.column().classes("items-center gap-1 w-full py-2"):
                        ui.label("快速认识新客户").classes("text-xs text-gray-400")
                        name = f"{info.first_name} {info.last_name}".strip() or info.login_id or info.ali_id
                        ui.label(name).classes("text-base font-bold")
                        parts = []
                        if info.country_code:
                            parts.append(info.country_code)
                        if info.register_date:
                            reg = datetime.fromtimestamp(info.register_date, tz=timezone.utc).strftime("%Y-%m-%d")
                            parts.append(f"注册于 {reg}")
                        if parts:
                            ui.label(" · ".join(parts)).classes("text-sm text-gray-500")

            prev_date = None
            prev_epoch = 0.0

            for msg in conv.messages:
                msg_dt = message_datetime(msg)
                msg_epoch = msg_dt.timestamp()
                msg_date = msg_dt.date()
                msg_time_str = msg_dt.strftime("%H:%M")
                msg_date_str = msg_dt.strftime("%Y-%m-%d")

                # Date label: cross-day or first message
                if prev_date is None or msg_date != prev_date:
                    ui.chat_message(label=msg_date_str).classes("w-full compact-label")
                    ui.chat_message(label=msg_time_str).classes("w-full compact-label")
                elif msg_epoch - prev_epoch > _TIME_GAP:
                    # Time label: >10min gap
                    ui.chat_message(label=msg_time_str).classes("w-full compact-label")

                prev_date = msg_date
                prev_epoch = msg_epoch

                stamp = format_created_at(msg.created_at)
                is_sent = resolver.is_self(msg.sender_id)
                if is_sent:
                    name = "我"
                else:
                    sid = (msg.sender_id or "").removesuffix("@icbu")
                    name = contact_display_name(sid) if sid else "unknown"

                text = message_text(msg)

                # System messages: centered small text (WeChat/QQ style)
                if msg.is_system:
                    ui.chat_message(label=text).classes("w-full compact-label")
                    continue

                # Card messages: render as rich component
                if msg.user_content_type == 10010:
                    color = "4caf50" if is_sent else "9e9e9e"
                    av_url = _avatar_url(name, color)

                    pc = product_card_from_message(msg)
                    if pc is not None:
                        with ui.chat_message(name=name, stamp=stamp, sent=is_sent, avatar=av_url).classes("w-full"):
                            product_card(pc, extra_classes="shadow-none border")
                        continue

                    gc = generic_card_from_message(msg)
                    if gc is not None:
                        with ui.chat_message(name=name, stamp=stamp, sent=is_sent, avatar=av_url).classes("w-full"):
                            generic_card(gc, extra_classes="shadow-none border")
                        continue

                # Normal text message
                if (
                    translation_state.get("show_results", True)
                    and not is_sent
                    and text
                    and translation_cached(text)
                ):
                    translated = get_translation(text)
                    if translated:
                        display_text = [text, f"[译] {translated}"]
                    else:
                        display_text = text
                else:
                    display_text = text

                if is_sent and msg.is_auto_reply:
                    color = "ff9800"
                    avatar_name = "Bot"
                elif is_sent:
                    color = "4caf50"
                    avatar_name = name
                else:
                    color = "9e9e9e"
                    avatar_name = name
                ui.chat_message(
                    display_text,
                    name=name,
                    stamp=stamp,
                    text_html=True,
                    sent=is_sent,
                    avatar=_avatar_url(avatar_name, color),
                ).classes("w-full")

            # Refresh button at the bottom of chat messages
            with ui.row().classes("w-full justify-center py-2"):
                ui.button("刷新页面以更新聊天记录", icon="refresh", on_click=lambda: ui.navigate.to("/chat")).props(
                    "flat dense size=sm color=grey"
                )

        # Auto-scroll to bottom
        scroll.scroll_to(percent=1)

    # ------------------------------------------------------------------
    # Agent toolbar
    # ------------------------------------------------------------------

    @ui.refreshable
    def tool_bar_section() -> None:
        contact = selected.get("contact")
        if not contact or contact not in conv_map:
            return

        conv = conv_map[contact]
        all_cached = _is_all_cached(conv.messages, resolver)

        async def _translate(force: bool = False) -> None:
            if translation_state.get("loading"):
                return
            translation_state["loading"] = True
            translation_state["error"] = None
            translation_state["done"] = False
            tool_bar_section.refresh()

            try:
                texts: list[str] = []
                for m in conv.messages:
                    is_me = resolver.is_self(m.sender_id)
                    text = message_text(m)
                    if is_me or not text or m.is_system:
                        continue
                    if not force and translation_cached(text):
                        continue
                    texts.append(text)

                saved = await asyncio.to_thread(request_translations, texts, force=force)
                logger.info("Translation agent saved {} rows", saved)
                translation_state["done"] = True
                messages.refresh()
            except Exception as exc:
                translation_state["error"] = f"翻译失败：{exc}"
                logger.error("Translation failed: {}", exc)
            finally:
                translation_state["loading"] = False
                tool_bar_section.refresh()

        def _toggle_translation_results() -> None:
            translation_state["show_results"] = not bool(
                translation_state.get("show_results", True)
            )
            messages.refresh()
            tool_bar_section.refresh()

        async def _handle_translate() -> None:
            await _translate(force=all_cached)

        async def _handle_suggestions() -> None:
            await _open_suggestions()

        async def _handle_stage_analysis() -> None:
            await _open_analysis(
                title="客户所处阶段分析",
                apid=CHAT_CUSTOMER_STAGE_AGENT_APID,
                task="分析客户当前所处成交阶段，并给出下一步推进建议。",
            )

        async def _handle_intent_analysis() -> None:
            await _open_analysis(
                title="客户意图分析",
                apid=CHAT_CUSTOMER_INTENT_AGENT_APID,
                task="分析客户真实意图、关注点、潜在异议和建议动作。",
            )

        with ui.card().props("flat bordered").classes("w-full bg-slate-50"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("auto_awesome").classes("text-amber-500")
                    ui.label("Agent 工具栏").classes("text-sm font-medium")
                with ui.row().classes("items-center gap-1"):
                    ui.button(
                        "AI建议",
                        icon="tips_and_updates",
                        on_click=_handle_suggestions,
                    ).props("size=sm flat color=amber")
                    ui.button(
                        "客户阶段",
                        icon="timeline",
                        on_click=_handle_stage_analysis,
                    ).props("size=sm flat color=primary")
                    ui.button(
                        "客户意图",
                        icon="psychology",
                        on_click=_handle_intent_analysis,
                    ).props("size=sm flat color=primary")
                if all_cached:
                    ui.button(
                        "重新翻译",
                        icon="refresh",
                        on_click=_handle_translate,
                    ).props("size=sm flat color=secondary")
                else:
                    ui.button(
                        "翻译",
                        icon="translate",
                        on_click=_handle_translate,
                    ).props("size=sm flat")
                show_results = bool(translation_state.get("show_results", True))
                toggle_text = "隐藏译文" if show_results else "显示译文"
                toggle_icon = "visibility_off" if show_results else "visibility"
                ui.button(
                    toggle_text,
                    icon=toggle_icon,
                    on_click=_toggle_translation_results,
                ).props("size=sm flat color=grey")

            if translation_state.get("loading"):
                with ui.row().classes("items-center gap-2 py-2"):
                    ui.spinner(size="sm", color="primary")
                    ui.label("翻译中…").classes("text-xs text-gray-500")

            err = translation_state.get("error")
            if isinstance(err, str) and err.strip():
                with ui.row().classes("items-center gap-2 py-1"):
                    ui.icon("error", color="red").classes("text-sm")
                    ui.label(err.strip()).classes("text-xs text-red-600")

            if translation_state.get("done"):
                ui.label("翻译完成，消息已更新").classes(
                    "text-xs text-green-600"
                )

    # ------------------------------------------------------------------
    # Send status
    # ------------------------------------------------------------------

    @ui.refreshable
    def send_status_section() -> None:
        task_id = send_state.get("task_id")
        if not isinstance(task_id, str):
            return

        snapshot = task_queue.get(task_id)
        if snapshot is None:
            return

        color = "grey"
        icon = "schedule"
        title = snapshot.description
        if snapshot.status == TaskStatus.RUNNING:
            color = "primary"
            icon = "send"
            title = "正在执行…"
        elif snapshot.status == TaskStatus.SUCCEEDED:
            color = "positive"
            icon = "check_circle"
            title = "执行成功"
        elif snapshot.status == TaskStatus.FAILED:
            color = "negative"
            icon = "error"
            title = "执行失败"

        with ui.card().classes("w-full"):
            with ui.row().classes("items-start gap-2 w-full"):
                ui.icon(icon, color=color).classes("mt-1")
                with ui.column().classes("gap-1"):
                    ui.label(title).classes("text-sm font-medium")
                    if snapshot.message and snapshot.message != title:
                        ui.label(snapshot.message).classes("text-xs text-gray-600")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _open_suggestions() -> None:
        contact = selected.get("contact")
        if not contact or contact not in conv_map:
            return
        conv = conv_map[contact]

        def _on_fill(text: str) -> None:
            msg_input.value = text
            contact_id = selected.get("contact")
            if contact_id:
                pending_pool.put(contact_id, text)

        await open_suggestion_dialog(conv, resolver, suggestion_state, _on_fill)

    async def _open_analysis(*, title: str, apid: str, task: str) -> None:
        contact = selected.get("contact")
        if not contact or contact not in conv_map:
            ui.notify("请选择一个会话", type="warning")
            return

        conv = conv_map[contact]
        with ui.dialog() as dialog, ui.card().classes("w-[640px] max-w-[92vw] gap-3"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(title).classes("text-base font-semibold")
                ui.button(icon="close", on_click=dialog.close).props("flat round dense")

            loading_ele = ui.row().classes("items-center gap-2 py-2")
            with loading_ele:
                ui.spinner(size="sm", color="primary")
                ui.label("分析中...").classes("text-xs text-gray-500")

            error_ele = ui.label().classes("text-xs text-red-600 py-1")
            error_ele.visible = False
            result_ele = ui.markdown("").classes("w-full text-sm")
            result_ele.style("max-height: 60vh; overflow-y: auto;")

            async def _run() -> None:
                try:
                    convo = conversation_for_suggestions(conv.messages, resolver)
                    result = await asyncio.to_thread(
                        run_chat_tool_agent,
                        apid,
                        build_analysis_input(task=task, conversation=convo),
                    )
                except Exception as exc:
                    error_ele.text = f"分析失败：{exc}"
                    error_ele.visible = True
                else:
                    result_ele.content = _format_analysis_result(result)
                finally:
                    loading_ele.visible = False

        dialog.open()
        await asyncio.sleep(0)
        await _run()

    async def _confirm_send(contact: str, login_id: str, text: str) -> str:
        with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[90vw]"):
            ui.label("确认发送消息？").classes("text-lg font-semibold")
            ui.label(f"收件人：{contact_display_name(contact)} ({login_id})").classes("text-sm text-gray-600")
            ui.separator()
            ui.label(text).classes("whitespace-pre-wrap max-h-64 overflow-auto")
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("取消", on_click=lambda: dialog.submit("cancel")).props("flat")
                ui.button("测试", on_click=lambda: dialog.submit("test")).props("color=warning")
                ui.button("确认发送", on_click=lambda: dialog.submit("confirm")).props("color=primary")
        return await dialog

    async def _send_current_message() -> None:
        contact = selected.get("contact")
        text = (msg_input.value or "").strip()
        if not contact or contact not in conv_map:
            ui.notify("请选择一个会话", type="warning")
            return
        if not text:
            ui.notify("请输入消息内容", type="warning")
            return

        login_id = _login_id_for_contact(contact)
        action = await _confirm_send(contact, login_id, text)
        if action == "cancel":
            return

        if action == "test":
            def _do_test() -> tuple[bool, str]:
                ok, msg = goto_contact(login_id)
                if not ok:
                    return False, f"选中联系人失败：{msg}"
                ok, msg = chat_input(text)
                if not ok:
                    return False, f"输入消息失败：{msg}"
                return True, "消息已输入（未发送）"
            fn = _do_test
            desc = f"测试: 跳转 {login_id} 并输入消息"
        else:
            def _do_send() -> tuple[bool, str]:
                ok, msg = goto_contact(login_id)
                if not ok:
                    return False, f"选中联系人失败：{msg}"
                ok, msg = chat_send(text)
                if not ok:
                    return False, f"发送消息失败：{msg}"
                return True, "消息发送成功"
            fn = _do_send
            desc = f"发送消息到 {login_id}"

        snapshot = task_queue.enqueue(fn, description=desc)
        send_state["task_id"] = snapshot.task_id
        send_status_section.refresh()

        while True:
            await asyncio.sleep(0.5)
            snap = task_queue.get(snapshot.task_id)
            if snap is None or snap.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
                break
            send_status_section.refresh()

        if snap and snap.result:
            ok, msg = snap.result
            ui.notify(msg, type="positive" if ok else "negative")
        send_status_section.refresh()

    def _refresh_send_status() -> None:
        task_id = send_state.get("task_id")
        if isinstance(task_id, str):
            send_status_section.refresh()

    # ------------------------------------------------------------------
    # Render sections
    # ------------------------------------------------------------------

    messages()
    send_status_section()

    # ------------------------------------------------------------------
    # Input card
    # ------------------------------------------------------------------

    with ui.card().classes("w-full"):
        tool_bar_section()
        with ui.row().classes("items-end w-full gap-2"):
            msg_input = ui.textarea(placeholder="输入消息…").props(
                "outlined autogrow rows=1 dense"
            ).classes("flex-grow max-h-[120px]")
            msg_input.on("blur", lambda: pending_pool.put(
                selected.get("contact") or "", msg_input.value or ""
            ))
            send_btn = ui.button(icon="send").props(
                "round color=primary"
            )

    # Restore initial contact's pending input
    initial = selected.get("contact")
    if initial:
        msg_input.value = pending_pool.get(initial)

    # Store handles back into ctx
    ctx["msg_input"] = msg_input
    ctx["refresh_messages"] = messages.refresh
    ctx["refresh_translate"] = tool_bar_section.refresh
    ctx["refresh_send_status"] = send_status_section.refresh

    send_btn.on("click", _send_current_message)
    send_status_timer = ui.timer(1.0, _refresh_send_status)
    ui.context.client.on_disconnect(lambda _client: send_status_timer.cancel())
