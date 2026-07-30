from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from nicegui import ui

from app.shared.agent.chat_tools import SYSTEM_AGENT_APIDS
from app.shared.crm.sdk import load_sdk

LEVELS = range(5)


def agent_manager_and_model():
    sdk = load_sdk()
    from core import AgentPresetManager

    return AgentPresetManager(), sdk["AgentPreset"]


def message_test_manager_and_model():
    sdk = load_sdk()
    from core import MessageTestManager

    return MessageTestManager(), sdk["MessageTest"]


def selected_tools(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(tool) for tool in value if str(tool).strip()]
    text = str(value).strip()
    return [text] if text else []


def available_agent_tools() -> list[str]:
    load_sdk()
    import agent_tools

    return sorted(
        name
        for name in getattr(agent_tools, "__all__", [])
        if not name.startswith("register_") and callable(getattr(agent_tools, name, None))
    )


def tool_options(selected: Any = None) -> list[str]:
    return sorted(set(available_agent_tools()) | set(selected_tools(selected)))


def read_agent_form(
    *,
    name: Any,
    description: Any,
    prompt: Any,
    level: Any,
    tools: Any = None,
    allow_tools: bool = True,
) -> dict[str, Any]:
    payload = {
        "name": str(name or "").strip(),
        "description": str(description or "").strip(),
        "prompt": str(prompt or "").strip(),
        "intelevel": level,
        "tools": selected_tools(tools) if allow_tools else [],
    }
    missing = [
        label
        for label, value in (
            ("名称", payload["name"]),
            ("描述", payload["description"]),
            ("Prompt", payload["prompt"]),
            ("LLM Level", "" if payload["intelevel"] is None or payload["intelevel"] == "" else "ok"),
        )
        if not value
    ]
    if missing:
        raise ValueError("必填项不能为空：" + "、".join(missing))

    payload["intelevel"] = int(payload["intelevel"])
    if payload["intelevel"] not in LEVELS:
        raise ValueError("LLM Level 必须在 0~4 之间")
    return payload


def prompt_editor(label: str = "Prompt", value: str = "") -> SimpleNamespace:
    prompt_state = SimpleNamespace(value=value or "")
    empty_hint = "尚未填写 Prompt，点击右侧按钮开始编辑。"

    with ui.card().classes("w-full gap-2 rounded-lg border border-gray-300 bg-white p-3 shadow-none"):
        with ui.row().classes("w-full items-center justify-between gap-3"):
            with ui.column().classes("gap-1"):
                ui.label(label).classes("text-sm text-gray-700")
                counter = ui.label(f"{len(prompt_state.value)} 字符").classes("text-xs text-gray-500")
            edit_btn = ui.button("编辑 Prompt", icon="edit").props("outline color=primary size=sm")
        preview = ui.label(prompt_state.value or empty_hint).classes(
            "w-full rounded-md border border-gray-200 bg-gray-50 p-3 text-sm leading-relaxed text-gray-700"
        )
        preview.style("white-space: pre-wrap; max-height: 9rem; overflow: hidden;")

    with ui.dialog() as dialog, ui.card().classes("max-w-none gap-4 rounded-2xl p-5").style(
        "width: min(920px, 92vw);"
    ):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-1"):
                ui.label(f"编辑 {label}").classes("text-lg font-bold")
                ui.label("在这里维护 Agent 的完整系统提示词，确认后再保存 Agent。").classes(
                    "text-xs text-gray-500"
                )
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")
        editor = (
            ui.textarea(label, value=prompt_state.value)
            .props("outlined autogrow autofocus")
            .classes("w-full min-h-[320px]")
        )
        editor.style("font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;")

        def _apply() -> None:
            prompt_state.value = editor.value or ""
            preview.text = prompt_state.value or empty_hint
            counter.text = f"{len(prompt_state.value)} 字符"
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("确认使用", icon="check", on_click=_apply).props("color=primary")

    edit_btn.on("click", lambda: (editor.set_value(prompt_state.value), dialog.open()))
    return prompt_state


def agent_form_fields(
    *,
    name: str = "",
    description: str = "",
    prompt: str = "",
    level: int = 0,
    tools: Any = None,
    allow_tools: bool = True,
) -> SimpleNamespace:
    with ui.row().classes("w-full gap-3"):
        name_input = ui.input("名称", value=name).props("required").classes("flex-1")
        level_input = (
            ui.select(list(LEVELS), label="LLM Level", value=level)
            .props("outlined required")
            .classes("w-40")
        )
    description_input = ui.input("描述", value=description).props("required").classes("w-full")
    prompt_input = prompt_editor(value=prompt)
    tools_input = None
    if allow_tools:
        tools_input = (
            ui.select(
                tool_options(tools),
                label="Tools",
                value=selected_tools(tools),
                multiple=True,
            )
            .props("outlined use-chips")
            .classes("w-full")
        )
    else:
        ui.label("系统 Agent 不暴露工具；输出会在代码侧自动规范化。").classes("text-xs text-gray-500")
    return SimpleNamespace(
        name=name_input,
        description=description_input,
        prompt=prompt_input,
        level=level_input,
        tools=tools_input,
        allow_tools=allow_tools,
    )


def form_to_payload(fields: SimpleNamespace) -> dict[str, Any]:
    return read_agent_form(
        name=fields.name.value,
        description=fields.description.value,
        prompt=fields.prompt.value,
        level=fields.level.value,
        tools=None if fields.tools is None else fields.tools.value,
        allow_tools=fields.allow_tools,
    )


def new_agent_apid() -> str:
    return f"agent-{uuid.uuid4().hex}"


def set_status(label, text: str, *, ok: bool) -> None:
    label.text = text
    label.classes(replace="text-sm text-green-600" if ok else "text-sm text-red-600")


def is_system_apid(apid: str) -> bool:
    return apid in SYSTEM_AGENT_APIDS
