"""IM database middleware — decrypts and caches the encrypted Alibaba IM SQLite database.

Provides a clean interface that hides encryption semantics from consumers.
Identity (self ali_id) comes solely from the manual selection in Settings;
per-account AES keys live in the app database (auto-captured from the running
AliWorkbench process or manually entered). The decrypted database is cached
for a configurable TTL to avoid repeated decryption.
"""

from __future__ import annotations

import sqlite3

from loguru import logger
import threading
import time
import zlib
from pathlib import Path

from Crypto.Cipher import AES

from backend.app.shared.mitm.pool import SelfInfo
from backend.app.shared.utils.im_db_decryptor import retrieve_db_key
from backend.app.shared.backend.im_chat_db import open_readonly
from backend.app.shared.crm import sync_im_database
from backend.app.shared.crm.account_keys import get_key_hex, get_key_source, save_key
from backend.app.shared.crm.identities import self_sender_id, strip_icbu_suffix
from backend.app.shared.utils.app_config import (
    get_configured_alibaba_data_dir,
    get_configured_self_ali_id,
    set_configured_self_ali_id,
    write_app_config,
    CONFIG_KEY_ALIBABA_DATA_DIR,
)
from backend.app.shared.utils.settings import resolve_backend_root

_CACHE_TTL = 5.0  # seconds

# Directory name to look for at each drive root when auto-detecting candidates.
DATA_DIR_NAME = "AlibabaSupplierData"
# Relative layout below the data dir proving it is a real Alibaba client dir.
DATA_DIR_SIGNATURE = Path("IMServiceDir") / "MessageSDK"

SOURCE_FILE = "file"
SOURCE_NONE = "none"

STATE_UNCONFIGURED = "unconfigured"
STATE_INVALID = "invalid"
STATE_OK = "ok"


