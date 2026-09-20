from __future__ import annotations

import json
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import func, text
from sqlmodel import Session, select

from backend.app.shared.crm.identities import (
    PLATFORM_PID,
    message_external_id,
    self_sender_id,
    session_key,
    session_key_prefix,
)
from backend.app.shared.crm.sdk import (
    Account,
    AccountManager,
    AccountMapping,
    AccountMappingManager,
    Customer,
    CustomerManager,
    Message,
    MessageManager,
    Platform,
    PlatformManager,
    SessionMeta,
    SessionMetaManager,
)
from backend.app.shared.crm.views import (
    CrmConversation,
    CrmConversationDigest,
    CrmMessage,
    coerce_epoch,
    message_display_text,
)
from backend.app.shared.crm.sync_store import canonical_source_dir, upsert_messages, write_sync_state
from backend.app.shared.crm.inbox_store import _epoch, write_inbox
from backend.app.shared.mitm.pool import SelfInfo, UserInfo, get_user_info_pool
from backend.app.shared.utils.log_context import bind_log_context, capture_log_context, log_event
from uuid import uuid4

if TYPE_CHECKING:
    from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow

MAPPING_ALI_ID = "ali_id"
MAPPING_LOGIN_ID = "login_id"
MAPPING_ENCRYPT_ACCOUNT_ID = "encrypt_account_id"

_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crm-sync")


