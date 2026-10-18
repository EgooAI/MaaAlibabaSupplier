"""Message → Markdown projection: card structure, HTML handling and speaker profiles."""

import json
import unittest
from unittest import mock

from backend.app.shared.crm.message_markdown import (
    AGENT_OPTIONS,
    EXPORT_OPTIONS,
    TRANSLATION_OPTIONS,
    card_markdown,
    html_to_text,
    transcript_lines,
    transcript_markdown,
)
from backend.app.shared.crm.views import CrmMessage, CrmResolver
from backend.app.shared.mitm.pool import ProductCard

SELF = "10001"


def _message(
    text: str,
    *,
    mid: str = "m1",
    sender: str = "20002@icbu",
    kind: int | None = 0,
    content: bytes | None = None,
    **kwargs,
) -> CrmMessage:
    return CrmMessage(
        table_name="msg_table",
        cid=f"{SELF}-20002",
        mid=mid,
        sender_id=sender,
        created_at=1756720000,
        user_content_type=kind,
        content_label=text,
        content=content if content is not None else (text or "").encode("utf-8"),
        **kwargs,
    )


def _card_blob(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class _ProductPool:
    def __init__(self, product: ProductCard | None) -> None:
        self._product = product

    def find_by_product_id(self, product_id: str) -> ProductCard | None:
        if self._product is None or not product_id:
            return None
        return self._product if product_id in self._product.product_id else None


class TestCardMarkdown(unittest.TestCase):
    def test_product_card_markdown_has_cover_summary_and_deduped_details(self) -> None:
        product = ProductCard(
            card_id="MDTEST1",
            title="Camping Wagon",
            price="$12.90",
            display_price="$12.90",
            product_image="https://img.example/w.png",
            moq="1",
            moq_unit="Unit",
            product_id="MDTEST1",
            product_url="https://detail.example/w.html",
        )
        message = _message("product card", kind=10010, content=_card_blob({"cardType": 3, "params": {"id": "MDTEST1"}}))
        with mock.patch("backend.app.shared.cards.get_product_card_pool", return_value=_ProductPool(product)):
            rendered = card_markdown(message)

        # 价格/MOQ 已在标题行；详情只补标题行没有的字段，避免同一事实出现两次。
        self.assertEqual(
            rendered,
            "**Camping Wagon**：$12.90 · MOQ 1Unit\n"
            "![Camping Wagon](https://img.example/w.png)\n"
            "- 商品 ID：MDTEST1\n"
            "- 链接：https://detail.example/w.html",
        )

    def test_product_card_without_pool_keeps_the_ids(self) -> None:
        message = _message("product card", kind=10010, content=_card_blob({"cardType": 3, "params": {"id": "MDTEST9"}}))
        with mock.patch("backend.app.shared.cards.get_product_card_pool", return_value=_ProductPool(None)):
            rendered = card_markdown(message)
        self.assertEqual(rendered, "**产品卡片**：商品 ID：MDTEST9")

    def test_file_card_markdown_links_the_download(self) -> None:
        message = _message(
            "产品目录.pdf",
            kind=10010,
            content=_card_blob({
                "cardType": 12,
                "params": {
                    "name": "产品目录.pdf",
                    "extensionType": "pdf",
                    "size": "1258291",
                    "downloadUrl": "https://download.example/f.pdf",
                },
            }),
        )
        rendered = card_markdown(message)
        self.assertIn("**产品目录.pdf**：PDF · 1.2 MB", rendered)
        self.assertIn("- [下载文件](https://download.example/f.pdf)", rendered)
        # 大小已在标题行，不再重复成一条详情。
        self.assertNotIn("- 大小：", rendered)

    def test_unparsable_card_falls_back_to_the_display_label(self) -> None:
        self.assertEqual(card_markdown(_message("产品卡片", kind=10010, content=b"not a card")), "产品卡片")
        self.assertEqual(card_markdown(_message("", kind=10010, content=b"")), "[卡片]")


class TestHtmlToText(unittest.TestCase):
    def test_keeps_line_breaks_and_drops_markup(self) -> None:
        self.assertEqual(html_to_text("你好<br>谢谢"), "你好\n谢谢")
        self.assertEqual(html_to_text("<p>第一段</p><p>第二段</p>"), "第一段\n\n第二段")
        self.assertEqual(html_to_text("<b>看</b>&amp;<i>这</i>"), "看&这")
        self.assertEqual(html_to_text("a   b\n\n\nc"), "a b\n\nc")
        self.assertEqual(html_to_text(None), "")


class TestProfiles(unittest.TestCase):
    def test_translation_profile_keeps_bodies_verbatim(self) -> None:
        resolver = CrmResolver(SELF)
        card = _message("product card", mid="m2", kind=10010, content=_card_blob({"cardType": 3, "params": {"id": "MDTEST1"}}))
        markup = _message("你好<br>谢谢", mid="m3")
        with mock.patch("backend.app.shared.cards.get_product_card_pool", return_value=_ProductPool(None)):
            rows = transcript_lines([card, markup], resolver=resolver, options=TRANSLATION_OPTIONS)
        # 翻译用原文查 annotate 里的 text_hash，正文必须与 message_display_text 一致。
        self.assertEqual([row.body for row in rows], ["product card", "你好<br>谢谢"])

        agent_rows = transcript_lines([card, markup], resolver=resolver, options=AGENT_OPTIONS)
        self.assertIn("**", agent_rows[0].body)
        self.assertEqual(agent_rows[1].body, "你好\n谢谢")

    def test_speaker_vocabulary_depends_on_the_profile(self) -> None:
        resolver = CrmResolver(SELF)
        messages = [
            _message("hi"),
            _message("yo", mid="m2", sender=f"{SELF}@icbu"),
            _message("auto", mid="m3", sender=f"{SELF}@icbu", is_auto_reply=True),
            _message("sys", mid="m4", is_system=True),
        ]
        agent = [row.speaker for row in transcript_lines(messages, resolver=resolver, options=AGENT_OPTIONS)]
        export = [
            row.speaker
            for row in transcript_lines(
                messages, resolver=resolver, options=EXPORT_OPTIONS, display_name="张三"
            )
        ]
        self.assertEqual(agent, ["买家", "商家(我)", "商家(我)", "系统"])
        self.assertEqual(export, ["张三", "我", "我的机器人", "系统"])

    def test_transcript_markdown_labels_each_message(self) -> None:
        rendered = transcript_markdown(
            [_message("hello"), _message("hi", mid="m2", sender=f"{SELF}@icbu")],
            resolver=CrmResolver(SELF),
            options=AGENT_OPTIONS,
        )
        self.assertIn("] 买家**\n\nhello", rendered)
        self.assertIn("] 商家(我)**\n\nhi", rendered)
        self.assertEqual(rendered.count("**"), 4)

    def test_limit_keeps_the_latest_messages(self) -> None:
        messages = [_message(f"m{index}", mid=f"m{index}") for index in range(5)]
        rows = transcript_lines(messages, resolver=CrmResolver(SELF), options=AGENT_OPTIONS, limit=2)
        self.assertEqual([row.body for row in rows], ["m3", "m4"])


if __name__ == "__main__":
    unittest.main()
