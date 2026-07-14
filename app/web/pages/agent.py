"""Agent settings page - manage CRM SDK LLM API configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from nicegui import ui

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
        "tool_round_limit_output_text": "调用超过次数限制",
        "max_tool_rounds": None,
    }


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"levels": {level: _default_level_config() for level in _LEVELS}}
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    if not isinstance(payload, dict):
        raise ValueError("llm_api.yaml 顶层必须是一个 YAML mapping")
    return payload


def _level_payload(config: dict[str, Any], level: int) -> dict[str, Any]:
    levels = config.get("levels") or {}
    if not isinstance(levels, dict):
        levels = {}
    raw = levels.get(level, levels.get(str(level), {})) or {}
    if not isinstance(raw, dict):
        raw = {}
    default = _default_level_config()
    default.update(raw)
    return default


def _read_controls(controls: dict[int, dict[str, Any]]) -> dict[str, Any]:
    levels: dict[int, dict[str, Any]] = {}
    for level, fields in controls.items():
        raw_context = fields["context"].value
        raw_max_tool_rounds = fields["max_tool_rounds"].value
        level_config: dict[str, Any] = {
            "base_url": (fields["base_url"].value or "").strip(),
            "api_key": fields["api_key"].value or "",
            "model_name": (fields["model_name"].value or "").strip(),
            "system_prompt": fields["system_prompt"].value or "",
            "context": int(raw_context or 0),
            "context_limit_output_text": fields["context_limit_output_text"].value or "",
            "tool_round_limit_output_text": fields["tool_round_limit_output_text"].value or "",
        }
        if raw_max_tool_rounds not in {None, ""}:
            level_config["max_tool_rounds"] = int(raw_max_tool_rounds)
        levels[level] = level_config
    return {"levels": levels}


def _validate_config(config: dict[str, Any]) -> None:
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


def _save_config(config: dict[str, Any]) -> None:
    _validate_config(config)
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _CONFIG_PATH.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(config, file, allow_unicode=True, sort_keys=False)


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

        with ui.column().classes("w-full max-w-4xl mx-auto p-6 gap-4"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-2"):
                    ui.button(icon="menu", on_click=drawer.toggle).props(
                        "flat dense round"
                    ).classes("drawer-toggle")
                    ui.label("Agent 配置").classes("text-xl font-bold")
                with ui.row().classes("gap-2"):
                    reload_btn = ui.button("重新加载", icon="refresh").props("outline size=sm")
                    save_btn = ui.button("保存配置", icon="save").props("color=primary size=sm")

            ui.label(str(_CONFIG_PATH)).classes("text-xs text-gray-500")
            status_label = ui.label("").classes("text-sm")
            form_container = ui.column().classes("w-full gap-4")
            controls: dict[int, dict[str, Any]] = {}

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
                        with ui.expansion(f"Level {level} | 最大工具轮数: {max_rounds_label}", value=True).classes("w-full"):
                            with ui.card().classes("w-full p-4 gap-3"):
                                base_url = ui.input("Base URL", value=data["base_url"]).classes("w-full")
                                api_key = ui.input("API Key", value=data["api_key"], password=True, password_toggle_button=True).classes("w-full")
                                model_name = ui.input("Model Name", value=data["model_name"]).classes("w-full")
                                system_prompt = ui.textarea("System Prompt", value=data["system_prompt"]).props(
                                    "outlined autogrow"
                                ).classes("w-full")
                                with ui.row().classes("w-full gap-3"):
                                    context = ui.number("Context", value=data["context"], min=1, step=1).classes("flex-1")
                                    max_tool_rounds = ui.number(
                                        "最大工具轮数 max_tool_rounds",
                                        value=data.get("max_tool_rounds"),
                                        min=1,
                                        step=1,
                                    ).classes("flex-1")
                                ui.label("max_tool_rounds 控制单次 agent 执行最多允许调用工具的轮数；为空时使用 SDK 默认值。").classes(
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

                    with ui.expansion("原始 YAML 参考", value=False).classes("w-full"):
                        raw_text = _CONFIG_PATH.read_text(encoding="utf-8") if _CONFIG_PATH.exists() else ""
                        ui.code(raw_text or "# llm_api.yaml 不存在").classes("w-full")

            def _save() -> None:
                try:
                    config = _read_controls(controls)
                    _save_config(config)
                except Exception as exc:
                    ui.notify(f"保存失败：{exc}", type="negative")
                    status_label.text = f"保存失败：{exc}"
                    status_label.classes(replace="text-sm text-red-600")
                    return
                ui.notify("Agent LLM 配置已保存", type="positive")
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
