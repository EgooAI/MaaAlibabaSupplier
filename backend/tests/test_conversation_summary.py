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
)
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm.identities import self_sender_id
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.crm.views import CrmConversationDigest
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
        self.adapter.sync_conversations([conv], self.self_info)
        self.conv = conversations_for(self.adapter, SELF_ALI_ID)[0]
        self.digest = CrmConversationDigest(
            contact_ali_id=self.conv.contact_ali_id,
            sid=self.conv.sid,
            key=self.conv.key,
            participants=self.conv.participants,
            latest_created_at=self.conv.last_created_at,
            latest_content_label=self.conv.last_content_label,
            latest_is_card=False,
            dialogue_count=sum(1 for message in self.conv.messages if not message.is_system),
        )
        accounts: dict = {}
        customers: dict = {}
        for aid in self.conv.participants:
            account = self.adapter.accounts.get_account(aid)
            if account is None:
                continue
            accounts[aid] = account
            customer = self.adapter.customers.get_customer(account.cid)
            if customer is not None:
                customers[account.cid] = customer
        self.preloaded = {
            "accounts": accounts, "customers": customers,
            "users": {CONTACT_ALI_ID: get_user_info_pool().get(CONTACT_ALI_ID)},
        }

    def tearDown(self) -> None:
        self.adapter.engine.dispose()
        from backend.app.shared.mitm.pool import UserInfoPool

        UserInfoPool.reset_for_tests()
        self.temp_dir.cleanup()

    def test_digest_carries_latest_pointer_and_counts(self) -> None:
        self.assertEqual(self.digest.sid, self.conv.sid)
        self.assertEqual(self.digest.key, self.conv.key)
        self.assertEqual(self.digest.contact_ali_id, CONTACT_ALI_ID)
        self.assertEqual(self.digest.latest_content_label, "system notice")
        self.assertFalse(self.digest.latest_is_card)
        self.assertEqual(self.digest.dialogue_count, 2)

    def test_summary_matches_aggregate_contract(self) -> None:
        full = _build_aggregate(self.adapter, SELF_ALI_ID, self.conv)
        summary = _build_summary(self.digest, self.preloaded)
        self.assertEqual(summary["customer_view"]["login_id"], "buyer")
        self.assertEqual(set(summary), set(full))
        for key in ("sid", "name", "participants", "latest", "unread_count",
                    "dialogue_count", "customer_view"):
            self.assertEqual(summary[key], full[key], key)
        for key in ("messages", "accounts", "customers", "account_mappings", "business_cards"):
            self.assertEqual(summary[key], [], key)
        self.assertEqual(summary["platforms"], [{"pid": "alibaba_icbu", "name": "Alibaba"}])

    def test_minimal_view_covers_full_key_set(self) -> None:
        full = _build_aggregate(self.adapter, SELF_ALI_ID, self.conv)
        assert full["customer_view"] is not None
        self.assertEqual(set(_minimal_customer_view("ghost")), set(full["customer_view"]))
        self.assertIsNone(_assemble_customer_view("ghost", [], [], None))


if __name__ == "__main__":
    unittest.main()
