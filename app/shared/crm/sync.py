from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from loguru import logger
from sqlmodel import Session, select

from app.shared.backend.im_chat_db import ContactConv, MessageRow, coerce_epoch
from app.shared.crm.sdk import load_sdk
from app.shared.crm.views import CrmConversation, CrmMessage
from app.shared.mitm.pool import SelfInfo, UserInfo, get_self_info_pool, get_user_info_pool

PID_ALIBABA = "alibaba_icbu"
MAPPING_ALI_ID = "ali_id"
MAPPING_LOGIN_ID = "login_id"
MAPPING_ENCRYPT_ACCOUNT_ID = "encrypt_account_id"

_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crm-sync")


class CRMAdapter:
    def __init__(self, database_path: Path | str | None = None) -> None:
        database_path = database_path or _default_database_path()
        sdk = load_sdk()
        self.Account = sdk["Account"]
        self.AccountManager = sdk["AccountManager"]
        self.AccountMapping = sdk["AccountMapping"]
        self.AccountMappingManager = sdk["AccountMappingManager"]
        self.Customer = sdk["Customer"]
        self.CustomerManager = sdk["CustomerManager"]
        self.Message = sdk["Message"]
        self.MessageManager = sdk["MessageManager"]
        self.Platform = sdk["Platform"]
        self.PlatformManager = sdk["PlatformManager"]
        self.SessionMeta = sdk["SessionMeta"]
        self.SessionMetaManager = sdk["SessionMetaManager"]

        self.platforms = self.PlatformManager(database_path=database_path)
        self.customers = self.CustomerManager(database_path=database_path)
        self.accounts = self.AccountManager(database_path=database_path)
        self.mappings = self.AccountMappingManager(database_path=database_path)
        self.sessions = self.SessionMetaManager(database_path=database_path)
        self.messages = self.MessageManager(database_path=database_path)
        self.engine = self.accounts.engine

    def ensure_platform(self) -> None:
        platform = self.Platform(pid=PID_ALIBABA, name="Alibaba", extra={"source": "app_adapter"})
        self.platforms.upsert_platform(platform)

    def upsert_user_info(self, info: UserInfo) -> int | None:
        return self._upsert_account(
            ali_id=info.ali_id,
            login_id=info.login_id,
            encrypt_account_id=info.encrypt_account_id,
            display_name=_user_display_name(info),
            avatar="",
            is_self=False,
            extra=info.model_dump(),
        )

    def upsert_self_info(self, info: SelfInfo) -> int | None:
        return self._upsert_account(
            ali_id=info.ali_id,
            login_id=info.login_id,
            encrypt_account_id=info.encrypt_account_id,
            display_name=_self_display_name(info),
            avatar=info.avatar_url,
            is_self=True,
            extra=info.model_dump(),
        )

    def sync_conversations(self, conversations: list[ContactConv], self_info: SelfInfo | None) -> None:
        self.ensure_platform()
        self_aid = self.upsert_self_info(self_info) if self_info and self_info.ali_id else None
        if self_aid is None:
            return

        user_pool = get_user_info_pool()
        for conv in conversations:
            contact_info = user_pool.get(conv.contact_ali_id)
            contact_aid = self.upsert_user_info(contact_info) if contact_info else self._upsert_account(
                ali_id=conv.contact_ali_id,
                login_id="",
                encrypt_account_id="",
                display_name=conv.contact_ali_id,
                avatar="",
                is_self=False,
                extra={"ali_id": conv.contact_ali_id},
            )
            if contact_aid is None:
                continue

            session_meta = self._upsert_session(self_info.ali_id, conv.contact_ali_id, [self_aid, contact_aid])
            if session_meta.sid is None:
                continue

            for message_row in conv.messages:
                sender_aid = self_aid if _message_from_self(message_row, self_info) else contact_aid
                self._upsert_message(message_row, session_meta.sid, sender_aid)

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
    ) -> int | None:
        identity_key = ali_id or login_id or encrypt_account_id
        if not identity_key:
            return None

        self.ensure_platform()
        account = None
        for mapping_type, key in (
            (MAPPING_ALI_ID, ali_id),
            (MAPPING_LOGIN_ID, login_id),
            (MAPPING_ENCRYPT_ACCOUNT_ID, encrypt_account_id),
        ):
            if key:
                account = self._account_by_mapping(mapping_type, key)
                if account is not None:
                    break
        customer = self.Customer(
            cid=account.cid if account is not None else None,
            name=display_name or login_id or ali_id or encrypt_account_id,
            region=str(extra.get("country_code") or extra.get("country") or ""),
            extra={"source": "mitm", "is_self": is_self},
        )
        self.customers.upsert_customer(customer)
        if customer.cid is None:
            return None

        account_payload = self.Account(
            aid=account.aid if account is not None else None,
            cid=customer.cid,
            pid=PID_ALIBABA,
            account=login_id or ali_id or encrypt_account_id,
            nickname=display_name or login_id or ali_id or encrypt_account_id,
            avatar=avatar or None,
            extra={**_merge_extra(account.extra if account is not None else None, extra), "is_self": is_self},
        )
        if account_payload.aid is None:
            self.accounts.upsert_account(account_payload)
        else:
            self.accounts.upsert_account(account_payload)
        if account_payload.aid is None:
            return None

        if ali_id:
            self._upsert_mapping(account_payload.aid, MAPPING_ALI_ID, ali_id)
        if login_id:
            self._upsert_mapping(account_payload.aid, MAPPING_LOGIN_ID, login_id)
        if encrypt_account_id:
            self._upsert_mapping(account_payload.aid, MAPPING_ENCRYPT_ACCOUNT_ID, encrypt_account_id)
        return account_payload.aid

    def _upsert_session(self, self_ali_id: str, contact_ali_id: str, participants: list[int]) -> Any:
        session_name = _session_name(self_ali_id, contact_ali_id)
        existing = self._session_by_name(session_name)
        session_meta = self.SessionMeta(
            sid=existing.sid if existing is not None else None,
            name=session_name,
            participants=participants,
        )
        self.sessions.upsert_session_meta(session_meta)
        return session_meta

    def _upsert_message(self, row: MessageRow, sid: int, sender_aid: int) -> None:
        external_mid = f"{row.table_name}:{row.mid}"
        content = _message_content(row)
        message = self.Message(
            external_mid=external_mid,
            sid=sid,
            sender=sender_aid,
            read=None,
            content=content,
            type=_message_type(row),
        )
        self.messages.upsert_message(message)

    def _account_by_mapping(self, mapping_type: str, key: str) -> Any | None:
        with Session(self.engine) as session:
            statement = select(self.AccountMapping).where(
                self.AccountMapping.type == mapping_type,
                self.AccountMapping.key == key,
            )
            mapping = session.exec(statement).first()
            if mapping is None:
                return None
            return session.get(self.Account, mapping.aid)

    def _session_by_name(self, name: str) -> Any | None:
        with Session(self.engine) as session:
            statement = select(self.SessionMeta).where(self.SessionMeta.name == name)
            return session.exec(statement).first()

    def _upsert_mapping(self, aid: int, mapping_type: str, key: str) -> None:
        if not key:
            return
        existing = self._mapping_by_key(mapping_type, key)
        if existing is not None:
            if existing.aid != aid:
                logger.warning(
                    "CRM mapping conflict: type={} key={} existing_aid={} new_aid={}",
                    mapping_type,
                    key,
                    existing.aid,
                    aid,
                )
            return
        mapping = self.AccountMapping(aid=aid, type=mapping_type, key=key)
        self.mappings.upsert_account_mapping(mapping)

    def _mapping_by_key(self, mapping_type: str, key: str) -> Any | None:
        with Session(self.engine) as session:
            statement = select(self.AccountMapping).where(
                self.AccountMapping.type == mapping_type,
                self.AccountMapping.key == key,
            )
            return session.exec(statement).first()

    def get_self_info(self) -> SelfInfo | None:
        with Session(self.engine) as session:
            statement = select(self.Account)
            for account in session.exec(statement).all():
                if not isinstance(account.extra, dict):
                    continue
                if account.extra.get("is_self") is not True:
                    continue
                try:
                    return SelfInfo.model_validate(account.extra)
                except ValueError:
                    return None
        return None

    def get_user_info(self, identifier: str) -> UserInfo | None:
        account = None
        for mapping_type in (MAPPING_ALI_ID, MAPPING_LOGIN_ID, MAPPING_ENCRYPT_ACCOUNT_ID):
            account = self._account_by_mapping(mapping_type, identifier)
            if account is not None:
                break
        if account is None or not isinstance(account.extra, dict):
            return None
        try:
            return UserInfo.model_validate(account.extra)
        except ValueError:
            return None

    def list_conversations(self, self_ali_id: str) -> list[CrmConversation]:
        prefix = f"{PID_ALIBABA}:{self_ali_id}:"
        conversations: list[CrmConversation] = []
        with Session(self.engine) as session:
            session_statement = select(self.SessionMeta).where(self.SessionMeta.name.startswith(prefix))
            sessions = list(session.exec(session_statement).all())
            for session_meta in sessions:
                if session_meta.sid is None:
                    continue
                message_statement = select(self.Message).where(self.Message.sid == session_meta.sid)
                messages = [_crm_message_from_sdk(message) for message in session.exec(message_statement).all()]
                messages.sort(key=lambda message: coerce_epoch(message.created_at))
                if not messages:
                    continue
                contact_ali_id = str(session_meta.name or "").removeprefix(prefix)
                conversations.append(CrmConversation(
                    contact_ali_id=contact_ali_id,
                    messages=messages,
                    last_created_at=messages[-1].created_at,
                    last_content_label=messages[-1].content_label,
                ))
        conversations.sort(key=lambda conversation: coerce_epoch(conversation.last_created_at), reverse=True)
        return conversations


