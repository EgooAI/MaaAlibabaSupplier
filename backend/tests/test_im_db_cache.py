"""Source-fingerprint guard: no repeated full decrypt while the encrypted IM DB is unchanged."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.shared.backend.im_db_middleware import IMDBMiddleware


class SourceFingerprintGuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        IMDBMiddleware._instance = None
        self.mw = IMDBMiddleware()
        patches = [
            patch.object(self.mw, "_get_self_ali_id", return_value="ali-1"),
            patch.object(self.mw, "resolve_encrypted_db_path", return_value=Path("encrypted-im.sqlite")),
            patch.object(self.mw, "_source_fingerprint_of"),
            patch.object(self.mw, "_crc32_of"),
            patch.object(self.mw, "_ensure_key", return_value=True),
            patch.object(self.mw, "_decrypt_db", return_value=12345),
            patch.object(self.mw, "_replace_connection"),
            patch.object(self.mw, "_cleanup_stale_caches"),
            patch.object(self.mw, "sync_to_crm"),
        ]
        started = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        # patches order: self_ali_id, resolve, fingerprint, crc32, ensure_key, decrypt, replace, cleanup, sync
        self.resolve = started[1]
        self.fingerprint = started[2]
        self.crc32 = started[3]
        self.decrypt = started[5]
        self.replace = started[6]
        self.sync = started[8]

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


if __name__ == "__main__":
    unittest.main()
