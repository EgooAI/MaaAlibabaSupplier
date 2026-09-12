from __future__ import annotations

from typing import Any

from nicegui import ui

from backend.app.shared.crm.sdk import LLMApiConfig, LLMApiConfigManager
from backend.app.web.pages.agent.common import LEVELS, set_status


def _default_level_config() -> dict[str, Any]:
    return {
        "base_url": "",
        "api_key": "",
        "model_name": "",
        "system_prompt": "",
        "context": 12000,
        "max_tool_rounds": None,
    }


def _load_llm_config() -> dict[str, Any]:
    payload = LLMApiConfigManager().to_payload()
    if payload is None:
        return {"levels": {level: _default_level_config() for level in LEVELS}}
    if not isinstance(payload, dict):
        raise ValueError("llm_api_config 配置格式无效")
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


def _read_controls(controls: dict[int, dict[str, Any]]) -> dict[str, Any]:
    levels: dict[int, dict[str, Any]] = {}
    for level, fields in controls.items():
        raw_max = str(fields["max_tool_rounds"].value or "").strip()
        level_config: dict[str, Any] = {
            "base_url": (fields["base_url"].value or "").strip(),
            "api_key": fields["api_key"].value or "",
            "model_name": (fields["model_name"].value or "").strip(),
            "system_prompt": fields["system_prompt"].value or "",
            "context": int(round(fields["context"].value or 0)),
        }
        if raw_max:
            rounds = int(raw_max)
            level_config["max_tool_rounds"] = rounds if rounds > 0 else None
        levels[level] = level_config
    return {"levels": levels}


def _validate(config: dict[str, Any]) -> None:
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


def _save(config: dict[str, Any]) -> None:
    _validate(config)
    levels = config.get("levels") or {}
    rows = []
    for level in LEVELS:
        data = levels.get(level, levels.get(str(level), {})) or {}
        rows.append(
            LLMApiConfig(
                level=level,
                base_url=data["base_url"],
                api_key=data["api_key"],
                model_name=data["model_name"],
                system_prompt=data.get("system_prompt") or "",
                context=int(data["context"]),
                max_tool_rounds=data.get("max_tool_rounds"),
            )
        )
    LLMApiConfigManager().replace_configs(rows)
    from agent_pipeline.llm_api import register_default_llms

    register_default_llms()


def render_llm_config_panel() -> None:
    controls: dict[int, dict[str, Any]] = {}

    with ui.card().classes("w-full p-4 gap-3"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-1"):
                ui.label("LLM 配置").classes("text-lg font-bold")
                ui.label("存储位置：data/crm.sqlite 表 llm_api_config").classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                reload_btn = ui.button("重新加载", icon="refresh").props("outline size=sm")
                save_btn = ui.button("保存配置", icon="save").props("color=primary size=sm")

        status_label = ui.label("").classes("text-sm")
        form_container = ui.column().classes("w-full gap-4")

        def _render_form() -> None:
            controls.clear()
            form_container.clear()
            try:
                config = _load_llm_config()
            except Exception as exc:
                set_status(status_label, f"加载失败：{exc}", ok=False)
                return

            set_status(status_label, "配置已加载", ok=True)
            with form_container:
                for level in LEVELS:
                    data = _level_payload(config, level)
                    max_rounds_label = data.get("max_tool_rounds") or "未设置"
                    with ui.expansion(
                        f"Level {level} | max_tool_rounds: {max_rounds_label}", value=False
                    ).classes("w-full"):
                        with ui.card().classes("w-full p-4 gap-3"):
                            base_url = ui.input("Base URL", value=data["base_url"]).classes("w-full")
                            api_key = ui.input(
                                "API Key",
                                value=data["api_key"],
                                password=True,
                                password_toggle_button=True,
                            ).classes("w-full")
                            model_name = ui.input("Model Name", value=data["model_name"]).classes("w-full")
                            system_prompt = (
                                ui.textarea("System Prompt", value=data["system_prompt"])
                                .props("outlined autogrow")
                                .classes("w-full")
                            )
                            with ui.row().classes("w-full gap-3"):
                                context = ui.number(
                                    "Context", value=data["context"], min=1, step=1
                                ).classes("flex-1")
                                max_tool_rounds = ui.input(
                                    "最大工具轮数 max_tool_rounds",
                                    value=""
                                    if data.get("max_tool_rounds") is None
                                    else str(data.get("max_tool_rounds")),
                                ).classes("flex-1")
                            ui.label(
                                "max_tool_rounds 控制单次 agent 执行最多允许调用工具的轮数；为空时不限制。"
                            ).classes("text-xs text-gray-500")
                            controls[level] = {
                                "base_url": base_url,
                                "api_key": api_key,
                                "model_name": model_name,
                                "system_prompt": system_prompt,
                                "context": context,
                                "max_tool_rounds": max_tool_rounds,
                            }

        def _on_save() -> None:
            try:
                _save(_read_controls(controls))
            except Exception as exc:
                ui.notify(f"保存失败：{exc}", type="negative")
                set_status(status_label, f"保存失败：{exc}", ok=False)
                return
            ui.notify("LLM 配置已保存", type="positive")
            set_status(status_label, "保存成功", ok=True)
            _render_form()

        ui.label(
            "保存后，新配置会写入 llm_api_config 表，并在下次注册或重新加载 LLM 配置时生效。"
        ).classes("text-xs text-gray-500")
        reload_btn.on("click", _render_form)
        save_btn.on("click", _on_save)
        _render_form()
