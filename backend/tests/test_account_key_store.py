import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.shared.crm.account_keys import (
    KEY_SOURCE_AUTO,
    KEY_SOURCE_MANUAL,
    AccountKeyStore,
    IMAccountKey,
    delete_key,
    get_key_hex,
    get_key_source,
    save_key,
)


class AccountKeyStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "account_key.sqlite"
        self.store = AccountKeyStore(database_path=self.db_path)

    def tearDown(self) -> None:
        self.store.engine.dispose()
        self.temp_dir.cleanup()

    def test_auto_creates_im_account_key_table(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()

        self.assertIn("im_account_key", tables)

    def test_upsert_key_inserts_then_updates(self) -> None:
        self.store.upsert_key(IMAccountKey(ali_id="10001", aes_key_hex="ab" * 16, source=KEY_SOURCE_AUTO))
        stored = self.store.get_key("10001")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.aes_key_hex, "ab" * 16)
        self.assertEqual(stored.source, KEY_SOURCE_AUTO)

        self.store.upsert_key(IMAccountKey(ali_id="10001", aes_key_hex="cd" * 16, source=KEY_SOURCE_MANUAL))
        stored = self.store.get_key("10001")
        assert stored is not None
        self.assertEqual(stored.aes_key_hex, "cd" * 16)
        self.assertEqual(stored.source, KEY_SOURCE_MANUAL)

    def test_delete_key_removes_record(self) -> None:
        self.store.upsert_key(IMAccountKey(ali_id="10001", aes_key_hex="ab" * 16, source=KEY_SOURCE_AUTO))
        self.store.delete_key("10001")
        self.assertIsNone(self.store.get_key("10001"))
        self.assertIsNone(self.store.get_key("never-existed"))

    def test_list_keys_returns_all(self) -> None:
        self.store.upsert_key(IMAccountKey(ali_id="10001", aes_key_hex="ab" * 16, source=KEY_SOURCE_AUTO))
        self.store.upsert_key(IMAccountKey(ali_id="10002", aes_key_hex="cd" * 16, source=KEY_SOURCE_MANUAL))
        self.assertEqual([record.ali_id for record in self.store.list_keys()], ["10001", "10002"])

    def test_helpers_use_given_database(self) -> None:
        key = bytes(range(16))
        save_key("10001", key, KEY_SOURCE_MANUAL, database_path=self.db_path)
        self.assertEqual(get_key_hex("10001", database_path=self.db_path), key)
        self.assertEqual(get_key_source("10001", database_path=self.db_path), KEY_SOURCE_MANUAL)
        self.assertTrue(delete_key("10001", database_path=self.db_path))
        self.assertIsNone(get_key_hex("10001", database_path=self.db_path))
        self.assertFalse(delete_key("10001", database_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
