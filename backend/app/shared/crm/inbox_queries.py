"""Read inbox projections in one SQLite snapshot, without loading message blobs.

The API explicitly initializes an existing archive before calling this module.
Store-private transaction helpers are confined here until a shared read-session
interface is available. Customer resolution is supplied by the API's existing
validated view builder, so filtering and rendered profiles cannot disagree.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict

from backend.app.shared.crm.identities import session_key_prefix
from backend.app.shared.crm.inbox_store import InboxStore
from backend.app.shared.crm.sdk import Account, Customer
from backend.app.shared.crm.sync_store import canonical_source_dir
from backend.app.shared.crm.views import CrmConversationDigest, coerce_epoch
from backend.app.shared.mitm.pool import UserInfo


STATE_FIELDS = (
    "unread_count", "reply_state", "pending_since", "due_at", "is_overdue",
    "history_pending", "uncertain",
)


def public_state(state: dict) -> dict:
    return {field: state[field] for field in STATE_FIELDS}


def scoped_message_ids(store: InboxStore, seller: str, data_dir: str, sid: int) -> set[str]:
    with store._connection() as conn:
        return {row[0] for row in conn.execute(
            "SELECT external_mid FROM app_inbox_message WHERE seller=? AND data_dir=? AND sid=?",
            (seller, canonical_source_dir(data_dir), sid),
        )}


def has_archive_messages(store: InboxStore, seller: str) -> bool:
    if not store.database_path.is_file():
        return False
    prefix = session_key_prefix(seller)
    with store._connection() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"message", "session_meta"} <= tables:
            return False
        return conn.execute(
            "SELECT 1 FROM message m JOIN session_meta s ON s.sid=m.sid "
            "WHERE substr(s.key, 1, ?)=? LIMIT 1", (len(prefix), prefix),
        ).fetchone() is not None


def observe_inbox(store: InboxStore, seller: str, data_dir: str) -> dict:
    """Pure observation, including the next deadline; never initialize a scope."""
    if not seller or not store.database_path.is_file():
        return {"inbox_revision": 0, "next_due_at": None}
    key = {"seller": seller, "data_dir": canonical_source_dir(data_dir)}
    now = time.time()
    with store._connection() as conn:
        metadata = store._metadata(conn, key)
        states = store._states(conn, key, None, now)
        return {
            "inbox_revision": metadata["inbox_revision"],
            "next_due_at": min((s["due_at"] for s in states.values()
                                if s["reply_state"] == "needs_reply" and s["due_at"] > now), default=None),
        }


def query_inbox(store: InboxStore, seller: str, data_dir: str, build_view, filters: dict) -> dict:
    key = {"seller": seller, "data_dir": canonical_source_dir(data_dir)}
    prefix = session_key_prefix(seller)
    now = time.time()
    fingerprint = hashlib.sha256()

    def hash_row(value):
        fingerprint.update(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str,
                                      separators=(",", ":")).encode())
        fingerprint.update(b"\n")

    with store._connection() as conn:
        metadata = store._metadata(conn, key)
        hash_row(metadata)
        hash_row(filters)
        metas = {row["sid"]: dict(row) for row in conn.execute(
            "SELECT sid, key, participants FROM session_meta WHERE substr(key, 1, :length)=:prefix "
            "AND sid IN (SELECT sid FROM app_inbox_message WHERE seller=:seller AND data_dir=:data_dir) ORDER BY sid",
            {**key, "length": len(prefix), "prefix": prefix},
        )}
        parameters = {**key, "sids": json.dumps(list(metas)), "q": filters.get("q", "")}
        # instr treats Chinese and SQL wildcard characters as literal text. Only
        # visible labels are searched, never the raw content/card payload.
        rows = conn.execute("""
            SELECT m.sid, m.external_mid, m.created_at,
                   json_extract(m.content, '$.content_label') AS content_label,
                   json_extract(m.content, '$.user_content_type') AS content_type,
                   json_extract(m.content, '$.sender_id') AS sender_id,
                   coalesce(json_extract(m.content, '$.is_system'), 0) AS is_system,
                   coalesce(json_extract(m.content, '$.is_auto_reply'), 0) AS is_auto_reply,
                   instr(lower(coalesce(json_extract(m.content, '$.content_label'), '')), lower(:q)) > 0 AS matches
            FROM message m JOIN app_inbox_message i ON i.external_mid=m.external_mid AND i.sid=m.sid
            WHERE i.seller=:seller AND i.data_dir=:data_dir
              AND m.sid IN (SELECT value FROM json_each(:sids))
            ORDER BY m.sid, m.created_at, m.external_mid
        """, parameters)
        latest, counts, message_matches = {}, {}, set()
        for row in rows:
            record = dict(row)
            hash_row(record)
            sid = row["sid"]
            latest[sid] = record
            counts[sid] = counts.get(sid, 0) + int(not row["is_system"] and not row["is_auto_reply"])
            if row["matches"]:
                message_matches.add(sid)
        digests = []
        for sid, row in latest.items():
            meta = metas[sid]
            digests.append(CrmConversationDigest(
                sid=sid, key=meta["key"], contact_ali_id=meta["key"][len(prefix):],
                participants=tuple(json.loads(meta["participants"] or "[]")),
                latest_created_at=row["created_at"], latest_content_label=row["content_label"],
                latest_is_card=row["content_type"] == 10010, dialogue_count=counts[sid],
            ))
        digests.sort(key=lambda d: (coerce_epoch(d.latest_created_at), d.sid), reverse=True)
        aids = {aid for d in digests for aid in d.participants}
        contacts = {d.contact_ali_id for d in digests}
        mappings = conn.execute("""
            SELECT key, aid, type FROM accountmapping
            WHERE key IN (SELECT value FROM json_each(?))
              AND type IN ('ali_id', 'login_id', 'encrypt_account_id')
            ORDER BY CASE type WHEN 'ali_id' THEN 0 WHEN 'login_id' THEN 1 ELSE 2 END, aid, key
        """, (json.dumps(sorted(contacts)),)).fetchall()
        key_to_aid = {}
        for row in mappings:
            hash_row(dict(row))
            key_to_aid.setdefault(row["key"], row["aid"])
        aids.update(key_to_aid.values())
        accounts, customers, users = {}, {}, {}
        for row in conn.execute("SELECT * FROM account WHERE aid IN (SELECT value FROM json_each(?)) ORDER BY aid",
                                (json.dumps(sorted(aids)),)):
            record = dict(row)
            hash_row(record)
            for field in ("extra", "sids"):
                record[field] = json.loads(record[field]) if record[field] else None
            accounts[row["aid"]] = Account.model_validate(record)
        cids = sorted({account.cid for account in accounts.values()})
        for row in conn.execute("SELECT * FROM customer WHERE cid IN (SELECT value FROM json_each(?)) ORDER BY cid",
                                (json.dumps(cids),)):
            record = dict(row)
            hash_row(record)
            for field in ("extra", "image"):
                record[field] = json.loads(record[field]) if record[field] else None
            customers[row["cid"]] = Customer.model_validate(record)
        for contact, aid in key_to_aid.items():
            account = accounts.get(aid)
            if account is not None and isinstance(account.extra, dict):
                try:
                    users[contact] = UserInfo.model_validate(account.extra)
                except ValueError:
                    pass
        preloaded = {"accounts": accounts, "customers": customers, "users": users}
        states = store._states(conn, key, latest, now)
        selected, views = [], {}
        for digest in digests:
            state = states[digest.sid]
            view = build_view(digest, preloaded)
            views[digest.sid] = view
            hash_row([asdict(digest), view, state])
            q = filters.get("q", "").casefold()
            customer_match = any(q in str(view.get(field) or "").casefold() for field in (
                "id", "ali_id", "login_id", "encrypt_account_id", "member_id", "name",
                "first_name", "last_name", "company", "email", "mobile", "phone",
            )) or q in digest.contact_ali_id.casefold()
            scope = filters.get("search_scope", "all")
            if q and not ((scope != "messages" and customer_match) or
                          (scope != "customer" and digest.sid in message_matches)):
                continue
            if filters.get("country") is not None and view["country"].casefold() != filters["country"].casefold():
                continue
            if filters.get("tag") is not None and filters["tag"] not in view["tags"]:
                continue
            if filters.get("reply_state") is not None and state["reply_state"] != filters["reply_state"]:
                continue
            if filters.get("unread") is not None and bool(state["unread_count"]) != filters["unread"]:
                continue
            if filters.get("overdue") is not None and state["is_overdue"] != filters["overdue"]:
                continue
            selected.append(digest)
        selected_states = [states[d.sid] for d in selected]
        counts = {"total": len(selected), **{
            name: sum(bool(s["unread_count"]) if name == "unread" else s["is_overdue"] if name == "overdue"
                      else s["reply_state"] == name for s in selected_states)
            for name in ("unread", "needs_reply", "overdue", "history_pending", "waiting_customer")
        }}
        return {
            "digests": selected, "preloaded": preloaded, "views": views, "states": states,
            "total": len(selected), "counts": counts, "updated_at": now,
            "inbox_revision": metadata["inbox_revision"], "timeout_seconds": metadata["timeout_seconds"],
            "pagination_revision": fingerprint.hexdigest(),
        }