class CRMAdapter:
    def __init__(self, database_path: Path | str | None = None) -> None:
        from backend.app.shared.crm.migrations import migrate_message_ids

        database_path = database_path or _default_database_path()
        migrate_message_ids(database_path)
        self.platforms = PlatformManager(database_path=database_path)
        self.customers = CustomerManager(database_path=database_path)
        self.accounts = AccountManager(database_path=database_path)
        self.mappings = AccountMappingManager(database_path=database_path)
        self.sessions = SessionMetaManager(database_path=database_path)
        self.messages = MessageManager(database_path=database_path)
        self.engine = self.accounts.engine

    def _save(self, manager, payload, session: Session) -> None:
        """Flush an identity-resolved payload; the snapshot caller alone commits."""
        pk = getattr(payload, manager.pk_field)
        current = session.get(manager.model, pk) if pk is not None else None
        if current is None:
            session.add(payload)
            current = payload
        else:
            for field in manager.editable_fields:
                if getattr(current, field) != getattr(payload, field):
                    setattr(current, field, getattr(payload, field))
        session.flush()
        for field in (manager.pk_field, *manager.state_fields):
            setattr(payload, field, getattr(current, field))

    def ensure_platform(self, session: Session | None = None) -> None:
        platform = Platform(pid=PLATFORM_PID, name="Alibaba", extra={"source": "app_adapter"})
        if session is None:
            self.platforms.upsert_platform(platform)
        else:
            self._save(self.platforms, platform, session)

    def upsert_user_info(self, info: UserInfo, session: Session | None = None) -> int | None:
        return self._upsert_account(
            ali_id=info.ali_id,
            login_id=info.login_id,
            encrypt_account_id=info.encrypt_account_id,
            display_name=_display_name(info),
            avatar="",
            is_self=False,
            extra=info.model_dump(),
            session=session,
        )

    def upsert_self_info(self, info: SelfInfo, session: Session | None = None) -> int | None:
        return self._upsert_account(
            ali_id=info.ali_id,
            login_id=info.login_id,
            encrypt_account_id=info.encrypt_account_id,
            display_name=_display_name(info),
            avatar=info.avatar_url,
            is_self=True,
            extra=info.model_dump(),
            session=session,
        )

    def sync_conversations(
        self, conversations: list[ContactConv], self_info: SelfInfo | None,
        *, source_revision: int = 0, data_dir: str = "",
    ) -> dict:
        if self_info is None or not self_info.ali_id:
            raise ValueError("IM sync requires a selected seller identity")
        user_pool = get_user_info_pool()
        with Session(self.engine) as session:
            # Explicit BEGIN also makes SQLite DDL transactional and serializes revisions.
            session.execute(text("BEGIN IMMEDIATE"))
            self.ensure_platform(session)
            self_aid = self.upsert_self_info(self_info, session)
            messages = []
            for conv in conversations:
                contact_info = user_pool.get(conv.contact_ali_id)
                contact_aid = self.upsert_user_info(contact_info, session) if contact_info else self._upsert_account(
                    ali_id=conv.contact_ali_id,
                    login_id="",
                    encrypt_account_id="",
                    display_name=conv.contact_ali_id,
                    avatar="",
                    is_self=False,
                    extra={"ali_id": conv.contact_ali_id},
                    session=session,
                )
                if self_aid is None or contact_aid is None:
                    raise ValueError("IM sync could not resolve account identity")
                session_meta = self._upsert_session(
                    self_info.ali_id, conv.contact_ali_id, [self_aid, contact_aid], session,
                )
                for row in conv.messages:
                    sender_aid = self_aid if _message_from_self(row, self_info) else contact_aid
                    messages.append(self._message_payload(row, session_meta.sid, sender_aid, self_info.ali_id))
            # Preserve the pre-sync archive/scope boundary for inbox upgrades.
            write_inbox(session, self_info.ali_id, data_dir, messages)
            counts = upsert_messages(session, messages)
            result = write_sync_state(session, self_info.ali_id, data_dir, source_revision, counts)
            session.commit()
            return result

    def _upsert_account(
        self,
        *,
        ali_id: str,
        login_id: str,
        encrypt_account_id: str,
        display_name: str,
        avatar: str,
        is_self: bool,
        extra: dict[str, Any],
        session: Session | None = None,
    ) -> int | None:
        identity_key = ali_id or login_id or encrypt_account_id
        if not identity_key:
            return None

        account = None
        for mapping_type, key in (
            (MAPPING_ALI_ID, ali_id),
            (MAPPING_LOGIN_ID, login_id),
            (MAPPING_ENCRYPT_ACCOUNT_ID, encrypt_account_id),
        ):
            if key:
                account = self._account_by_mapping(mapping_type, key, session)
                if account is not None:
                    break
        customer = self._build_customer_payload(
            account=account,
            display_name=display_name,
            login_id=login_id,
            ali_id=ali_id,
            encrypt_account_id=encrypt_account_id,
            extra=extra,
            is_self=is_self,
            session=session,
        )
        if session is None:
            self.customers.upsert_customer(customer)
        else:
            self._save(self.customers, customer, session)
        if customer.cid is None:
            return None

        account_payload = Account(
            aid=account.aid if account is not None else None,
            cid=customer.cid,
            pid=PLATFORM_PID,
            account=login_id or ali_id or encrypt_account_id,
            nickname=display_name or login_id or ali_id or encrypt_account_id,
            avatar=avatar or None,
            sids=account.sids if account is not None else None,
            extra={**_merge_extra(account.extra if account is not None else None, extra), "is_self": is_self},
        )
        if session is None:
            self.accounts.upsert_account(account_payload)
        else:
            self._save(self.accounts, account_payload, session)
        if account_payload.aid is None:
            return None

        if ali_id:
            self._upsert_mapping(account_payload.aid, MAPPING_ALI_ID, ali_id, session)
        if login_id:
            self._upsert_mapping(account_payload.aid, MAPPING_LOGIN_ID, login_id, session)
        if encrypt_account_id:
            self._upsert_mapping(account_payload.aid, MAPPING_ENCRYPT_ACCOUNT_ID, encrypt_account_id, session)
        return account_payload.aid

    def _build_customer_payload(
        self,
        *,
        account: Any | None,
        display_name: str,
        login_id: str,
        ali_id: str,
        encrypt_account_id: str,
        extra: dict[str, Any],
        is_self: bool,
        session: Session | None = None,
    ) -> Any:
        """Build the Customer upsert payload without clobbering user-editable fields.

        已有 Customer 只在现值为空时补齐 name/region，其余字段原样保留；
        extra 与现值合并。仅首次建档时写入完整展示数据。
        """
        name = display_name or login_id or ali_id or encrypt_account_id
        region = str(extra.get("country_code") or extra.get("country") or "")
        current = None
        if account is not None:
            current = session.get(Customer, account.cid) if session is not None else self.customers.get_customer(account.cid)
        if current is None:
            return Customer(
                cid=account.cid if account is not None else None,
                name=name,
                region=region,
                extra={"source": "mitm", "is_self": is_self},
            )
        merged_extra = dict(current.extra) if isinstance(current.extra, dict) else {}
        merged_extra["source"] = "mitm"
        merged_extra["is_self"] = is_self
        return Customer(
            cid=current.cid,
            name=current.name or name,
            sex=current.sex,
            birthdate=current.birthdate,
            region=current.region or region,
            extra=merged_extra,
            image=current.image,
        )

    def _upsert_session(
        self, self_ali_id: str, contact_ali_id: str, participants: list[int], session: Session | None = None,
    ) -> Any:
        key = session_key(self_ali_id, contact_ali_id)
        existing = self._session_by_key(key, session)
        session_meta = SessionMeta(
            sid=existing.sid if existing is not None else None,
            key=key,
            name=key,
            participants=participants,
        )
        if session is None:
            self.sessions.upsert_session_meta(session_meta)
        else:
            self._save(self.sessions, session_meta, session)
        return session_meta

    def _message_payload(self, row: MessageRow, sid: int, sender_aid: int, self_ali_id: str) -> Message:
        external_mid = message_external_id(self_ali_id, row.table_name, row.mid)
        content = _message_content(row)
        epoch = _epoch(row.created_at)
        return Message(
            external_mid=external_mid,
            sid=sid,
            sender=sender_aid,
            read=None,
            content=content,
            type=_message_type(row),
            created_at=datetime.fromtimestamp(epoch) if epoch is not None else None,
        )

    def _account_by_mapping(self, mapping_type: str, key: str, session: Session | None = None) -> Any | None:
        with nullcontext(session) if session is not None else Session(self.engine) as session:
            statement = select(AccountMapping).where(
                AccountMapping.type == mapping_type,
                AccountMapping.key == key,
            )
            mapping = session.exec(statement).first()
            if mapping is None:
                return None
            return session.get(Account, mapping.aid)

    def _session_by_key(self, key: str, session: Session | None = None) -> Any | None:
        with nullcontext(session) if session is not None else Session(self.engine) as session:
            statement = select(SessionMeta).where(SessionMeta.key == key)
            return session.exec(statement).first()

    def _upsert_mapping(self, aid: int, mapping_type: str, key: str, session: Session | None = None) -> None:
        if not key:
            return
        existing = self._mapping_by_key(mapping_type, key, session)
        if existing is not None:
            if existing.aid != aid:
                logger.warning(
                    "CRM mapping redirect: type={} from_aid={} to_aid={}",
                    mapping_type,
                    existing.aid,
                    aid,
                )
            else:
                return
        mapping = AccountMapping(
            amid=existing.amid if existing is not None else None, aid=aid, type=mapping_type, key=key,
        )
        if session is None:
            self.mappings.upsert_account_mapping(mapping)
        else:
            self._save(self.mappings, mapping, session)

    def _mapping_by_key(self, mapping_type: str, key: str, session: Session | None = None) -> Any | None:
        with nullcontext(session) if session is not None else Session(self.engine) as session:
            statement = select(AccountMapping).where(
                AccountMapping.type == mapping_type,
                AccountMapping.key == key,
            )
            return session.exec(statement).first()

    def get_self_info(self, self_ali_id: str | None = None) -> SelfInfo | None:
        if self_ali_id is None:
            from backend.app.shared.utils.app_config import get_configured_self_ali_id

            self_ali_id = get_configured_self_ali_id()
        if not self_ali_id:
            return None
        account = self._account_by_mapping(MAPPING_ALI_ID, self_ali_id)
        if account is None or account.pid != PLATFORM_PID or not isinstance(account.extra, dict):
            return None
        if account.extra.get("is_self") is not True or account.extra.get("ali_id") != self_ali_id:
            return None
        try:
            return SelfInfo.model_validate(account.extra)
        except ValueError:
            return None

    def get_user_info(self, identifier: str) -> UserInfo | None:
        account = self._account_by_any_mapping(identifier)
        if account is None or not isinstance(account.extra, dict):
            return None
        try:
            return UserInfo.model_validate(account.extra)
        except ValueError:
            return None

    def _account_by_any_mapping(self, key: str) -> Any | None:
        with Session(self.engine) as session:
            statement = select(AccountMapping).where(
                AccountMapping.key == key,
                AccountMapping.type.in_([MAPPING_ALI_ID, MAPPING_LOGIN_ID, MAPPING_ENCRYPT_ACCOUNT_ID]),
            )
            rows = list(session.exec(statement).all())
            if not rows:
                return None
            priority = {MAPPING_ALI_ID: 0, MAPPING_LOGIN_ID: 1, MAPPING_ENCRYPT_ACCOUNT_ID: 2}
            rows.sort(key=lambda row: priority.get(row.type, 99))
            return session.get(Account, rows[0].aid)

    def batch_load_conversation_maps(
        self, aids: set[int], contact_keys: set[str]
    ) -> tuple[dict[int, Any], dict[int, Any], dict[str, int]]:
        """Batch preload for the conversation list: one session, few IN queries.

        Returns (accounts by aid, customers by cid, contact key -> aid with the
        same ali_id > login_id > encrypt_account_id priority as
        :meth:`_account_by_any_mapping`. Mapping targets outside *aids* are
        fetched as well so every contact resolves without extra round trips.
        """
        aid_list = sorted(aid for aid in aids if aid is not None)
        key_list = sorted(key for key in contact_keys if key)
        mapping_types = [MAPPING_ALI_ID, MAPPING_LOGIN_ID, MAPPING_ENCRYPT_ACCOUNT_ID]
        priority = {MAPPING_ALI_ID: 0, MAPPING_LOGIN_ID: 1, MAPPING_ENCRYPT_ACCOUNT_ID: 2}
        accounts: dict[int, Any] = {}
        customers: dict[int, Any] = {}
        key_to_aid: dict[str, int] = {}
        with Session(self.engine) as session:
            if aid_list:
                for account in session.exec(select(Account).where(Account.aid.in_(aid_list))).all():
                    if account.aid is not None:
                        accounts[account.aid] = account
            if key_list:
                best: dict[str, Any] = {}
                rows = session.exec(
                    select(AccountMapping).where(
                        AccountMapping.key.in_(key_list),
                        AccountMapping.type.in_(mapping_types),
                    )
                ).all()
                for row in rows:
                    current = best.get(row.key or "")
                    if current is None or priority.get(row.type, 99) < priority.get(current.type, 99):
                        best[row.key or ""] = row
                key_to_aid = {key: row.aid for key, row in best.items() if row.aid is not None}
                missing = sorted({aid for aid in key_to_aid.values()} - set(accounts))
                if missing:
                    for account in session.exec(select(Account).where(Account.aid.in_(missing))).all():
                        if account.aid is not None:
                            accounts[account.aid] = account
            cids = sorted({account.cid for account in accounts.values() if account.cid is not None})
            if cids:
                for customer in session.exec(select(Customer).where(Customer.cid.in_(cids))).all():
                    if customer.cid is not None:
                        customers[customer.cid] = customer
        return accounts, customers, key_to_aid

    def list_conversations(self, self_ali_id: str) -> list[CrmConversation]:
        prefix = session_key_prefix(self_ali_id)
        conversations: list[CrmConversation] = []
        with Session(self.engine) as session:
            session_statement = select(SessionMeta).where(SessionMeta.key.startswith(prefix))
            sessions = [meta for meta in session.exec(session_statement).all() if meta.sid is not None]
            if not sessions:
                return []
            sids = [meta.sid for meta in sessions if meta.sid is not None]
            message_statement = select(Message).where(Message.sid.in_(sids))
            grouped: dict[int, list] = {sid: [] for sid in sids}
            for message in session.exec(message_statement).all():
                if message.sid in grouped:
                    grouped[message.sid].append(_crm_message_from_sdk(message))
            for session_meta in sessions:
                messages = grouped.get(session_meta.sid or 0, [])
                messages.sort(
                    key=lambda message: message.created_at.timestamp() if message.created_at else 0.0
                )
                if not messages:
                    continue
                contact_ali_id = str(session_meta.key or "").removeprefix(prefix)
                conversations.append(CrmConversation(
                    contact_ali_id=contact_ali_id,
                    messages=messages,
                    last_created_at=messages[-1].created_at,
                    last_content_label=messages[-1].content_label,
                    sid=session_meta.sid or 0,
                    key=str(session_meta.key or ""),
                    participants=tuple(session_meta.participants or []),
                ))
        conversations.sort(key=lambda conversation: coerce_epoch(conversation.last_created_at), reverse=True)
        return conversations

    def list_conversation_digests(self, self_ali_id: str) -> list[CrmConversationDigest]:
        """List-path digests: latest pointer + non-system count per session.

        Three light queries, zero message bodies: the multi-MB ``content`` JSON
        blobs are projected inside SQLite (``json_extract``) instead of being
        ORM-loaded and deserialized one by one in Python. Sessions without
        messages are skipped, like :meth:`list_conversations`.
        """
        prefix = session_key_prefix(self_ali_id)
        with Session(self.engine) as session:
            metas = [
                meta
                for meta in session.exec(
                    select(SessionMeta).where(SessionMeta.key.startswith(prefix))
                ).all()
                if meta.sid is not None
            ]
            if not metas:
                return []
            by_sid = {meta.sid: meta for meta in metas if meta.sid is not None}
            sids = sorted(by_sid)
            not_system = func.coalesce(func.json_extract(Message.content, "$.is_system"), 0) != 1
            not_auto = func.coalesce(func.json_extract(Message.content, "$.is_auto_reply"), 0) != 1
            ranked = (
                select(
                    Message.sid.label("sid"),
                    Message.created_at.label("created_at"),
                    func.json_extract(Message.content, "$.content_label").label("content_label"),
                    func.json_extract(Message.content, "$.user_content_type").label("user_content_type"),
                    func.row_number().over(
                        partition_by=Message.sid,
                        order_by=(Message.created_at.desc(), Message.external_mid.desc()),
                    ).label("rn"),
                ).where(Message.sid.in_(sids)).subquery()
            )
            latest = {
                row["sid"]: row
                for row in session.execute(select(ranked).where(ranked.c.rn == 1)).mappings().all()
            }
            counts = {
                row["sid"]: row["n"]
                for row in session.execute(
                    select(Message.sid.label("sid"), func.count().label("n"))
                    .where(Message.sid.in_(sids), not_system, not_auto)
                    .group_by(Message.sid)
                ).mappings().all()
            }
            digests = []
            for sid, meta in by_sid.items():
                row = latest.get(sid)
                if row is None:
                    continue
                digests.append(CrmConversationDigest(
                    contact_ali_id=str(meta.key or "").removeprefix(prefix),
                    sid=sid,
                    key=str(meta.key or ""),
                    participants=tuple(meta.participants or []),
                    latest_created_at=row["created_at"],
                    latest_content_label=row["content_label"],
                    latest_is_card=row["user_content_type"] == 10010,
                    dialogue_count=int(counts.get(sid, 0)),
                ))
        digests.sort(key=lambda digest: coerce_epoch(digest.latest_created_at), reverse=True)
        return digests

    def get_conversation_detail(self, self_ali_id: str, sid: int) -> CrmConversation | None:
        prefix = session_key_prefix(self_ali_id)
        with Session(self.engine) as session:
            session_meta = session.get(SessionMeta, sid)
            if session_meta is None or session_meta.sid is None:
                return None
            if not str(session_meta.key or "").startswith(prefix):
                return None
            message_statement = select(Message).where(Message.sid == session_meta.sid)
            messages = [_crm_message_from_sdk(message) for message in session.exec(message_statement).all()]
            messages.sort(
                key=lambda message: message.created_at.timestamp() if message.created_at else 0.0
            )
            if not messages:
                return None
            contact_ali_id = str(session_meta.key or "").removeprefix(prefix)
            return CrmConversation(
                contact_ali_id=contact_ali_id,
                messages=messages,
                last_created_at=messages[-1].created_at,
                last_content_label=messages[-1].content_label,
                sid=session_meta.sid,
                key=str(session_meta.key or ""),
                participants=tuple(session_meta.participants or []),
            )