def sync_user_info(info: UserInfo) -> Future:
    return _submit_sync(_sync_user_info_now, info)


def sync_self_info(info: SelfInfo) -> Future:
    return _submit_sync(_sync_self_info_now, info)


def sync_all_identities() -> Future:
    return _submit_sync(_sync_all_identities_now)


def sync_conversations(conversations: list[ContactConv], self_info: SelfInfo | None) -> Future:
    return _submit_sync(_sync_conversations_now, conversations, self_info)


def sync_im_database(db_path: Path, self_ali_id: str, self_info: SelfInfo | None) -> Future:
    return _submit_sync(_sync_im_database_now, db_path, self_ali_id, self_info)


def _submit_sync(func, *args: Any) -> Future:
    future = _SYNC_EXECUTOR.submit(func, *args)
    future.add_done_callback(_log_sync_failure)
    return future


def _log_sync_failure(future) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("Failed to sync data into CRM SDK")


def _sync_user_info_now(info: UserInfo) -> None:
    CRMAdapter().upsert_user_info(info)


def _sync_self_info_now(info: SelfInfo) -> None:
    CRMAdapter().upsert_self_info(info)


def _sync_all_identities_now() -> None:
    adapter = CRMAdapter()
    self_info = get_self_info_pool().get()
    if self_info is not None:
        adapter.upsert_self_info(self_info)
    for info in get_user_info_pool().all().values():
        adapter.upsert_user_info(info)


