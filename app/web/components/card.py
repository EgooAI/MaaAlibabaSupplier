"""Shared card UI components for product, generic, and inquiry cards."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from app.shared.mitm.pool import GenericCard, InquiryCard, ProductCard

# ---------------------------------------------------------------------------
# Card type metadata (used by generic_card and other views)
# ---------------------------------------------------------------------------

_CARD_TYPE_NAMES = {
    1: "资质认证",
    3: "产品卡片",
    6: "询盘/反馈",
    8: "未知",
    9: "订单/交易",
    12: "文件附件",
    20: "未知",
    23: "资质认证",
    2000: "产品批量",
    2008: "商品目录",
    2028: "报价",
    2086: "关注提醒",
    2098: "报价提醒",
    2106: "关注提醒",
}

_CARD_TYPE_COLORS = {
    1: "blue",
    6: "orange",
    9: "green",
    12: "purple",
    2000: "teal",
    2008: "cyan",
    2028: "red",
    2098: "pink",
    2106: "amber",
}


def card_type_name(card_type: int) -> str:
    return _CARD_TYPE_NAMES.get(card_type, f"类型{card_type}")


# ---------------------------------------------------------------------------
# Product card
# ---------------------------------------------------------------------------


def product_card(card: ProductCard, *, extra_classes: str = "") -> None:
    """Render a product card as a NiceGUI card component."""
    with ui.card().classes(f"w-full p-4 {extra_classes}".strip()):
        with ui.row().classes("items-start gap-4"):
            if card.product_image:
                ui.image(card.product_image).classes("w-32 h-32 rounded-lg object-cover")
            else:
                ui.icon("image", size="4rem").classes("text-gray-300")

            with ui.column().classes("flex-grow gap-1"):
                ui.label(card.title or "—").classes("text-sm font-bold")

                with ui.row().classes("items-center gap-3"):
                    price = card.display_price or card.price or "—"
                    ui.label(price).classes("text-base font-semibold text-red-600")

                    if card.moq and card.moq != "0":
                        unit = f" {card.moq_unit}" if card.moq_unit else ""
                        ui.label(f"MOQ: {card.moq}{unit}").classes("text-xs text-gray-500")

                    if card.expired:
                        ui.badge("已过期", color="negative").props("outline")

                if card.product_url:
                    ui.link("查看产品", card.product_url, new_tab=True).classes(
                        "text-xs text-blue-500"
                    )

                ui.label(f"ID: {card.product_id}").classes("text-xs text-gray-400")


# ---------------------------------------------------------------------------
# Generic card
# ---------------------------------------------------------------------------


def generic_card(gc: GenericCard, *, extra_classes: str = "") -> None:
    """Render a generic card as a compact NiceGUI card component."""
    type_label = card_type_name(gc.card_type)
    color = _CARD_TYPE_COLORS.get(gc.card_type, "grey")

    # Parse key fields from raw JSON
    preview_fields: dict[str, str] = {}
    try:
        obj = json.loads(gc.raw_json)
        params = obj.get("params") or {}
        if isinstance(params, dict):
            for k in ("title", "name", "from", "to", "encryFeedbackId", "orderId", "catalogId", "quoId"):
                v = params.get(k)
                if v:
                    preview_fields[k] = str(v)[:80]
    except (json.JSONDecodeError, TypeError):
        pass

    with ui.card().classes(f"w-full p-3 {extra_classes}".strip()):
        with ui.row().classes("items-center gap-3"):
            ui.badge(type_label, color=color).props("outline")
            ui.label(gc.card_id[:40]).classes("text-xs text-gray-500 font-mono")
        if preview_fields:
            with ui.column().classes("gap-0 mt-1"):
                for k, v in preview_fields.items():
                    with ui.row().classes("items-center gap-1"):
                        ui.label(f"{k}:").classes("text-xs text-gray-400")
                        ui.label(v).classes("text-xs")


# ---------------------------------------------------------------------------
# Inquiry card
# ---------------------------------------------------------------------------


def inquiry_card(card: InquiryCard, *, extra_classes: str = "") -> None:
    """Render an inquiry card as a NiceGUI card component."""
    with ui.card().classes(f"w-full p-4 {extra_classes}".strip()):
        # Header
        with ui.row().classes("items-center gap-2 w-full"):
            ui.badge("询盘", color="orange").props("outline")
            if card.is_seller:
                ui.badge("收到", color="blue").props("outline")
            else:
                ui.badge("发送", color="green").props("outline")
            if card.attachment_count and card.attachment_count != "0":
                ui.badge(f"{card.attachment_count}个附件", color="grey").props("outline")

        # Inquiry content
        if card.inquiry_content:
            ui.label(card.inquiry_content).classes("text-sm mt-2")

        # Products
        for p in card.products:
            with ui.card().classes("w-full p-3 mt-2 bg-gray-50"):
                with ui.row().classes("items-start gap-3"):
                    if p.product_image:
                        ui.image(p.product_image).classes("w-20 h-20 rounded object-cover")
                    with ui.column().classes("flex-grow gap-1"):
                        ui.label(p.product_name or "—").classes("text-sm font-bold")
                        with ui.row().classes("items-center gap-3"):
                            price = p.product_unit_price or "—"
                            ui.label(price).classes("text-sm font-semibold text-red-600")
                            if p.discount_price:
                                ui.label(p.discount_price).classes("text-xs text-gray-400 line-through")
                        with ui.row().classes("items-center gap-2"):
                            if p.product_moq:
                                unit = f" {p.product_unit}" if p.product_unit else ""
                                ui.label(f"MOQ: {p.product_moq}{unit}").classes("text-xs text-gray-500")
                        if p.product_url:
                            ui.link("查看产品", p.product_url, new_tab=True).classes("text-xs text-blue-500")
