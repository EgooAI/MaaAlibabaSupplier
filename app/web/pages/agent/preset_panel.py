from __future__ import annotations

from nicegui import ui

from app.shared.crm.system_agents import load_system_agent_definitions, restore_system_agent_default
from app.web.pages.agent.chat_dialog import open_agent_chat_dialog
from app.web.pages.agent.common import (
    agent_form_fields,
    agent_manager_and_model,
    form_to_payload,
    is_system_apid,
    new_agent_apid,
    set_status,
)


def _action_row(*, on_chat, on_save, on_restore=None, on_delete=None, locked: bool = False) -> None:
    with ui.row().classes("gap-2"):
        ui.button("对话", icon="forum", on_click=on_chat).props("outline color=primary size=sm")
        ui.button("保存", icon="save", on_click=on_save).props("color=primary size=sm")
        if on_restore is not None:
            ui.button("恢复默认设置", icon="restart_alt", on_click=on_restore).props(
                "outline color=warning size=sm"
            )
        if locked:
            ui.button("不可删除", icon="lock").props("outline color=grey size=sm disable")
        elif on_delete is not None:
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

                        def _create() -> None:
                            try:
                                preset = AgentPreset(apid=new_agent_apid(), **form_to_payload(fields))
                                manager.upsert_agent_preset(preset)
                            except Exception as exc:
                                ui.notify(f"新增失败：{exc}", type="negative")
                                return
                            ui.notify("Agent 已保存", type="positive")
                            _render()

                        ui.button("保存新增 Agent", icon="add", on_click=_create).props("color=primary")

                if not presets:
                    ui.label("暂无普通 Agent。系统 Agent 请在“系统 Agent”页签中管理。").classes(
                        "text-sm text-gray-500"
                    )
                    return

                for preset in presets:
                    with ui.expansion(
                        f"{preset.name} | level={preset.intelevel}", value=False
                    ).classes("w-full"):
                        with ui.card().classes(
                            "w-full rounded-2xl border border-slate-100 p-4 gap-4 shadow-sm"
                        ):
                            fields = agent_form_fields(
                                name=preset.name,
                                description=preset.description,
                                prompt=preset.prompt,
                                level=preset.intelevel,
                                tools=preset.tools,
                            )

                            def _save(
                                preset_apid: str = preset.apid,
                                form=fields,
                            ) -> None:
                                try:
                                    manager.upsert_agent_preset(
                                        AgentPreset(apid=preset_apid, **form_to_payload(form))
                                    )
                                except Exception as exc:
                                    ui.notify(f"保存失败：{exc}", type="negative")
                                    return
                                ui.notify("Agent 已保存", type="positive")
                                _render()

                            def _delete(preset_apid: str = preset.apid) -> None:
                                if is_system_apid(preset_apid):
                                    ui.notify("系统 Agent 不可删除", type="warning")
                                    return
                                try:
                                    manager.delete_agent_preset(preset_apid)
                                except Exception as exc:
                                    ui.notify(f"删除失败：{exc}", type="negative")
                                    return
                                ui.notify("Agent 已删除", type="positive")
                                _render()

                            def _open_chat(
                                preset_apid: str = preset.apid, preset_name: str = preset.name
                            ):
                                async def _run() -> None:
                                    await open_agent_chat_dialog(preset_apid, preset_name)

                                return _run

                            _action_row(
                                on_chat=_open_chat(),
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
                ui.label("四个系统 Agent 的名称、说明和默认配置都从数据库读取。").classes(
                    "text-xs text-gray-500"
                )
            refresh_btn = ui.button("刷新", icon="refresh").props("outline size=sm")

        status_label = ui.label("").classes("text-sm")
        container = ui.column().classes("w-full gap-4")

        def _render() -> None:
            container.clear()
            try:
                manager, AgentPreset = agent_manager_and_model()
                system_agent_definitions = load_system_agent_definitions(manager.database_path)
            except Exception as exc:
                set_status(status_label, f"加载失败：{exc}", ok=False)
                return

            set_status(status_label, "系统 Agent 已加载", ok=True)
            with container:
                for display_name, apid, helper_text in system_agent_definitions:
                    preset = manager.get_agent_preset(apid)
                    with ui.expansion(display_name, value=False).classes("w-full"):
                        with ui.card().classes(
                            "w-full rounded-2xl border border-slate-100 p-4 gap-4 shadow-sm"
                        ):
                            ui.label(helper_text).classes("text-xs text-gray-500")
                            if preset is None:
                                ui.label("该系统 Agent 在数据库中不存在，请恢复默认设置。").classes(
                                    "text-sm text-red-600"
                                )

                                def _restore_missing(preset_apid: str = apid) -> None:
                                    try:
                                        restore_system_agent_default(preset_apid, manager.database_path)
                                    except Exception as exc:
                                        ui.notify(f"恢复默认失败：{exc}", type="negative")
                                        return
                                    ui.notify("已恢复默认设置", type="positive")
                                    _render()

                                ui.button(
                                    "恢复默认设置",
                                    icon="restart_alt",
                                    on_click=_restore_missing,
                                ).props("outline color=warning size=sm")
                                continue

                            fields = agent_form_fields(
                                name=preset.name,
                                description=preset.description,
                                prompt=preset.prompt,
                                level=preset.intelevel,
                                allow_tools=False,
                            )

                            def _save(preset_apid: str = apid, form=fields) -> None:
                                try:
                                    manager.upsert_agent_preset(
                                        AgentPreset(apid=preset_apid, **form_to_payload(form))
                                    )
                                except Exception as exc:
                                    ui.notify(f"保存失败：{exc}", type="negative")
                                    return
                                ui.notify("系统 Agent 已保存", type="positive")
                                _render()

                            def _restore_default(preset_apid: str = apid) -> None:
                                try:
                                    restore_system_agent_default(preset_apid, manager.database_path)
                                except Exception as exc:
                                    ui.notify(f"恢复默认失败：{exc}", type="negative")
                                    return
                                ui.notify("已恢复默认设置", type="positive")
                                _render()

                            def _open_chat(
                                preset_apid: str = preset.apid, preset_name: str = preset.name
                            ):
                                async def _run() -> None:
                                    await open_agent_chat_dialog(preset_apid, preset_name)

                                return _run

                            _action_row(
                                on_chat=_open_chat(),
                                on_save=_save,
                                on_restore=_restore_default,
                                locked=True,
                            )

        refresh_btn.on("click", _render)
        _render()
