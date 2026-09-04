"""Customer analysis dialog for chat agent tools."""

from __future__ import annotations

import asyncio
import json

from nicegui import ui

from app.shared.agent.inputs import build_analysis_input
from app.shared.agent.runner import run_chat_tool_agent
from app.web.chat_presenter import conversation_for_suggestions


def _markdown_list(title: str, items: list[str]) -> str:
    if not items:
        return ""
    return "\n".join([f"**{title}**", *(f"- {item}" for item in items)])


def format_analysis_result(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return "暂无分析结果。"

    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("analysis result must be a JSON object")

    sections: list[str] = []
    for key, title in (
        ("intent", "客户意图"),
        ("stage", "客户阶段"),
        ("confidence", "置信度"),
    ):
        value = payload.get(key)
        if value:
            sections.append(f"**{title}**\n\n{str(value).strip()}")

    for key, title in (
        ("evidence", "判断依据"),
        ("concerns", "客户顾虑"),
        ("next_actions", "下一步建议"),
    ):
        raw_items = payload.get(key) or []
        items = (
            [str(item).strip() for item in raw_items if str(item).strip()]
            if isinstance(raw_items, list)
            else []
        )
        section = _markdown_list(title, items)
        if section:
            sections.append(section)

    return "\n\n".join(sections) if sections else text


async def open_analysis_dialog(*, title: str, apid: str, task: str, conv, resolver) -> None:
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
                result_ele.content = format_analysis_result(result)
            finally:
                loading_ele.visible = False

    dialog.open()
    await asyncio.sleep(0)
    await _run()
