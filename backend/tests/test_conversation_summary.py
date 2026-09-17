import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend.app.api.routers.conversations import (
    _assemble_customer_view,
    _build_aggregate,
    _build_summary,
    _minimal_customer_view,
    _preload_conversation_maps,
)
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm.identities import self_sender_id
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.mitm.pool import SelfInfo, UserInfo

SELF_ALI_ID = "10001"
CONTACT_ALI_ID = "20002"


def _row(mid: str, sender_ali: str | None, text: str, **kwargs) -> MessageRow:
    params: dict = {
        "table_name": "msg_table",
        "cid": f"{SELF_ALI_ID}-{CONTACT_ALI_ID}",
        "mid": mid,
        "sender_id": self_sender_id(sender_ali) if sender_ali else None,
        "created_at": 1756720000,
        "user_content_type": 0,
        "content_label": text,
        "content": text.encode("utf-8"),
    }
    params.update(kwargs)
    return MessageRow(**params)


class ConversationSummaryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "crm.sqlite"
        self.pools_path = Path(self.temp_dir.name) / "pools.db"
        # Router-level helpers build their own default-path adapter; point it here.
        self.enterContext(mock.patch.dict(os.environ, {
            "MAA_POOLS_DB_PATH": str(self.pools_path),
            "MAA_CRM_DB_PATH": str(self.db_path),
        }))
        self.adapter = CRMAdapter(database_path=self.db_path)
        self.self_info = SelfInfo(ali_id=SELF_ALI_ID, login_id="seller")
        from backend.app.shared.mitm.pool import get_user_info_pool

        get_user_info_pool().put(
            UserInfo(ali_id=CONTACT_ALI_ID, login_id="buyer", first_name="Buy", last_name="Er")
        )
        conv = ContactConv(
            contact_ali_id=CONTACT_ALI_ID,
            messages=[
                _row("m1", CONTACT_ALI_ID, "hello, quote please"),
                _row("m2", SELF_ALI_ID, "sure, best price", created_at=1756720001),
                _row("m3", None, "system notice", is_system=True, created_at=1756720002),
            ],
            last_created_at=1756720002,
            last_content_label="system notice",
        )
        self.adapter.sync_conversations([conv], self.self_info)
        self.convs = self.adapter.list_conversations(SELF_ALI_ID)
        self.assertEqual(len(self.convs), 1)
        self.digests = self.adapter.list_conversation_digests(SELF_ALI_ID)
        self.assertEqual(len(self.digests), 1)
        self.preloaded = _preload_conversation_maps(self.adapter, self.digests)

    def tearDown(self) -> None:
        self.adapter.engine.dispose()
        from backend.app.shared.mitm.pool import UserInfoPool

        UserInfoPool.reset_for_tests()
        self.temp_dir.cleanup()

    def test_digest_carries_latest_pointer_and_counts(self) -> None:
        digest = self.digests[0]
        conv = self.convs[0]
        self.assertEqual(digest.sid, conv.sid)
        self.assertEqual(digest.key, conv.key)
        self.assertEqual(digest.contact_ali_id, CONTACT_ALI_ID)
        self.assertEqual(digest.latest_content_label, "system notice")
        self.assertFalse(digest.latest_is_card)
        self.assertEqual(digest.dialogue_count, 2)

    def test_preload_resolves_contact_user_without_extra_queries(self) -> None:
        users = self.preloaded["users"]
        self.assertIn(CONTACT_ALI_ID, users)
        self.assertEqual(users[CONTACT_ALI_ID].login_id, "buyer")
        self.assertNotIn("unknown-contact", users)

    def test_summary_matches_aggregate_contract(self) -> None:
        conv = self.convs[0]
        full = _build_aggregate(self.adapter, SELF_ALI_ID, conv)
        summary = _build_summary(self.digests[0], self.preloaded)
        self.assertEqual(set(summary), set(full))
        for key in ("sid", "name", "participants", "latest", "unread_count",
                    "dialogue_count", "customer_view"):
            self.assertEqual(summary[key], full[key], key)
        for key in ("messages", "accounts", "customers", "account_mappings", "business_cards"):
            self.assertEqual(summary[key], [], key)
        self.assertEqual(summary["platforms"], [{"pid": "alibaba_icbu", "name": "Alibaba"}])

    def test_minimal_view_covers_full_key_set(self) -> None:
        conv = self.convs[0]
        full = _build_aggregate(self.adapter, SELF_ALI_ID, conv)
        assert full["customer_view"] is not None
        self.assertEqual(set(_minimal_customer_view("ghost")), set(full["customer_view"]))
        self.assertIsNone(_assemble_customer_view("ghost", [], [], None))

    def test_empty_conversation_list_preloads_nothing(self) -> None:
        preloaded = _preload_conversation_maps(self.adapter, [])
        self.assertEqual(preloaded, {"accounts": {}, "customers": {}, "users": {}})
        self.assertEqual(self.adapter.list_conversation_digests("nope"), [])


if __name__ == "__main__":
    unittest.main()
