"""Chat message tab — displays messages, translation, send status, and input."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger

from nicegui import ui

from app.shared.backend.chat_ai import translate_buyer_messages
from app.shared.backend.maafw_runner import chat_input, chat_send, goto_contact
from app.task_queue import TaskStatus, get_task_queue
from app.shared.mitm.pool import get_user_info_pool
from app.shared.backend.im_chat_db import format_created_at
from app.web.chat_presenter import (
    contact_display_name,
    conversation_for_translation,
    generic_card_from_message,
    message_datetime,
    message_text,
    product_card_from_message,
)
from app.web.components.ai_suggestion import open_suggestion_dialog
from app.web.components.card import generic_card, product_card

_AVATAR_API = "https://ui-avatars.com/api/"


def _avatar_url(name: str, color: str) -> str:
    return f"{_AVATAR_API}?name={name}&background={color}&color=fff&size=32"


def _is_all_cached(messages, resolver, cache) -> bool:
    """Return True when every buyer message in the conversation is already cached."""
    for m in messages:
        if resolver.is_self(m.sender_id) or m.is_system:
            continue
        text = message_text(m)
        if text and not cache.is_cached(text):
            return False
    return True


def _login_id_for_contact(contact_ali_id: str) -> str:
    info = get_user_info_pool().get(contact_ali_id) or get_user_info_pool().get_by_login_id(contact_ali_id)
    if info and info.login_id:
        return info.login_id
    return contact_ali_id.removesuffix("@icbu")


def render(ctx: dict) -> None:
    """Render the chat message tab content inside the current container.

    Writes refresh handles and ``msg_input`` back into *ctx*.
    """
    selected = ctx["selected"]
    conv_map = ctx["conv_map"]
    resolver = ctx["resolver"]
    cache = ctx["cache"]
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
            info = get_user_info_pool().get(contact) or get_user_info_pool().get_by_login_id(contact)
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
                if not is_sent and text and cache.is_cached(text):
                    translated = cache.get(text)
                    if translated is not None:
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
    # Translate section
    # ------------------------------------------------------------------

    @ui.refreshable
    def translate_section() -> None:
        contact = selected.get("contact")
        if not contact or contact not in conv_map:
            return

        conv = conv_map[contact]
        all_cached = _is_all_cached(conv.messages, resolver, cache)

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("translate").classes("text-blue-500")
                    ui.label("翻译").classes("text-sm font-medium")
                if all_cached:
                    trans_btn = ui.button("重新全部翻译", icon="refresh").props(
                        "size=sm flat color=secondary"
                    )
                else:
                    trans_btn = ui.button("翻译买家消息", icon="translate").props(
                        "size=sm flat"
                    )

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

            async def _translate(force: bool = False) -> None:
                if translation_state.get("loading"):
                    return
                translation_state["loading"] = True
                translation_state["error"] = None
                translation_state["done"] = False
                translate_section.refresh()

                try:
                    convo_for_llm = conversation_for_translation(conv.messages, resolver, cache, force=force)
                    result = await asyncio.to_thread(translate_buyer_messages, convo_for_llm)
                    if result is None:
                        translation_state["done"] = True
                        translation_state["loading"] = False
                        translate_section.refresh()
                        return

                    # Map msg IDs back to original texts and cache
                    counter = 0
                    for m in conv.messages:
                        is_me = resolver.is_self(m.sender_id)
                        text = message_text(m)
                        if is_me or not text or m.is_system:
                            continue
                        if not force and cache.is_cached(text):
                            continue
                        counter += 1
                        msg_id = f"msg{counter}"
                        translated = result.translations.get(msg_id)
                        cache.put(text, translated)
                        logger.info(
                            "Translated {}: {} -> {}",
                            msg_id,
                            text[:40],
                            (translated or "[已是中文]")[:40],
                        )

                    translation_state["done"] = True
                    messages.refresh()
                except Exception as exc:
                    translation_state["error"] = f"翻译失败：{exc}"
                    logger.error("Translation failed: {}", exc)
                finally:
                    translation_state["loading"] = False
                    translate_section.refresh()

            trans_btn.on("click", lambda: asyncio.create_task(_translate(force=all_cached)))

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
    # Render sections
    # ------------------------------------------------------------------

    messages()
    translate_section()
    send_status_section()

    # ------------------------------------------------------------------
    # Input card
    # ------------------------------------------------------------------

    with ui.card().classes("w-full"):
        with ui.row().classes("items-end w-full gap-2"):
            msg_input = ui.textarea(placeholder="输入消息…").props(
                "outlined autogrow rows=1 dense"
            ).classes("flex-grow max-h-[120px]")
            msg_input.on("blur", lambda: pending_pool.put(
                selected.get("contact") or "", msg_input.value or ""
            ))
            suggest_btn = ui.button(icon="auto_awesome").props(
                "round flat color=amber"
            ).tooltip("AI 建议")
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
    ctx["refresh_translate"] = translate_section.refresh
    ctx["refresh_send_status"] = send_status_section.refresh

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

    suggest_btn.on("click", _open_suggestions)
    send_btn.on("click", _send_current_message)
    ui.timer(1.0, _refresh_send_status)