def sync_user_info(info: UserInfo) -> Future:
    return _submit_sync(_sync_user_info_now, info)


def sync_self_info(info: SelfInfo) -> Future:
    return _submit_sync(_sync_self_info_now, info)


def sync_im_database(
    db_path: Path, self_ali_id: str, self_info: SelfInfo | None,
    *, source_revision: int = 0, data_dir: str = "",
) -> Future:
    """Queue one complete source snapshot, capturing paths and identity at submission.

    The Future resolves only after commit, with the same dict as read_sync_state().
    Each successful call advances revision, even for zero changed messages; source
    deduplication belongs to the coordinator. Empty data_dir is for internal callers.
    """
    if not self_ali_id or (self_info is not None and self_info.ali_id != self_ali_id):
        raise ValueError("IM sync requires a matching selected seller identity")
    snapshot = self_info.model_copy(deep=True) if self_info is not None else SelfInfo(ali_id=self_ali_id)
    return _submit_sync(
        _sync_im_database_now, Path(db_path).resolve(), self_ali_id, snapshot,
        _default_database_path().resolve(), source_revision, canonical_source_dir(data_dir),
    )


def _submit_sync(func, *args: Any) -> Future:
    context = capture_log_context()
    context.setdefault("sync_id", uuid4().hex)

    def run():
        with bind_log_context(**context):
            log_event("crm.started")
            return func(*args)

    def completed(done):
        with bind_log_context(**context):
            _log_sync_failure(done)

    with bind_log_context(**context):
        log_event("crm.submitted")
    future = _SYNC_EXECUTOR.submit(run)
    future.add_done_callback(completed)
    return future


