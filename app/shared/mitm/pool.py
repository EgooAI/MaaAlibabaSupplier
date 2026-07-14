"""Thread-safe pools with SQLite persistence for MITM-intercepted data."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

_DEFAULT_DB_PATH = Path("data/pools.db")
FIXED_SELF_ALI_ID = "2500001168191"


def _get_db_path() -> Path:
    return Path(os.environ.get("MAA_POOLS_DB_PATH", str(_DEFAULT_DB_PATH)))


def _get_connection() -> sqlite3.Connection:
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("DROP TABLE IF EXISTS user_info")
    conn.execute("DROP TABLE IF EXISTS translation_cache")
    conn.commit()
    return conn


def _execute_schema(conn: sqlite3.Connection, sql: str) -> None:
    conn.execute(sql)


def _save_model(conn: sqlite3.Connection, table: str, key_column: str, key, model: BaseModel) -> None:
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({key_column}, data) VALUES (?, ?)",
        (key, model.model_dump_json()),
    )
    conn.commit()


def _clear_table(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(f"DELETE FROM {table}")
    conn.commit()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class UserInfo(BaseModel):
    """Consolidated user info from multiple Alibaba APIs."""

    # Identity
    ali_id: str = ""
    ali_member_id: str = ""
    login_id: str = ""
    encrypt_account_id: str = ""

    # Profile
    first_name: str = ""
    last_name: str = ""
    country_code: str = ""
    company_name: str = ""
    register_date: int = 0  # unix seconds

    # Contact
    email: str = ""
    mobile_number: str = ""
    phone_number: str = ""

    # Behavior (D90)
    product_view_count: int = 0
    valid_inquiry_count: int = 0
    replied_inquiry_count: int = 0
    valid_rfq_count: int = 0
    login_days: int = 0
    spam_inquiry_count: int = 0
    blacklisted_count: int = 0

    # Tags
    high_quality_level_tag: str = ""
    growth_level: str = ""
    preferred_industries: list[str] = Field(default_factory=list)

    # Availability
    available: bool = True
    joining_years: int = 0
    potential_score: int = 0
    recent_contact: bool = False
    email_validated: bool = False


class SelfInfo(BaseModel):
    """Current logged-in user's own profile."""

    ali_id: str = ""
    login_id: str = ""
    encrypt_account_id: str = ""
    first_name: str = ""
    last_name: str = ""
    country: str = ""
    company_name: str = ""
    avatar_url: str = ""
    account_status: str = ""


class ProductCard(BaseModel):
    """Product card from chat message."""

    card_id: str = ""
    title: str = ""
    price: str = ""
    display_price: str = ""
    product_image: str = ""
    moq: str = ""
    moq_unit: str = ""
    product_id: str = ""
    product_url: str = ""
    expired: bool = False


class GenericCard(BaseModel):
    """Non-product card captured from fetchcard (RFQ, order, feedback, etc.)."""

    card_type: int = 0
    card_id: str = ""
    source_url: str = ""
    raw_json: str = ""


class InquiryProduct(BaseModel):
    """A single product referenced in an inquiry card."""

    product_name: str = ""
    product_id: str = ""
    product_unit_price: str = ""
    product_moq: str = ""
    product_unit: str = ""
    product_image: str = ""
    discount_price: str = ""
    product_url: str = ""


class InquiryCard(BaseModel):
    """Inquiry (询盘) card from chat."""

    inquiry_id: str = ""
    inquiry_content: str = ""
    products: list[InquiryProduct] = Field(default_factory=list)
    product_image: str = ""
    is_seller: bool = False
    attachment_count: str = ""


# ---------------------------------------------------------------------------
# Merge helper
# ---------------------------------------------------------------------------


def _is_empty(val) -> bool:
    if isinstance(val, bool):
        return False  # bool False is a valid value, not "empty"
    return val is None or val == "" or val == 0 or val == []


def _merge_user_info(existing: UserInfo, new: UserInfo) -> UserInfo:
    """Merge new fields into existing, preferring non-empty/non-zero values."""
    merged = existing.model_dump()
    for field_name, new_val in new:
        if _is_empty(new_val):
            continue
        old_val = merged.get(field_name)
        if field_name == "ali_id":
            # Replace ali_id if existing is login-based (non-numeric) and new is real
            if not old_val or not str(old_val).isdigit():
                merged[field_name] = new_val
            continue
        if not _is_empty(old_val):
            continue
        merged[field_name] = new_val
    return UserInfo.model_validate(merged)


M = TypeVar("M", bound=BaseModel)


