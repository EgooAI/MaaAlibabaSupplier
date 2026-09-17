"""IM database middleware — decrypts and caches the encrypted Alibaba IM SQLite database.

Provides a clean interface that hides encryption semantics from consumers.
Identity (self ali_id) comes solely from the manual selection in Settings;
per-account AES keys live in the app database (auto-captured from the running
AliWorkbench process or manually entered). The decrypted database is cached
for a configurable TTL to avoid repeated decryption.
"""

from __future__ import annotations

import shutil
import sqlite3
import struct
import os
from concurrent.futures import Future
from contextlib import contextmanager

from loguru import logger
import threading
import time
import zlib
from pathlib import Path
from uuid import uuid4

from Crypto.Cipher import AES

from backend.app.shared.mitm.pool import SelfInfo
from backend.app.shared.utils.im_db_decryptor import retrieve_db_key
from backend.app.shared.backend.im_chat_db import list_msg_tables, open_readonly
from backend.app.shared.backend.account_context import AccountContext, account_lock, changing_account, get_account_context, invalidate_account_context
from backend.app.shared.backend.sync_coordinator import SyncCoordinator
from backend.app.shared.crm import sync_im_database
from backend.app.shared.crm.sync_store import read_sync_state
from backend.app.shared.crm.account_keys import get_key_hex, get_key_source, save_key
from backend.app.shared.crm.identities import self_sender_id, strip_icbu_suffix
from backend.app.shared.utils.app_config import (
    CONFIG_KEY_ALIBABA_DATA_DIR,
    CONFIG_KEY_IM_DATA_REVISION,
    CONFIG_KEY_SELF_ALI_ID,
    get_configured_alibaba_data_dir,
    get_configured_self_ali_id,
    get_im_data_revision,
    set_configured_self_ali_id,
    write_app_config,
)
from backend.app.shared.utils.env import get_env_str
from backend.app.shared.utils.im_wal import WalError, checksum_chain, read_wal_header, rekey_wal_copy
from backend.app.shared.utils.settings import resolve_backend_root