def _log_sync_failure(future) -> None:
    try:
        result = future.result()
        fields = {key: result[key] for key in ("revision", "inserted", "updated", "unchanged")
                  if key in result} if isinstance(result, dict) else {}
        log_event("crm.committed", **fields)
    except Exception:
        logger.exception("Failed to sync data into CRM SDK")
        log_event("crm.failed")


def _sync_user_info_now(info: UserInfo) -> None:
    adapter = CRMAdapter()
    adapter.ensure_platform()
    adapter.upsert_user_info(info)


def _sync_self_info_now(info: SelfInfo) -> None:
    adapter = CRMAdapter()
    adapter.ensure_platform()
    adapter.upsert_self_info(info)


def _sync_im_database_now(
    db_path: Path, self_ali_id: str, self_info: SelfInfo | None,
    database_path: Path, source_revision: int, data_dir: str,
) -> dict:
    from backend.app.shared.backend.im_chat_db import build_conversations, open_readonly

    conn = open_readonly(db_path)
    try:
        conn.execute("BEGIN")
        conversations = build_conversations(conn, self_ali_id)
    finally:
        conn.close()
    return CRMAdapter(database_path).sync_conversations(
        conversations, self_info, source_revision=source_revision, data_dir=data_dir,
    )


def _default_database_path() -> Path:
    from backend.app.shared.utils.env import get_env_str
    from backend.app.shared.utils.settings import CRM_DB_RELATIVE_DEFAULT, resolve_backend_root

    raw = get_env_str("MAA_CRM_DB_PATH", CRM_DB_RELATIVE_DEFAULT)
    path = Path(raw)
    if not path.is_absolute():
        path = resolve_backend_root() / path
    return path


