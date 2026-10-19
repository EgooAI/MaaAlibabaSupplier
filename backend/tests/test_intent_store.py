"""Reply-intent storage: legal edges, batch discard on acceptance, scope isolation."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend.app.shared.crm.intent_store import (
    IntentConflict,
    IntentStore,
    STATUS_ACCEPTED,
    STATUS_DISCARDED,
    STATUS_REQUESTED,
    STATUS_SIMULATED,
)


class IntentStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "crm.sqlite"
        self.enterContext(mock.patch.dict(os.environ, {"MAA_CRM_DB_PATH": str(self.db_path)}))
        self.store = IntentStore(self.db_path)
        self.scope = {"seller": "seller-a", "data_dir": self.temp_dir.name}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create(self, batch: str = "batch-1", conversation: int = 7, **overrides) -> dict:
        payload = {
            "apid": "agent-suggestion", "agent_name": "建议", "revision_id": None,
            "conversation_id": conversation, "batch_id": batch,
            "reply": "Hello", "zh": "你好", "tone": "formal",
        }
        payload.update(overrides)
        return self.store.create(**self.scope, **payload)

    def test_accepting_one_discards_the_rest_of_its_batch(self) -> None:
        first = self._create(reply="one")
        second = self._create(reply="two")
        other = self._create(batch="batch-2", reply="three")

        accepted = self.store.transition(first["id"], STATUS_ACCEPTED, **self.scope)

        self.assertEqual(accepted["status"], STATUS_ACCEPTED)
        self.assertEqual(self._status(second["id"]), STATUS_DISCARDED)
        self.assertEqual(self._status(other["id"]), STATUS_REQUESTED)

    def test_illegal_transition_is_rejected(self) -> None:
        intent = self._create()
        self.store.transition(intent["id"], STATUS_ACCEPTED, **self.scope)
        with self.assertRaises(IntentConflict):
            self.store.transition(intent["id"], STATUS_DISCARDED, **self.scope)
        with self.assertRaises(IntentConflict):
            self.store.transition(intent["id"], STATUS_SIMULATED, **self.scope)

    def test_scope_isolation_hides_and_rejects_other_accounts(self) -> None:
        intent = self._create()
        self.assertIsNone(self.store.get(intent["id"], seller="seller-b", data_dir=self.temp_dir.name))
        self.assertIsNone(self.store.get(intent["id"], seller="seller-a", data_dir=str(Path(self.temp_dir.name) / "other")))
        with self.assertRaises(IntentConflict):
            self.store.transition(intent["id"], STATUS_ACCEPTED, seller="seller-b", data_dir=self.temp_dir.name)
        self.assertEqual(self._status(intent["id"]), STATUS_REQUESTED)

    def test_simulated_intents_are_terminal_and_cannot_be_accepted(self) -> None:
        intent = self._create(status=STATUS_SIMULATED)
        with self.assertRaises(IntentConflict):
            self.store.transition(intent["id"], STATUS_ACCEPTED, **self.scope)
        self.assertEqual(self._status(intent["id"]), STATUS_SIMULATED)

    def test_conversation_listing_is_scoped_and_newest_first(self) -> None:
        self._create(reply="older")
        self._create(reply="newer")
        self._create(conversation=8, reply="elsewhere")
        rows = self.store.list_conversation(7, **self.scope)
        self.assertEqual([row["reply"] for row in rows], ["newer", "older"])

    def test_reads_do_not_create_the_table(self) -> None:
        missing = IntentStore(Path(self.temp_dir.name) / "missing.sqlite")
        self.assertIsNone(missing.get("nope", **self.scope))
        self.assertFalse((Path(self.temp_dir.name) / "missing.sqlite").exists())

    def _status(self, intent_id: str) -> str:
        row = self.store.get(intent_id, **self.scope)
        assert row is not None
        return row["status"]


if __name__ == "__main__":
    unittest.main()
