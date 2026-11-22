import json
import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend.app.api.routers.conversations import (
    _build_aggregate,
    _build_summary,
)
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm.identities import self_sender_id
from backend.app.shared.crm.inbox_queries import query_inbox
from backend.app.shared.crm.inbox_store import InboxStore
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.mitm.pool import SelfInfo, UserInfo
from backend.tests.crm_helpers import conversations_for

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
        self.adapter.sync_conversations([conv], self.self_info, data_dir=self.temp_dir.name)
        self.conv = conversations_for(self.adapter, SELF_ALI_ID)[0]

    def tearDown(self) -> None:
        self.adapter.engine.dispose()
        from backend.app.shared.mitm.pool import UserInfoPool

        UserInfoPool.reset_for_tests()
        self.temp_dir.cleanup()

    def test_summary_matches_aggregate_contract(self) -> None:
        full = _build_aggregate(self.adapter, SELF_ALI_ID, self.conv)
        projection = query_inbox(InboxStore(self.db_path), SELF_ALI_ID, self.temp_dir.name, {})
        digest, = projection["digests"]
        summary = _build_summary(digest, projection["views"][digest.sid])
        self.assertEqual(summary["latest"]["content"], "system notice")
        self.assertEqual(summary["dialogue_count"], 2)
        self.assertEqual(summary["customer_view"]["login_id"], "buyer")
        self.assertEqual(set(summary), set(full))
        for key in ("sid", "name", "participants", "latest", "unread_count",
                    "dialogue_count", "customer_view"):
            self.assertEqual(summary[key], full[key], key)
        for key in ("messages", "accounts", "customers", "account_mappings", "business_cards"):
            self.assertEqual(summary[key], [], key)
        self.assertEqual(summary["platforms"], [{"pid": "alibaba_icbu", "name": "Alibaba"}])

    def test_unresolved_contact_does_not_inherit_seller_or_alias_profiles(self) -> None:
        original = _build_aggregate(self.adapter, SELF_ALI_ID, self.conv)["customer_view"]
        self.adapter.upsert_self_info(SelfInfo(
            ali_id=SELF_ALI_ID, first_name="SELLER", avatar_url="https://img.example/seller.png",
        ))
        seller_aid = self.conv.participants[0]
        for participants in ([], [seller_aid], [seller_aid, 999999]):
            with self.subTest(participants=participants), closing(sqlite3.connect(self.db_path)) as conn:
                conn.execute("UPDATE session_meta SET participants=? WHERE sid=?", (json.dumps(participants), self.conv.sid))
                conn.commit()
                conv = self.adapter.get_conversation_detail(SELF_ALI_ID, self.conv.sid)
                full = _build_aggregate(self.adapter, SELF_ALI_ID, conv)["customer_view"]
                summary = query_inbox(InboxStore(self.db_path), SELF_ALI_ID, self.temp_dir.name, {})["views"][conv.sid]
                self.assertEqual(full, summary)
                self.assertEqual(set(full), set(original))
                self.assertEqual(full["id"], CONTACT_ALI_ID)
                self.assertEqual(full["name"], CONTACT_ALI_ID)
                for field in ("cid", "ali_id", "login_id", "avatar_url"):
                    self.assertIsNone(full[field], field)
                self.assertFalse(full["portrait_editable"])


if __name__ == "__main__":
    unittest.main()
