from __future__ import annotations

import asyncio

from nicegui import ui

from app.shared.agent.system_agents import SYSTEM_AGENT_DEFINITIONS
from app.web.pages.agent.chat_dialog import open_agent_chat_dialog
from app.web.pages.agent.common import (
    agent_form_fields,
    agent_manager_and_model,
    form_to_payload,
    is_system_apid,
    new_agent_apid,
    open_chat_action,
    set_status,
)


def _action_row(*, on_chat, on_save=None, on_delete=None) -> None:
    with ui.row().classes("gap-2"):
        ui.button("对话", icon="forum", on_click=on_chat).props("outline color=primary size=sm")
        if on_save is not None:
            ui.button("保存", icon="save", on_click=on_save).props("color=primary size=sm")
        if on_delete is not None:
            ui.button("删除", icon="delete", on_click=on_delete).props(
                "outline color=negative size=sm"
            )


def render_agent_management_panel() -> None:
    with ui.card().classes("w-full p-4 gap-4"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-1"):
                ui.label("Agent 管理").classes("text-lg font-bold")
                ui.label("管理 data/crm.sqlite 中的 agentpreset 表").classes("text-xs text-gray-500")
            refresh_btn = ui.button("刷新", icon="refresh").props("outline size=sm")

        status_label = ui.label("").classes("text-sm")
        container = ui.column().classes("w-full gap-4")

        def _render() -> None:
            container.clear()
            try:
                manager, AgentPreset = agent_manager_and_model()
                presets = [
                    preset
                    for preset in manager.list_agent_preset()
                    if not is_system_apid(preset.apid)
                ]
            except Exception as exc:
                set_status(status_label, f"加载失败：{exc}", ok=False)
                return

            set_status(status_label, f"已加载 {len(presets)} 个普通 Agent", ok=True)
            with container:
                with ui.expansion("新增 Agent", value=not presets).classes("w-full"):
                    with ui.card().classes(
                        "w-full rounded-2xl border border-slate-100 p-4 gap-4 shadow-sm"
                    ):
                        fields = agent_form_fields()

                        async def _create() -> None:
                            try:
                                preset = AgentPreset(apid=new_agent_apid(), **form_to_payload(fields))
                                await asyncio.to_thread(manager.upsert_agent_preset, preset)
                            except Exception as exc:
                                ui.notify(f"新增失败：{exc}", type="negative")
                                return
                            ui.notify("Agent 已保存", type="positive")
                            _render()

                        ui.button("保存新增 Agent", icon="add", on_click=_create).props(
                            "color=primary"
                        )

                if not presets:
                    ui.label("暂无普通 AgentPreset。系统 Agent 请在“系统 Agent”页签管理。").classes(
                        "text-sm text-gray-500"
                    )
                    return

                for preset in presets:
                    with ui.expansion(
                        f"{preset.name} | level={preset.llm_level}", value=False
                    ).classes("w-full"):
                        with ui.card().classes(
                            "w-full rounded-2xl border border-slate-100 p-4 gap-4 shadow-sm"
                        ):
                            fields = agent_form_fields(
                                name=preset.name,
                                description=preset.description,
                                prompt=preset.prompt,
                                level=preset.llm_level,
                                tools=preset.tools,
                            )

                            async def _save(
                                preset_apid: str = preset.apid,
                                form=fields,
                            ) -> None:
                                try:
                                    preset = AgentPreset(apid=preset_apid, **form_to_payload(form))
                                    await asyncio.to_thread(manager.upsert_agent_preset, preset)
                                except Exception as exc:
                                    ui.notify(f"保存失败：{exc}", type="negative")
                                    return
                                ui.notify("Agent 已保存", type="positive")
                                _render()

                            async def _delete(preset_apid: str = preset.apid) -> None:
                                if is_system_apid(preset_apid):
                                    ui.notify("系统 Agent 不可删除", type="warning")
                                    return
                                try:
                                    await asyncio.to_thread(manager.delete_agent_preset, preset_apid)
                                except Exception as exc:
                                    ui.notify(f"删除失败：{exc}", type="negative")
                                    return
                                ui.notify("Agent 已删除", type="positive")
                                _render()

                            _action_row(
                                on_chat=open_chat_action(preset.apid, preset.name),
                                on_save=_save,
                                on_delete=_delete,
                            )

        refresh_btn.on("click", _render)
        _render()


def render_system_agent_management_panel() -> None:
    with ui.card().classes("w-full p-4 gap-4"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-1"):
                ui.label("系统 Agent 管理").classes("text-lg font-bold")
                ui.label("Chat 工具栏固定绑定的四个系统 Agent；由程序启动时统一维护，不可编辑。").classes(
                    "text-xs text-gray-500"
                )
            refresh_btn = ui.button("刷新", icon="refresh").props("outline size=sm")

        status_label = ui.label("").classes("text-sm")
        container = ui.column().classes("w-full gap-4")

        def _render() -> None:
            container.clear()
            try:
                manager, _ = agent_manager_and_model()
            except Exception as exc:
                set_status(status_label, f"加载失败：{exc}", ok=False)
                return

            set_status(status_label, "已加载系统 Agent 绑定", ok=True)
            with container:
                for display_name, apid, helper_text in SYSTEM_AGENT_DEFINITIONS:
                    preset = manager.get_agent_preset(apid)
                    with ui.expansion(display_name, value=False).classes("w-full"):
                        with ui.card().classes(
                            "w-full rounded-2xl border border-slate-100 p-4 gap-4 shadow-sm"
                        ):
                            ui.label(helper_text).classes("text-xs text-gray-500")
                            if preset is None:
                                ui.label(
                                    "该系统 Agent 在数据库中不存在，Chat 工具调用时会报错。"
                                ).classes("text-sm text-red-600")
                                continue

                            with ui.row().classes("w-full items-center gap-3"):
                                ui.label("名称").classes("w-16 text-xs text-gray-500")
                                ui.label(preset.name).classes("text-sm font-medium")
                            with ui.row().classes("w-full items-center gap-3"):
                                ui.label("LLM Level").classes("w-16 text-xs text-gray-500")
                                ui.label(str(preset.llm_level)).classes("text-sm")
                            if preset.description:
                                with ui.row().classes("w-full items-start gap-3"):
                                    ui.label("描述").classes("w-16 text-xs text-gray-500")
                                    ui.label(preset.description).classes("text-sm text-gray-700")

                            _action_row(on_chat=open_chat_action(preset.apid, preset.name))

        refresh_btn.on("click", _render)
        _render()
