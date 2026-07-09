"""Shared UI helper components used across multiple pages."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from nicegui import ui


def info_row(icon: str, label: str, value: str, *, label_width: str = "w-28") -> None:
    """Render an info row with icon, fixed-width label, and value."""
    with ui.row().classes("items-center gap-3"):
        ui.icon(icon).classes("text-gray-400 text-lg")
        ui.label(f"{label}:").classes(f"text-sm text-gray-500 {label_width}")
        ui.label(value or "—").classes("text-sm font-medium")


def empty_state(icon: str, message: str, *, subtitle: str = "") -> None:
    """Render a centered placeholder with a large icon and message."""
    with ui.column().classes("items-center justify-center flex-grow"):
        ui.icon(icon, size="4rem").classes("text-grey-3")
        ui.label(message).classes("text-grey-5")
        if subtitle:
            ui.label(subtitle).classes("text-xs text-gray-400")


@contextmanager
def section_card(title: str) -> Generator[None, None, None]:
    """Context manager that creates a card with a section header."""
    with ui.card().classes("w-full"):
        ui.label(title).classes("text-sm font-semibold text-gray-600 mb-2")
        yield
