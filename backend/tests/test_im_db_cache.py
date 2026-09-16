"""Source-fingerprint guard: no repeated full decrypt while the encrypted IM DB is unchanged."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.shared.backend.im_db_middleware import IMDBMiddleware
import backend.app.shared.backend.im_db_middleware as im_db_middleware_mod


class SourceFingerprintGuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(IMDBMiddleware, "_instance", None))
        self.mw = IMDBMiddleware()
        self.addCleanup(self.mw._reset_runtime_state)
        patches = [
            patch.object(im_db_middleware_mod, "write_app_config", return_value={}),
            patch.object(self.mw, "_resolve_self_ali_id", return_value="ali-1"),
            patch.object(self.mw, "resolve_encrypted_db_path", return_value=Path("encrypted-im.sqlite")),
            patch.object(self.mw, "_source_fingerprint_of"),
            patch.object(self.mw, "_crc32_of"),
            patch.object(self.mw, "_ensure_key", return_value=True),
            patch.object(self.mw, "_copy_source_pair", return_value=None),
            patch.object(self.mw, "_decrypt_db", return_value=12345),
            patch.object(self.mw, "_verify_cached_db", return_value=True),
            patch.object(self.mw, "_replace_connection"),
            patch.object(self.mw, "_cleanup_stale_caches"),
            patch.object(self.mw, "sync_to_crm"),
        ]
        started = [self.enterContext(p) for p in patches]
        # patches order: persist, self_ali_id, resolve, fingerprint, crc32,
        # ensure_key, copy_pair, decrypt, verify, replace, cleanup, sync
        self.resolve = started[2]
        self.fingerprint = started[3]
        self.crc32 = started[4]
        self.decrypt = started[7]
        self.replace = started[9]
        self.sync = started[11]

    def _prime_cache(self, fingerprint=(111, 222), crc=12345):
        def fake_replace(cached, ali_id):
            self.mw._cached_db_path = cached
            self.mw._cache_time = time.time()

        self.replace.side_effect = fake_replace
        self.fingerprint.return_value = fingerprint
        self.crc32.return_value = crc
        self.assertTrue(self.mw._refresh())
        self.assertEqual(self.decrypt.call_count, 1)

    def _expire_cache(self):
        self.mw._cache_time = 0.0
        self.mw._last_refresh_start = 0.0
        self.mw._backoff_until = 0.0

    def test_unchanged_source_skips_second_decrypt(self):
        self._prime_cache()
        cached_before = self.mw._cached_db_path
        self._expire_cache()

        self.assertTrue(self.mw._refresh())

        self.assertEqual(self.decrypt.call_count, 1)
        self.assertEqual(self.sync.call_count, 1)
        self.assertEqual(self.mw._cached_db_path, cached_before)

    def test_changed_source_redecrypts(self):
        self._prime_cache()
        self._expire_cache()
        self.fingerprint.return_value = (333, 444)

        self.assertTrue(self.mw._refresh())

        self.assertEqual(self.decrypt.call_count, 2)
        self.assertEqual(self.sync.call_count, 2)

    def test_matching_fingerprint_with_changed_crc32_redecrypts(self):
        self._prime_cache()
        self._expire_cache()
        self.crc32.return_value = 99999

        self.assertTrue(self.mw._refresh())

        self.assertEqual(self.decrypt.call_count, 2)
        self.assertEqual(self.sync.call_count, 2)

    def test_burst_writes_coalesce_within_min_interval(self):
        self._prime_cache()
        self.mw._cache_time = 0.0  # TTL expired, but the rebuild was just now
        self.fingerprint.return_value = (333, 444)

        self.assertTrue(self.mw._refresh())

        self.assertEqual(self.decrypt.call_count, 1)
        self.assertEqual(self.sync.call_count, 1)

    def test_rebuild_failure_serves_stale_cache(self):
        self._prime_cache()
        cached_before = self.mw._cached_db_path
        self._expire_cache()
        self.fingerprint.return_value = (333, 444)
        self.decrypt.return_value = None  # decrypt blows up on the new source

        self.assertTrue(self.mw._refresh())

        self.assertEqual(self.mw._cached_db_path, cached_before)
        self.assertTrue(self.mw.sync_status()["stale"])

    def test_successful_rebuild_bumps_revision(self):
        revision_before = self.mw._data_revision
        self._prime_cache()
        self.assertEqual(self.mw._data_revision, revision_before + 1)
        self.assertFalse(self.mw.sync_status()["stale"])


if __name__ == "__main__":
    unittest.main()
