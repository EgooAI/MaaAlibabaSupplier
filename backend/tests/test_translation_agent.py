"""Translation agent layer: prompt input shape, short hashes and the three-value protocol."""

import hashlib
import json
import re

import pytest

from backend.app.shared.agent import translation as translation_mod
from backend.app.shared.agent.inputs import build_translation_input
from backend.app.shared.agent.output_normalizers import parse_translation_payload
from backend.app.shared.agent.translation import (
    ABNORMAL_MESSAGE,
    NO_NEED_TO_TRANSLATE,
    SHORT_HASH_LENGTH,
    TranslationOutcome,
    assign_short_hashes,
    translate_texts_to_crm,
)
from backend.app.shared.crm.sdk import TranslateManager
from backend.app.shared.crm.translation_cache import get_translation, translation_cached


def _items_payload(rendered: str) -> dict:
    match = re.search(r"```json\n(.*?)\n```", rendered, flags=re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


class TestShortHash:
    def test_assign_short_hashes_is_unique_and_covers_all_texts(self):
        texts = [f"msg-{index}" for index in range(200)]
        mapping = assign_short_hashes(texts)
        assert set(mapping) == set(texts)
        assert len(set(mapping.values())) == len(texts)
        assert all(len(value) <= SHORT_HASH_LENGTH for value in mapping.values())
        assert assign_short_hashes(texts) == mapping

    def test_colliding_short_hashes_do_not_cross_contaminate_rows(self, monkeypatch):
        # 两文本短哈希冲突被盐化重导后，落库仍按各自 32 位 md5 主键互不串扰。
        def fake(text, *, salt=0):
            return "aaaaa" if salt == 0 else f"b{salt:04d}"

        monkeypatch.setattr(translation_mod, "short_text_hash", fake)
        assert assign_short_hashes(["x", "y"]) == {"x": "aaaaa", "y": "b0001"}

        def fake_runner(apid, user_input):
            return json.dumps({"translations": {"aaaaa": "甲", "b0001": "乙"}}, ensure_ascii=False)

        monkeypatch.setattr(translation_mod, "run_chat_tool_agent", fake_runner)
        assert translate_texts_to_crm(["x", "y"]).saved == 2
        assert get_translation("x") == "甲"
        assert get_translation("y") == "乙"


class TestBuildTranslationInput:
    ROWS = [
        ("2026-09-18 10:00", "买家", "Hola, ¿precio?"),
        ("2026-09-18 10:01", "商家(我)", "你好，请看 <b>报价单</b><br>谢谢"),
        ("2026-09-18 10:02", "系统", "Auto reception text"),
        ("2026-09-18 10:03", "买家", "已经翻译过的消息"),
    ]

    def test_full_history_context_with_hash_markers_for_any_speaker(self):
        annotate = assign_short_hashes(["Hola, ¿precio?", "Auto reception text"])
        items = [{"text_hash": annotate["Hola, ¿precio?"], "text": "Hola, ¿precio?"}]
        rendered = build_translation_input(items, conversation=self.ROWS, annotate=annotate)
        # 已翻译行仍纳入上下文，且整行不带 text_hash 标记。
        assert "[2026-09-18 10:03] 买家: 已经翻译过的消息" in rendered
        # 任意一方（买家/系统）的待翻译行均带短哈希标记。
        assert f"买家: text_hash={annotate['Hola, ¿precio?']}: Hola, ¿precio?" in rendered
        assert f"系统: text_hash={annotate['Auto reception text']}: Auto reception text" in rendered
        # 上下文保留原始 HTML。
        assert "你好，请看 <b>报价单</b><br>谢谢" in rendered

        assert rendered.index("对话记录：") < rendered.index("翻译规则：") < rendered.index("待翻译条目：")
        assert NO_NEED_TO_TRANSLATE in rendered and ABNORMAL_MESSAGE in rendered
        assert _items_payload(rendered) == {"items": items}

    def test_without_conversation_renders_rules_and_items_only(self):
        annotate = assign_short_hashes(["hello"])
        rendered = build_translation_input([{"text_hash": annotate["hello"], "text": "hello"}])
        # 规则文本会提到"对话记录"，但不含对话记录章节（带冒号分隔头）。
        assert "对话记录：" not in rendered
        assert rendered.startswith("翻译规则")

    def test_empty_items_render_nothing(self):
        assert build_translation_input([], conversation=self.ROWS) == ""


class TestTranslateTextsToCrm:
    @pytest.fixture
    def agent_output(self, monkeypatch):
        captured: dict = {}

        def fake_runner(apid, user_input):
            captured["apid"] = apid
            captured["input"] = user_input
            return json.dumps({"translations": captured.pop("reply", {})}, ensure_ascii=False)

        monkeypatch.setattr(translation_mod, "run_chat_tool_agent", fake_runner)
        return captured

    @pytest.mark.parametrize("reply", [NO_NEED_TO_TRANSLATE, None], ids=["no-need", "null"])
    def test_no_need_to_translate_is_cached_as_empty_sentinel(self, agent_output, reply):
        mapping = assign_short_hashes(["已是中文"])
        agent_output["reply"] = {mapping["已是中文"]: reply}
        assert translate_texts_to_crm(["已是中文"], annotate=mapping) == TranslationOutcome(saved=1, omitted=0)
        # 缓存命中（不再重复提交），查询返回空串哨兵而非 null。
        assert translation_cached("已是中文")
        assert get_translation("已是中文") == ""

    def test_abnormal_message_is_not_cached(self, agent_output):
        mapping = assign_short_hashes(["[[占位符]]"])
        agent_output["reply"] = {mapping["[[占位符]]"]: ABNORMAL_MESSAGE}
        assert translate_texts_to_crm(["[[占位符]]"], annotate=mapping) == TranslationOutcome(saved=0, omitted=0)
        assert not translation_cached("[[占位符]]")
        assert get_translation("[[占位符]]") is None

    def test_constants_are_case_insensitive(self, agent_output):
        mapping = assign_short_hashes(["x", "y"])
        agent_output["reply"] = {mapping["x"]: " no_need_to_translate ", mapping["y"]: " abnormal_message "}
        assert translate_texts_to_crm(["x", "y"], annotate=mapping) == TranslationOutcome(saved=1, omitted=0)
        assert translation_cached("x")
        assert not translation_cached("y")

    def test_cached_texts_skip_llm_unless_force(self, agent_output):
        mapping = assign_short_hashes(["hello"])
        agent_output["reply"] = {mapping["hello"]: "你好"}
        assert translate_texts_to_crm(["hello"], annotate=mapping) == TranslationOutcome(saved=1, omitted=0)
        # The stored key must remain full MD5, independent of the prompt's short hash.
        record = TranslateManager().get_translate(hashlib.md5(b"hello").hexdigest())
        assert record is not None and record.translation == "你好"
        agent_output.pop("input", None)
        # Second call without force: cached, no LLM call at all.
        assert translate_texts_to_crm(["hello"], annotate=mapping) == TranslationOutcome(saved=0, omitted=0)
        assert "input" not in agent_output
        # Force retranslates even though cached.
        agent_output["reply"] = {mapping["hello"]: "你好（重译）"}
        assert translate_texts_to_crm(["hello"], force=True, annotate=mapping) == TranslationOutcome(saved=1, omitted=0)
        assert get_translation("hello") == "你好（重译）"

    def test_omitted_key_is_reported_as_protocol_violation(self, agent_output):
        mapping = assign_short_hashes(["hello", "world"])
        agent_output["reply"] = {mapping["hello"]: "你好"}
        assert translate_texts_to_crm(["hello", "world"], annotate=mapping) == TranslationOutcome(saved=1, omitted=1)
        assert get_translation("world") is None

    def test_context_lines_carry_job_level_short_hashes(self, agent_output):
        rows = [("10:00", "买家", "Hola"), ("10:01", "买家", "Bonjour")]
        annotate = assign_short_hashes(["Hola", "Bonjour"])
        translate_texts_to_crm(["Hola"], conversation=rows, annotate=annotate)
        # 该 chunk 只译 Hola，但上下文里 Bonjour 行也带 job 级短哈希（前缀稳定）。
        assert f"买家: text_hash={annotate['Bonjour']}: Bonjour" in agent_output["input"]
        assert _items_payload(agent_output["input"]) == {
            "items": [{"text_hash": annotate["Hola"], "text": "Hola"}]
        }

    def test_derives_its_own_hashes_when_annotate_missing(self, agent_output):
        agent_output["reply"] = {}
        translate_texts_to_crm(["hello"], conversation=[("10:00", "买家", "hello")])
        mapping = assign_short_hashes(["hello"])
        assert f"买家: text_hash={mapping['hello']}: hello" in agent_output["input"]


class TestParseTranslationPayload:
    def test_constants_and_null_pass_through(self):
        parsed = parse_translation_payload(
            json.dumps({"translations": {"a": NO_NEED_TO_TRANSLATE, "b": None, "c": " 译文 "}})
        )
        assert parsed == {"a": NO_NEED_TO_TRANSLATE, "b": None, "c": "译文"}

    def test_strips_json_fence(self):
        fenced = "```json\n{\"translations\": {\"a\": \"甲\"}}\n```"
        assert parse_translation_payload(fenced) == {"a": "甲"}

    @pytest.mark.parametrize(
        "raw_text",
        [
            "不是 JSON",
            json.dumps(["not", "an", "object"]),
            json.dumps({"translations": ["not", "a", "dict"]}),
            json.dumps({"suggestions": {}}),
        ],
    )
    def test_invalid_payloads_raise_value_error(self, raw_text):
        with pytest.raises(ValueError):
            parse_translation_payload(raw_text)