class IMDBMiddleware:
    """Thread-safe singleton that manages encrypted IM database access."""

    _instance: IMDBMiddleware | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> IMDBMiddleware:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.Lock()
                instance._data_dir: Path | None = None
                instance._key: bytes | None = None
                instance._key_ali_id: str = ""
                instance._key_source: str = "none"
                instance._data_dir_source: str = SOURCE_NONE
                instance._cache_dir: Path | None = None
                instance._cached_db_path: Path | None = None
                instance._cache_time: float = 0.0
                instance._source_fingerprint: tuple[int, int] | None = None
                instance._source_crc32: int | None = None
                instance._conn: sqlite3.Connection | None = None
                instance._init_data_dir()
                cls._instance = instance
            return cls._instance

    def _init_data_dir(self) -> None:
        # Single source: the per-machine config file written from the Settings page.
        file_raw = get_configured_alibaba_data_dir()
        if file_raw:
            self._data_dir = Path(file_raw)
            self._data_dir_source = SOURCE_FILE
        self._cache_dir = resolve_backend_root() / "data" / ".cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _reset_runtime_state(self) -> None:
        """Drop connection/key/fingerprint; caller must hold the lock."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                logger.debug("IM缓存连接关闭失败，已忽略")
            self._conn = None
        self._key = None
        self._key_ali_id = ""
        self._key_source = "none"
        self._cached_db_path = None
        self._cache_time = 0.0
        self._source_fingerprint = None
        self._source_crc32 = None

    def set_data_dir(self, raw: str) -> None:
        """Apply a newly configured data dir at runtime (persists to config file)."""
        write_app_config({CONFIG_KEY_ALIBABA_DATA_DIR: raw})
        with self._lock:
            self._data_dir = Path(raw)
            self._data_dir_source = SOURCE_FILE
            self._reset_runtime_state()

    def set_self_ali_id(self, ali_id: str) -> None:
        """Apply a newly selected identity at runtime (persists to config file)."""
        set_configured_self_ali_id(ali_id)
        with self._lock:
            self._reset_runtime_state()

    def drop_cached_key(self) -> None:
        """Drop the in-memory key after it was changed/deleted in the database."""
        with self._lock:
            self._key = None
            self._key_ali_id = ""
            self._key_source = "none"

    @staticmethod
    def looks_like_data_dir(path: Path) -> bool:
        """True when *path* has the Alibaba client layout with at least one im.sqlite."""
        try:
            signature = path / DATA_DIR_SIGNATURE
            if not signature.is_dir():
                return False
            return next(signature.glob("*/database/im.sqlite"), None) is not None
        except OSError:
            return False

    def data_dir_status(self) -> dict:
        """Return {state, path, source, detail} for the settings UI."""
        if self._data_dir is None:
            return {
                "state": STATE_UNCONFIGURED,
                "path": "",
                "source": SOURCE_NONE,
                "detail": "尚未配置阿里客户端数据目录，请在设置页填写或从候选中选择",
            }
        if not self._data_dir.exists():
            return {
                "state": STATE_INVALID,
                "path": str(self._data_dir),
                "source": self._data_dir_source,
                "detail": "配置的目录不存在，请检查路径或重新选择",
            }
        if not self.looks_like_data_dir(self._data_dir):
            return {
                "state": STATE_INVALID,
                "path": str(self._data_dir),
                "source": self._data_dir_source,
                "detail": "目录下未找到 IMServiceDir/MessageSDK IM 数据库结构",
            }
        return {
            "state": STATE_OK,
            "path": str(self._data_dir),
            "source": self._data_dir_source,
            "detail": "",
        }

    # -- Identity (manual selection is the single source) -----------------------

    @staticmethod
    def _resolve_self_ali_id() -> str:
        return get_configured_self_ali_id()

    def scan_ali_ids(self) -> list[dict]:
        """List accounts found under the configured data dir, most-recent first."""
        if self._data_dir is None:
            return []
        try:
            entries = (self._data_dir / DATA_DIR_SIGNATURE).glob("*/database/im.sqlite")
            found = []
            for db_path in entries:
                try:
                    stat = db_path.stat()
                except OSError:
                    continue
                found.append(
                    {
                        "ali_id": strip_icbu_suffix(db_path.parent.parent.name),
                        "db_size": stat.st_size,
                        "last_modified": stat.st_mtime,
                    }
                )
        except OSError:
            return []
        found.sort(key=lambda item: item["last_modified"], reverse=True)
        return found

    # -- Path resolution ------------------------------------------------------

    def resolve_encrypted_db_path(self, ali_id: str) -> Path | None:
        if self._data_dir is None:
            logger.warning("阿里客户端数据目录未配置，请在设置页配置后重试")
            return None
        if not ali_id:
            logger.warning("尚未在设置页选择阿里账号身份，请选择后重试")
            return None

        db_path = self._data_dir / "IMServiceDir" / "MessageSDK" / self_sender_id(ali_id) / "database" / "im.sqlite"
        if not db_path.exists():
            logger.warning("Encrypted DB not found: {}", db_path)
            return None

        return db_path

    # -- Key management (per-account, stored in the app database) --------------

    def _migrate_legacy_key_file(self, ali_id: str) -> None:
        """One-time import of the legacy aes_key.bin into the database."""
        if get_key_hex(ali_id) is not None:
            return
        candidates = [
            resolve_backend_root() / "data" / ".cache" / "aes_key.bin",
            Path("data/.cache/aes_key.bin"),
        ]
        for path in candidates:
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if len(raw) not in (16, 24, 32):
                logger.warning("Legacy key file {} has invalid size, skipping", path)
                return
            save_key(ali_id, raw, "auto")
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("Failed to remove migrated key file {}: {}", path, exc)
            logger.info("Migrated legacy AES key file into database for ali_id={}", ali_id)
            return

    def _ensure_key(self, db_path: Path, ali_id: str) -> bool:
        if self._key is not None and self._key_ali_id == ali_id:
            return True

        stored = get_key_hex(ali_id)
        if stored is not None:
            self._key = stored
            self._key_ali_id = ali_id
            self._key_source = get_key_source(ali_id) or "auto"
            return True

        self._migrate_legacy_key_file(ali_id)
        stored = get_key_hex(ali_id)
        if stored is not None:
            self._key = stored
            self._key_ali_id = ali_id
            self._key_source = get_key_source(ali_id) or "auto"
            return True

        logger.info("Retrieving AES key from process memory...")
        try:
            key = retrieve_db_key(str(db_path))
            save_key(ali_id, key, "auto")
            self._key = key
            self._key_ali_id = ali_id
            self._key_source = "live"
            logger.info("AES key retrieved successfully")
            return True
        except (ValueError, EOFError, OSError) as exc:
            logger.warning("Failed to retrieve DB key from live process: {}", exc)

        return False

    # -- Decryption ------------------------------------------------------------

    def _decrypt_db(self, src: Path, dst: Path) -> int | None:
        """Decrypt src to dst; return CRC32 of the encrypted source, or None on failure."""
        try:
            encrypted = src.read_bytes()
            if len(encrypted) == 0 or len(encrypted) % 16 != 0:
                logger.error("Encrypted DB size is not a multiple of 16 bytes")
                return None

            cipher = AES.new(self._key, AES.MODE_ECB)
            decrypted = bytearray(len(encrypted))
            for offset in range(0, len(encrypted), 16):
                block = encrypted[offset : offset + 16]
                decrypted[offset : offset + 16] = cipher.decrypt(block)

            dst.write_bytes(decrypted)
            return zlib.crc32(encrypted) & 0xFFFFFFFF
        except Exception as exc:
            logger.error("DB decryption failed: {}", exc)
            return None

    # -- Cache refresh ---------------------------------------------------------

    def _has_fresh_cache(self, now: float) -> bool:
        return self._cached_db_path is not None and (now - self._cache_time) < _CACHE_TTL

    def _cache_path_for(self, ali_id: str) -> Path:
        assert self._cache_dir is not None
        return self._cache_dir / f"im_{ali_id}_{int(time.time() * 1000)}.sqlite"

    def _cleanup_stale_caches(self, keep: Path) -> None:
        assert self._cache_dir is not None
        for path in self._cache_dir.glob("im_*.sqlite"):
            if path == keep or path == self._cached_db_path:
                continue
            try:
                path.unlink()
            except OSError:
                pass

    def _source_fingerprint_of(self, db_path: Path) -> tuple[int, int] | None:
        try:
            stat = db_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _crc32_of(self, db_path: Path) -> int | None:
        try:
            crc = 0
            with db_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    crc = zlib.crc32(chunk, crc)
            return crc & 0xFFFFFFFF
        except OSError:
            return None

    def _is_source_fresh(self, db_path: Path, now: float) -> bool:
        """Two-level freshness: fingerprint only within TTL, fingerprint + CRC32 beyond TTL."""
        if self._cached_db_path is None or self._source_fingerprint is None:
            return False
        fingerprint = self._source_fingerprint_of(db_path)
        if fingerprint is None or fingerprint != self._source_fingerprint:
            return False
        if (now - self._cache_time) < _CACHE_TTL:
            return True
        crc = self._crc32_of(db_path)
        if crc is None or crc != self._source_crc32:
            return False
        self._cache_time = now
        return True

    def _replace_connection(self, cached: Path, ali_id: str) -> None:
        old_conn = self._conn
        conn = open_readonly(cached)

        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                logger.debug("旧IM缓存连接关闭失败，已忽略")

        self._conn = conn
        self._cached_db_path = cached
        self._cache_time = time.time()

    def _refresh(self) -> bool:
        with self._lock:
            ali_id = self._resolve_self_ali_id()
            db_path = self.resolve_encrypted_db_path(ali_id)
            if db_path is None:
                return self._has_fresh_cache(time.time())

            if self._is_source_fresh(db_path, time.time()):
                return True

            if not self._ensure_key(db_path, ali_id):
                return False

            cached = self._cache_path_for(ali_id)
            logger.info("Decrypting IM database...")
            crc = self._decrypt_db(db_path, cached)
            if crc is None:
                return False

            try:
                self._replace_connection(cached, ali_id)
            except sqlite3.Error as exc:
                logger.error("Failed to open cached IM database: {}", exc)
                return False
            fingerprint = self._source_fingerprint_of(db_path)
            if fingerprint is not None:
                self._source_fingerprint = fingerprint
            self._source_crc32 = crc
            self._cleanup_stale_caches(cached)
        self.sync_to_crm()
        logger.info("IM database refreshed (cached at {})", cached)
        return True

    def sync_to_crm(self, wait: bool = False) -> None:
        cached = self._cached_db_path
        ali_id = self._resolve_self_ali_id()
        if cached is None or not ali_id:
            return
        future = sync_im_database(cached, ali_id, SelfInfo(ali_id=ali_id))
        if wait:
            future.result()

    # -- Public API ------------------------------------------------------------

    def key_status(self) -> tuple[bool, str]:
        """Return ``(has_key, source)`` for the selected identity (no live lookup)."""
        ali_id = self._resolve_self_ali_id()
        if not ali_id:
            return False, "none"
        if self._key is not None and self._key_ali_id == ali_id:
            return True, self._key_source
        source = get_key_source(ali_id)
        return (True, source) if source else (False, "none")

    def get_connection(self) -> sqlite3.Connection | None:
        self._refresh()
        return self._conn


def get_im_db_middleware() -> IMDBMiddleware:
    return IMDBMiddleware()


def find_data_dir_candidates() -> list[str]:
    """Scan drive roots (C:..Z:) for an ``AlibabaSupplierData`` dir with IM layout.

    Existence checks only — no recursion, completes in milliseconds.
    """
    import string

    found: list[str] = []
    for letter in string.ascii_uppercase:
        if letter < "C":
            continue
        candidate = Path(f"{letter}:/{DATA_DIR_NAME}")
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        if IMDBMiddleware.looks_like_data_dir(candidate):
            found.append(str(candidate.resolve()))
    return found
