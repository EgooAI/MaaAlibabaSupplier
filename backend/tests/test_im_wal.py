"""WAL sidecar pipeline: synthetic encrypted-DB round trip (no live client needed)."""

from __future__ import annotations

import shutil
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
