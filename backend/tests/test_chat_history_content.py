"""Session content schema: turn records survive saves, old sessions still load."""

import unittest

from backend.app.shared.agent.chat_history_content import (
    SCHEMA_VERSION,
    ChatHistoryPin,
    ChatHistoryScope,
    ChatHistorySource,
    assistant_turn_payload,
    build_history_content,
    history_messages,
    load_history_content,
)


class ChatHistoryContentTestCase(unittest.TestCase):
    def test_tool_records_survive_a_save_round_trip(self) -> None:
        turn = assistant_turn_payload({
            "text": "理由",
            "toolCalls": [{
                "callId": "c1", "name": "reply_customer", "arguments": {"reply": "hi"},
                "ok": True, "content": {"recorded": True}, "error": None,
                "durationMs": 3.5, "round": 1,
            }],
            "intents": [{
                "intentId": "i1", "kind": "reply_customer", "status": "requested",
                "reply": "hi", "zh": "你好", "tone": "formal",
            }],
            "usage": {"totalTokens": 12},
            "iterations": 2,
            "durationMs": 40.0,
            "restricted": True,
            "batchId": "b1",
        })
        content = build_history_content(
            apid="a", agent_name="A",
            messages=[{"role": "user", "content": "ask"}, turn],
        )

        reloaded = load_history_content(content)
        first, second = reloaded.messages
        self.assertEqual(first.toolCalls, [])
        self.assertEqual(second.content, "理由")
        self.assertEqual(second.toolCalls[0].callId, "c1")
        self.assertEqual(second.toolCalls[0].arguments, {"reply": "hi"})
        self.assertEqual(second.intents[0].intentId, "i1")
        self.assertEqual(second.usage.totalTokens, 12)
        self.assertTrue(second.restricted)
        self.assertEqual(second.batchId, "b1")
        self.assertEqual(content["schema_version"], SCHEMA_VERSION)

    def test_legacy_session_without_tool_fields_loads_as_empty(self) -> None:
        legacy = {
            "schema_version": 2,
            "apid": "a",
            "agent_name": "A",
            "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        }

        loaded = load_history_content(legacy)

        self.assertEqual([message.content for message in loaded.messages], ["hi", "hello"])
        self.assertTrue(all(message.toolCalls == [] and message.intents == [] for message in loaded.messages))
        self.assertIsNone(loaded.scope)
        self.assertIsNone(loaded.pinned)
        self.assertEqual(loaded.session_type, "test")

    def test_scope_pin_and_source_are_preserved_across_edits(self) -> None:
        scope = ChatHistoryScope(seller="s", data_dir="D:/d")
        pin = ChatHistoryPin(kind="draft", prompt="p", llm_level=1, tools=["t"], requires_tool=True)
        source = ChatHistorySource(message_index=2, frozen_input="frozen", conversation_id=5)
        raw = build_history_content(
            apid="a", agent_name="A", messages=[], scope=scope, pinned=pin,
            source=source, session_type="fork", parent_id="12",
        )

        # A later save that only appends messages keeps ownership and origin.
        edited = build_history_content(
            apid="a", agent_name="A", messages=[{"role": "user", "content": "x"}], existing=raw,
        )

        self.assertEqual(edited["scope"]["seller"], "s")
        self.assertEqual(edited["pinned"]["prompt"], "p")
        self.assertTrue(edited["pinned"]["requires_tool"])
        self.assertEqual(edited["source"]["frozen_input"], "frozen")
        self.assertEqual(edited["session_type"], "fork")
        self.assertEqual(edited["parent_id"], "12")

    def test_unknown_message_fields_do_not_break_loading(self) -> None:
        raw = {
            "apid": "a",
            "messages": [{"role": "assistant", "content": "x", "toolCalls": [{"name": "t", "extra": 1}]}],
        }

        loaded = load_history_content(raw)

        self.assertEqual(history_messages(loaded)[0]["toolCalls"][0]["name"], "t")

    def test_a_missing_draft_argument_keeps_the_stored_snapshot(self) -> None:
        created = build_history_content(
            apid="a", agent_name="A", messages=[], draft=None,
        )
        self.assertIsNone(created["draft"])

        kept = build_history_content(
            apid="a", agent_name="A", messages=[],
            existing={"draft": {"prompt": "old", "llm_level": 0, "tools": []}},
            draft=None,
        )
        self.assertEqual(kept["draft"]["prompt"], "old")


if __name__ == "__main__":
    unittest.main()
