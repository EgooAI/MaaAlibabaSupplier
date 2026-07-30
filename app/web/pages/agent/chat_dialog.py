from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from nicegui import ui

from app.shared.agent.chat_tools import run_chat_tool_agent
from app.web.pages.agent.common import message_test_manager_and_model


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _history_content(history: Any) -> dict[str, Any]:
    content = getattr(history, "content", None)
    return content if isinstance(content, dict) else {}


def _history_messages(content: dict[str, Any]) -> list[dict[str, str]]:
    messages = content.get("messages")
    if not isinstance(messages, list):
        return []
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        text = str(message.get("content") or "")
        if role in {"user", "assistant"}:
            normalized.append({"role": role, "content": text})
    return normalized


def _history_title(agent_name: str, messages: list[dict[str, str]]) -> str:
    for message in messages:
        if message["role"] == "user" and message["content"].strip():
            return message["content"].strip().replace("\n", " ")[:40]
    return f"{agent_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def _build_history_content(
    *,
    apid: str,
    agent_name: str,
    messages: list[dict[str, str]],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    existing = existing or {}
    return {
        "schema_version": 1,
        "apid": apid,
        "agent_name": agent_name,
        "messages": [dict(message) for message in messages],
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }


def _message_preview(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        text = message["content"].strip().replace("\n", " ")
        if text:
            return text[:80]
    return "暂无消息"


def _compose_input(messages: list[dict[str, str]]) -> str:
    history = "\n".join(
        f"{'User' if message['role'] == 'user' else 'Agent'}: {message['content']}"
        for message in messages
    )
    return (
        "You are chatting with the user in a multi-turn dialog.\n"
        "Use the conversation history below as context, and answer the latest User message.\n\n"
        f"{history}"
    )


async def _confirm_delete(history_name: str) -> bool:
    with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[90vw] gap-3"):
        ui.label("删除历史对话？").classes("text-lg font-semibold")
        ui.label("删除后将无法继续该历史对话。 ").classes("text-sm text-gray-600")
        ui.separator()
        ui.label(history_name).classes("whitespace-pre-wrap max-h-64 overflow-auto")
        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button("确认删除", on_click=lambda: dialog.submit(True)).props("color=negative")
    return bool(await dialog)


async def open_agent_chat_dialog(apid: str, agent_name: str, history_id: int | None = None) -> None:
    message_test_manager, MessageTest = message_test_manager_and_model()
    history_record = message_test_manager.get_message_test(history_id) if history_id is not None else None
    history_content = _history_content(history_record) if history_record is not None else {}

    if history_id is not None and history_record is None:
        ui.notify("历史对话不存在", type="negative")
        return
    if history_content and history_content.get("apid") != apid:
        ui.notify("历史对话与当前 Agent 不匹配，无法继续", type="negative")
        return

    messages: list[dict[str, str]] = _history_messages(history_content)
    state: dict[str, Any] = {
        "history_id": history_id,
        "history_name": getattr(history_record, "name", None),
        "created_content": history_content,
    }

    with ui.dialog() as dialog, ui.card().classes("max-w-none gap-4 rounded-2xl p-5").style(
        "width: min(980px, 96vw);"
    ):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-1"):
                ui.label(f"与 {agent_name} 对话").classes("text-lg font-bold")
                ui.label(
                    "使用当前保存的 AgentPreset 和 LLM 配置运行；成功回复后会保存到 Message_test。"
                ).classes("text-xs text-gray-500")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        status_label = ui.label("已加载历史对话" if history_id is not None else "").classes(
            "text-xs text-gray-500"
        )

        def _load_histories() -> list[Any]:
            histories = [
                history
                for history in message_test_manager.list_message_test()
                if _history_content(history).get("apid") == apid
            ]
            histories.sort(
                key=lambda history: str(_history_content(history).get("updated_at") or ""),
                reverse=True,
            )
            return histories

        def _reset_conversation(status: str) -> None:
            messages.clear()
            state["history_id"] = None
            state["history_name"] = None
            state["created_content"] = {}
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
            selected_history = message_test_manager.get_message_test(selected_history_id)
            if selected_history is None:
                ui.notify("历史对话不存在", type="negative")
                _render_sidebar()
                return
            selected_content = _history_content(selected_history)
            if selected_content.get("apid") != apid:
                ui.notify("历史对话与当前 Agent 不匹配", type="negative")
                return
            messages.clear()
            messages.extend(_history_messages(selected_content))
            state["history_id"] = selected_history.id
            state["history_name"] = selected_history.name
            state["created_content"] = selected_content
            status_label.text = "已加载历史对话"
            status_label.classes(replace="text-xs text-gray-500")
            _render_messages()
            _render_sidebar()

        async def _delete_sidebar_history(selected_history_id: int, selected_history_name: str) -> None:
            if not await _confirm_delete(selected_history_name):
                return
            try:
                message_test_manager.delete_message_test(selected_history_id)
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
            histories = _load_histories()
            current_history_id = state.get("history_id")
            with sidebar_container:
                ui.label("历史对话").classes("font-semibold")
                if not histories:
                    ui.label("暂无历史。成功完成一次回复后会保存。").classes("text-xs text-gray-500")
                    return
                for history in histories:
                    content = _history_content(history)
                    history_messages = _history_messages(content)
                    updated_at = str(content.get("updated_at") or "未知时间")
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
                                ui.label(f"{len(history_messages)} 条 · {updated_at}").classes(
                                    "text-[11px] text-gray-500"
                                )
                                ui.label(_message_preview(history_messages)).classes(
                                    "text-xs text-gray-600"
                                )
                            ui.button(
                                icon="delete",
                                on_click=lambda _, hid=history.id, hname=history.name: _delete_sidebar_history(
                                    hid, hname
                                ),
                            ).props("flat round dense color=negative").tooltip("删除历史对话")

        def _save_history() -> None:
            content = _build_history_content(
                apid=apid,
                agent_name=agent_name,
                messages=messages,
                existing=dict(state.get("created_content") or {}),
            )
            title = state.get("history_name") or _history_title(agent_name, messages)
            current_history_id = state.get("history_id")
            if current_history_id is None:
                record = MessageTest(name=title, content=content)
                message_test_manager.add_message_test(record)
                state["history_id"] = record.id
                state["history_name"] = record.name
            else:
                record = MessageTest(id=current_history_id, name=title, content=content)
                message_test_manager.edit_message_test(current_history_id, record)
            state["created_content"] = content

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

                    messages.append({"role": "user", "content": text})
                    user_input.set_value("")
                    status_label.text = "Agent 正在回复..."
                    status_label.classes(replace="text-xs text-gray-500")
                    if send_button is not None:
                        send_button.disable()
                    _render_messages()

                    try:
                        reply = await asyncio.to_thread(
                            run_chat_tool_agent,
                            apid,
                            _compose_input(messages),
                        )
                    except Exception as exc:
                        messages.pop()
                        status_label.text = f"回复失败：{exc}"
                        status_label.classes(replace="text-xs text-red-600")
                        ui.notify(f"回复失败：{exc}", type="negative")
                        _render_messages()
                    else:
                        messages.append({"role": "assistant", "content": reply or ""})
                        try:
                            _save_history()
                        except Exception as exc:
                            status_label.text = f"保存历史失败：{exc}"
                            status_label.classes(replace="text-xs text-red-600")
                            ui.notify(f"保存历史失败：{exc}", type="negative")
                        else:
                            status_label.text = "回复完成，历史已保存"
                            status_label.classes(replace="text-xs text-green-600")
                            _render_sidebar()
                        _render_messages()
                    finally:
                        if send_button is not None:
                            send_button.enable()

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
