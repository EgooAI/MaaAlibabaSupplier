"""Status card UI components."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from app.web.components.ui_helpers import info_row


def status_card(
    title: str,
    icon: str,
    ok: bool,
    data: Any,
    rows: list[tuple[str, str, str]],
) -> None:
    """Render a status card with a badge and info rows.

    *rows* is a list of ``(icon, label, value)`` tuples.
    """
    with ui.card().classes("w-full p-5 gap-3"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon).classes("text-xl")
                ui.label(title).classes("text-base font-semibold")
            color = "positive" if ok else "negative"
            label = "正常" if ok else "异常"
            ui.badge(label, color=color).props("outline")

        for row_icon, row_label, row_value in rows:
            info_row(row_icon, row_label, row_value)
