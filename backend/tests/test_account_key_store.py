import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from Crypto.Cipher import AES

from backend.app.shared.backend import im_db_middleware as middleware_module

from backend.app.shared.crm.account_keys import (
    KEY_SOURCE_AUTO,
    KEY_SOURCE_MANUAL,
    AccountKeyStore,
    IMAccountKey,
    KeyFormatError,
    delete_key,
    get_key_hex,
    get_key_source,
    read_saved_key,
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

    def test_read_saved_key_is_readonly_and_distinguishes_corrupt_values(self):
        missing = Path(self.temp_dir.name) / "missing" / "crm.sqlite"
        self.assertIsNone(read_saved_key("10001", missing))
        self.assertFalse(missing.parent.exists())
        empty = Path(self.temp_dir.name) / "empty.sqlite"
        with closing(sqlite3.connect(empty)) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
        before = empty.read_bytes()
        self.assertIsNone(read_saved_key("10001", empty))
        self.assertEqual(empty.read_bytes(), before)
        self.assertIsNone(read_saved_key("10001", self.db_path))
        save_key("10001", bytes(16), "manual", self.db_path)
        self.assertEqual(read_saved_key("10001", self.db_path), (bytes(16), "manual"))
        for malformed in ("not-hex", "aa"):
            self.store.upsert_key(IMAccountKey(ali_id="10001", aes_key_hex=malformed))
            with self.assertRaises(KeyFormatError):
                read_saved_key("10001", self.db_path)
            self.assertEqual(self.store.get_key("10001").aes_key_hex, malformed)

    def test_read_saved_key_propagates_storage_lock_and_recovers(self):
        save_key("10001", bytes(16), "manual", self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(sqlite3.OperationalError):
                read_saved_key("10001", self.db_path)
        finally:
            conn.close()
        self.assertEqual(read_saved_key("10001", self.db_path), (bytes(16), "manual"))


class MiddlewareKeyRecoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.mw = middleware_module.IMDBMiddleware()
        self.mw.set_self_ali_id("10001")
        self.key = bytes(range(16))
        self.source = Path.cwd() / "synthetic-encrypted.sqlite"
        self.source.write_bytes(AES.new(self.key, AES.MODE_ECB).encrypt(b"SQLite format 3\x00"))
        self.capture = self.enterContext(patch.object(
            middleware_module, "retrieve_db_key", side_effect=ValueError("no client")
        ))

    def test_wrong_stored_key_is_recaptured_and_replaced(self):
        save_key("10001", bytes(16), "manual")
        self.capture.side_effect = None
        self.capture.return_value = self.key
        self.assertTrue(self.mw._ensure_key(self.source, "10001"))
        self.assertEqual(get_key_hex("10001"), self.key)
        self.assertEqual(self.mw.key_status(), (True, "live"))
        self.assertEqual(self.mw.key_validation_status(), "valid")

    def test_wrong_stored_key_still_exists_but_is_not_validated(self):
        save_key("10001", bytes(16), "manual")
        self.assertFalse(self.mw._ensure_key(self.source, "10001"))
        self.assertEqual(self.mw.key_status(), (True, "manual"))
        self.assertEqual(self.mw.key_validation_status(), "invalid")
        self.assertIsNone(self.mw._key)

    def test_unmatched_legacy_keys_are_preserved_without_migration(self):
        paths = [self.mw._cache_dir / "aes_key.bin", Path.cwd() / "data" / ".cache" / "aes_key.bin"]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(16))
        self.assertFalse(self.mw._ensure_key(self.source, "10001"))
        self.assertIsNone(get_key_hex("10001"))
        for path in paths:
            self.assertEqual(path.read_bytes(), bytes(16))

    def test_matching_legacy_key_migrates_without_deleting_file(self):
        legacy = self.mw._cache_dir / "aes_key.bin"
        legacy.write_bytes(self.key)
        self.assertTrue(self.mw._ensure_key(self.source, "10001"))
        self.assertEqual(get_key_hex("10001"), self.key)
        self.assertEqual(legacy.read_bytes(), self.key)
        self.capture.assert_not_called()

    def test_invalid_live_key_is_not_saved(self):
        self.capture.side_effect = None
        self.capture.return_value = bytes(16)
        self.assertFalse(self.mw._ensure_key(self.source, "10001"))
        self.assertIsNone(get_key_hex("10001"))
        self.assertEqual(self.mw.key_validation_status(), "invalid")

    def test_in_memory_key_is_revalidated_when_source_changes(self):
        save_key("10001", self.key, "manual")
        self.assertTrue(self.mw._ensure_key(self.source, "10001"))
        changed_key = b"new-source-key!!"
        self.source.write_bytes(AES.new(changed_key, AES.MODE_ECB).encrypt(b"SQLite format 3\x00"))
        self.capture.side_effect = None
        self.capture.return_value = changed_key
        self.assertTrue(self.mw._ensure_key(self.source, "10001"))
        self.assertEqual(get_key_hex("10001"), changed_key)


if __name__ == "__main__":
    unittest.main()
