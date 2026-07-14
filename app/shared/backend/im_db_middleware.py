"""IM database middleware — decrypts and caches the encrypted Alibaba IM SQLite database.

Provides a clean interface that hides encryption semantics from consumers.
The key is lazily loaded from the running AliWorkbench process on first access.
The decrypted database is cached for a configurable TTL to avoid repeated decryption.
"""

from __future__ import annotations

import sqlite3

from loguru import logger
import threading
import time
from pathlib import Path

from Crypto.Cipher import AES

from app.shared.mitm.pool import FIXED_SELF_ALI_ID, get_self_info_pool
from app.shared.utils.env import get_env_str, load_workdir_env
from app.shared.utils.im_db_decryptor import retrieve_db_key
from app.shared.backend.im_chat_db import (
    MsgTableResolver,
    open_readonly,
)
from app.shared.crm import sync_im_database

_CACHE_TTL = 5.0  # seconds


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
                instance._key_source: str = "none"
                instance._cache_dir: Path | None = None
                instance._cached_db_path: Path | None = None
                instance._cache_time: float = 0.0
                instance._conn: sqlite3.Connection | None = None
                instance._resolver: MsgTableResolver | None = None
                instance._init_data_dir()
                cls._instance = instance
            return cls._instance

    def _init_data_dir(self) -> None:
        load_workdir_env()
        raw = get_env_str("ALIBABA_DATA_DIR")
        if raw:
            self._data_dir = Path(raw)
        self._cache_dir = Path("data/.cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # -- Path resolution ------------------------------------------------------

    def _get_self_ali_id(self) -> str:
        return FIXED_SELF_ALI_ID

    def _resolve_encrypted_db_path(self, ali_id: str) -> Path | None:
        if self._data_dir is None:
            logger.warning("ALIBABA_DATA_DIR not set")
            return None
        if not ali_id:
            logger.warning("SelfInfoPool has no ali_id yet")
            return None

        db_path = self._data_dir / "IMServiceDir" / "MessageSDK" / f"{ali_id}@icbu" / "database" / "im.sqlite"
        if not db_path.exists():
            logger.warning("Encrypted DB not found: {}", db_path)
            return None

        return db_path

    # -- Key management --------------------------------------------------------

    def _key_cache_path(self) -> Path:
        assert self._cache_dir is not None
        return self._cache_dir / "aes_key.bin"

    def _save_key(self) -> None:
        try:
            self._key_cache_path().write_bytes(self._key)
        except OSError as exc:
            logger.warning("Failed to cache AES key: {}", exc)

    def _ensure_key(self, db_path: Path) -> bool:
        if self._key is not None:
            return True

        logger.info("Retrieving AES key from process memory...")
        try:
            self._key = retrieve_db_key(str(db_path))
            logger.info("AES key retrieved successfully")
            self._key_source = "live"
            self._save_key()
            return True
        except (ValueError, EOFError, OSError) as exc:
            logger.error("Failed to retrieve DB key from live process: {}", exc)

        # Fall back to cached key
        cached = self._key_cache_path()
        if cached.exists():
            try:
                self._key = cached.read_bytes()
                logger.info("Using cached AES key from {}", cached)
                self._key_source = "cached"
                return True
            except OSError as exc:
                logger.warning("Failed to read cached key: {}", exc)

        return False

    # -- Decryption ------------------------------------------------------------

    def _decrypt_db(self, src: Path, dst: Path) -> bool:
        try:
            encrypted = src.read_bytes()
            if len(encrypted) == 0 or len(encrypted) % 16 != 0:
                logger.error("Encrypted DB size is not a multiple of 16 bytes")
                return False

            cipher = AES.new(self._key, AES.MODE_ECB)
            decrypted = bytearray(len(encrypted))
            for offset in range(0, len(encrypted), 16):
                block = encrypted[offset : offset + 16]
                decrypted[offset : offset + 16] = cipher.decrypt(block)

            dst.write_bytes(decrypted)
            return True
        except Exception as exc:
            logger.error("DB decryption failed: {}", exc)
            return False

    # -- Cache refresh ---------------------------------------------------------

    def _has_fresh_cache(self, now: float) -> bool:
        return self._cached_db_path is not None and (now - self._cache_time) < _CACHE_TTL

    def _cache_path_for(self, ali_id: str) -> Path:
        assert self._cache_dir is not None
        return self._cache_dir / f"im_{ali_id}.sqlite"

    def _replace_connection(self, cached: Path, ali_id: str) -> None:
        old_conn = self._conn
        conn = open_readonly(cached)
        resolver = MsgTableResolver(ali_id)

        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                pass

        self._conn = conn
        self._resolver = resolver
        self._cached_db_path = cached
        self._cache_time = time.time()

    def _refresh(self) -> bool:
        now = time.time()
        if self._has_fresh_cache(now):
            return True

        with self._lock:
            if self._has_fresh_cache(time.time()):
                return True

            ali_id = self._get_self_ali_id()
            db_path = self._resolve_encrypted_db_path(ali_id)
            if db_path is None:
                return False

            if not self._ensure_key(db_path):
                return False

            cached = self._cache_path_for(ali_id)
            logger.info("Decrypting IM database...")
            if not self._decrypt_db(db_path, cached):
                return False

            try:
                self._replace_connection(cached, ali_id)
            except sqlite3.Error as exc:
                logger.error("Failed to open cached IM database: {}", exc)
                return False
            self._sync_cached_db(cached, ali_id)
            logger.info("IM database refreshed (cached at {})", cached)
            return True

    def _sync_cached_db(self, cached: Path, ali_id: str) -> None:
        sync_im_database(cached, ali_id, get_self_info_pool().get())

    def sync_to_crm(self, wait: bool = False) -> None:
        cached = self._cached_db_path
        ali_id = self._get_self_ali_id()
        if cached is None or not ali_id:
            return
        future = sync_im_database(cached, ali_id, get_self_info_pool().get())
        if wait:
            future.result()

    # -- Public API ------------------------------------------------------------

    def key_status(self) -> tuple[bool, str]:
        """Return ``(has_key, source)`` where *source* is ``"live"``, ``"cached"``, or ``"none"``."""
        if self._key is not None:
            return True, self._key_source
        if self._key_cache_path().exists():
            return True, "cached"
        return False, "none"

    def get_connection(self) -> sqlite3.Connection | None:
        self._refresh()
        return self._conn

    def get_resolver(self) -> MsgTableResolver | None:
        self._refresh()
        return self._resolver


def get_im_db_middleware() -> IMDBMiddleware:
    return IMDBMiddleware()
