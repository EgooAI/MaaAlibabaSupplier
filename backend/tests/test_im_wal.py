"""WAL sidecar pipeline: synthetic encrypted-DB round trip (no live client needed)."""

from __future__ import annotations

import shutil
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from backend.app.shared.utils.im_wal import (
    WalError,
    checksum_chain,
    ecb_decrypt_blocks,
    read_wal_header,
    rekey_wal_copy,
)


def _encrypt_fixture(main_path: Path, wal_path: Path, key: bytes) -> None:
    """Mimic the client codec: whole-file ECB on main, per-frame payload ECB on WAL."""
    main_path.write_bytes(_ecb_encrypt_all(main_path.read_bytes(), key))
    raw = bytearray(wal_path.read_bytes())
    header = read_wal_header(bytes(raw))
    page_size = header["page_size"]
    salt = bytes(raw[16:24])
    offset = 32
    while offset + 24 <= len(raw):
        if bytes(raw[offset + 8:offset + 16]) != salt:
            break
        end = offset + 24 + page_size
        if end > len(raw):
            break
        raw[offset + 24:end] = _ecb_encrypt_all(bytes(raw[offset + 24:end]), key)
        # Recompute the checksum over CIPHERTEXT, like the pager-level codec does.
        offset = end
    # Rebuild the ciphertext checksum chain so the fixture is self-consistent.
    s0, s1 = header["cksum0"], header["cksum1"]
    offset = 32
    while offset + 24 <= len(raw):
        if bytes(raw[offset + 8:offset + 16]) != salt:
            break
        end = offset + 24 + page_size
        if end > len(raw):
            break
        s0, s1 = checksum_chain(bytes(raw[offset:offset + 8]), (s0, s1))
        s0, s1 = checksum_chain(bytes(raw[offset + 24:end]), (s0, s1))
        raw[offset + 16:offset + 24] = struct.pack(">II", s0, s1)
        offset = end
    wal_path.write_bytes(bytes(raw))


def _ecb_encrypt_all(data: bytes, key: bytes) -> bytes:
    return AES.new(key, AES.MODE_ECB).encrypt(data)


class ImWalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="imwal"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.key = get_random_bytes(16)
        build = self.tmp / "build.sqlite"
        conn = sqlite3.connect(build)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE msg_test(id INTEGER PRIMARY KEY, body TEXT)")
            conn.execute("INSERT INTO msg_test(body) VALUES ('first')")
            conn.commit()
            conn.execute("INSERT INTO msg_test(body) VALUES ('second')")
            conn.commit()
            # Snapshot the files while the writer is still open: this build
            # checkpoints + removes the WAL on last close, so copying after
            # close would lose the uncheckpointed frames under test.
            self.db = self.tmp / "orig.sqlite"
            self.wal = Path(str(self.db) + "-wal")
            shutil.copyfile(build, self.db)
            shutil.copyfile(Path(str(build) + "-wal"), self.wal)
        finally:
            conn.close()
        self.assertTrue(self.wal.exists(), "fixture must produce a WAL sidecar")
        self.assertGreater(self.wal.stat().st_size, 32)
        _encrypt_fixture(self.db, self.wal, self.key)
        page_size = struct.unpack(">H", ecb_decrypt_blocks(self.db.read_bytes()[:4096], self.key)[16:18])[0]
        self.page_size = page_size

    def _work_copies(self):
        work = self.tmp / "work.sqlite"
        work_wal = Path(str(work) + "-wal")
        work.write_bytes(ecb_decrypt_blocks(self.db.read_bytes(), self.key))
        work_wal.write_bytes(self.wal.read_bytes())
        return work, work_wal

    def test_rekeyed_wal_replays_uncheckpointed_commit(self) -> None:
        work, work_wal = self._work_copies()
        frames = rekey_wal_copy(work_wal, self.key, self.page_size)
        self.assertGreater(frames, 0)
        conn = sqlite3.connect(work)
        try:
            log = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            self.assertGreater(log[1], 0, f"SQLite rejected all frames: {log}")
            bodies = [row[0] for row in conn.execute("SELECT body FROM msg_test ORDER BY id")]
        finally:
            conn.close()
        self.assertEqual(bodies, ["first", "second"])

    def test_torn_tail_is_truncated_and_earlier_frames_survive(self) -> None:
        work, work_wal = self._work_copies()
        with work_wal.open("ab") as stream:
            stream.write(b"\x00" * 5000)  # torn / old-epoch tail
        frames = rekey_wal_copy(work_wal, self.key, self.page_size)
        self.assertGreater(frames, 0)
        conn = sqlite3.connect(work)
        try:
            log = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            bodies = [row[0] for row in conn.execute("SELECT body FROM msg_test ORDER BY id")]
        finally:
            conn.close()
        self.assertGreater(log[1], 0)
        self.assertEqual(bodies, ["first", "second"])

    def test_bad_magic_raises(self) -> None:
        _, work_wal = self._work_copies()
        raw = bytearray(work_wal.read_bytes())
        raw[0:4] = b"\x00\x00\x00\x00"
        work_wal.write_bytes(bytes(raw))
        with self.assertRaises(WalError):
            rekey_wal_copy(work_wal, self.key, self.page_size)

    def test_page_size_mismatch_raises(self) -> None:
        _, work_wal = self._work_copies()
        with self.assertRaises(WalError):
            rekey_wal_copy(work_wal, self.key, self.page_size + 1)

    def _middleware(self):
        from backend.app.shared.backend.im_db_middleware import IMDBMiddleware

        mw = IMDBMiddleware()
        mw._key = self.key
        mw._key_validation = "valid"
        mw._auto_enabled = True
        self.enterContext(patch.object(mw, "_resolve_self_ali_id", return_value="wal-test"))
        self.enterContext(patch.object(mw, "resolve_encrypted_db_path", return_value=self.db))
        self.enterContext(patch.object(mw, "sync_to_crm"))
        return mw

    def test_middleware_replays_wal_and_preserves_source_bytes(self):
        mw = self._middleware()
        before = self.db.read_bytes(), self.wal.read_bytes()
        self.assertTrue(mw._refresh())
        bodies = [row[0] for row in mw._conn.execute("SELECT body FROM msg_test ORDER BY id")]
        self.assertEqual(bodies, ["first", "second"])
        self.assertGreater(mw.sync_status()["wal_frames_applied"], 0)
        self.assertEqual(before, (self.db.read_bytes(), self.wal.read_bytes()))

    def test_middleware_reads_native_restart_wal_with_old_salt_tail(self):
        build = self.tmp / "restart.sqlite"
        conn = sqlite3.connect(build)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("CREATE TABLE msg_test(id INTEGER PRIMARY KEY, body TEXT)")
            for index in range(20):
                conn.execute("INSERT INTO msg_test(body) VALUES (?)", (str(index),))
                conn.commit()
            wal = Path(str(build) + "-wal")
            previous = wal.read_bytes()
            checkpoint = conn.execute("PRAGMA wal_checkpoint(RESTART)").fetchone()
            self.assertEqual(checkpoint[0], 0)
            self.assertEqual(checkpoint[1], checkpoint[2])
            conn.execute("INSERT INTO msg_test(body) VALUES ('after restart')")
            conn.commit()
            reused = wal.read_bytes()
            page_size = read_wal_header(reused)["page_size"]
            next_frame = 32 + 24 + page_size
            self.assertEqual(len(reused), len(previous))
            self.assertNotEqual(reused[16:24], previous[16:24])
            self.assertEqual(reused[40:48], reused[16:24])
            self.assertEqual(reused[next_frame + 8:next_frame + 16], previous[16:24])
            expected = conn.execute("SELECT body FROM msg_test ORDER BY id").fetchall()
            self.assertEqual(len(expected), 21)
            shutil.copyfile(build, self.db)
            shutil.copyfile(wal, self.wal)
        finally:
            conn.close()
        _encrypt_fixture(self.db, self.wal, self.key)
        source_bytes = self.db.read_bytes(), self.wal.read_bytes()
        mw = self._middleware()
        self.assertTrue(mw._refresh())
        self.assertEqual([tuple(row) for row in mw._conn.execute("SELECT body FROM msg_test ORDER BY id")], expected)
        self.assertEqual(mw.sync_status()["wal_frames_applied"], 1)
        self.assertEqual(mw.sync_status()["error_code"], "")
        self.assertEqual(source_bytes, (self.db.read_bytes(), self.wal.read_bytes()))

        # Corruption in the current prefix still fails, even with a legitimate old tail.
        old_path, revision = mw._cached_db_path, mw._source_revision
        damaged = bytearray(self.wal.read_bytes())
        damaged[32 + 24 + 100] ^= 1
        self.wal.write_bytes(damaged)
        mw._cache_time = mw._last_refresh_start = mw._backoff_until = 0
        self.assertTrue(mw._refresh())
        self.assertEqual(mw.sync_status()["error_code"], "wal_error")
        self.assertEqual(mw._cached_db_path, old_path)
        self.assertEqual(mw._source_revision, revision)

    def test_middleware_rejects_corrupt_header_frame_and_torn_tail(self):
        mw = self._middleware()
        self.assertTrue(mw._refresh())
        old_path, revision = mw._cached_db_path, mw._source_revision
        original = self.wal.read_bytes()
        for offset in (0, 32 + 24 + 100, -1):
            with self.subTest(offset=offset):
                raw = bytearray(original)
                if offset == -1:
                    raw = raw[:-1]
                else:
                    raw[offset] ^= 1
                self.wal.write_bytes(raw)
                mw._cache_time = mw._last_refresh_start = mw._backoff_until = 0
                self.assertTrue(mw._refresh())
                self.assertEqual(mw._cached_db_path, old_path)
                self.assertEqual(mw._source_revision, revision)
                self.assertEqual(mw.sync_status()["error_code"], "wal_error")
                self.assertTrue(mw.sync_status()["stale"])
                self.assertTrue(old_path.exists())

    def test_disabled_wal_never_publishes_complete_main_only_snapshot(self):
        mw = self._middleware()
        self.enterContext(patch.object(mw, "_wal_pipeline_enabled", return_value=False))
        self.assertFalse(mw._refresh())
        self.assertIsNone(mw._cached_db_path)
        self.assertEqual(mw.sync_status()["error_code"], "wal_disabled")
        self.assertTrue(mw.sync_status()["stale"])
        self.assertFalse(mw.sync_status()["ready"])
        mw.sync_to_crm.assert_not_called()

    def test_wal_appearing_during_copy_discards_build(self):
        mw = self._middleware()
        raw = self.wal.read_bytes()
        self.wal.unlink()
        copy = mw._copy_source_pair

        def changed_pair(source, cached):
            result = copy(source, cached)
            self.wal.write_bytes(raw)
            return result

        self.enterContext(patch.object(mw, "_copy_source_pair", side_effect=changed_pair))
        self.assertFalse(mw._refresh())
        self.assertIsNone(mw._cached_db_path)
        self.assertEqual(mw.sync_status()["error_code"], "source_changed")


if __name__ == "__main__":
    unittest.main()
