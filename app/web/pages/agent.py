"""Agent status page: LLM configuration and AgentPreset management."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from nicegui import ui

from app.shared.crm.sdk import load_sdk
from app.web.components.nav import nav

_CONFIG_PATH = Path("app/crm_sdk/llm_api.yaml")
_EXAMPLE_CONFIG_PATH = Path("app/crm_sdk/llm_api.example.yaml")
_LEVELS = range(5)


def _default_level_config() -> dict[str, Any]:
    return {
        "base_url": "",
        "api_key": "",
        "model_name": "",
        "system_prompt": "",
        "context": 12000,
        "context_limit_output_text": "上下文超过限制",
        "tool_round_limit_output_text": "工具调用超过次数限制",
        "max_tool_rounds": None,
    }


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"levels": {level: _default_level_config() for level in _LEVELS}}
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    if not isinstance(payload, dict):
        raise ValueError("llm_api.yaml 顶层必须是 YAML mapping")
    return payload


def _level_payload(config: dict[str, Any], level: int) -> dict[str, Any]:
    levels = config.get("levels") or {}
    if not isinstance(levels, dict):
        levels = {}
    raw = levels.get(level, levels.get(str(level), {})) or {}
    if not isinstance(raw, dict):
        raw = {}
    payload = _default_level_config()
    payload.update(raw)
    return payload


def _read_llm_controls(controls: dict[int, dict[str, Any]]) -> dict[str, Any]:
    levels: dict[int, dict[str, Any]] = {}
    for level, fields in controls.items():
        raw_context = fields["context"].value
        raw_max_tool_rounds = str(fields["max_tool_rounds"].value or "").strip()
        level_config: dict[str, Any] = {
            "base_url": (fields["base_url"].value or "").strip(),
            "api_key": fields["api_key"].value or "",
            "model_name": (fields["model_name"].value or "").strip(),
            "system_prompt": fields["system_prompt"].value or "",
            "context": int(raw_context or 0),
            "context_limit_output_text": fields["context_limit_output_text"].value or "",
            "tool_round_limit_output_text": fields["tool_round_limit_output_text"].value or "",
        }
        if raw_max_tool_rounds:
            level_config["max_tool_rounds"] = int(raw_max_tool_rounds)
        levels[level] = level_config
    return {"levels": levels}


def _validate_llm_config(config: dict[str, Any]) -> None:
    levels = config.get("levels")
    if not isinstance(levels, dict) or not levels:
        raise ValueError("levels 必须存在且不能为空")
    for level, payload in levels.items():
        prefix = f"levels.{level}"
        if not isinstance(payload, dict):
            raise ValueError(f"{prefix} 必须是 mapping")
        for field in ("base_url", "api_key", "model_name"):
            if not str(payload.get(field) or "").strip():
                raise ValueError(f"{prefix}.{field} 不能为空")
        context = payload.get("context")
        if not isinstance(context, int) or context <= 0:
            raise ValueError(f"{prefix}.context 必须是正整数")
        max_tool_rounds = payload.get("max_tool_rounds")
        if max_tool_rounds is not None and (not isinstance(max_tool_rounds, int) or max_tool_rounds <= 0):
            raise ValueError(f"{prefix}.max_tool_rounds 必须为空或正整数")


def _save_llm_config(config: dict[str, Any]) -> None:
    _validate_llm_config(config)
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _CONFIG_PATH.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(config, file, allow_unicode=True, sort_keys=False)


def _agent_manager_and_model():
    sdk = load_sdk()
    from core import AgentPresetManager

    return AgentPresetManager(), sdk["AgentPreset"]


def _available_agent_tools() -> list[str]:
    load_sdk()
    import agent_tools

    names = getattr(agent_tools, "__all__", [])
    return sorted(
        name
        for name in names
        if not name.startswith("register_")
        and callable(getattr(agent_tools, name, None))
    )


def _tool_options(selected_tools: Any = None) -> list[str]:
    selected = _selected_tools(selected_tools)
    return sorted(set(_available_agent_tools()) | set(selected))


def _selected_tools(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(tool) for tool in value if str(tool).strip()]
    if isinstance(value, tuple | set):
        return [str(tool) for tool in value if str(tool).strip()]
    text = str(value).strip()
    return [text] if text else []


def _read_agent_form(
    *,
    name: Any,
    description: Any,
    prompt: Any,
    level: Any,
    tools: Any,
) -> dict[str, Any]:
    payload = {
        "name": str(name or "").strip(),
        "description": str(description or "").strip(),
        "prompt": str(prompt or "").strip(),
        "intelevel": level,
        "tools": _selected_tools(tools),
    }
    missing: list[str] = []
    if not payload["name"]:
        missing.append("名称")
    if not payload["description"]:
        missing.append("描述")
    if not payload["prompt"]:
        missing.append("Prompt")
    if payload["intelevel"] is None or payload["intelevel"] == "":
        missing.append("LLM Level")
    if not payload["tools"]:
        missing.append("Tools")
    if missing:
        raise ValueError("必填项不能为空：" + "、".join(missing))

    payload["intelevel"] = int(payload["intelevel"])
    if payload["intelevel"] not in _LEVELS:
        raise ValueError("LLM Level 必须在 0~4 之间")
    return payload


def _prompt_editor(label: str = "Prompt", value: str = "") -> SimpleNamespace:
    prompt_state = SimpleNamespace(value=value or "")
    preview_text = prompt_state.value or "尚未填写 Prompt，点击右侧按钮开始编辑。"

    with ui.card().classes("w-full gap-2 rounded-lg border border-gray-300 bg-white p-3 shadow-none"):
        with ui.row().classes("w-full items-center justify-between gap-3"):
            with ui.column().classes("gap-1"):
                ui.label(label).classes("text-sm text-gray-700")
                counter = ui.label(f"{len(prompt_state.value)} 字符").classes("text-xs text-gray-500")
            edit_btn = ui.button("编辑 Prompt", icon="edit").props("outline color=primary size=sm")

        preview = ui.label(preview_text).classes(
            "w-full rounded-md border border-gray-200 bg-gray-50 p-3 text-sm leading-relaxed text-gray-700"
        )
        preview.style("white-space: pre-wrap; max-height: 9rem; overflow: hidden;")

    with ui.dialog() as dialog, ui.card().classes("max-w-none gap-4 rounded-2xl p-5").style("width: min(920px, 92vw);"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-1"):
                ui.label(f"编辑 {label}").classes("text-lg font-bold")
                ui.label("在这里维护 Agent 的完整系统提示词，确认后再保存 Agent。").classes("text-xs text-gray-500")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        editor = ui.textarea(label, value=prompt_state.value).props("outlined autogrow autofocus").classes(
            "w-full min-h-[320px]"
        )
        editor.style("font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;")

        def _apply_prompt() -> None:
            prompt_state.value = editor.value or ""
            preview.text = prompt_state.value or "尚未填写 Prompt，点击右侧按钮开始编辑。"
            counter.text = f"{len(prompt_state.value)} 字符"
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("确认使用", icon="check", on_click=_apply_prompt).props("color=primary")

    def _open_dialog() -> None:
        editor.set_value(prompt_state.value)
        dialog.open()

    edit_btn.on("click", _open_dialog)
    return prompt_state


def _render_llm_config_panel() -> None:
    controls: dict[int, dict[str, Any]] = {}

    with ui.card().classes("w-full p-4 gap-3"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-1"):
                ui.label("LLM 配置").classes("text-lg font-bold")
                ui.label(str(_CONFIG_PATH)).classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                reload_btn = ui.button("重新加载", icon="refresh").props("outline size=sm")
                save_btn = ui.button("保存配置", icon="save").props("color=primary size=sm")

        status_label = ui.label("").classes("text-sm")
        form_container = ui.column().classes("w-full gap-4")

        def _render_form() -> None:
            controls.clear()
            form_container.clear()
            try:
                config = _load_yaml_file(_CONFIG_PATH)
            except Exception as exc:
                status_label.text = f"加载失败：{exc}"
                status_label.classes(replace="text-sm text-red-600")
                return

            status_label.text = "配置已加载"
            status_label.classes(replace="text-sm text-green-600")
            with form_container:
                for level in _LEVELS:
                    data = _level_payload(config, level)
                    max_rounds_label = data.get("max_tool_rounds") or "未设置"
                    with ui.expansion(f"Level {level} | max_tool_rounds: {max_rounds_label}", value=False).classes("w-full"):
                        with ui.card().classes("w-full p-4 gap-3"):
                            base_url = ui.input("Base URL", value=data["base_url"]).classes("w-full")
                            api_key = ui.input(
                                "API Key",
                                value=data["api_key"],
                                password=True,
                                password_toggle_button=True,
                            ).classes("w-full")
                            model_name = ui.input("Model Name", value=data["model_name"]).classes("w-full")
                            system_prompt = ui.textarea("System Prompt", value=data["system_prompt"]).props(
                                "outlined autogrow"
                            ).classes("w-full")
                            with ui.row().classes("w-full gap-3"):
                                context = ui.number("Context", value=data["context"], min=1, step=1).classes("flex-1")
                                max_tool_rounds = ui.input(
                                    "最大工具轮数 max_tool_rounds",
                                    value="" if data.get("max_tool_rounds") is None else str(data.get("max_tool_rounds")),
                                ).classes("flex-1")
                            ui.label("max_tool_rounds 控制单次 agent 执行最多允许调用工具的轮数；为空时不限制。").classes(
                                "text-xs text-gray-500"
                            )
                            context_limit_output_text = ui.input(
                                "Context Limit Output Text",
                                value=data["context_limit_output_text"],
                            ).classes("w-full")
                            tool_round_limit_output_text = ui.input(
                                "Tool Round Limit Output Text",
                                value=data["tool_round_limit_output_text"],
                            ).classes("w-full")
                            controls[level] = {
                                "base_url": base_url,
                                "api_key": api_key,
                                "model_name": model_name,
                                "system_prompt": system_prompt,
                                "context": context,
                                "max_tool_rounds": max_tool_rounds,
                                "context_limit_output_text": context_limit_output_text,
                                "tool_round_limit_output_text": tool_round_limit_output_text,
                            }

                with ui.expansion("原始 YAML 预览", value=False).classes("w-full"):
                    raw_text = _CONFIG_PATH.read_text(encoding="utf-8") if _CONFIG_PATH.exists() else ""
                    ui.code(raw_text or "# llm_api.yaml 不存在").classes("w-full")

        def _save() -> None:
            try:
                _save_llm_config(_read_llm_controls(controls))
            except Exception as exc:
                ui.notify(f"保存失败：{exc}", type="negative")
                status_label.text = f"保存失败：{exc}"
                status_label.classes(replace="text-sm text-red-600")
                return
            ui.notify("LLM 配置已保存", type="positive")
            status_label.text = "保存成功"
            status_label.classes(replace="text-sm text-green-600")
            _render_form()

        def _copy_example() -> None:
            if not _EXAMPLE_CONFIG_PATH.exists():
                ui.notify("示例配置不存在", type="negative")
                return
            _CONFIG_PATH.write_text(_EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            ui.notify("已从示例配置生成 llm_api.yaml", type="positive")
            _render_form()

        with ui.row().classes("w-full gap-2"):
            ui.button("从示例重置", icon="restore_page", on_click=_copy_example).props("outline color=warning size=sm")
            ui.label("保存后，新配置会在下次注册或重新加载 LLM 配置时生效。").classes("text-xs text-gray-500 self-center")

        reload_btn.on("click", _render_form)
        save_btn.on("click", _save)
        _render_form()


def _render_agent_management_panel() -> None:
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
                manager, AgentPreset = _agent_manager_and_model()
                presets = manager.list_agent_preset()
            except Exception as exc:
                status_label.text = f"加载失败：{exc}"
                status_label.classes(replace="text-sm text-red-600")
                return

            status_label.text = f"已加载 {len(presets)} 个 Agent"
            status_label.classes(replace="text-sm text-green-600")
            with container:
                with ui.expansion("新增 Agent", value=not presets).classes("w-full"):
                    with ui.card().classes("w-full rounded-2xl border border-slate-100 p-4 gap-4 shadow-sm"):
                        with ui.row().classes("w-full gap-3"):
                            new_name = ui.input("名称").props("required").classes("flex-1")
                            new_level = ui.select(
                                list(_LEVELS),
                                label="LLM Level",
                                value=0,
                            ).props("outlined required").classes("w-40")
                        new_description = ui.input("描述").props("required").classes("w-full")
                        new_prompt = _prompt_editor()
                        new_tools = ui.select(
                            _tool_options(),
                            label="Tools",
                            value=[],
                            multiple=True,
                        ).props("outlined use-chips required").classes("w-full")

                        def _create_agent() -> None:
                            try:
                                payload = _read_agent_form(
                                    name=new_name.value,
                                    description=new_description.value,
                                    prompt=new_prompt.value,
                                    level=new_level.value,
                                    tools=new_tools.value,
                                )
                                preset = AgentPreset(
                                    apid=f"agent-{uuid.uuid4().hex}",
                                    **payload,
                                )
                                manager.upsert_agent_preset(preset)
                            except Exception as exc:
                                ui.notify(f"新增失败：{exc}", type="negative")
                                return
                            ui.notify("Agent 已保存", type="positive")
                            _render()

                        ui.button("保存新增 Agent", icon="add", on_click=_create_agent).props("color=primary")

                if not presets:
                    ui.label("暂无 AgentPreset。可以在上方新增一个 Agent。").classes("text-sm text-gray-500")
                    return

                for preset in presets:
                    title = f"{preset.name} | level={preset.intelevel}"
                    with ui.expansion(title, value=False).classes("w-full"):
                        with ui.card().classes("w-full rounded-2xl border border-slate-100 p-4 gap-4 shadow-sm"):
                            with ui.row().classes("w-full gap-3"):
                                name = ui.input("名称", value=preset.name).props("required").classes("flex-1")
                                level = ui.select(
                                    list(_LEVELS),
                                    label="LLM Level",
                                    value=preset.intelevel,
                                ).props("outlined required").classes("w-40")
                            description = ui.input("描述", value=preset.description).props("required").classes("w-full")
                            prompt = _prompt_editor(value=preset.prompt)
                            tools = ui.select(
                                _tool_options(preset.tools),
                                label="Tools",
                                value=_selected_tools(preset.tools),
                                multiple=True,
                            ).props("outlined use-chips required").classes("w-full")

                            def _make_save_agent(
                                preset_apid: str,
                                name_input,
                                description_input,
                                prompt_input,
                                level_input,
                                tools_input,
                            ):
                                def _save_agent() -> None:
                                    try:
                                        payload = _read_agent_form(
                                            name=name_input.value,
                                            description=description_input.value,
                                            prompt=prompt_input.value,
                                            level=level_input.value,
                                            tools=tools_input.value,
                                        )
                                        updated = AgentPreset(
                                            apid=preset_apid,
                                            **payload,
                                        )
                                        manager.upsert_agent_preset(updated)
                                    except Exception as exc:
                                        ui.notify(f"保存失败：{exc}", type="negative")
                                        return
                                    ui.notify("Agent 已保存", type="positive")
                                    _render()

                                return _save_agent

                            def _make_delete_agent(preset_apid: str):
                                def _delete_agent() -> None:
                                    try:
                                        manager.delete_agent_preset(preset_apid)
                                    except Exception as exc:
                                        ui.notify(f"删除失败：{exc}", type="negative")
                                        return
                                    ui.notify("Agent 已删除", type="positive")
                                    _render()

                                return _delete_agent

                            with ui.row().classes("gap-2"):
                                ui.button(
                                    "保存",
                                    icon="save",
                                    on_click=_make_save_agent(preset.apid, name, description, prompt, level, tools),
                                ).props("color=primary size=sm")
                                ui.button(
                                    "删除",
                                    icon="delete",
                                    on_click=_make_delete_agent(preset.apid),
                                ).props("outline color=negative size=sm")

        refresh_btn.on("click", _render)
        _render()


def create() -> None:
    """Register the /status/agent page."""

    @ui.page("/status/agent")
    def agent_page() -> None:
        ui.add_head_html(
            "<style>@media(min-width:993px){.drawer-toggle{display:none!important}}</style>"
        )
        drawer = ui.left_drawer(top_corner=True, bottom_corner=True).props("bordered")
        with drawer:
            nav("/status/agent")

        with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4"):
            with ui.row().classes("items-center gap-2 w-full"):
                ui.button(icon="menu", on_click=drawer.toggle).props(
                    "flat dense round"
                ).classes("drawer-toggle")
                with ui.column().classes("gap-1"):
                    ui.label("Agent 控制台").classes("text-xl font-bold")
                    ui.label("统一管理 LLM 配置与 AgentPreset").classes("text-xs text-gray-500")

            with ui.tabs().classes("w-full") as tabs:
                llm_tab = ui.tab("LLM 配置", icon="settings")
                agent_tab = ui.tab("Agent 管理", icon="smart_toy")

            with ui.tab_panels(tabs, value=llm_tab).classes("w-full"):
                with ui.tab_panel(llm_tab).classes("p-0"):
                    _render_llm_config_panel()
                with ui.tab_panel(agent_tab).classes("p-0"):
                    _render_agent_management_panel()
