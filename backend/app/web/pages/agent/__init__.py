"""Agent console page: LLM configuration and AgentPreset management."""

from __future__ import annotations

from nicegui import ui

from backend.app.web.components.nav import nav
from backend.app.web.pages.agent.llm_panel import render_llm_config_panel
from backend.app.web.pages.agent.preset_panel import (
    render_agent_management_panel,
    render_system_agent_management_panel,
)


def _render_agent_page(active_path: str) -> None:
    ui.add_head_html(
        "<style>@media(min-width:993px){.drawer-toggle{display:none!important}}</style>"
    )
    drawer = ui.left_drawer(top_corner=True, bottom_corner=True).props("bordered")
    with drawer:
        nav(active_path)

    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4"):
        with ui.row().classes("items-center gap-2 w-full"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round").classes(
                "drawer-toggle"
            )
            with ui.column().classes("gap-1"):
                ui.label("Agent 控制台").classes("text-xl font-bold")
                ui.label("统一管理 LLM 配置与 AgentPreset").classes("text-xs text-gray-500")

        with ui.tabs().classes("w-full") as tabs:
            llm_tab = ui.tab("LLM 配置", icon="settings")
            system_agent_tab = ui.tab("系统 Agent", icon="shield")
            agent_tab = ui.tab("普通 Agent", icon="smart_toy")

        with ui.tab_panels(tabs, value=system_agent_tab).classes("w-full"):
            with ui.tab_panel(llm_tab).classes("p-0"):
                render_llm_config_panel()
            with ui.tab_panel(system_agent_tab).classes("p-0"):
                render_system_agent_management_panel()
            with ui.tab_panel(agent_tab).classes("p-0"):
                render_agent_management_panel()


def create() -> None:
    """Register the /agent page."""

    @ui.page("/agent")
    def agent_page() -> None:
        _render_agent_page("/agent")