_CACHE_TTL = 5.0  # seconds
# Minimum gap between two full source rebuilds; bursts of client writes coalesce.
_MIN_REFRESH_INTERVAL = 3.0  # seconds
# Backoff ceiling after consecutive rebuild failures (3s, 6s, 12s, ... capped here).
_MAX_BACKOFF = 30.0  # seconds
# Disabling WAL refuses snapshots with frames instead of claiming completeness.
_WAL_PIPELINE_ENV = "MAA_IM_WAL_PIPELINE"

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
                instance._lock = threading.RLock()
                instance._data_dir: Path | None = None
                instance._key: bytes | None = None
                instance._key_ali_id: str = ""
                instance._key_source: str = "none"
                instance._key_validation: str = "unverified"
                instance._data_dir_source: str = SOURCE_NONE
                instance._cache_dir: Path | None = None
                instance._cached_db_path: Path | None = None
                instance._reader_pins: dict[Path, int] = {}
                instance._cache_time: float = 0.0
                instance._source_fingerprint: tuple[int, int, int, int, int] | None = None
                instance._source_crc32: int | None = None
                instance._source_wal_crc32: int | None = None
                instance._conn: sqlite3.Connection | None = None
                instance._last_refresh_start: float = 0.0
                instance._backoff_until: float = 0.0
                instance._consecutive_failures: int = 0
                instance._last_error: str = ""
                instance._error_code: str = ""
                instance._wal_frames_applied: int = 0
                instance._last_refresh_ms: float = 0.0
                instance._source_revision: int = 0
                instance._sync_future: Future | None = None
                instance._auto_enabled = False
                instance._source_dirty = False
                instance._last_checked: float | None = None
                instance._source_status: dict = {}
                instance._coordinator = SyncCoordinator(instance._submit_sync)
                try:
                    instance._source_revision = get_im_data_revision()
                except Exception:
                    logger.debug("IM数据版本号读取失败，从0开始")
                instance._init_data_dir()
                instance._select_sync_context()
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
        self._key_validation = "unverified"
        self._cached_db_path = None
        self._cache_time = 0.0
        self._source_fingerprint = None
        self._source_crc32 = None
        self._source_wal_crc32 = None
        self._last_refresh_start = 0.0
        self._backoff_until = 0.0
        self._consecutive_failures = 0
        self._last_error = ""
        self._error_code = ""
        self._wal_frames_applied = 0
        self._last_refresh_ms = 0.0
        self._sync_future = None
        self._auto_enabled = False
        self._source_dirty = False
        self._last_checked = None
        self._publish_source_status()

    def _submit_sync(self, target):
        return sync_im_database(
            target.path, target.context.self_ali_id, SelfInfo(ali_id=target.context.self_ali_id),
            source_revision=target.source_revision, data_dir=target.context.data_dir,
        )

    def _select_sync_context(self):
        context = get_account_context()
        # Invalidate pending work before the potentially slower archive read.
        self._coordinator.select(context)
        restored = None
        if context.self_ali_id:
            try:
                restored = read_sync_state(context.self_ali_id, context.data_dir)
            except (sqlite3.Error, OSError) as exc:
                self._last_error = str(exc) or type(exc).__name__
                self._error_code = "sync_error"
                logger.warning("CRM sync state restore failed: {}", exc)
        if restored:
            self._source_revision = max(self._source_revision, restored["applied_source_revision"])
            self._coordinator.select(context, restored)
        self._publish_source_status()

    def set_data_dir(self, raw: str) -> None:
        """Apply a newly configured data dir at runtime (persists to config file)."""
        raw = raw.strip()
        with changing_account(), self._lock:
            configured = get_configured_alibaba_data_dir()
            if raw == configured or (raw and configured and Path(raw) == Path(configured)):
                return
            write_app_config({CONFIG_KEY_ALIBABA_DATA_DIR: raw, CONFIG_KEY_SELF_ALI_ID: ""})
            self._data_dir = Path(raw) if raw else None
            self._data_dir_source = SOURCE_FILE if raw else SOURCE_NONE
            self._reset_runtime_state()
            invalidate_account_context()
            self._select_sync_context()

    def set_self_ali_id(self, ali_id: str) -> None:
        """Apply a newly selected identity at runtime (persists to config file)."""
        ali_id = ali_id.strip()
        with changing_account(), self._lock:
            if ali_id == self._resolve_self_ali_id():
                return
            set_configured_self_ali_id(ali_id)
            self._reset_runtime_state()
            invalidate_account_context()
            self._select_sync_context()

    def drop_cached_key(self) -> None:
        """Invalidate decrypted data and require explicit key validation again."""
        with account_lock, self._lock:
            self._reset_runtime_state()
            invalidate_account_context()
            self._select_sync_context()

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
        data_dir, source = self._data_dir, self._data_dir_source
        if data_dir is None:
            return {
                "state": STATE_UNCONFIGURED,
                "path": "",
                "source": SOURCE_NONE,
                "detail": "尚未配置阿里客户端数据目录，请在设置页填写或从候选中选择",
            }
        if not data_dir.exists():
            return {
                "state": STATE_INVALID,
                "path": str(data_dir),
                "source": source,
                "detail": "配置的目录不存在，请检查路径或重新选择",
            }
        if not self.looks_like_data_dir(data_dir):
            return {
                "state": STATE_INVALID,
                "path": str(data_dir),
                "source": source,
                "detail": "目录下未找到 IMServiceDir/MessageSDK IM 数据库结构",
            }
        return {
            "state": STATE_OK,
            "path": str(data_dir),
            "source": source,
            "detail": "",
        }

    # -- Identity (manual selection is the single source) -----------------------

    @staticmethod
    def _resolve_self_ali_id() -> str:
        return get_configured_self_ali_id()

    def scan_ali_ids(self) -> list[dict]:
        """List accounts found under the configured data dir, most-recent first."""
        with account_lock, self._lock:
            data_dir = self._data_dir
        if data_dir is None:
            return []
        try:
            entries = (data_dir / DATA_DIR_SIGNATURE).glob("*/database/im.sqlite")
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
        with account_lock, self._lock:
            data_dir = self._data_dir
        if data_dir is None:
            logger.warning("阿里客户端数据目录未配置，请在设置页配置后重试")
            return None
        if not ali_id:
            logger.warning("尚未在设置页选择阿里账号身份，请选择后重试")
            return None

        db_path = data_dir / "IMServiceDir" / "MessageSDK" / self_sender_id(ali_id) / "database" / "im.sqlite"
        if not db_path.exists():
            logger.warning("Encrypted DB not found: {}", db_path)
            return None

        return db_path

    # -- Key management (per-account, stored in the app database) --------------

    @staticmethod
    def _verify_source_key(key: bytes, db_path: Path) -> bool:
        """Unreadable/incomplete headers are transient source failures, not bad keys."""
        with db_path.open("rb") as stream:
            header = stream.read(16)
        if len(header) != 16:
            raise OSError("IM source header is incomplete")
        try:
            return AES.new(key, AES.MODE_ECB).decrypt(header) == b"SQLite format 3\x00"
        except ValueError:
            return False

    def _migrate_legacy_key_file(self, db_path: Path, ali_id: str) -> bytes | None:
        """One-time import of the legacy aes_key.bin into the database."""
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
                continue
            if not self._verify_source_key(raw, db_path):
                continue
            save_key(ali_id, raw, "auto")
            # Keep the legacy file: another account may still need it.
            logger.info("Migrated legacy AES key file into database for ali_id={}", ali_id)
            return raw
        return None

    def _ensure_key(self, db_path: Path, ali_id: str) -> bool:
        if self._key is not None and self._key_ali_id == ali_id:
            if self._verify_source_key(self._key, db_path):
                self._key_validation = "valid"
                return True
        self._key = None
        self._key_ali_id = ""
        self._key_source = "none"

        stored = get_key_hex(ali_id)
        self._key_validation = "unverified" if stored is not None else "unavailable"
        if stored is not None:
            if self._verify_source_key(stored, db_path):
                self._key = stored
                self._key_ali_id = ali_id
                self._key_source = get_key_source(ali_id) or "auto"
                self._key_validation = "valid"
                return True
            self._key_validation = "invalid"

        stored = self._migrate_legacy_key_file(db_path, ali_id)
        if stored is not None:
            self._key = stored
            self._key_ali_id = ali_id
            self._key_source = get_key_source(ali_id) or "auto"
            self._key_validation = "valid"
            return True

        logger.info("Retrieving AES key from process memory...")
        try:
            key = retrieve_db_key(str(db_path))
        except (ValueError, EOFError, OSError) as exc:
            logger.warning("Failed to retrieve DB key from live process: {}", exc)
            return False

        if not self._verify_source_key(key, db_path):
            self._key_validation = "invalid"
            return False
        save_key(ali_id, key, "auto")
        self._key = key
        self._key_ali_id = ali_id
        self._key_source = "live"
        self._key_validation = "valid"
        logger.info("AES key retrieved successfully")
        return True

    # -- Decryption ------------------------------------------------------------

    def _decrypt_db(self, src: Path, dst: Path) -> int | None:
        """Decrypt src to dst; return CRC32 of the encrypted source, or None on failure."""
        try:
            encrypted = src.read_bytes()
            if len(encrypted) == 0 or len(encrypted) % 16 != 0:
                logger.error("Encrypted DB size is not a multiple of 16 bytes")
                return None

            # One-shot ECB decrypt: page-aligned whole-file decrypt is ~100x faster
            # than a per-block Python loop on a 10MB+ database.
            decrypted = AES.new(self._key, AES.MODE_ECB).decrypt(encrypted)
            dst.write_bytes(decrypted)
            return zlib.crc32(encrypted) & 0xFFFFFFFF
        except Exception as exc:
            logger.error("DB decryption failed: {}", exc)
            return None

    @staticmethod
    def _wal_source_for(db_path: Path) -> Path:
        return db_path.with_name(db_path.name + "-wal")

    @staticmethod
    def _wal_cache_for(cached: Path) -> Path:
        return cached.with_name(cached.name + "-wal")

    @staticmethod
    def _shm_cache_for(cached: Path) -> Path:
        # SQLite creates this next to any WAL-mode database it opens.
        return cached.with_name(cached.name + "-shm")

    def _wal_pipeline_enabled(self) -> bool:
        return get_env_str(_WAL_PIPELINE_ENV, "1") != "0"

    def _copy_source_pair(self, db_path: Path, cached: Path) -> Path | None:
        """Read-only copy of the live main file plus its WAL sidecar (if any).

        Returns the copied WAL path, or None when there is no sidecar.
        Raises OSError when the main file cannot be read
        (locked, vanished); the caller keeps serving the previous cache.
        """
        shutil.copyfile(db_path, cached)
        src_wal = self._wal_source_for(db_path)
        if not src_wal.exists():
            return None
        dst_wal = self._wal_cache_for(cached)
        shutil.copyfile(src_wal, dst_wal)
        return dst_wal

    def _verify_cached_db(self, cached: Path) -> bool:
        """Smoke-test a freshly built cache copy: it must open and list msg tables.

        Deliberately NOT integrity_check/quick_check: the schema ships FTS5
        tables using Alibaba's custom 'mobile' tokenizer, which stock SQLite
        cannot instantiate.
        """
        try:
            conn = open_readonly(cached)
            try:
                list_msg_tables(conn)
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("重建的IM缓存校验失败，已丢弃: {}", exc)
            return False
        return True

    def _discard_build(self, cached: Path) -> None:
        for path in (cached, self._wal_cache_for(cached), self._shm_cache_for(cached)):
            try:
                path.unlink()
            except OSError:
                pass

    def _rebuild_cache(self, db_path: Path, ali_id: str) -> tuple[Path, int] | None:
        """Accept only a stable source pair whose captured bytes match the copy."""
        self._build_error = ("缓存重建失败", "decrypt_error")
        cached = self._cache_path_for(ali_id)
        try:
            captured = self._capture_source(db_path)
            if captured is None:
                self._build_error = ("Source changed during capture", "source_changed")
                return None
            wal_copy = self._copy_source_pair(db_path, cached)
            copied = (self._crc32_of(cached), self._crc32_of(wal_copy) if wal_copy else 0)
            if self._capture_source(db_path) != captured or copied != captured[1:]:
                self._build_error = ("Source changed during copy", "source_changed")
                self._discard_build(cached)
                return None
        except OSError as exc:
            logger.warning("IM源库拷贝失败（可能被客户端锁定），沿用旧缓存: {}", exc)
            self._build_error = (str(exc), "source_copy_error")
            self._discard_build(cached)
            return None
        crc = self._decrypt_db(cached, cached)
        if crc is None:
            self._discard_build(cached)
            return None
        frames = 0
        if wal_copy is not None:
            try:
                raw = wal_copy.read_bytes()
                if raw:
                    header = read_wal_header(raw)
                    page_size = header["page_size"]
                    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
                        raise WalError("Invalid WAL page size")
                    seed = (header["cksum0"], header["cksum1"])
                    for offset in range(32, len(raw), 24 + page_size):
                        end = offset + 24 + page_size
                        if offset + 24 > len(raw):
                            raise WalError("Incomplete WAL frame header")
                        if raw[offset + 8:offset + 16] != raw[16:24]:
                            # RESTART reuses the physical file; old-salt tail frames
                            # are outside the current WAL, even if their bytes remain.
                            wal_copy.write_bytes(raw[:offset])
                            break
                        if end > len(raw):
                            raise WalError("Incomplete WAL frame")
                        if struct.unpack(">I", raw[offset:offset + 4])[0] == 0:
                            raise WalError("Invalid WAL page number")
                        seed = checksum_chain(raw[offset:offset + 8], seed)
                        seed = checksum_chain(raw[offset + 24:end], seed)
                        if seed != struct.unpack(">II", raw[offset + 16:offset + 24]):
                            raise WalError("WAL frame checksum mismatch")
                    with cached.open("rb") as stream:
                        main_page_size = struct.unpack(">H", stream.read(32)[16:18])[0]
                    main_page_size = 65536 if main_page_size == 1 else main_page_size
                    frames = rekey_wal_copy(wal_copy, self._key, main_page_size)
                    if frames and not self._wal_pipeline_enabled():
                        self._build_error = ("WAL contains frames but its pipeline is disabled", "wal_disabled")
                        self._discard_build(cached)
                        return None
                else:
                    wal_copy.unlink()
            except (WalError, OSError, struct.error) as exc:
                self._build_error = (str(exc), "wal_error")
                self._discard_build(cached)
                return None
        if not self._verify_cached_db(cached):
            self._discard_build(cached)
            return None
        self._built_source = captured
        return cached, frames

    # -- Cache refresh ---------------------------------------------------------

    def _cache_path_for(self, ali_id: str) -> Path:
        assert self._cache_dir is not None
        return self._cache_dir / f"im_{ali_id}_{uuid4().hex}.sqlite"

    def _cleanup_stale_caches(self, keep: Path) -> None:
        assert self._cache_dir is not None
        with self._lock:
            protected = {keep, self._wal_cache_for(keep), self._shm_cache_for(keep)}
            for cached in self._coordinator.pinned_paths():
                protected.update((cached, self._wal_cache_for(cached), self._shm_cache_for(cached)))
            for cached in self._reader_pins:
                protected.update((cached, self._wal_cache_for(cached), self._shm_cache_for(cached)))
            if self._cached_db_path is not None:
                # Never remove the live files out from under open readers.
                protected.add(self._cached_db_path)
                protected.add(self._wal_cache_for(self._cached_db_path))
                protected.add(self._shm_cache_for(self._cached_db_path))
            for path in self._cache_dir.glob("im_*.sqlite*"):
                if path in protected:
                    continue
                try:
                    path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _stat_pair(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)

    def _source_fingerprint_of(self, db_path: Path) -> tuple[int, int, int, int, int] | None:
        """Fingerprint covering the main file AND the WAL sidecar.

        Client writes land in ``im.sqlite-wal`` first; a main-only fingerprint
        would never notice them.
        """
        try:
            main_mtime_ns, main_size = self._stat_pair(db_path)
        except OSError:
            return None
        src_wal = self._wal_source_for(db_path)
        try:
            wal_mtime_ns, wal_size = self._stat_pair(src_wal)
            return (main_mtime_ns, main_size, wal_mtime_ns, wal_size, 1)
        except OSError:
            return (main_mtime_ns, main_size, 0, 0, 0)

    def _crc32_of(self, db_path: Path) -> int | None:
        try:
            crc = 0
            with db_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    crc = zlib.crc32(chunk, crc)
            return crc & 0xFFFFFFFF
        except OSError:
            return None

    def _wal_crc32_of(self, db_path: Path) -> int | None:
        src_wal = self._wal_source_for(db_path)
        if not src_wal.exists():
            return 0
        return self._crc32_of(src_wal)

    def _capture_source(self, db_path: Path):
        fingerprint = self._source_fingerprint_of(db_path)
        main_crc, wal_crc = self._crc32_of(db_path), self._wal_crc32_of(db_path)
        if fingerprint is None or main_crc is None or wal_crc is None:
            return None
        if fingerprint != self._source_fingerprint_of(db_path):
            return None
        return fingerprint, main_crc, wal_crc

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
        wal_crc = self._wal_crc32_of(db_path)
        if wal_crc is None or wal_crc != self._source_wal_crc32:
            return False
        self._cache_time = now
        return True

    def _replace_connection(self, cached: Path, ali_id: str) -> None:
        old_conn = self._conn
        # Requests may refresh and reset on different threads; lifecycle is locked.
        conn = sqlite3.connect(cached.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                logger.debug("旧IM缓存连接关闭失败，已忽略")

        self._conn = conn
        self._cached_db_path = cached
        self._cache_time = time.time()

    def _note_success(self, elapsed_ms: float, wal_frames: int) -> None:
        self._consecutive_failures = 0
        self._backoff_until = 0.0
        self._last_error = ""
        self._error_code = ""
        self._last_refresh_ms = elapsed_ms
        self._wal_frames_applied = wal_frames
        self._source_revision += 1
        try:
            write_app_config({CONFIG_KEY_IM_DATA_REVISION: self._source_revision})
        except OSError:
            logger.debug("IM数据版本号持久化失败，仅保留内存值")

    def _note_failure(self, detail: str, code: str = "decrypt_error") -> None:
        self._consecutive_failures += 1
        delay = min(_MIN_REFRESH_INTERVAL * (2 ** min(self._consecutive_failures - 1, 4)), _MAX_BACKOFF)
        self._backoff_until = time.time() + delay
        self._last_error = detail
        self._error_code = code
        logger.warning("IM源库刷新失败({}次连败)，{}s后重试: {}", self._consecutive_failures, delay, detail)

    def _refresh(self) -> bool:
        with account_lock, self._lock:
            try:
                return self._refresh_locked()
            finally:
                self._publish_source_status()

    def _refresh_locked(self) -> bool:
        if not self._auto_enabled:
            return self._cached_db_path is not None
        ali_id = self._resolve_self_ali_id()
        now = time.time()
        if now < self._backoff_until:
            return self._cached_db_path is not None
        db_path = self.resolve_encrypted_db_path(ali_id)
        self._last_checked = now
        if db_path is None:
            self._note_failure("IM source database is missing or not configured", "source_missing")
            return self._cached_db_path is not None
        if self._wal_frames_applied and not self._wal_pipeline_enabled():
            self._note_failure("WAL pipeline is disabled", "wal_disabled")
            return self._cached_db_path is not None

        if self._is_source_fresh(db_path, now):
            self._last_error = self._error_code = ""
            self._backoff_until = 0.0
            self._consecutive_failures = 0
            self._source_dirty = False
            return True
        self._source_dirty = True

        # Coalesce write bursts while serving the previous cache.
        if self._cached_db_path is not None and now - self._last_refresh_start < _MIN_REFRESH_INTERVAL:
            return True

        try:
            key_valid = self._key is not None and self._verify_source_key(self._key, db_path)
        except OSError as exc:
            self._note_failure(str(exc), "source_unreadable")
            return self._cached_db_path is not None
        if not key_valid:
            self._key_validation = "invalid" if self._key else "unavailable"
            self._auto_enabled = False
            self._note_failure("AES Key不可用", "key_unavailable")
            return self._cached_db_path is not None

        self._last_refresh_start = now
        self._publish_source_status()
        started = time.perf_counter()
        logger.info("Decrypting IM database...")
        built = self._rebuild_cache(db_path, ali_id)
        if built is None:
            self._note_failure(*self._build_error)
            return self._cached_db_path is not None
        cached, wal_frames = built

        try:
            self._replace_connection(cached, ali_id)
        except sqlite3.Error as exc:
            logger.error("Failed to open cached IM database: {}", exc)
            self._discard_build(cached)
            self._note_failure("缓存打开失败")
            return self._cached_db_path is not None
        self._source_fingerprint, self._source_crc32, self._source_wal_crc32 = self._built_source
        self._source_dirty = False
        self._note_success((time.perf_counter() - started) * 1000, wal_frames)
        self.sync_to_crm()
        self._cleanup_stale_caches(cached)
        logger.info("IM database refreshed (cached at {})", cached)
        return True

    def sync_to_crm(self, wait: bool = False) -> Future | None:
        """Wait for this source target, including a superseding pending target."""
        with account_lock, self._lock:
            context = get_account_context()
            cached = self._cached_db_path
            if cached is None or not context.self_ali_id or not self._auto_enabled:
                return
            future = self._coordinator.request(context, cached, self._source_revision)
            self._sync_future = future
            self._coordinator.tick()
        if wait:
            self.wait_for_sync(future)
        return future

    def wait_for_sync(self, future: Future) -> dict:
        """Wait for the captured target without selecting or submitting another one."""
        return self._coordinator.wait(future)

    def sync_tick(self):
        """One controllable worker iteration; GUI contention skips source work."""
        self._coordinator.tick()
        if not account_lock.acquire(blocking=False):
            return
        try:
            if not self._lock.acquire(blocking=False):
                return
            try:
                if self._auto_enabled:
                    self._refresh()
            finally:
                self._lock.release()
        finally:
            account_lock.release()

    def _publish_source_status(self):
        fingerprint = self._source_fingerprint
        self._source_status = {
            "source_revision": self._source_revision,
            "cache_time": self._cache_time,
            "source_mtime": fingerprint[0] / 1e9 if fingerprint else None,
            "wal_frames_applied": self._wal_frames_applied,
            "last_refresh_ms": round(self._last_refresh_ms, 1),
            "last_checked": self._last_checked,
            "last_error": self._last_error,
            "error_code": self._error_code,
            "retry_at": self._backoff_until or None,
            "key_validation": self._key_validation,
            "auto_enabled": self._auto_enabled,
            "source_dirty": self._source_dirty,
        }

    def sync_status(self) -> dict:
        """Pure observation: no source reads, completion draining or scheduling."""
        source = self._source_status
        sync = self._coordinator.snapshot()
        error = source["last_error"] or sync["last_error"]
        syncing = sync["syncing"] or sync["pending"]
        stale = bool(error or not source["auto_enabled"] or source["source_dirty"] or syncing
                     or source["source_revision"] != sync["applied_source_revision"])
        phase = "error" if error else ("syncing" if syncing else sync["phase"])
        return {
            **source, **sync,
            "last_error": error,
            "error_code": source["error_code"] or ("sync_error" if sync["last_error"] else ""),
            "retry_at": max(source["retry_at"] or 0, sync["retry_at"] or 0) or None,
            "wal_pipeline": self._wal_pipeline_enabled(),
            "phase": phase, "stale": stale, "ready": phase == "ready" and not stale,
            "freshness": "stale" if stale and error else ("syncing" if syncing else ("stale" if stale else "fresh")),
        }

    # -- Public API ------------------------------------------------------------

    @contextmanager
    def pin_source_reader(self, context: AccountContext):
        """Pin verified cache metadata under locks; the caller reads outside them.

        This never refreshes or captures keys. Pins outlive runtime resets. Epoch
        is intentionally excluded: local evidence can be read after a restart in
        the same selected seller/directory scope. GUI authorization is separate.
        """
        with account_lock, self._lock:
            current = get_account_context()
            directory = os.path.normcase(str(Path(context.data_dir).expanduser().resolve()))
            if (not context.self_ali_id or not context.data_dir
                    or current.self_ali_id != context.self_ali_id
                    or not current.data_dir or self._data_dir is None
                    or os.path.normcase(str(Path(current.data_dir).expanduser().resolve())) != directory
                    or os.path.normcase(str(self._data_dir.expanduser().resolve())) != directory):
                raise ValueError("source_context_mismatch")
            if (not self._auto_enabled or self._key_validation != "valid"
                    or self._key is None or self._key_ali_id != context.self_ali_id):
                raise ValueError("source_not_verified")
            cached = self._cached_db_path
            if cached is None or self._source_fingerprint is None:
                raise ValueError("source_cache_missing")
            source = (Path(directory) / DATA_DIR_SIGNATURE / self_sender_id(context.self_ali_id)
                      / "database" / "im.sqlite")
            captured = {
                "path": cached,
                "origin": {"seller": context.self_ali_id, "data_dir": directory,
                           "source_path": os.path.normcase(str(source))},
                "source_revision": self._source_revision,
                "signature": (self._source_fingerprint, self._source_crc32, self._source_wal_crc32),
            }
            self._reader_pins[cached] = self._reader_pins.get(cached, 0) + 1
        try:
            yield captured
        finally:
            with self._lock:
                remaining = self._reader_pins[cached] - 1
                if remaining:
                    self._reader_pins[cached] = remaining
                else:
                    del self._reader_pins[cached]

    def key_status(self) -> tuple[bool, str]:
        """Return ``(has_key, source)`` for the selected identity (no live lookup)."""
        ali_id = self._resolve_self_ali_id()
        if not ali_id:
            return False, "none"
        if self._key is not None and self._key_ali_id == ali_id:
            return True, self._key_source
        source = get_key_source(ali_id)
        return (True, source) if source else (False, "none")

    def key_validation_status(self) -> str:
        """Return unverified/valid/invalid/unavailable without attempting capture."""
        return self._key_validation

    def get_connection(self) -> sqlite3.Connection | None:
        with account_lock:
            self._refresh()
            with self._lock:
                return self._conn

    def retry_connection(self, *, wait: bool = False) -> sqlite3.Connection | None:
        """The only entry point allowed to capture a key and enable source checks."""
        future = None
        with account_lock, self._lock:
            self._backoff_until = 0.0
            self._last_refresh_start = 0.0
            self._consecutive_failures = 0
            self._coordinator.clear_retry()
            ali_id = self._resolve_self_ali_id()
            db_path = self.resolve_encrypted_db_path(ali_id)
            self._last_checked = time.time()
            try:
                if db_path is None:
                    self._note_failure("IM source database is missing or not configured", "source_missing")
                elif not self._ensure_key(db_path, ali_id):
                    self._auto_enabled = False
                    self._note_failure("AES Key不可用", "key_unavailable")
                else:
                    self._auto_enabled = True
                    # Force content validation even when metadata and the TTL match.
                    self._cache_time = 0.0
                    previous_revision = self._source_revision
                    self._refresh()
                    if not self._last_error:
                        if self._source_revision == previous_revision:
                            self.sync_to_crm()
                        future = self._sync_future
            except OSError as exc:
                self._note_failure(str(exc), "source_unreadable")
            self._publish_source_status()
            conn = self._conn
        if wait and future is not None:
            self.wait_for_sync(future)
        return conn


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
