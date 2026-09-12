from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from nicegui import ui

from app.shared.agent.chat_history_content import (
    ChatHistoryContent,
    build_history_content,
    history_messages,
    load_history_content,
)
from app.shared.agent.inputs import build_chat_dialog_input
from app.shared.agent.runner import run_chat_tool_agent
from app.shared.crm.sdk import ChatHistory, ChatHistoryManager
from app.shared.crm.views import format_created_at
from app.web.components.ui_helpers import confirm_dialog


def _history_title(agent_name: str, messages: list[dict[str, str]]) -> str:
    for message in messages:
        if message["role"] == "user" and message["content"].strip():
            return message["content"].strip().replace("\n", " ")[:40]
    return f"{agent_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def _message_preview(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        text = message["content"].strip().replace("\n", " ")
        if text:
            return text[:80]
    return "暂无消息"


async def open_agent_chat_dialog(apid: str, agent_name: str) -> None:
    chat_history_manager = ChatHistoryManager()

    messages: list[dict[str, str]] = []
    state: dict[str, Any] = {
        "history_id": None,
        "history_name": None,
        "existing_content": {},
    }

    with ui.dialog() as dialog, ui.card().classes("max-w-none gap-4 rounded-2xl p-5").style(
        "width: min(980px, 96vw);"
    ):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-1"):
                ui.label(f"与 {agent_name} 对话").classes("text-lg font-bold")
                ui.label(
                    "使用当前保存的 AgentPreset 和 LLM 配置运行；成功回复后会保存到 chat_history。"
                ).classes("text-xs text-gray-500")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        status_label = ui.label("").classes("text-xs text-gray-500")

        def _load_histories() -> list[tuple[Any, ChatHistoryContent]]:
            entries: list[tuple[Any, ChatHistoryContent]] = []
            for history in chat_history_manager.list_chat_history():
                content = load_history_content(history.content)
                if content.apid == apid:
                    entries.append((history, content))
            entries.sort(key=lambda entry: entry[1].updated_at, reverse=True)
            return entries

        def _reset_conversation(status: str) -> None:
            messages.clear()
            state["history_id"] = None
            state["history_name"] = None
            state["existing_content"] = {}
            status_label.text = status
            status_label.classes(replace="text-xs text-gray-500")
            _render_messages()
            _render_sidebar()

        def _render_messages() -> None:
            message_container.clear()
            with message_container:
                if not messages:
                    ui.label("输入一条消息开始测试这个 Agent。").classes("text-sm text-gray-500")
                    return
                for message in messages:
                    is_user = message["role"] == "user"
                    with ui.chat_message(
                        name="你" if is_user else agent_name,
                        sent=is_user,
                    ).classes("w-full"):
                        ui.label(message["content"]).style("white-space: pre-wrap;")

        def _load_sidebar_history(selected_history_id: int) -> None:
            selected_history = chat_history_manager.get_chat_history(selected_history_id)
            if selected_history is None:
                ui.notify("历史对话不存在", type="negative")
                _render_sidebar()
                return
            selected_content = load_history_content(selected_history.content)
            if selected_content.apid != apid:
                ui.notify("历史对话与当前 Agent 不匹配", type="negative")
                return
            messages.clear()
            messages.extend(history_messages(selected_content))
            state["history_id"] = selected_history.id
            state["history_name"] = selected_history.name
            state["existing_content"] = selected_content.model_dump()
            status_label.text = "已加载历史对话"
            status_label.classes(replace="text-xs text-gray-500")
            _render_messages()
            _render_sidebar()

        async def _delete_sidebar_history(selected_history_id: int, selected_history_name: str) -> None:
            confirmed = await confirm_dialog(
                "删除历史对话？",
                f"删除后将无法继续该历史对话。\n\n{selected_history_name}",
                confirm_text="确认删除",
                confirm_color="negative",
            )
            if not confirmed:
                return
            try:
                chat_history_manager.delete_chat_history(selected_history_id)
            except Exception as exc:
                ui.notify(f"删除历史失败：{exc}", type="negative")
                return
            if state.get("history_id") == selected_history_id:
                _reset_conversation("当前历史已删除，已切换到新对话")
            else:
                _render_sidebar()
            ui.notify("历史对话已删除", type="positive")

        def _render_sidebar() -> None:
            sidebar_container.clear()
            entries = _load_histories()
            current_history_id = state.get("history_id")
            with sidebar_container:
                ui.label("历史对话").classes("font-semibold")
                if not entries:
                    ui.label("暂无历史。成功完成一次回复后会保存。").classes("text-xs text-gray-500")
                    return
                for history, content in entries:
                    history_messages_ = history_messages(content)
                    updated_at = format_created_at(content.updated_at) if content.updated_at else "未知时间"
                    selected = history.id == current_history_id
                    with ui.card().classes(
                        "w-full gap-1 rounded-lg border p-2 shadow-none "
                        + ("border-primary bg-blue-50" if selected else "border-gray-200 bg-gray-50")
                    ):
                        with ui.row().classes("w-full items-start justify-between gap-1"):
                            with ui.column().classes("min-w-0 flex-1 gap-1 cursor-pointer").on(
                                "click",
                                lambda _, selected_id=history.id: _load_sidebar_history(selected_id),
                            ):
                                ui.label(history.name or f"历史 #{history.id}").classes(
                                    "text-sm font-medium"
                                )
                                ui.label(f"{len(history_messages_)} 条 · {updated_at}").classes(
                                    "text-[11px] text-gray-500"
                                )
                                ui.label(_message_preview(history_messages_)).classes(
                                    "text-xs text-gray-600"
                                )
                            ui.button(
                                icon="delete",
                                on_click=lambda _, hid=history.id, hname=history.name: _delete_sidebar_history(
                                    hid, hname
                                ),
                            ).props("flat round dense color=negative").tooltip("删除历史对话")

        async def _save_history() -> None:
            content = build_history_content(
                apid=apid,
                agent_name=agent_name,
                messages=messages,
                existing=dict(state.get("existing_content") or {}),
            )
            title = state.get("history_name") or _history_title(agent_name, messages)
            current_history_id = state.get("history_id")
            if current_history_id is None:
                record = ChatHistory(name=title, content=content)
                await asyncio.to_thread(chat_history_manager.add_chat_history, record)
                state["history_id"] = record.id
                state["history_name"] = record.name
            else:
                record = ChatHistory(id=current_history_id, name=title, content=content)
                await asyncio.to_thread(chat_history_manager.edit_chat_history, current_history_id, record)
            state["existing_content"] = content

        with ui.row().classes("w-full gap-4 items-start"):
            with ui.column().classes("w-64 shrink-0 gap-2"):
                sidebar_container = ui.column().classes(
                    "w-full gap-2 rounded-lg border border-gray-200 bg-white p-3"
                )
                sidebar_container.style("height: min(560px, 62vh); overflow-y: auto;")
            with ui.column().classes("flex-1 gap-3"):
                message_container = ui.column().classes(
                    "w-full gap-2 rounded-lg border border-gray-200 bg-gray-50 p-3"
                )
                message_container.style("height: min(440px, 50vh); overflow-y: auto;")
                user_input = ui.textarea("消息").props("outlined autogrow").classes("w-full")
                send_button = None

                async def _send() -> None:
                    text = str(user_input.value or "").strip()
                    if not text:
                        ui.notify("请输入消息", type="warning")
                        return

                    def _set_status(text: str, *, error: bool = False, success: bool = False) -> None:
                        status_label.text = text
                        color = "text-xs text-red-600" if error else "text-xs text-green-600" if success else "text-xs text-gray-500"
                        status_label.classes(replace=color)

                    messages.append({"role": "user", "content": text})
                    user_input.set_value("")
                    _set_status("Agent 正在回复...")
                    if send_button is not None:
                        send_button.disable()
                    _render_messages()

                    try:
                        reply = await asyncio.to_thread(
                            run_chat_tool_agent,
                            apid,
                            build_chat_dialog_input(messages),
                        )
                    except Exception as exc:
                        messages.pop()
                        _set_status(f"回复失败：{exc}", error=True)
                        ui.notify(f"回复失败：{exc}", type="negative")
                        _render_messages()
                        return
                    finally:
                        if send_button is not None:
                            send_button.enable()

                    messages.append({"role": "assistant", "content": reply or ""})
                    try:
                        await _save_history()
                    except Exception as exc:
                        _set_status(f"保存历史失败：{exc}", error=True)
                        ui.notify(f"保存历史失败：{exc}", type="negative")
                    else:
                        _set_status("回复完成，历史已保存", success=True)
                        _render_sidebar()
                    _render_messages()

                with ui.row().classes("w-full justify-between gap-2"):
                    ui.button(
                        "新对话",
                        icon="add",
                        on_click=lambda: _reset_conversation("已切换到新对话"),
                    ).props("outline color=warning")
                    with ui.row().classes("gap-2"):
                        ui.button("关闭", on_click=dialog.close).props("flat")
                        send_button = ui.button("发送", icon="send", on_click=_send).props(
                            "color=primary"
                        )

        _render_sidebar()
        _render_messages()

    dialog.open()
    await asyncio.sleep(0)
