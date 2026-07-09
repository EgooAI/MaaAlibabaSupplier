from __future__ import annotations

from nicegui import ui

_NAV_ITEMS = [
    ("home", "Home", "/"),
    ("chat", "Chat", "/chat"),
    ("inventory_2", "Cards", "/card"),
    ("health_and_safety", "Status", "/status"),
]


def nav(current: str) -> None:
    """Render sidebar navigation links.

    Call inside a ``ui.left_drawer`` context to place nav items at the top.
    """
    with ui.column().classes("w-full gap-0"):
        for icon, label, route in _NAV_ITEMS:
            is_active = route == current
            link_classes = "no-underline w-full px-2 py-2 rounded-lg"
            if is_active:
                link_classes += " bg-primary/15 text-primary font-semibold"
            else:
                link_classes += " hover:bg-gray-100"
            with ui.link(target=route).classes(link_classes):
                with ui.row().classes("items-center gap-2"):
                    ui.icon(icon).classes("text-lg")
                    ui.label(label).classes("text-sm")

    ui.separator()
