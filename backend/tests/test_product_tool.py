"""Host tools: the cached product lookup and the executor's result size limit."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from agent_pipeline import LLMConfig, ToolExecutor, ToolSpec, register_tool, tool_registry
from agent_pipeline.resolver import AgentPresetRuntimeConfig
from agent_pipeline.tools import DEFAULT_MAX_RESULT_CHARS
from models import AgentPreset

from backend.app.shared.agent.product_tool import (
    COVERAGE_NOTE,
    MAX_RESULTS,
    TOOL_NAME,
    query_product,
)
from backend.app.shared.mitm.pool import ProductCard


def _card(index: int, *, title: str = "", product_id: str = "", **extra) -> ProductCard:
    return ProductCard(
        card_id=f"card-{index}",
        product_id=product_id or f"16017{index:06d}",
        title=title or f"Product {index}",
        price="$10.00",
        display_price="$10.00-12.00",
        moq="1",
        moq_unit="Unit",
        product_url=extra.pop("product_url", f"https://example.test/p/{index}"),
        **extra,
    )


class QueryProductTestCase(unittest.TestCase):
    def _run(self, cards: list[ProductCard], **arguments):
        pool = mock.MagicMock()
        pool.list_cards.return_value = list(cards)
        with mock.patch(
            "backend.app.shared.mitm.pool.get_product_card_pool", return_value=pool,
        ):
            return query_product(**arguments)

    def test_matches_a_product_id_fragment(self) -> None:
        cards = [_card(1, title="Camping Wagon", product_id="1601713005360"), _card(2)]

        payload = self._run(cards, product_id="13005360")

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["title"], "Camping Wagon")
        self.assertEqual(payload["items"][0]["url"], "https://example.test/p/1")

    def test_every_keyword_must_appear_in_the_title(self) -> None:
        cards = [
            _card(1, title="Camping Wagon"),
            _card(2, title="Garden Wagon"),
            _card(3, title="Camping Chair"),
        ]

        self.assertEqual(self._run(cards, product_id="", keywords="camping chair")["total"], 1)
        self.assertEqual(self._run(cards, product_id="", keywords="camping wagon")["total"], 1)
        self.assertEqual(self._run(cards, product_id="", keywords="camping")["total"], 2)
        self.assertEqual(self._run(cards, product_id="", keywords="camping sofa")["total"], 0)
        self.assertEqual(
            [item["title"] for item in self._run(cards, product_id="", keywords="wagon")["items"]],
            ["Camping Wagon", "Garden Wagon"],
        )

    def test_results_are_capped_but_the_total_is_reported(self) -> None:
        cards = [_card(index, title=f"Bulk Item {index}") for index in range(MAX_RESULTS + 3)]

        payload = self._run(cards, product_id="", keywords="bulk")

        self.assertEqual(payload["total"], MAX_RESULTS + 3)
        self.assertEqual(payload["returned"], MAX_RESULTS)
        self.assertEqual(len(payload["items"]), MAX_RESULTS)

    def test_empty_pool_still_explains_its_coverage(self) -> None:
        payload = self._run([], product_id="missing")

        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["coverage"], COVERAGE_NOTE)

    def test_payload_stays_within_the_read_result_limit(self) -> None:
        cards = [
            _card(index, title="X" * 400, product_url="https://example.test/p/" + "y" * 200)
            for index in range(MAX_RESULTS)
        ]

        serialized = json.dumps(self._run(cards, product_id="", keywords=""), ensure_ascii=False)

        self.assertLess(len(serialized), DEFAULT_MAX_RESULT_CHARS)


class RegistrationTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        tool_registry.clear()

    def test_business_registration_exposes_the_product_tool(self) -> None:
        from backend.app.shared.agent.runner import register_business_tools

        registered = register_business_tools()

        self.assertIn(TOOL_NAME, registered)
        self.assertEqual(query_product.__name__, TOOL_NAME)
        spec = tool_registry.spec(TOOL_NAME)
        self.assertEqual(spec.category, "read")
        self.assertIn(COVERAGE_NOTE, spec.description)


class ReadResultLimitTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        tool_registry.clear()

    @staticmethod
    def _runtime(name: str, tool, spec: ToolSpec) -> AgentPresetRuntimeConfig:
        preset = AgentPreset(
            apid="limit-test", name="t", description="d", prompt="p", llm_level=2, tools=[name],
        )
        return AgentPresetRuntimeConfig(
            preset=preset,
            llm=LLMConfig(base_url="https://e/v1", api_key="k", model_name="m"),
            tools=[tool],
            tool_names=[name],
            tool_specs=[spec],
        )

    def test_a_long_text_result_is_cut_and_reports_its_original_size(self) -> None:
        def big() -> str:
            return "x" * 9000

        runtime = self._runtime("big", big, ToolSpec(name="big"))

        result, record = ToolExecutor().execute(runtime, "big", {})

        self.assertTrue(result.ok)
        self.assertIsInstance(result.content, str)
        self.assertLess(len(result.content), 9000)
        self.assertIn("结果已截断", result.content)
        self.assertEqual(record.original_chars, 9000)

    def test_an_oversized_structured_result_becomes_a_truncation_notice(self) -> None:
        payload = {"items": ["y" * 400 for _ in range(30)]}

        def big() -> dict:
            return payload

        runtime = self._runtime("big", big, ToolSpec(name="big"))

        result, record = ToolExecutor().execute(runtime, "big", {})

        self.assertEqual(result.content["truncated"], True)
        self.assertEqual(result.content["original_chars"], len(json.dumps(payload, ensure_ascii=False)))
        self.assertEqual(record.original_chars, result.content["original_chars"])

    def test_a_result_within_the_limit_is_untouched(self) -> None:
        def small() -> dict:
            return {"ok": True}

        runtime = self._runtime("small", small, ToolSpec(name="small"))

        result, record = ToolExecutor().execute(runtime, "small", {})

        self.assertEqual(result.content, {"ok": True})
        self.assertIsNone(record.original_chars)

    def test_effect_results_are_never_truncated(self) -> None:
        def effect() -> dict:
            return {"confirmation": "z" * 9000}

        runtime = self._runtime("effect", effect, ToolSpec(name="effect", category="effect"))

        result, record = ToolExecutor().execute(runtime, "effect", {})

        self.assertEqual(len(result.content["confirmation"]), 9000)
        self.assertIsNone(record.original_chars)


if __name__ == "__main__":
    unittest.main()
