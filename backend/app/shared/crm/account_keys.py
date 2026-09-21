"""Per-account AES keys for Alibaba IM databases, stored in the app database.

Each Alibaba account encrypts its ``im.sqlite`` with a different key. Keys are
kept in the app-owned ``im_account_key`` table (auto-captured from the running
client or manually entered), replacing the legacy single ``aes_key.bin`` file
cache.

NOTE: this table is IM/Alibaba-specific on purpose and therefore lives in the
app layer — the generic CRM SDK must not depend on it. Only the generic
``BaseManager`` machinery and engine bootstrap are reused from the SDK.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Optional

from Crypto.Cipher import AES
from loguru import logger
from sqlmodel import Field, SQLModel

from backend.app.crm_sdk.core.base import BaseManager
from backend.app.crm_sdk.utils.common import bootstrap_engine, get_database_lock, utc_now
from backend.app.shared.crm.paths import default_crm_database_path

AES_KEY_SIZES = (16, 24, 32)
SQLITE_MAGIC = b"SQLite format 3\x00"

KEY_SOURCE_AUTO = "auto"
KEY_SOURCE_MANUAL = "manual"


class IMAccountKey(SQLModel, table=True):
    __tablename__ = "im_account_key"

    ali_id: str = Field(primary_key=True)
    aes_key_hex: str
    source: str = Field(default=KEY_SOURCE_AUTO)
    updated_time: datetime = Field(
        default_factory=utc_now,
        sa_column_kwargs={"onupdate": utc_now},
    )


class AccountKeyStore(BaseManager[IMAccountKey]):
    """App-owned store for the ``im_account_key`` table (same DB file, own table)."""

    model = IMAccountKey
    pk_field = "ali_id"
    editable_fields = ("aes_key_hex", "source")
    pk_is_auto = False
    touch_updated_time = True

    def __init__(self, database_path: Optional[Path | str] = None) -> None:
        self.database_path, self.engine = bootstrap_engine(database_path)
        self._lock = get_database_lock(self.database_path)
        IMAccountKey.__table__.create(self.engine, checkfirst=True)

    def get_key(self, ali_id: str) -> Optional[IMAccountKey]:
        return self._get(ali_id)

    def list_keys(self) -> list[IMAccountKey]:
        return self._list()

    def upsert_key(self, record: IMAccountKey) -> None:
        self._upsert(record)

    def delete_key(self, ali_id: str) -> None:
        self._delete(ali_id)


class KeyFormatError(ValueError):
    """Raised when a user-supplied key is malformed or fails trial decryption."""


def parse_key_hex(raw: str) -> bytes:
    """Parse user-supplied hex into key bytes; raise KeyFormatError on any problem."""
    text = (raw or "").strip().lower().removeprefix("0x")
    try:
        key = bytes.fromhex(text)
    except ValueError:
        raise KeyFormatError("Key 必须是十六进制字符串") from None
    if len(key) not in AES_KEY_SIZES:
        raise KeyFormatError(f"Key 长度必须为 {list(AES_KEY_SIZES)} 字节，当前为 {len(key)} 字节")
    return key


def mask_key_preview(key_hex: str) -> str:
    """Mask a stored key for display, e.g. ``ab***cd``. Never log or return full keys."""
    text = (key_hex or "").strip().lower()
    if len(text) < 8:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


def get_key_hex(ali_id: str, database_path: Optional[Path | str] = None) -> bytes | None:
    """Return stored key bytes for *ali_id*, or None when absent/corrupt."""
    if not ali_id:
        return None
    record = AccountKeyStore(database_path=database_path).get_key(ali_id)
    if record is None:
        return None
    try:
        return parse_key_hex(record.aes_key_hex)
    except KeyFormatError:
        logger.warning("Stored account key is corrupt, ignoring")
        return None


def get_key_source(ali_id: str, database_path: Optional[Path | str] = None) -> str:
    record = AccountKeyStore(database_path=database_path).get_key(ali_id) if ali_id else None
    return record.source if record is not None else ""


def read_saved_key(ali_id: str, database_path: Optional[Path | str] = None) -> tuple[bytes, str] | None:
    """Read without schema creation/migration; distinguish absent, corrupt and busy."""
    if not ali_id:
        return None
    if database_path is None:
        database_path = default_crm_database_path()
    path = Path(database_path).resolve()
    try:
        path.stat()
    except FileNotFoundError:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='im_account_key'",
        ).fetchone():
            return None
        record = conn.execute(
            "SELECT aes_key_hex, source FROM im_account_key WHERE ali_id=?", (ali_id,),
        ).fetchone()
    if record is None:
        return None
    if not isinstance(record[0], str):
        raise KeyFormatError("Stored AES key is malformed")
    return parse_key_hex(record[0]), record[1]


def save_key(ali_id: str, key: bytes, source: str, database_path: Optional[Path | str] = None) -> None:
    AccountKeyStore(database_path=database_path).upsert_key(
        IMAccountKey(ali_id=ali_id, aes_key_hex=key.hex(), source=source)
    )


def delete_key(ali_id: str, database_path: Optional[Path | str] = None) -> bool:
    """Delete the stored key; return True when one existed."""
    store = AccountKeyStore(database_path=database_path)
    if store.get_key(ali_id) is None:
        return False
    store.delete_key(ali_id)
    return True


def verify_key_against_db(key: bytes, db_path: Path) -> bool:
    """Trial-decrypt the first block of *db_path*; True when SQLite magic appears."""
    try:
        with db_path.open("rb") as stream:
            first_block = stream.read(16)
        if len(first_block) != 16:
            return False
        plain = AES.new(key, AES.MODE_ECB).decrypt(first_block)
        return plain == SQLITE_MAGIC
    except (OSError, ValueError):
        return False


def parse_and_verify_key(raw: str, db_path: Path) -> bytes:
    """Parse user input and trial-decrypt; raise KeyFormatError with the reason."""
    key = parse_key_hex(raw)
    if not db_path.exists():
        raise KeyFormatError("该账号的 im.sqlite 不存在，无法校验 Key")
    if not verify_key_against_db(key, db_path):
        raise KeyFormatError("Key 与该账号的数据库不匹配（试解密失败）")
    return key
