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
from concurrent.futures import Future

from loguru import logger
import threading
import time
import zlib
from pathlib import Path
from queue import Empty, SimpleQueue
from uuid import uuid4

from Crypto.Cipher import AES

from backend.app.shared.mitm.pool import SelfInfo
from backend.app.shared.utils.im_db_decryptor import retrieve_db_key
from backend.app.shared.backend.im_chat_db import list_msg_tables, open_readonly
from backend.app.shared.backend.account_context import account_lock, changing_account, get_account_context, invalidate_account_context
from backend.app.shared.crm import sync_im_database
from backend.app.shared.crm.account_keys import get_key_hex, get_key_source, save_key, verify_key_against_db
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
from backend.app.shared.utils.im_wal import WalError, rekey_wal_copy
from backend.app.shared.utils.settings import resolve_backend_root

_CACHE_TTL = 5.0  # seconds
# Minimum gap between two full source rebuilds; bursts of client writes coalesce.
_MIN_REFRESH_INTERVAL = 3.0  # seconds
# Backoff ceiling after consecutive rebuild failures (3s, 6s, 12s, ... capped here).
_MAX_BACKOFF = 30.0  # seconds
# Kill switch for the WAL sidecar pipeline ("0" restores main-file-only decrypt).
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
                instance._data_revision: int = 0
                instance._sync_future: Future | None = None
                instance._sync_epoch: str = ""
                instance._sync_path: Path | None = None
                instance._syncing_caches: dict[Future, Path] = {}
                instance._completed_syncs: SimpleQueue[tuple[Future, str, float]] = SimpleQueue()
                instance._sync_phase: str = "idle"
                instance._sync_error: str = ""
                instance._last_success: float | None = None
                try:
                    instance._data_revision = get_im_data_revision()
                except Exception:
                    logger.debug("IM数据版本号读取失败，从0开始")
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
        # Copies remain protected until their queued completions are drained.
        self._sync_future = None
        self._sync_epoch = ""
        self._sync_path = None
        self._sync_phase = "idle"
        self._sync_error = ""
        self._last_success = None

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

    def set_self_ali_id(self, ali_id: str) -> None:
        """Apply a newly selected identity at runtime (persists to config file)."""
        ali_id = ali_id.strip()
        with changing_account(), self._lock:
            if ali_id == self._resolve_self_ali_id():
                return
            set_configured_self_ali_id(ali_id)
            self._reset_runtime_state()
            invalidate_account_context()

    def drop_cached_key(self) -> None:
        """Invalidate decrypted data too, forcing the next read to verify the key."""
        with account_lock, self._lock:
            self._reset_runtime_state()

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
        with self._lock:
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
            if not verify_key_against_db(raw, db_path):
                continue
            save_key(ali_id, raw, "auto")
            # Keep the legacy file: another account may still need it.
            logger.info("Migrated legacy AES key file into database for ali_id={}", ali_id)
            return raw
        return None

    def _ensure_key(self, db_path: Path, ali_id: str) -> bool:
        if self._key is not None and self._key_ali_id == ali_id:
            if verify_key_against_db(self._key, db_path):
                self._key_validation = "valid"
                return True
        self._key = None
        self._key_ali_id = ""
        self._key_source = "none"

        stored = get_key_hex(ali_id)
        self._key_validation = "invalid" if stored is not None else "unavailable"
        if stored is not None and verify_key_against_db(stored, db_path):
            self._key = stored
            self._key_ali_id = ali_id
            self._key_source = get_key_source(ali_id) or "auto"
            self._key_validation = "valid"
            return True

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
            if not verify_key_against_db(key, db_path):
                self._key_validation = "invalid"
                return False
            save_key(ali_id, key, "auto")
            self._key = key
            self._key_ali_id = ali_id
            self._key_source = "live"
            self._key_validation = "valid"
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

        Returns the copied WAL path, or None when there is no sidecar / the
        pipeline is disabled. Raises OSError when the main file cannot be read
        (locked, vanished); the caller keeps serving the previous cache.
        """
        shutil.copyfile(db_path, cached)
        if not self._wal_pipeline_enabled():
            return None
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
        """Build a fresh cache copy at a new timestamped path.

        Returns (cache_path, wal_frames_applied), or None when the source
        could not be turned into a verified copy. A WAL-sidecar failure never
        fails the whole rebuild — it degrades to main-file-only.
        """
        cached = self._cache_path_for(ali_id)
        try:
            wal_copy = self._copy_source_pair(db_path, cached)
        except OSError as exc:
            logger.warning("IM源库拷贝失败（可能被客户端锁定），沿用旧缓存: {}", exc)
            self._discard_build(cached)
            return None
        crc = self._decrypt_db(cached, cached)
        if crc is None:
            self._discard_build(cached)
            return None
        frames = 0
        if wal_copy is not None:
            try:
                with cached.open("rb") as stream:
                    page_size = struct.unpack(">H", stream.read(32)[16:18])[0]
                frames = rekey_wal_copy(wal_copy, self._key, page_size)
            except (WalError, OSError, struct.error) as exc:
                logger.warning("WAL解密失败，降级为仅主文件: {}", exc)
                try:
                    wal_copy.unlink()
                except OSError:
                    pass
                frames = 0
        if not self._verify_cached_db(cached):
            self._discard_build(cached)
            return None
        return cached, frames

    # -- Cache refresh ---------------------------------------------------------

    def _has_fresh_cache(self, now: float) -> bool:
        return self._cached_db_path is not None and (now - self._cache_time) < _CACHE_TTL

    def _cache_path_for(self, ali_id: str) -> Path:
        assert self._cache_dir is not None
        return self._cache_dir / f"im_{ali_id}_{uuid4().hex}.sqlite"

    def _cleanup_stale_caches(self, keep: Path) -> None:
        assert self._cache_dir is not None
        protected = {keep, self._wal_cache_for(keep), self._shm_cache_for(keep)}
        for cached in self._syncing_caches.values():
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
        self._data_revision += 1
        try:
            write_app_config({CONFIG_KEY_IM_DATA_REVISION: self._data_revision})
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
            ali_id = self._resolve_self_ali_id()
            db_path = self.resolve_encrypted_db_path(ali_id)
            now = time.time()
            if now < self._backoff_until:
                return self._cached_db_path is not None
            if db_path is None:
                self._note_failure("IM source database is missing or not configured", "source_missing")
                return self._has_fresh_cache(now)

            if self._is_source_fresh(db_path, now):
                return True

            # Coalesce write bursts while serving the previous cache.
            if self._cached_db_path is not None:
                if now - self._last_refresh_start < _MIN_REFRESH_INTERVAL:
                    return True

            if not self._ensure_key(db_path, ali_id):
                self._note_failure("AES Key不可用", "key_unavailable")
                return self._cached_db_path is not None

            self._last_refresh_start = now
            started = time.perf_counter()
            logger.info("Decrypting IM database...")
            built = self._rebuild_cache(db_path, ali_id)
            if built is None:
                self._note_failure("缓存重建失败")
                return self._cached_db_path is not None
            cached, wal_frames = built

            try:
                self._replace_connection(cached, ali_id)
            except sqlite3.Error as exc:
                logger.error("Failed to open cached IM database: {}", exc)
                self._discard_build(cached)
                self._note_failure("缓存打开失败")
                return self._cached_db_path is not None
            fingerprint = self._source_fingerprint_of(db_path)
            if fingerprint is not None:
                self._source_fingerprint = fingerprint
            self._source_crc32 = self._crc32_of(db_path)
            self._source_wal_crc32 = self._wal_crc32_of(db_path)
            self._note_success((time.perf_counter() - started) * 1000, wal_frames)
            self._cleanup_stale_caches(cached)
            self.sync_to_crm()
        logger.info("IM database refreshed (cached at {})", cached)
        return True

    def sync_to_crm(self, wait: bool = False) -> None:
        """Submit one immutable account/cache snapshot; wait outside both locks."""
        with account_lock, self._lock:
            self._drain_sync_completions()
            context = get_account_context()
            cached = self._cached_db_path
            ali_id = context.self_ali_id
            if cached is None or not ali_id:
                return
            future = self._sync_future
            if future is None or self._sync_epoch != context.epoch or (
                future.done() and (self._sync_path != cached or future.cancelled() or future.exception() is not None)
            ):
                self._sync_phase = "syncing"
                self._sync_error = ""
                self._sync_future = None
                try:
                    future = sync_im_database(cached, ali_id, SelfInfo(ali_id=ali_id))
                except Exception as exc:
                    self._sync_phase = "error"
                    self._sync_error = str(exc) or type(exc).__name__
                    if wait:
                        raise
                    return
                self._sync_future = future
                self._sync_epoch = context.epoch
                self._sync_path = cached
                self._syncing_caches[future] = cached
                future.add_done_callback(lambda done: self._finish_sync(done, context.epoch))
        if wait:
            try:
                future.result()
            finally:
                # Future.result() may return before its callbacks finish.
                self._finish_sync(future, context.epoch)
                with self._lock:
                    self._drain_sync_completions()

    def _finish_sync(self, future: Future, epoch: str) -> None:
        # Never block the CRM executor on a lock owned by a waiting GUI task.
        self._completed_syncs.put((future, epoch, time.time()))

    def _drain_sync_completions(self) -> None:
        """Apply completed work on the caller's thread; caller holds _lock."""
        current = self._sync_future
        if current in self._syncing_caches and current.done():
            # A result waiter can wake before the executor invokes callbacks.
            self._finish_sync(current, self._sync_epoch)
        while True:
            try:
                future, epoch, completed_at = self._completed_syncs.get_nowait()
            except Empty:
                return
            if future not in self._syncing_caches:
                continue
            self._syncing_caches.pop(future)
            if epoch != get_account_context().epoch or future is not self._sync_future:
                continue
            try:
                future.result()
                error = ""
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            self._sync_error = error
            self._sync_phase = "error" if error else "ready"
            if not error:
                self._last_success = completed_at

    def sync_status(self) -> dict:
        """Freshness/observability snapshot for the revision + status APIs."""
        with self._lock:
            self._drain_sync_completions()
            context = get_account_context()
            fingerprint = self._source_fingerprint
            source_mtime = fingerprint[0] / 1e9 if fingerprint else None
            error = self._last_error or self._sync_error
            phase = "error" if error else self._sync_phase
            return {
                "revision": self._data_revision,
                "cache_time": self._cache_time,
                "source_mtime": source_mtime,
                "wal_frames_applied": self._wal_frames_applied,
                "last_refresh_ms": round(self._last_refresh_ms, 1),
                "wal_pipeline": self._wal_pipeline_enabled(),
                "stale": bool(error),
                "last_error": error,
                "error_code": self._error_code or ("sync_error" if self._sync_error else ""),
                "phase": phase,
                "ready": phase == "ready" and self._cached_db_path is not None,
                "last_success": self._last_success,
                "key_validation": self._key_validation,
                "self_ali_id": context.self_ali_id,
                "epoch": context.epoch,
            }

    # -- Public API ------------------------------------------------------------

    def key_status(self) -> tuple[bool, str]:
        """Return ``(has_key, source)`` for the selected identity (no live lookup)."""
        with self._lock:
            ali_id = self._resolve_self_ali_id()
            if not ali_id:
                return False, "none"
            if self._key is not None and self._key_ali_id == ali_id:
                return True, self._key_source
            source = get_key_source(ali_id)
            return (True, source) if source else (False, "none")

    def key_validation_status(self) -> str:
        """Return unverified/valid/invalid/unavailable without attempting capture."""
        with self._lock:
            return self._key_validation

    def get_connection(self) -> sqlite3.Connection | None:
        with account_lock:
            self._refresh()
            with self._lock:
                return self._conn

    def retry_connection(self, *, wait: bool = False) -> sqlite3.Connection | None:
        """Clear backoff, revalidate the source key, rebuild and retry CRM sync."""
        with account_lock, self._lock:
            self._key = None
            self._key_ali_id = ""
            self._key_source = "none"
            self._key_validation = "unverified"
            self._source_fingerprint = None
            self._backoff_until = 0.0
            self._last_refresh_start = 0.0
            self._consecutive_failures = 0
            self._refresh()
            conn = self._conn
            future = self._sync_future
            epoch = self._sync_epoch
        if wait and future is not None:
            try:
                future.result()
            finally:
                self._finish_sync(future, epoch)
                with self._lock:
                    self._drain_sync_completions()
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
