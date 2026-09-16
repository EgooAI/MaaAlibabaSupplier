"""WAL sidecar support for encrypted Alibaba IM SQLite databases.

The AliWorkbench client encrypts database pages with AES-ECB *below* the WAL
layer (pager-level codec, SQLCipher-style): the main ``im.sqlite`` file
decrypts as a whole, while WAL frame headers stay plaintext, each frame's page
image is ECB-encrypted, and frame checksums cover the *ciphertext*.

Consequences for readers:

* Copying/decrypting the main file alone silently drops every not-yet-
  checkpointed commit (new messages, read receipts, sync deltas).
* After decrypting frame payloads, the checksum chain must be recomputed over
  plaintext and patched back into the frame headers, otherwise stock SQLite
  recovery rejects every frame (``wal_checkpoint`` reports 0 frames).

Checksum convention (calibrated against a live client WAL): seeds come from
the WAL-header checksum words; accumulation runs over 32-bit little-endian
word pairs (``s0 += x_even + s1; s1 += x_odd + s0``); stored words are
big-endian.

Only ever operate on *copies* — never open, checkpoint, or write the client's
live files.
"""

from __future__ import annotations

import struct
from pathlib import Path

from Crypto.Cipher import AES

WAL_MAGIC = 0x377F0682
WAL_HEADER_LEN = 32
FRAME_HEADER_LEN = 24

_AES_BLOCK = 16


class WalError(ValueError):
    """Raised when a WAL sidecar is structurally unusable."""


def _words_le(data: bytes) -> tuple[int, ...]:
    count, rest = divmod(len(data), 4)
    if rest:
        raise WalError(f"checksum input length {len(data)} is not a multiple of 4")
    return struct.unpack(f"<{count}I", data)


def checksum_chain(data: bytes, seed: tuple[int, int]) -> tuple[int, int]:
    """Accumulate the SQLite WAL checksum over *data* (length must be a multiple of 8)."""
    words = _words_le(data)
    if len(words) % 2:
        raise WalError(f"checksum input covers {len(words)} words, expected an even count")
    s0, s1 = seed
    for index in range(0, len(words), 2):
        s0 = (s0 + words[index] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[index + 1] + s0) & 0xFFFFFFFF
    return s0, s1


def read_wal_header(data: bytes) -> dict[str, int]:
    """Validate the 32-byte WAL header; return magic/version/page_size/checkpoint/salts/checksum."""
    if len(data) < WAL_HEADER_LEN:
        raise WalError(f"WAL header is {len(data)} bytes, expected at least {WAL_HEADER_LEN}")
    magic, version, page_size, checkpoint = struct.unpack(">IIII", data[0:16])
    if magic != WAL_MAGIC:
        raise WalError(f"WAL magic mismatch: {magic:#x}")
    salt1, salt2, cksum0, cksum1 = struct.unpack(">IIII", data[16:32])
    # Self-check: header checksum covers bytes [0:24] from a zero seed.
    if checksum_chain(data[0:24], (0, 0)) != (cksum0, cksum1):
        raise WalError("WAL header checksum mismatch")
    return {
        "magic": magic,
        "version": version,
        "page_size": page_size,
        "checkpoint": checkpoint,
        "salt1": salt1,
        "salt2": salt2,
        "cksum0": cksum0,
        "cksum1": cksum1,
    }


def ecb_decrypt_blocks(data: bytes, key: bytes) -> bytes:
    """Decrypt *data* (length must be a multiple of 16) with AES-ECB in one shot."""
    if len(data) % _AES_BLOCK:
        raise WalError(f"decrypt input length {len(data)} is not a multiple of {_AES_BLOCK}")
    return AES.new(key, AES.MODE_ECB).decrypt(data)


def rekey_wal_copy(wal_path: Path, key: bytes, page_size: int) -> int:
    """Decrypt frame payloads of the WAL *copy* at *wal_path* and rechecksum it.

    Frames are processed in order; iteration stops at the first salt mismatch,
    illegal page number, or truncated frame (torn copy / old salt-epoch tail),
    and the file is truncated there. Returns the number of usable frames.
    Raises WalError when the header itself is invalid.
    """
    raw = bytearray(wal_path.read_bytes())
    header = read_wal_header(bytes(raw))
    wal_page_size = header["page_size"]
    if wal_page_size != page_size:
        raise WalError(f"WAL page_size {wal_page_size} != main database {page_size}")
    salt = bytes(raw[16:24])
    seed: tuple[int, int] = (header["cksum0"], header["cksum1"])

    offset = WAL_HEADER_LEN
    frames = 0
    total = len(raw)
    s0, s1 = seed
    while offset + FRAME_HEADER_LEN <= total:
        if bytes(raw[offset + 8:offset + 16]) != salt:
            break
        page_no = struct.unpack(">I", raw[offset:offset + 4])[0]
        if page_no == 0:
            break
        end = offset + FRAME_HEADER_LEN + page_size
        if end > total:
            break
        payload = ecb_decrypt_blocks(bytes(raw[offset + FRAME_HEADER_LEN:end]), key)
        raw[offset + FRAME_HEADER_LEN:end] = payload
        s0, s1 = checksum_chain(bytes(raw[offset:offset + 8]), (s0, s1))
        s0, s1 = checksum_chain(payload, (s0, s1))
        raw[offset + 16:offset + 24] = struct.pack(">II", s0, s1)
        offset = end
        frames += 1
    wal_path.write_bytes(bytes(raw[:offset]))
    return frames