def _sync_conversations_now(conversations: list[ContactConv], self_info: SelfInfo | None) -> None:
    CRMAdapter().sync_conversations(conversations, self_info)


def _sync_im_database_now(db_path: Path, self_ali_id: str, self_info: SelfInfo | None) -> None:
    from app.shared.backend.im_chat_db import build_conversations, open_readonly

    conn = open_readonly(db_path)
    try:
        CRMAdapter().sync_conversations(build_conversations(conn, self_ali_id), self_info)
    finally:
        conn.close()


def _default_database_path() -> Path:
    return Path(os.environ.get("MAA_CRM_DB_PATH", "data/crm.sqlite"))


def _session_name(self_ali_id: str, contact_ali_id: str) -> str:
    return f"{PID_ALIBABA}:{self_ali_id}:{contact_ali_id}"


def _user_display_name(info: UserInfo) -> str:
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
    if isinstance(value, bool):
        return False
    return value is None or value == "" or value == 0 or value == []


def _self_display_name(info: SelfInfo) -> str:
    full_name = " ".join(part for part in [info.first_name, info.last_name] if part).strip()
    return full_name or info.company_name or info.login_id or info.ali_id


def _message_from_self(row: MessageRow, self_info: SelfInfo | None) -> bool:
    if self_info is None or not self_info.ali_id:
        return False
    return row.sender_id == f"{self_info.ali_id}@icbu"


def _message_content(row: MessageRow) -> dict[str, Any]:
    text = _decode_content(row.content)
    content_latin1 = bytes(row.content).decode("latin1") if row.content is not None else None
    return {
        "cid": row.cid,
        "mid": row.mid,
        "table_name": row.table_name,
        "sender_id": row.sender_id,
        "created_at": row.created_at,
        "user_content_type": row.user_content_type,
        "content_label": row.content_label,
        "content": text,
        "content_latin1": content_latin1,
        "is_system": row.is_system,
        "is_auto_reply": row.is_auto_reply,
    }


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
        created_at=content.get("created_at"),
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
