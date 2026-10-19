"""The ``query_product`` read tool: look up cached product cards.

The pool is filled passively from intercepted ``fetchcard`` traffic, so it only
knows the products whose cards already arrived in this process. The tool says so
in its own description rather than promising a catalog, and it deliberately does
not filter by seller: the pool has no owner column, so every cached card is
visible to every run. That trade-off is intentional for now; adding an owner
column is what a real catalog would need.
"""

from __future__ import annotations

from typing import Annotated, Any

from agent_pipeline.tools import CATEGORY_READ, ToolSpec

TOOL_NAME = "query_product"
MAX_RESULTS = 5
COVERAGE_NOTE = (
    "只覆盖本机已收到的商品卡片，不是完整商品目录；没有结果不代表商品不存在。"
)


def _match(card, product_id: str, keywords: list[str]) -> bool:
    if product_id and product_id.lower() not in (card.product_id or "").lower():
        return False
    title = (card.title or "").lower()
    return all(keyword in title for keyword in keywords)


def query_product(
    product_id: Annotated[str, "商品的 product_id，可只写其中一段，用于精确匹配已缓存的商品卡片。"],
    keywords: Annotated[str, "可选：按商品标题筛选的关键词，多个词用空格分隔，需全部命中。"] = "",
) -> dict[str, Any]:
    """查询已缓存的商品卡片；只覆盖本机收到过卡片的商品。"""
    from backend.app.shared.mitm.pool import get_product_card_pool

    lookup = (product_id or "").strip()
    words = [word for word in (keywords or "").lower().split() if word]
    cards = get_product_card_pool().list_cards()
    matched = [card for card in cards if _match(card, lookup, words)]
    # Every summary stays small so the whole payload fits the executor's limit
    # even at MAX_RESULTS; the cap is part of the tool's contract.
    items = [
        {
            "product_id": card.product_id,
            "card_id": card.card_id,
            "title": card.title,
            "price": card.display_price or card.price,
            "moq": f"{card.moq}{card.moq_unit}",
            "expired": card.expired,
            "url": card.product_url,
        }
        for card in matched[:MAX_RESULTS]
    ]
    return {
        "total": len(matched),
        "returned": len(items),
        "items": items,
        "coverage": COVERAGE_NOTE,
    }


TOOL_SPEC = ToolSpec(
    name=TOOL_NAME,
    title="查询商品",
    description=(
        "按 product_id（可只写片段）以及可选标题关键词查询已缓存的商品卡片，"
        "返回标题、价格、MOQ 和链接。" + COVERAGE_NOTE
    ),
    category=CATEGORY_READ,
    repeatable=True,
    max_calls=3,
    timeout_seconds=5.0,
)

__all__ = [
    "COVERAGE_NOTE",
    "MAX_RESULTS",
    "TOOL_NAME",
    "TOOL_SPEC",
    "query_product",
]
