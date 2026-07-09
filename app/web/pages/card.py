"""Card page — displays intercepted cards (product + generic)."""

from __future__ import annotations

from nicegui import ui

from app.shared.mitm.pool import get_generic_card_pool, get_inquiry_card_pool, get_product_card_pool
from app.web.components.card import card_type_name, generic_card, inquiry_card, product_card
from app.web.components.nav import nav

_PAGE_SIZE = 20


def create() -> None:
    """Register the /card page on the current NiceGUI app."""

    @ui.page("/card")
    def card_page() -> None:
        ui.add_head_html(
            "<style>@media(min-width:993px){.drawer-toggle{display:none!important}}</style>"
        )
        drawer = ui.left_drawer(top_corner=True, bottom_corner=True).props("bordered")
        with drawer:
            nav("/card")

        # Build unified card list: ("product", card) or ("generic", card)
        product_items = [("product", c) for c in get_product_card_pool().all().values()]
        inquiry_items = [("inquiry", c) for c in get_inquiry_card_pool().all().values()]
        generic_items = [("generic", c) for c in get_generic_card_pool().all().values()]

        # Collect available card types for filter
        generic_type_ids = sorted({c.card_type for _, c in generic_items})
        filter_options = {"全部": None, "产品卡片": "product", "询盘": "inquiry"}
        for ct in generic_type_ids:
            filter_options[card_type_name(ct)] = ct

        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-4"):
            with ui.row().classes("items-center gap-2"):
                ui.button(icon="menu", on_click=drawer.toggle).props(
                    "flat dense round"
                ).classes("drawer-toggle")
                ui.label("卡片浏览").classes("text-xl font-bold")
            state: dict[str, object] = {"filter": None, "page": 1}

            def _filtered() -> list[tuple[str, object]]:
                f = state["filter"]
                if f is None:
                    return product_items + inquiry_items + generic_items
                if f == "product":
                    return product_items
                if f == "inquiry":
                    return inquiry_items
                return [(t, c) for t, c in generic_items if c.card_type == f]

            filtered = _filtered()
            total = len(filtered)
            total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
            state["page"] = 1

            # Filter + pager
            filter_row = ui.row().classes("items-center justify-between w-full")

            def _render_filter() -> None:
                filter_row.clear()
                with filter_row:
                    f = state["filter"]
                    label = "全部"
                    for k, v in filter_options.items():
                        if v == f:
                            label = k
                            break
                    ui.select(
                        filter_options,
                        value=f,
                        label="筛选",
                        on_change=lambda e: _apply_filter(e.value),
                    ).props("dense outlined").classes("w-48")

                    cur = _filtered()
                    cur_total = len(cur)
                    cur_pages = max(1, (cur_total + _PAGE_SIZE - 1) // _PAGE_SIZE)
                    ui.label(f"共 {cur_total} 个卡片").classes("text-sm text-gray-500")
                    if cur_pages > 1:
                        ui.pagination(
                            1, cur_pages,
                            value=state["page"],
                            on_change=lambda e: _go_page(e.value),
                        ).props("boundary-numbers direction-links")

            def _apply_filter(value) -> None:
                state["filter"] = value
                state["page"] = 1
                _render_filter()
                card_list.refresh()

            def _go_page(page: int) -> None:
                state["page"] = page
                _render_filter()
                card_list.refresh()

            _render_filter()

            if not total:
                with ui.card().classes("w-full p-6"):
                    ui.icon("inventory_2", size="3rem").classes("text-gray-300 mx-auto")
                    ui.label("暂无卡片数据").classes("text-gray-500 text-center")
                return

            @ui.refreshable
            def card_list() -> None:
                items = _filtered()
                page = state["page"]
                start = (page - 1) * _PAGE_SIZE
                for kind, card in items[start : start + _PAGE_SIZE]:
                    if kind == "product":
                        product_card(card)
                    elif kind == "inquiry":
                        inquiry_card(card)
                    else:
                        generic_card(card)

            card_list()