class _DictPool(Generic[M]):
    _lock: threading.Lock
    _conn: sqlite3.Connection
    _data: dict[str, M]

    def _init_pool(self, table: str, key_column: str, model_cls: type[M]) -> None:
        self._lock = threading.Lock()
        self._conn = _get_connection()
        self._table = table
        self._key_column = key_column
        _execute_schema(
            self._conn,
            f"CREATE TABLE IF NOT EXISTS {table} ({key_column} TEXT PRIMARY KEY, data TEXT NOT NULL)",
        )
        self._data = {}
        self._load_all(model_cls)

    def _load_all(self, model_cls: type[M]) -> None:
        for row in self._conn.execute(
            f"SELECT {self._key_column}, data FROM {self._table}"
        ):
            self._data[row[0]] = model_cls.model_validate_json(row[1])

    def _get_key(self, item: M) -> str:
        raise NotImplementedError

    def put(self, item: M) -> None:
        key = self._get_key(item)
        if not key:
            return
        with self._lock:
            self._data[key] = item
            _save_model(self._conn, self._table, self._key_column, key, item)

    def all(self) -> dict[str, M]:
        with self._lock:
            return dict(self._data)

    def clear(self) -> None:
        with self._lock:
            _clear_table(self._conn, self._table)
            self._data.clear()


# ---------------------------------------------------------------------------
# UserInfoPool
# ---------------------------------------------------------------------------


