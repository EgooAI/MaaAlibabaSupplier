import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from Crypto.Cipher import AES
from fastapi.testclient import TestClient

from backend.app.api.main import app
from backend.app.api.routers import settings as settings_router
from backend.app.shared.backend import im_db_middleware as mw_mod
from backend.app.shared.crm import account_keys
from backend.app.shared.utils import app_config


def _make_layout(root: Path) -> Path:
    target = root / "AlibabaSupplierData" / "IMServiceDir" / "MessageSDK" / "10001@icbu" / "database"
    target.mkdir(parents=True, exist_ok=True)
    (target / "im.sqlite").write_bytes(b"\x00" * 32)
    return root / "AlibabaSupplierData"


def _make_account_db(layout: Path, ali_id: str, size: int = 64) -> Path:
    db_path = layout / "IMServiceDir" / "MessageSDK" / f"{ali_id}@icbu" / "database" / "im.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"\x00" * size)
    return db_path


def _encrypt_sqlite_header(key: bytes) -> bytes:
    return AES.new(key, AES.MODE_ECB).encrypt(b"SQLite format 3\x00")


class DataDirSettingsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "app_config.json"
        patcher = mock.patch.object(app_config, "config_file_path", return_value=self.config_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.addCleanup(self.temp_dir.cleanup)
        self._saved_instance = mw_mod.IMDBMiddleware._instance
        mw_mod.IMDBMiddleware._instance = None
        self.addCleanup(self._restore_singleton)

    def _restore_singleton(self) -> None:
        mw_mod.IMDBMiddleware._instance = self._saved_instance

    def _fresh_middleware(self) -> mw_mod.IMDBMiddleware:
        mw_mod.IMDBMiddleware._instance = None
        return mw_mod.get_im_db_middleware()

    def test_config_roundtrip(self) -> None:
        self.assertEqual(app_config.read_app_config(), {})
        self.assertEqual(app_config.get_configured_alibaba_data_dir(), "")
        merged = app_config.write_app_config({app_config.CONFIG_KEY_ALIBABA_DATA_DIR: "D:/AlibabaSupplierData"})
        self.assertEqual(merged[app_config.CONFIG_KEY_ALIBABA_DATA_DIR], "D:/AlibabaSupplierData")
        self.assertEqual(app_config.get_configured_alibaba_data_dir(), "D:/AlibabaSupplierData")

    def test_config_corrupt_file_returns_empty(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_bytes(b"{not json")
        self.assertEqual(app_config.read_app_config(), {})

    def test_status_unconfigured(self) -> None:
        mw = self._fresh_middleware()
        status = mw.data_dir_status()
        self.assertEqual(status["state"], "unconfigured")
        self.assertEqual(status["path"], "")

    def test_status_invalid_for_empty_dir(self) -> None:
        empty = Path(self.temp_dir.name) / "empty"
        empty.mkdir()
        mw = self._fresh_middleware()
        mw.set_data_dir(str(empty))
        status = mw.data_dir_status()
        self.assertEqual(status["state"], "invalid")
        self.assertEqual(app_config.get_configured_alibaba_data_dir(), str(empty))

    def test_status_ok_for_real_layout(self) -> None:
        layout = _make_layout(Path(self.temp_dir.name))
        self.assertTrue(mw_mod.IMDBMiddleware.looks_like_data_dir(layout))
        mw = self._fresh_middleware()
        mw.set_data_dir(str(layout))
        status = mw.data_dir_status()
        self.assertEqual(status["state"], "ok")
        self.assertEqual(status["source"], "file")

    def test_env_var_is_ignored(self) -> None:
        layout = _make_layout(Path(self.temp_dir.name))
        os.environ["ALIBABA_DATA_DIR"] = str(layout)
        self.addCleanup(os.environ.pop, "ALIBABA_DATA_DIR", None)
        mw = self._fresh_middleware()
        status = mw.data_dir_status()
        self.assertEqual(status["state"], "unconfigured")
        self.assertEqual(status["source"], "none")

    def test_set_data_dir_resets_cached_connection(self) -> None:
        mw = self._fresh_middleware()
        sentinel = object()
        mw._conn = sentinel  # type: ignore[assignment]
        target = Path(self.temp_dir.name) / "other"
        target.mkdir()
        mw.set_data_dir(str(target))
        self.assertIsNone(mw._conn)
        self.assertIsNone(mw._key)

    def test_scan_ali_ids_lists_most_recent_first(self) -> None:
        layout = _make_layout(Path(self.temp_dir.name))
        second = _make_account_db(layout, "10002")
        stale = layout / "IMServiceDir" / "MessageSDK" / "99999@icbu"
        stale.mkdir(parents=True)
        first = layout / "IMServiceDir" / "MessageSDK" / "10001@icbu" / "database" / "im.sqlite"
        os.utime(first, (1_000_000, 1_000_000))
        os.utime(second, (2_000_000, 2_000_000))
        mw = self._fresh_middleware()
        mw._data_dir = layout
        rows = mw.scan_ali_ids()
        self.assertEqual([row["ali_id"] for row in rows], ["10002", "10001"])

    def test_resolve_self_ali_id_reads_manual_selection_only(self) -> None:
        app_config.write_app_config({app_config.CONFIG_KEY_SELF_ALI_ID: "10001"})
        self.assertEqual(mw_mod.IMDBMiddleware._resolve_self_ali_id(), "10001")


class AccountKeyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_parse_key_hex_rejects_garbage(self) -> None:
        with self.assertRaises(account_keys.KeyFormatError):
            account_keys.parse_key_hex("not-hex")
        with self.assertRaises(account_keys.KeyFormatError):
            account_keys.parse_key_hex("ab" * 10)

    def test_mask_key_preview(self) -> None:
        self.assertEqual(account_keys.mask_key_preview("ab12cd34"), "ab***34")
        self.assertEqual(account_keys.mask_key_preview(""), "***")

    def test_verify_key_against_db(self) -> None:
        key = bytes(range(16))
        db_path = Path(self.temp_dir.name) / "im.sqlite"
        db_path.write_bytes(_encrypt_sqlite_header(key) + b"\x00" * 48)
        self.assertTrue(account_keys.verify_key_against_db(key, db_path))
        self.assertFalse(account_keys.verify_key_against_db(bytes(16), db_path))
        self.assertFalse(account_keys.verify_key_against_db(key, db_path.with_name("missing.sqlite")))

    def test_parse_and_verify_key(self) -> None:
        key = bytes(range(32))
        db_path = Path(self.temp_dir.name) / "im.sqlite"
        db_path.write_bytes(_encrypt_sqlite_header(key[:16]) + b"\x00" * 48)
        self.assertEqual(account_keys.parse_and_verify_key(key[:16].hex(), db_path), key[:16])
        with self.assertRaises(account_keys.KeyFormatError):
            account_keys.parse_and_verify_key(bytes(16).hex(), db_path)


class DataDirSettingsApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)
        self.fake = mock.Mock()
        self.fake.data_dir_status.return_value = {"state": "unconfigured", "path": "", "source": "none", "detail": "x"}
        patcher = mock.patch.object(settings_router, "get_im_db_middleware", return_value=self.fake)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_get_status(self) -> None:
        body = self.client.get("/api/settings/alibaba-data-dir").json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["state"], "unconfigured")

    def test_put_rejects_blank(self) -> None:
        resp = self.client.put("/api/settings/alibaba-data-dir", json={"path": "   "})
        self.assertEqual(resp.status_code, 422)
        self.fake.set_data_dir.assert_not_called()

    def test_put_rejects_missing_dir(self) -> None:
        resp = self.client.put("/api/settings/alibaba-data-dir", json={"path": "Z:/no/such/dir"})
        self.assertEqual(resp.status_code, 422)
        self.fake.set_data_dir.assert_not_called()

    def test_put_accepts_real_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            layout = _make_layout(Path(tmp))
            self.fake.data_dir_status.return_value = {"state": "ok", "path": str(layout), "source": "file", "detail": ""}

            resp = self.client.put("/api/settings/alibaba-data-dir", json={"path": str(layout)})
            self.assertEqual(resp.status_code, 200)
            self.fake.set_data_dir.assert_called_once_with(str(layout))

    def test_candidates_returns_list(self) -> None:
        body = self.client.get("/api/settings/alibaba-data-dir/candidates").json()
        self.assertEqual(body["code"], 0)
        self.assertIsInstance(body["data"]["candidates"], list)


class AliIdentityApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.fake = mock.Mock()
        self.fake.data_dir_status.return_value = {"state": "ok", "path": "D:/data", "source": "file", "detail": ""}
        self.fake.scan_ali_ids.return_value = [
            {"ali_id": "10001", "db_size": 64, "last_modified": 2_000_000.0},
            {"ali_id": "10002", "db_size": 32, "last_modified": 1_000_000.0},
        ]
        patchers = [
            mock.patch.object(settings_router, "get_im_db_middleware", return_value=self.fake),
            mock.patch.object(settings_router, "get_configured_self_ali_id", return_value="10001"),
            mock.patch.object(settings_router, "get_key_hex", return_value=bytes(range(16))),
            mock.patch.object(settings_router, "get_key_source", return_value="auto"),
        ]
        for patcher in patchers:
            self.addCleanup(patcher.stop)
            patcher.start()

    def test_list_ali_ids(self) -> None:
        body = self.client.get("/api/settings/ali-ids").json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["selected"], "10001")
        rows = body["data"]["accounts"]
        self.assertEqual([row["ali_id"] for row in rows], ["10001", "10002"])
        self.assertTrue(rows[0]["is_active"])
        self.assertTrue(rows[0]["has_key"])
        self.assertEqual(rows[0]["key_preview"], "00***0f")

    def test_save_ali_id_rejects_unknown(self) -> None:
        resp = self.client.put("/api/settings/ali-id", json={"ali_id": "99999"})
        self.assertEqual(resp.status_code, 422)
        self.fake.set_self_ali_id.assert_not_called()

    def test_save_ali_id_accepts_known(self) -> None:
        resp = self.client.put("/api/settings/ali-id", json={"ali_id": "10002"})
        self.assertEqual(resp.status_code, 200)
        self.fake.set_self_ali_id.assert_called_once_with("10002")

    def test_save_ali_key_rejects_garbage(self) -> None:
        self.fake.resolve_encrypted_db_path.return_value = Path(self.temp_dir.name) / "im.sqlite"
        resp = self.client.put("/api/settings/ali-keys", json={"ali_id": "10001", "aes_key_hex": "zz"})
        self.assertEqual(resp.status_code, 422)

    def test_save_ali_key_rejects_mismatch(self) -> None:
        db_path = Path(self.temp_dir.name) / "im.sqlite"
        db_path.write_bytes(_encrypt_sqlite_header(bytes(range(16))) + b"\x00" * 48)
        self.fake.resolve_encrypted_db_path.return_value = db_path
        with mock.patch.object(settings_router, "save_key") as save:
            resp = self.client.put("/api/settings/ali-keys", json={"ali_id": "10001", "aes_key_hex": bytes(16).hex()})
        self.assertEqual(resp.status_code, 422)
        save.assert_not_called()

    def test_save_ali_key_accepts_matching(self) -> None:
        key = bytes(range(16))
        db_path = Path(self.temp_dir.name) / "im.sqlite"
        db_path.write_bytes(_encrypt_sqlite_header(key) + b"\x00" * 48)
        self.fake.resolve_encrypted_db_path.return_value = db_path
        with mock.patch.object(settings_router, "save_key") as save:
            resp = self.client.put("/api/settings/ali-keys", json={"ali_id": "10001", "aes_key_hex": key.hex()})
        self.assertEqual(resp.status_code, 200)
        save.assert_called_once_with("10001", key, "manual")
        self.fake.drop_cached_key.assert_called_once()

    def test_clear_ali_key_missing_returns_422(self) -> None:
        with mock.patch.object(settings_router, "delete_key", return_value=False):
            resp = self.client.delete("/api/settings/ali-keys/10001")
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
