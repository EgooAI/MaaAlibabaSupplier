"""Host tool runs: records, intents, dispatcher policy and the reply tool's validation."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agent_pipeline import (
    AgentPipeline,
    AgentPipelineInput,
    AgentPipelineResultStatus,
    LLMConfig,
    LLMResponse,
    LLMToolCall,
    ToolContext,
    llm_registry,
    register_llm,
    register_tool,
    tool_registry,
)
from agent_pipeline.llm import StaticLLMClient

from backend.app.shared.agent.effect_dispatch import IntentDispatcher
from backend.app.shared.agent.reply_tool import (
    INTENT_KIND,
    TOOL_NAME,
    TOOL_SPEC,
    ReplyScope,
    reply_customer,
)
from backend.app.shared.agent.runner import register_business_tools, run_chat_agent
from backend.app.shared.crm.intent_store import IntentStore
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager

APID = "agent-reply-tool-test"


def _has_intent_table(path: Path) -> bool:
    import sqlite3

    from contextlib import closing

    with closing(sqlite3.connect(path)) as conn:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_agent_intent'"
        ).fetchone())


class ReplyToolTestCase(unittest.TestCase):
    def _scope(self, prior: tuple[str, ...] = ()) -> ReplyScope:
        return ReplyScope(
            seller="seller-a", data_dir="D:/data", apid=APID, agent_name="建议",
            conversation_id=3, prior_replies=prior,
        )

    def _context(self, prior: tuple[str, ...] = ()) -> ToolContext:
        return ToolContext(mode="simulated", scope=self._scope(prior))

    def test_records_one_intent_with_its_scope_attached(self) -> None:
        context = self._context()
        result = reply_customer(context, reply="Hello", zh="你好", tone="friendly")

        self.assertTrue(result["recorded"])
        self.assertEqual(len(context.intents), 1)
        intent = context.intents[0]
        self.assertEqual(intent.kind, INTENT_KIND)
        self.assertEqual(intent.content["reply"], "Hello")
        self.assertEqual(intent.content["tone"], "friendly")
        self.assertIs(intent.content["scope"], context.scope)

    def test_missing_scope_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            reply_customer(ToolContext(mode="live"), reply="Hello", zh="你好")

    def test_empty_duplicate_repeat_prior_and_placeholder_replies_are_refused(self) -> None:
        context = self._context(prior=("Already sent",))
        cases = (
            ("   ", "空"),
            ("Already sent", "之前回复"),
            ("Hi {{name}}", "占位符"),
            ("Hi [product]", "占位符"),
        )
        for reply, label in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    reply_customer(context, reply=reply, zh=reply)

    def test_same_reply_twice_in_one_run_is_refused(self) -> None:
        context = self._context()
        reply_customer(context, reply="Hello", zh="你好")
        with self.assertRaises(ValueError):
            reply_customer(context, reply="Hello", zh="你好")

    def test_fourth_reply_is_refused(self) -> None:
        context = self._context()
        for index in range(3):
            reply_customer(context, reply=f"Reply {index}", zh=f"回复 {index}")
        with self.assertRaises(ValueError):
            reply_customer(context, reply="Reply 4", zh="回复 4")

    def test_invalid_tone_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            reply_customer(self._context(), reply="Hello", zh="你好", tone="angry")

    def test_schema_never_advertises_a_recipient(self) -> None:
        from agent_pipeline import build_tool_schema

        schema = build_tool_schema(reply_customer, TOOL_SPEC)
        self.assertEqual(sorted(schema["properties"]), ["reply", "tone", "zh"])
        self.assertEqual(schema["required"], ["reply", "zh"])


class PipelineIntentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {
            "MAA_CRM_DB_PATH": str(Path(self.temp_dir.name) / "crm.sqlite"),
        }))
        register_llm(2, LLMConfig(base_url="https://e/v1", api_key="k", model_name="m"))
        register_tool(TOOL_NAME, reply_customer, spec=TOOL_SPEC)

    def tearDown(self) -> None:
        tool_registry.clear()
        llm_registry.clear()

    def _preset(self) -> AgentPreset:
        return AgentPreset(
            apid=APID, name="建议", description="d", prompt="p", llm_level=2,
            tools=[TOOL_NAME], requires_tool=True,
        )

    def _run(self, context: ToolContext, replies: list[str]):
        client = StaticLLMClient([
            LLMResponse(text="", needs_tool=True, tool_call=LLMToolCall(
                name=TOOL_NAME, tool_input={"reply": reply, "zh": reply, "tone": "formal"},
                call_id=f"c{index}",
            ))
            for index, reply in enumerate(replies)
        ] + [LLMResponse(text="理由")])
        return AgentPipeline(llm_client=client).run(
            AgentPipelineInput(user_input="go", agent_preset=self._preset(), tool_context=context)
        )

    def test_three_replies_return_three_ordered_intents(self) -> None:
        context = ToolContext(mode="simulated", scope=ReplyScope(
            seller="s", data_dir="D:/d", apid=APID, agent_name="建议", conversation_id=1,
        ))
        result = self._run(context, ["one", "two", "three"])

        self.assertEqual(result.status, AgentPipelineResultStatus.COMPLETED)
        self.assertEqual([intent.content["reply"] for intent in result.intents], ["one", "two", "three"])
        self.assertEqual([intent.sequence for intent in result.intents], [1, 2, 3])
        self.assertEqual(
            [record.call_id for record in result.tool_call_records], ["c0", "c1", "c2"],
        )
        self.assertEqual(result.output_text, "理由")

    def test_dispatcher_simulation_writes_no_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "crm.sqlite"
            store = IntentStore(db_path)
            scope = ReplyScope(
                seller="s", data_dir=temp_dir, apid=APID, agent_name="建议", conversation_id=1,
            )
            context = ToolContext(mode="simulated", scope=scope)
            result = self._run(context, ["one", "two"])

            dispatched = IntentDispatcher(store).dispatch(
                result.intents, mode="simulated", batch_id="batch",
            )

            self.assertEqual([item.status for item in dispatched.intents], ["simulated", "simulated"])
            self.assertEqual([item.intent_id for item in dispatched.intents], [None, None])
            self.assertFalse(db_path.exists())


class RunnerIntegrationTestCase(unittest.TestCase):
    """Runs ``run_chat_agent`` with the real pipeline and a fake LLM request layer."""

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.enterContext(mock.patch.dict(os.environ, {
            "MAA_CRM_DB_PATH": str(Path(self.temp_dir.name) / "crm.sqlite"),
        }))
        register_business_tools()
        register_llm(0, LLMConfig(base_url="https://e/v1", api_key="k", model_name="m"))
        AgentPresetManager().upsert_agent_preset(AgentPreset(
            apid=APID, name="建议", description="d", prompt="p", llm_level=0,
            tools=[TOOL_NAME], requires_tool=True,
        ))

    def tearDown(self) -> None:
        tool_registry.clear()
        llm_registry.clear()
        AgentPresetManager().engine.dispose()
        self.temp_dir.cleanup()

    def _patch_client(self, responses: list[LLMResponse]):
        client = StaticLLMClient(responses)

        class _Factory:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def invoke(self, request):
                return client.invoke(request)

        return mock.patch("agent_pipeline.llm.OpenAICompatibleLLMClient", _Factory)

    def test_live_run_persists_requested_intents(self) -> None:
        scope = ReplyScope(
            seller="seller-x", data_dir=self.temp_dir.name, apid=APID,
            agent_name="建议", conversation_id=9,
        )
        responses = [
            LLMResponse(text="", needs_tool=True, tool_call=LLMToolCall(
                name=TOOL_NAME, tool_input={"reply": "Hello", "zh": "你好", "tone": "friendly"},
                call_id="c1",
            )),
            LLMResponse(text="理由"),
        ]
        with self._patch_client(responses):
            turn = run_chat_agent(
                APID, "go", tool_context=ToolContext(mode="live", scope=scope),
                mode="live", conversation_id=9,
            )

        self.assertEqual(len(turn.intents), 1)
        self.assertEqual(turn.intents[0]["status"], "requested")
        self.assertIsNotNone(turn.intents[0]["intentId"])
        self.assertNotIn("scope", turn.intents[0])
        rows = IntentStore().list_conversation(9, seller="seller-x", data_dir=self.temp_dir.name)
        self.assertEqual([row["status"] for row in rows], ["requested"])
        self.assertEqual(turn.duration_ms >= 0, True)

    def test_simulated_run_keeps_intents_out_of_the_database(self) -> None:
        scope = ReplyScope(
            seller="seller-x", data_dir=self.temp_dir.name, apid=APID,
            agent_name="建议", conversation_id=9,
        )
        responses = [
            LLMResponse(text="", needs_tool=True, tool_call=LLMToolCall(
                name=TOOL_NAME, tool_input={"reply": "Hello", "zh": "你好"}, call_id="c1",
            )),
            LLMResponse(text="理由"),
        ]
        with self._patch_client(responses):
            turn = run_chat_agent(
                APID, "go", tool_context=ToolContext(mode="simulated", scope=scope),
                mode="simulated",
            )

        self.assertEqual(turn.intents[0]["status"], "simulated")
        self.assertIsNone(turn.intents[0]["intentId"])
        # A simulated run never writes intent rows, even if the CRM file exists.
        rows = IntentStore().list_conversation(
            9, seller="seller-x", data_dir=self.temp_dir.name,
        )
        self.assertEqual(rows, [])
        self.assertFalse(
            (Path(self.temp_dir.name) / "crm.sqlite").exists()
            and _has_intent_table(Path(self.temp_dir.name) / "crm.sqlite")
        )


if __name__ == "__main__":
    unittest.main()