class UserInfoPool:
    """Thread-safe in-memory pool keyed by ali_id with login_id index."""

    _instance: UserInfoPool | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> UserInfoPool:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.Lock()
                instance._data: dict[str, UserInfo] = {}
                instance._login_id_index: dict[str, str] = {}
                cls._instance = instance
            return cls._instance

    def put(self, info: UserInfo) -> None:
        with self._lock:
            existing: UserInfo | None = None
            old_key: str | None = None

            if info.ali_id:
                existing = self._data.get(info.ali_id)
                old_key = info.ali_id
                if existing is None and info.login_id:
                    # ali_id not found — try login_id (e.g. im.id.get providing real ali_id
                    # when queryCustomerInfo already stored a login-based entry)
                    ali_by_login = self._login_id_index.get(info.login_id)
                    if ali_by_login:
                        existing = self._data.get(ali_by_login)
                        old_key = ali_by_login
            elif info.login_id:
                # queryCustomerInfo provides login_id but no ali_id — look up by login_id
                ali_by_login = self._login_id_index.get(info.login_id)
                if ali_by_login:
                    existing = self._data.get(ali_by_login)
                    old_key = ali_by_login

            if existing is None:
                # No existing entry
                if not info.ali_id:
                    if not info.login_id:
                        return  # no key at all — skip
                    # queryCustomerInfo provides login_id but no ali_id — use login_id as temporary key
                    info = info.model_copy(update={"ali_id": info.login_id})
                self._data[info.ali_id] = info
            else:
                info = _merge_user_info(existing, info)
                if info.ali_id and info.ali_id != old_key:
                    # ali_id changed (e.g. im.id.get provided real ali_id) — migrate key
                    if old_key:
                        self._data.pop(old_key, None)
                    self._data[info.ali_id] = info
                elif info.ali_id:
                    self._data[info.ali_id] = info

            lid = info.login_id
            if lid:
                self._login_id_index[lid] = info.ali_id

    def get(self, ali_id: str) -> UserInfo | None:
        with self._lock:
            return self._data.get(ali_id)

    def get_by_login_id(self, login_id: str) -> UserInfo | None:
        with self._lock:
            ali_id = self._login_id_index.get(login_id)
            if ali_id is None:
                return None
            return self._data.get(ali_id)

    def all(self) -> dict[str, UserInfo]:
        with self._lock:
            return dict(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._login_id_index.clear()


def get_user_info_pool() -> UserInfoPool:
    return UserInfoPool()


# ---------------------------------------------------------------------------
# SelfInfoPool
# ---------------------------------------------------------------------------


class SelfInfoPool:
    """Thread-safe singleton pool backed by SQLite for the current user."""

    _instance: SelfInfoPool | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> SelfInfoPool:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.Lock()
                instance._conn = _get_connection()
                _execute_schema(
                    instance._conn,
                    "CREATE TABLE IF NOT EXISTS self_info "
                    "(id INTEGER PRIMARY KEY CHECK(id = 1), data TEXT NOT NULL)",
                )
                instance._data: SelfInfo | None = None
                instance._load()
                cls._instance = instance
            return cls._instance

    def _load(self) -> None:
        row = self._conn.execute("SELECT data FROM self_info WHERE id = 1").fetchone()
        if row:
            self._data = SelfInfo.model_validate_json(row[0])

    def put(self, info: SelfInfo) -> None:
        info = info.model_copy(update={"ali_id": FIXED_SELF_ALI_ID})
        with self._lock:
            if self._data is None:
                self._data = info
            else:
                merged = self._data.model_dump()
                for field_name, new_val in info:
                    if new_val and not merged.get(field_name):
                        merged[field_name] = new_val
                self._data = SelfInfo.model_validate(merged)
            _save_model(self._conn, "self_info", "id", 1, self._data)

    def get(self) -> SelfInfo | None:
        with self._lock:
            if self._data is not None:
                return self._data.model_copy(update={"ali_id": FIXED_SELF_ALI_ID})
            return SelfInfo(ali_id=FIXED_SELF_ALI_ID)

    def clear(self) -> None:
        with self._lock:
            _clear_table(self._conn, "self_info")
            self._data = None


def get_self_info_pool() -> SelfInfoPool:
    return SelfInfoPool()


# ---------------------------------------------------------------------------
# ProductCardPool
# ---------------------------------------------------------------------------


class ProductCardPool(_DictPool[ProductCard]):
    _instance: ProductCardPool | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> ProductCardPool:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._init_pool("product_card", "card_id", ProductCard)
                cls._instance = instance
        return cls._instance

    def _get_key(self, item: ProductCard) -> str:
        return item.card_id

    def get(self, card_id: str) -> ProductCard | None:
        with self._lock:
            return self._data.get(card_id)

    def find_by_product_id(self, product_id: str) -> ProductCard | None:
        if not product_id:
            return None
        with self._lock:
            for card in self._data.values():
                if product_id in card.product_id:
                    return card
            return None


def get_product_card_pool() -> ProductCardPool:
    return ProductCardPool()


# ---------------------------------------------------------------------------
# GenericCardPool
# ---------------------------------------------------------------------------


class GenericCardPool(_DictPool[GenericCard]):
    _instance: GenericCardPool | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> GenericCardPool:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._init_pool("generic_card", "key", GenericCard)
                cls._instance = instance
        return cls._instance

    def _get_key(self, item: GenericCard) -> str:
        return f"{item.card_type}:{item.card_id}"

    def get(self, card_type: int, card_id: str) -> GenericCard | None:
        key = f"{card_type}:{card_id}"
        with self._lock:
            return self._data.get(key)

    def by_type(self, card_type: int) -> dict[str, GenericCard]:
        prefix = f"{card_type}:"
        with self._lock:
            return {k: v for k, v in self._data.items() if k.startswith(prefix)}


def get_generic_card_pool() -> GenericCardPool:
    return GenericCardPool()


# ---------------------------------------------------------------------------
# InquiryCardPool
# ---------------------------------------------------------------------------


class InquiryCardPool(_DictPool[InquiryCard]):
    _instance: InquiryCardPool | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> InquiryCardPool:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._init_pool("inquiry_card", "inquiry_id", InquiryCard)
                cls._instance = instance
        return cls._instance

    def _get_key(self, item: InquiryCard) -> str:
        return item.inquiry_id

    def get(self, inquiry_id: str) -> InquiryCard | None:
        with self._lock:
            return self._data.get(inquiry_id)


def get_inquiry_card_pool() -> InquiryCardPool:
    return InquiryCardPool()


# ---------------------------------------------------------------------------
# TranslationCache
# ---------------------------------------------------------------------------

class TranslationCache:
    """Thread-safe in-memory translation cache keyed by message text hash."""

    _instance: TranslationCache | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> TranslationCache:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.Lock()
                instance._cache: dict[str, str | None] = {}
                cls._instance = instance
            return cls._instance

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def is_cached(self, text: str) -> bool:
        h = self._hash(text)
        with self._lock:
            return h in self._cache

    def get(self, text: str) -> str | None:
        """Return translated text or None (already Chinese). Must check is_cached first."""
        h = self._hash(text)
        with self._lock:
            return self._cache.get(h)

    def put(self, text: str, translated: str | None) -> None:
        h = self._hash(text)
        with self._lock:
            self._cache[h] = translated

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def get_translation_cache() -> TranslationCache:
    return TranslationCache()


# ---------------------------------------------------------------------------
# InputPendingPool  — per-contact unsent input text
# ---------------------------------------------------------------------------


class InputPendingPool:
    """Thread-safe SQLite-backed store for per-contact unsent input text."""

    _instance: InputPendingPool | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> InputPendingPool:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.Lock()
                instance._conn = _get_connection()
                _execute_schema(
                    instance._conn,
                    "CREATE TABLE IF NOT EXISTS input_pending "
                    "(contact_ali_id TEXT PRIMARY KEY, text TEXT NOT NULL DEFAULT '')",
                )
                instance._cache: dict[str, str] = {}
                for row in instance._conn.execute(
                    "SELECT contact_ali_id, text FROM input_pending"
                ):
                    instance._cache[row[0]] = row[1]
                cls._instance = instance
            return cls._instance

    def get(self, contact_ali_id: str) -> str:
        with self._lock:
            return self._cache.get(contact_ali_id, "")

    def put(self, contact_ali_id: str, text: str) -> None:
        with self._lock:
            self._cache[contact_ali_id] = text
            self._conn.execute(
                "INSERT OR REPLACE INTO input_pending (contact_ali_id, text) VALUES (?, ?)",
                (contact_ali_id, text),
            )
            self._conn.commit()


def get_input_pending_pool() -> InputPendingPool:
    return InputPendingPool()
