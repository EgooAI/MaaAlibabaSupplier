import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm.identities import self_sender_id
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.crm.views import (
    CrmResolver,
    normalize_message_type,
    resolve_role,
)
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


class ConversationAggregateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "crm.sqlite"
        self.pools_path = Path(self.temp_dir.name) / "pools.db"
        os.environ["MAA_POOLS_DB_PATH"] = str(self.pools_path)
        self.adapter = CRMAdapter(database_path=self.db_path)
        self.self_info = SelfInfo(ali_id=SELF_ALI_ID, login_id="seller")
        self.contact_info = UserInfo(ali_id=CONTACT_ALI_ID, login_id="buyer", first_name="Buy", last_name="Er")
        from backend.app.shared.mitm.pool import get_user_info_pool

        get_user_info_pool().put(self.contact_info)
        conv = ContactConv(
            contact_ali_id=CONTACT_ALI_ID,
            messages=[
                _row("m1", CONTACT_ALI_ID, "hello, quote please"),
                _row("m2", SELF_ALI_ID, "sure, best price"),
                _row("m3", None, "system notice", is_system=True),
                _row("m4", CONTACT_ALI_ID, "product card", user_content_type=10010,
                     content=b'{"cardType":3,"params":{"id":"P123"}}'),
            ],
            last_created_at=1756720000,
            last_content_label="product card",
        )
        self.adapter.sync_conversations([conv], self.self_info)

    def tearDown(self) -> None:
        self.adapter.engine.dispose()
        self.temp_dir.cleanup()
        os.environ.pop("MAA_POOLS_DB_PATH", None)

    def test_list_transmits_sid_and_contact(self) -> None:
        convs = self.adapter.list_conversations(SELF_ALI_ID)
        self.assertEqual(len(convs), 1)
        conv = convs[0]
        self.assertGreater(conv.sid, 0)
        self.assertEqual(conv.contact_ali_id, CONTACT_ALI_ID)
        self.assertTrue(conv.key.endswith(CONTACT_ALI_ID))
        self.assertEqual(len(conv.participants), 2)
        self.assertEqual(len(conv.messages), 4)

    def test_detail_roundtrip_and_guards(self) -> None:
        conv = self.adapter.list_conversations(SELF_ALI_ID)[0]
        detail = self.adapter.get_conversation_detail(SELF_ALI_ID, conv.sid)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.contact_ali_id, CONTACT_ALI_ID)
        self.assertIsNone(self.adapter.get_conversation_detail(SELF_ALI_ID, 999999))
        self.assertIsNone(self.adapter.get_conversation_detail("nope", conv.sid))
        self.assertEqual(self.adapter.list_conversations(""), [])

    def test_roles_and_types(self) -> None:
        conv = self.adapter.list_conversations(SELF_ALI_ID)[0]
        resolver = CrmResolver(SELF_ALI_ID)
        self.assertEqual(
            [(resolve_role(m, resolver), normalize_message_type(m)) for m in conv.messages],
            [("buyer", "text"), ("seller", "text"), ("system", "system"), ("card", "card")],
        )

    def test_sender_aids_and_external_mid(self) -> None:
        messages = self.adapter.messages.list_message()
        self.assertEqual(len(messages), 4)
        senders = {message.sender for message in messages}
        self.assertEqual(len(senders), 2)
        for message in messages:
            self.assertTrue(message.external_mid.startswith("msg_table:"))
        self_aid = self.adapter._account_by_mapping("ali_id", SELF_ALI_ID).aid
        by_mid = {message.external_mid: message.sender for message in messages}
        self.assertEqual(by_mid["msg_table:m2"], self_aid)

    def test_mapping_dedup_across_ids(self) -> None:
        self.adapter.upsert_user_info(
            UserInfo(ali_id=CONTACT_ALI_ID, login_id="buyer-new", first_name="Buy", last_name="Er")
        )
        self.assertIsNotNone(self.adapter.get_user_info(CONTACT_ALI_ID))
        self.assertIsNotNone(self.adapter.get_user_info("buyer-new"))


if __name__ == "__main__":
    unittest.main()