def _display_name(info: UserInfo | SelfInfo) -> str:
    full_name = " ".join(part for part in [info.first_name, info.last_name] if part).strip()
    return full_name or info.company_name or info.login_id or info.ali_id


def _merge_extra(existing: Any, new: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in new.items():
        if _is_empty(value):
            continue
        merged[key] = value
    return merged


def _is_empty(value: Any) -> bool:
    from backend.app.shared.utils.empty import is_empty_value

    return is_empty_value(value)


def _message_from_self(row: MessageRow, self_info: SelfInfo | None) -> bool:
    if self_info is None or not self_info.ali_id:
        return False
    return row.sender_id == self_sender_id(self_info.ali_id)


def _message_content(row: MessageRow) -> dict[str, Any]:
    text = _decode_content(row.content)
    content_latin1 = bytes(row.content).decode("latin1") if row.content is not None else None
    content = {
        "cid": row.cid,
        "mid": row.mid,
        "table_name": row.table_name,
        "sender_id": row.sender_id,
        "created_at": row.created_at,
        "user_content_type": row.user_content_type,
        "content_label": row.content_label if row.user_content_type in (0, 10010) else message_display_text(row),
        "content": text,
        "content_latin1": content_latin1,
        "is_system": row.is_system,
        "is_auto_reply": row.is_auto_reply,
    }
    if row.user_content_type not in (0, 10010):
        content["source_content_label"] = row.content_label
    return content


def _message_type(row: MessageRow) -> str:
    return str(row.user_content_type) if row.user_content_type is not None else "unknown"


def _decode_content(content: bytes | None) -> Any:
    if content is None:
        return None
    text = content.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _crm_message_from_sdk(message: Any) -> CrmMessage:
    content = message.content if isinstance(message.content, dict) else {}
    return CrmMessage(
        table_name=str(content.get("table_name") or ""),
        cid=str(content.get("cid") or ""),
        mid=str(content.get("mid") or message.external_mid),
        sender_id=content.get("sender_id"),
        created_at=message.created_at,
        user_content_type=content.get("user_content_type"),
        content_label=content.get("content_label"),
        content=_content_bytes(content),
        is_system=bool(content.get("is_system")),
        is_auto_reply=bool(content.get("is_auto_reply")),
    )


def _content_bytes(content: dict[str, Any]) -> bytes | None:
    value = content.get("content_latin1")
    if isinstance(value, str):
        return value.encode("latin1", errors="ignore")
    value = content.get("content")
    if isinstance(value, str):
        return value.encode("utf-8", errors="ignore")
    return None
