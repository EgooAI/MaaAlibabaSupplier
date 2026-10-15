"""Customer portrait persistence in app-owned CRM extension tables.

Portraits bind to the resolved contact ``Customer.cid`` inside one workspace
(``seller`` + canonical data directory). Tag definitions are shared by the CRM
database while tag relations and portraits stay workspace-scoped. The store
never creates a missing CRM database and never migrates on reads.

Concurrency contract: every mutation validates target visibility, identity and
``expected_version`` in a single ``BEGIN IMMEDIATE`` transaction; any conflict
rejects the whole batch. A successful operation commits portraits, tag
relations, the operation record and workspace revisions together.
"""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from backend.app.shared.crm.paths import default_crm_database_path
from backend.app.shared.crm.sync_store import canonical_source_dir
from backend.app.shared.crm.views import is_self_account, resolve_contact_cid

PORTRAIT_STAGES = ("unknown", "new_inquiry", "following_up", "negotiating", "won", "dormant", "lost")
LOST_REASONS = ("unreachable", "price", "product_mismatch", "delivery", "competitor", "unclear")
MAX_BATCH_TARGETS = 50
MAX_EXPORT_TARGETS = 100
TAG_NAME_MAX = 40
TAG_DESCRIPTION_MAX = 200
LOST_NOTE_MAX = 500


class PortraitConflict(Exception):
    """Submitted targets or request identifiers no longer match committed state."""

    def __init__(self, message: str, items: list[dict] | None = None) -> None:
        super().__init__(message)
        self.items = items or []


class PortraitNotFound(LookupError):
    """The referenced tag or operation does not exist."""


class PortraitNotReady(RuntimeError):
    """The CRM database does not exist yet; portrait writes must not create it."""


def _scope(seller: str, data_dir: str | Path) -> dict:
    if not isinstance(seller, str) or not seller.strip():
        raise ValueError("seller must be a nonempty string")
    return {"seller": seller, "data_dir": canonical_source_dir(data_dir)}


def normalize_tag_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("标签名称必须是文本")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ValueError("标签名称不能为空")
    if len(normalized) > TAG_NAME_MAX:
        raise ValueError(f"标签名称不能超过 {TAG_NAME_MAX} 个字符")
    return normalized


def normalize_tag_description(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("标签说明必须是文本")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if len(normalized) > TAG_DESCRIPTION_MAX:
        raise ValueError(f"标签说明不能超过 {TAG_DESCRIPTION_MAX} 个字符")
    return normalized


def tag_key(name: str) -> str:
    return name.casefold()


def default_portrait_record() -> dict:
    return {
        "stage": "unknown", "lost_reason": None, "lost_note": None, "lost_at": None,
        "version": 0, "updated_at": None, "tags": [], "stored": False,
    }


def public_portrait_fields(cid: int | None, record: dict | None) -> dict:
    """Fields merged into ``customer_view`` for both list and detail DTOs."""
    if cid is None:
        return {
            "cid": None, "business_stage": "unknown", "business_tags": [], "business_version": 0,
            "lost_reason": None, "lost_note": None, "lost_at": None, "portrait_editable": False,
        }
    record = record or {}
    return {
        "cid": cid,
        "business_stage": record.get("stage") or "unknown",
        "business_tags": [
            {"tag_id": tag["tag_id"], "name": tag["name"], "archived": bool(tag.get("archived"))}
            for tag in record.get("tags") or []
        ],
        "business_version": record.get("version", 0),
        "lost_reason": record.get("lost_reason"),
        "lost_note": record.get("lost_note"),
        "lost_at": record.get("lost_at"),
        "portrait_editable": True,
    }


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


def inbox_scope_ready(conn) -> bool:
    """Whether the chat baseline tables exist; portrait writes require them."""
    return all(_table_exists(conn, name) for name in ("app_inbox_message", "session_meta", "account"))


def resolve_scope_contacts(
    conn, key: dict, sids: list[int],
) -> tuple[set[int], dict[int, tuple[int, dict]]]:
    """Resolve the contact of each ledger-visible session in one workspace.

    Returns the sessions that are both ledger-visible and still have session
    metadata, and a map from those sessions to ``(cid, contact account)``.
    Sessions whose identity is ambiguous or unresolved are absent from the map;
    callers decide whether that is a conflict or an export omission.
    """
    parameters = {**key, "sids": json.dumps(sorted(set(sids)))}
    visible = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT sid FROM app_inbox_message WHERE seller=:seller AND data_dir=:data_dir "
            "AND sid IN (SELECT value FROM json_each(:sids))", parameters,
        )
    }
    participants_by_sid: dict[int, list[int]] = {}
    for row in conn.execute(
        "SELECT sid, participants FROM session_meta WHERE sid IN (SELECT value FROM json_each(:sids))",
        parameters,
    ):
        participants_by_sid[row["sid"]] = [
            aid for aid in json.loads(row["participants"] or "[]") if type(aid) is int
        ]
    aids = sorted({aid for group in participants_by_sid.values() for aid in group})
    accounts: dict[int, dict] = {}
    if aids:
        for row in conn.execute(
            "SELECT * FROM account WHERE aid IN (SELECT value FROM json_each(:aids))",
            {"aids": json.dumps(aids)},
        ):
            record = dict(row)
            record["extra"] = json.loads(record["extra"]) if record["extra"] else None
            accounts[row["aid"]] = record
    known = visible & set(participants_by_sid)
    contacts: dict[int, tuple[int, dict]] = {}
    for sid in known:
        group = participants_by_sid[sid]
        cid = resolve_contact_cid(group, accounts)
        if cid is None:
            continue
        account = next(
            (accounts[aid] for aid in group
             if aid in accounts and not is_self_account(accounts[aid]) and accounts[aid].get("cid") == cid),
            {},
        )
        contacts[sid] = (cid, account)
    return known, contacts


def load_portrait_revisions(conn, seller: str, data_dir: str | Path) -> dict:
    empty = {"portrait_revision": 0, "tag_revision": 0}
    if not _table_exists(conn, "app_customer_portrait"):
        return empty
    parameters = {"seller": seller, "data_dir": canonical_source_dir(data_dir)}
    portrait = conn.execute(
        "SELECT portrait_revision FROM app_portrait_scope WHERE seller=:seller AND data_dir=:data_dir", parameters,
    ).fetchone()
    meta = conn.execute("SELECT revision FROM app_portrait_tag_meta WHERE id=1").fetchone()
    return {
        "portrait_revision": portrait["portrait_revision"] if portrait is not None else 0,
        "tag_revision": meta["revision"] if meta is not None else 0,
    }


def load_portrait_bundle(conn, seller: str, data_dir: str | Path, cids: list[int]) -> dict[int, dict]:
    """Read portrait records with tags in the caller's transaction.

    Missing tables or rows yield default (unknown, empty) records so callers can
    project and filter without distinguishing "never maintained" from "empty".
    """
    directory = canonical_source_dir(data_dir)
    result = {cid: default_portrait_record() for cid in cids}
    if not cids or not _table_exists(conn, "app_customer_portrait"):
        return result
    parameters = {
        "seller": seller, "data_dir": directory, "cids": json.dumps(sorted(cids)),
    }
    for row in conn.execute(
        "SELECT * FROM app_customer_portrait WHERE seller=:seller AND data_dir=:data_dir "
        "AND cid IN (SELECT value FROM json_each(:cids))",
        parameters,
    ):
        result[row["cid"]] = {
            "stage": row["stage"], "lost_reason": row["lost_reason"], "lost_note": row["lost_note"],
            "lost_at": row["lost_at"], "version": row["version"], "updated_at": row["updated_at"],
            "tags": [], "stored": True,
        }
    if _table_exists(conn, "app_customer_portrait_tag"):
        for row in conn.execute(
            "SELECT r.cid, t.tag_id, t.name, t.archived FROM app_customer_portrait_tag r "
            "JOIN app_portrait_tag t ON t.tag_id=r.tag_id "
            "WHERE r.seller=:seller AND r.data_dir=:data_dir "
            "AND r.cid IN (SELECT value FROM json_each(:cids)) ORDER BY t.name_key, t.tag_id",
            parameters,
        ):
            result[row["cid"]]["tags"].append(
                {"tag_id": row["tag_id"], "name": row["name"], "archived": bool(row["archived"])},
            )
    return result


def _normalize_targets(targets: list[dict]) -> list[dict]:
    if not isinstance(targets, list) or not targets:
        raise ValueError("至少需要一个画像目标")
    if len(targets) > MAX_BATCH_TARGETS:
        raise ValueError(f"单次画像操作最多处理 {MAX_BATCH_TARGETS} 个会话")
    normalized = []
    seen = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("画像目标必须是对象")
        sid = target.get("sid")
        cid = target.get("expected_cid")
        version = target.get("expected_version", 0)
        if type(sid) is not int or sid < 1:
            raise ValueError("目标会话标识无效")
        if type(cid) is not int or cid < 1:
            raise ValueError("目标客户标识无效")
        if type(version) is not int or version < 0:
            raise ValueError("目标版本无效")
        if sid in seen:
            raise ValueError("同一会话不能重复提交")
        seen.add(sid)
        normalized.append({"sid": sid, "expected_cid": cid, "expected_version": version})
    normalized.sort(key=lambda item: item["sid"])
    return normalized


def _normalize_changes(changes: dict) -> dict:
    if not isinstance(changes, dict):
        raise ValueError("画像变更必须是对象")
    unknown = set(changes) - {"stage", "lost_reason", "lost_note", "add_tag_ids", "remove_tag_ids"}
    if unknown:
        raise ValueError("画像变更包含未知字段")
    stage = changes.get("stage")
    if stage is not None and stage not in PORTRAIT_STAGES:
        raise ValueError("客户阶段无效")
    reason = changes.get("lost_reason")
    reason_set = "lost_reason" in changes and reason is not None
    if reason_set and reason not in LOST_REASONS:
        raise ValueError("失败原因无效")
    note = changes.get("lost_note")
    note_set = "lost_note" in changes and note is not None
    if note_set:
        if not isinstance(note, str) or len(note) > LOST_NOTE_MAX:
            raise ValueError(f"失败备注不能超过 {LOST_NOTE_MAX} 个字符")
        note = note.strip() or None
    add_value, remove_value = changes.get("add_tag_ids"), changes.get("remove_tag_ids")
    if add_value is not None and not isinstance(add_value, list):
        raise ValueError("标签标识无效")
    if remove_value is not None and not isinstance(remove_value, list):
        raise ValueError("标签标识无效")
    add = list(dict.fromkeys(add_value or []))
    remove = list(dict.fromkeys(remove_value or []))
    if any(not isinstance(tag_id, str) or not tag_id for tag_id in (*add, *remove)):
        raise ValueError("标签标识无效")
    if set(add) & set(remove):
        raise ValueError("同一标签不能同时增加和移除")
    if stage is None and not add and not remove and not reason_set and not note_set:
        raise ValueError("没有可应用的画像变更")
    return {
        "stage": stage, "stage_set": stage is not None,
        "lost_reason": reason, "lost_note": note,
        "reason_set": reason_set, "note_set": note_set,
        "add_tag_ids": add, "remove_tag_ids": remove,
    }


def _canonical_payload(targets: list[dict], changes: dict) -> str:
    return json.dumps({
        "targets": targets,
        "changes": {
            "stage": changes["stage"] if changes["stage_set"] else None,
            "lost_reason": changes["lost_reason"] if changes["reason_set"] else None,
            "lost_note": changes["lost_note"] if changes["note_set"] else None,
            "add_tag_ids": changes["add_tag_ids"],
            "remove_tag_ids": changes["remove_tag_ids"],
        },
    }, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _tag_record(row, usage_count: int | None = None) -> dict:
    record = {
        "tag_id": row["tag_id"], "name": row["name"], "description": row["description"],
        "archived": bool(row["archived"]), "version": row["version"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if usage_count is not None:
        record["usage_count"] = usage_count
    return record


class CustomerPortraitStore:
    def __init__(self, database_path: Path | str | None = None) -> None:
        if database_path is None:
            database_path = default_crm_database_path()
        self.database_path = Path(database_path).expanduser().resolve()

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[Any]:
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(
            self.database_path.as_uri() + f"?mode={mode}", uri=True,
            timeout=30, isolation_level=None,
        )) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            try:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
                if write:
                    conn.commit()
            except BaseException:
                if write:
                    conn.rollback()
                raise

    def _ensure_schema(self, conn) -> None:
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
        # Databases initialized before the audit snapshot get the column here;
        # the additive migration must stay inside the caller's write transaction.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(app_portrait_operation)")}
        if "before" not in columns:
            conn.execute("ALTER TABLE app_portrait_operation ADD COLUMN before TEXT")
        conn.execute("INSERT OR IGNORE INTO app_portrait_tag_meta (id, revision) VALUES (1, 0)")

    def _require_writable(self) -> None:
        if not self.database_path.is_file():
            raise PortraitNotReady("CRM 数据库不存在，画像功能未就绪")

    # ---------------------------------------------------------------- reads

    def portrait_records(self, seller: str, data_dir: str | Path, cids: list[int]) -> dict[int, dict]:
        key = _scope(seller, data_dir)
        if not cids or not self.database_path.is_file():
            return {cid: default_portrait_record() for cid in cids}
        with self._connection() as conn:
            if not _table_exists(conn, "app_customer_portrait"):
                return {cid: default_portrait_record() for cid in cids}
            return load_portrait_bundle(conn, key["seller"], key["data_dir"], cids)

    def tag_catalog(self, seller: str, data_dir: str | Path) -> list[dict]:
        key = _scope(seller, data_dir)
        if not self.database_path.is_file():
            return []
        with self._connection() as conn:
            if not _table_exists(conn, "app_customer_portrait"):
                return []
            return [
                _tag_record(row, row["usage_count"])
                for row in conn.execute(
                    "SELECT t.*, (SELECT COUNT(*) FROM app_customer_portrait_tag r "
                    "WHERE r.seller=:seller AND r.data_dir=:data_dir AND r.tag_id=t.tag_id) AS usage_count "
                    "FROM app_portrait_tag t ORDER BY t.archived, t.name_key, t.tag_id",
                    {"seller": key["seller"], "data_dir": key["data_dir"]},
                )
            ]

    def observe_revisions(self, seller: str, data_dir: str | Path) -> dict:
        empty = {"portrait_revision": 0, "tag_revision": 0}
        if not seller or not self.database_path.is_file():
            return empty
        key = _scope(seller, data_dir)
        with self._connection() as conn:
            if not _table_exists(conn, "app_customer_portrait"):
                return empty
            return load_portrait_revisions(conn, key["seller"], key["data_dir"])

    def operations(self, seller: str, data_dir: str | Path, cid: int | None, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        if cid is not None and (type(cid) is not int or cid < 1):
            raise ValueError("客户标识无效")
        key = _scope(seller, data_dir)
        if not self.database_path.is_file():
            return []
        with self._connection() as conn:
            if not _table_exists(conn, "app_customer_portrait"):
                return []
            entries: list[dict] = []
            rows = conn.execute(
                "SELECT * FROM app_portrait_operation WHERE seller=:seller AND data_dir=:data_dir "
                "ORDER BY created_at DESC, rowid DESC", key,
            )
            for row in rows:
                results = json.loads(row["results"])
                payload = json.loads(row["payload"])
                targets = [
                    entry for entry in (*results.get("changed", []), *results.get("unchanged", []))
                    if cid is None or entry.get("cid") == cid
                ]
                if cid is not None and not targets:
                    continue
                # Legacy rows predating the audit snapshot carry no column and read as null.
                raw_before = row["before"] if "before" in row.keys() else None
                entries.append({
                    "request_id": row["request_id"], "kind": row["kind"], "created_at": row["created_at"],
                    "changes": payload.get("changes") or {}, "targets": targets,
                    "before": json.loads(raw_before) if raw_before else None,
                })
                if len(entries) >= limit:
                    break
            return entries

    # --------------------------------------------------------------- writes

    def apply(self, *, seller: str, data_dir: str | Path, request_id: str, targets: list[dict], changes: dict) -> dict:
        key = _scope(seller, data_dir)
        self._require_writable()
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 64:
            raise ValueError("请求标识无效")
        normalized_targets = _normalize_targets(targets)
        normalized_changes = _normalize_changes(changes)
        payload = _canonical_payload(normalized_targets, normalized_changes)
        now = time.time()
        with self._connection(write=True) as conn:
            self._ensure_schema(conn)
            prior = conn.execute(
                "SELECT seller, data_dir, payload, results FROM app_portrait_operation WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if prior is not None:
                if prior["seller"] != key["seller"] or prior["data_dir"] != key["data_dir"]:
                    raise PortraitConflict("请求标识已用于其他工作区")
                if prior["payload"] != payload:
                    raise PortraitConflict("请求标识已用于其他画像操作")
                return json.loads(prior["results"])

            if not inbox_scope_ready(conn):
                raise PortraitConflict("聊天数据未就绪，暂不能维护客户画像")
            known, contacts = resolve_scope_contacts(
                conn, key, [target["sid"] for target in normalized_targets],
            )
            conflicts: list[dict] = []
            cid_by_sid: dict[int, int] = {}
            for target in normalized_targets:
                sid = target["sid"]
                if sid not in known:
                    conflicts.append({"sid": sid, "reason": "not_visible"})
                    continue
                resolved = contacts.get(sid)
                if resolved is None:
                    conflicts.append({"sid": sid, "reason": "identity_unresolved"})
                elif resolved[0] != target["expected_cid"]:
                    conflicts.append({"sid": sid, "reason": "identity_changed"})
                else:
                    cid_by_sid[sid] = resolved[0]

            current = load_portrait_bundle(conn, key["seller"], key["data_dir"], sorted(set(cid_by_sid.values())))
            sids_by_cid: dict[int, list[int]] = {}
            for sid, cid in cid_by_sid.items():
                sids_by_cid.setdefault(cid, []).append(sid)
            for cid, sids in sids_by_cid.items():
                expected = {
                    target["expected_version"] for target in normalized_targets
                    if cid_by_sid.get(target["sid"]) == cid
                }
                if len(expected) != 1:
                    conflicts.append({"cid": cid, "reason": "target_version_mismatch"})
                    continue
                current_version = current.get(cid, {}).get("version", 0)
                if next(iter(expected)) != current_version:
                    conflicts.append({"cid": cid, "reason": "version_changed", "current_version": current_version})

            if normalized_changes["reason_set"] or normalized_changes["note_set"]:
                for cid in sids_by_cid:
                    stored = current.get(cid) or default_portrait_record()
                    stage = normalized_changes["stage"] if normalized_changes["stage_set"] else stored["stage"]
                    if stage != "lost":
                        raise ValueError("失败原因和备注仅在流失阶段维护")

            tag_ids = sorted({*normalized_changes["add_tag_ids"], *normalized_changes["remove_tag_ids"]})
            tags: dict[str, dict] = {}
            if tag_ids:
                for row in conn.execute(
                    "SELECT * FROM app_portrait_tag WHERE tag_id IN (SELECT value FROM json_each(:tag_ids))",
                    {"tag_ids": json.dumps(tag_ids)},
                ):
                    tags[row["tag_id"]] = dict(row)
            for tag_id in normalized_changes["add_tag_ids"]:
                tag = tags.get(tag_id)
                if tag is None:
                    raise ValueError("新增标签不存在，请刷新后重试")
                if tag["archived"]:
                    raise ValueError(f"标签「{tag['name']}」已停用，不能新增")
            for tag_id in normalized_changes["remove_tag_ids"]:
                if tag_id not in tags:
                    raise ValueError("移除标签不存在，请刷新后重试")
            if conflicts:
                raise PortraitConflict("画像目标已变化，请刷新后重试", items=conflicts)

            # Audit snapshot of every committed target's state before this batch,
            # read from the same in-transaction bundle; never-maintained records
            # appear as their defaults so "before" always shows where it started.
            before = {}
            for cid in sorted(sids_by_cid):
                record = current.get(cid) or default_portrait_record()
                before[str(cid)] = {
                    "stage": record["stage"], "lost_reason": record["lost_reason"],
                    "lost_note": record["lost_note"], "lost_at": record["lost_at"],
                    "tags": [{"tag_id": tag["tag_id"], "name": tag["name"]} for tag in record["tags"]],
                }

            changed: list[dict] = []
            unchanged: list[dict] = []
            for cid in sorted(sids_by_cid):
                record = current.get(cid) or default_portrait_record()
                old_tags = {tag["tag_id"] for tag in record["tags"]}
                add_now = [tag_id for tag_id in normalized_changes["add_tag_ids"] if tag_id not in old_tags]
                remove_now = [tag_id for tag_id in normalized_changes["remove_tag_ids"] if tag_id in old_tags]
                new_stage = normalized_changes["stage"] if normalized_changes["stage_set"] else record["stage"]
                if new_stage == "lost":
                    new_reason = normalized_changes["lost_reason"] if normalized_changes["reason_set"] else record["lost_reason"]
                    new_note = normalized_changes["lost_note"] if normalized_changes["note_set"] else record["lost_note"]
                    new_lost_at = record["lost_at"] if record["stage"] == "lost" and record["lost_at"] is not None else now
                else:
                    new_reason, new_note, new_lost_at = None, None, None
                stage_changed = (
                    new_stage != record["stage"] or new_reason != record["lost_reason"]
                    or new_note != record["lost_note"] or new_lost_at != record["lost_at"]
                )
                if not stage_changed and not add_now and not remove_now:
                    unchanged.append({"cid": cid, "sids": sorted(sids_by_cid[cid]), "version": record["version"]})
                    continue
                new_version = record["version"] + 1
                if not record.get("stored"):
                    conn.execute(
                        "INSERT INTO app_customer_portrait "
                        "(seller, data_dir, cid, stage, lost_reason, lost_note, lost_at, version, updated_at) "
                        "VALUES (:seller, :data_dir, :cid, :stage, :lost_reason, :lost_note, :lost_at, :version, :updated_at)",
                        {**key, "cid": cid, "stage": new_stage, "lost_reason": new_reason,
                         "lost_note": new_note, "lost_at": new_lost_at, "version": new_version, "updated_at": now},
                    )
                else:
                    conn.execute(
                        "UPDATE app_customer_portrait SET stage=:stage, lost_reason=:lost_reason, "
                        "lost_note=:lost_note, lost_at=:lost_at, version=:version, updated_at=:updated_at "
                        "WHERE seller=:seller AND data_dir=:data_dir AND cid=:cid",
                        {**key, "cid": cid, "stage": new_stage, "lost_reason": new_reason,
                         "lost_note": new_note, "lost_at": new_lost_at, "version": new_version, "updated_at": now},
                    )
                for tag_id in add_now:
                    conn.execute(
                        "INSERT OR IGNORE INTO app_customer_portrait_tag (seller, data_dir, cid, tag_id, created_at) "
                        "VALUES (:seller, :data_dir, :cid, :tag_id, :created_at)",
                        {**key, "cid": cid, "tag_id": tag_id, "created_at": now},
                    )
                for tag_id in remove_now:
                    conn.execute(
                        "DELETE FROM app_customer_portrait_tag WHERE seller=:seller AND data_dir=:data_dir "
                        "AND cid=:cid AND tag_id=:tag_id",
                        {**key, "cid": cid, "tag_id": tag_id},
                    )
                changed.append({"cid": cid, "sids": sorted(sids_by_cid[cid]), "version": new_version})

            if changed:
                updated = conn.execute(
                    "UPDATE app_portrait_scope SET portrait_revision=portrait_revision+1 "
                    "WHERE seller=:seller AND data_dir=:data_dir", key,
                )
                if updated.rowcount == 0:
                    conn.execute(
                        "INSERT INTO app_portrait_scope (seller, data_dir, portrait_revision) "
                        "VALUES (:seller, :data_dir, 1)", key,
                    )
            revisions = load_portrait_revisions(conn, key["seller"], key["data_dir"])
            results = {
                "request_id": request_id,
                "portrait_revision": revisions["portrait_revision"],
                "tag_revision": revisions["tag_revision"],
                "changed": changed, "unchanged": unchanged,
            }
            conn.execute(
                "INSERT INTO app_portrait_operation "
                "(request_id, seller, data_dir, kind, payload, results, before, created_at) "
                "VALUES (:request_id, :seller, :data_dir, 'batch', :payload, :results, :before, :created_at)",
                {"request_id": request_id, **key, "payload": payload,
                 "results": json.dumps(results, ensure_ascii=False),
                 "before": json.dumps(before, ensure_ascii=False), "created_at": now},
            )
            return results

    def create_tag(self, *, name: str, description: str = "") -> dict:
        self._require_writable()
        label = normalize_tag_name(name)
        key = tag_key(label)
        desc = normalize_tag_description(description)
        now = time.time()
        with self._connection(write=True) as conn:
            self._ensure_schema(conn)
            if conn.execute("SELECT 1 FROM app_portrait_tag WHERE name_key=?", (key,)).fetchone():
                raise PortraitConflict("同名标签已存在")
            tag_id = uuid4().hex
            conn.execute(
                "INSERT INTO app_portrait_tag (tag_id, name, name_key, description, archived, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, 1, ?, ?)",
                (tag_id, label, key, desc, now, now),
            )
            conn.execute("UPDATE app_portrait_tag_meta SET revision=revision+1 WHERE id=1")
            row = conn.execute("SELECT * FROM app_portrait_tag WHERE tag_id=?", (tag_id,)).fetchone()
            return _tag_record(row, 0)

    def update_tag(
        self, *, seller: str, data_dir: str | Path, tag_id: str,
        name: str | None = None, description: str | None = None, archived: bool | None = None,
    ) -> dict:
        scope = _scope(seller, data_dir)
        self._require_writable()
        if not isinstance(tag_id, str) or not tag_id:
            raise ValueError("标签标识无效")
        if archived is not None and type(archived) is not bool:
            raise ValueError("停用状态无效")
        with self._connection(write=True) as conn:
            self._ensure_schema(conn)
            row = conn.execute("SELECT * FROM app_portrait_tag WHERE tag_id=?", (tag_id,)).fetchone()
            if row is None:
                raise PortraitNotFound("标签不存在")
            updates: dict[str, Any] = {}
            if name is not None:
                label = normalize_tag_name(name)
                name_key = tag_key(label)
                if name_key != row["name_key"]:
                    if conn.execute(
                        "SELECT 1 FROM app_portrait_tag WHERE name_key=? AND tag_id != ?", (name_key, tag_id),
                    ).fetchone():
                        raise PortraitConflict("同名标签已存在")
                    updates["name"], updates["name_key"] = label, name_key
            if description is not None:
                desc = normalize_tag_description(description)
                if desc != row["description"]:
                    updates["description"] = desc
            if archived is not None and bool(archived) != bool(row["archived"]):
                updates["archived"] = 1 if archived else 0
            if updates:
                updates["version"] = row["version"] + 1
                updates["updated_at"] = time.time()
                assignments = ", ".join(f"{field}=:{field}" for field in updates)
                conn.execute(
                    f"UPDATE app_portrait_tag SET {assignments} WHERE tag_id=:tag_id",
                    {**updates, "tag_id": tag_id},
                )
                conn.execute("UPDATE app_portrait_tag_meta SET revision=revision+1 WHERE id=1")
                row = conn.execute("SELECT * FROM app_portrait_tag WHERE tag_id=?", (tag_id,)).fetchone()
            usage = conn.execute(
                "SELECT COUNT(*) FROM app_customer_portrait_tag "
                "WHERE seller=:seller AND data_dir=:data_dir AND tag_id=:tag_id",
                {**scope, "tag_id": tag_id},
            ).fetchone()[0]
            return _tag_record(row, usage)


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS app_customer_portrait (
        seller TEXT NOT NULL,
        data_dir TEXT NOT NULL,
        cid INTEGER NOT NULL,
        stage TEXT NOT NULL,
        lost_reason TEXT,
        lost_note TEXT,
        lost_at REAL,
        version INTEGER NOT NULL CHECK (version >= 1),
        updated_at REAL NOT NULL,
        PRIMARY KEY (seller, data_dir, cid)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_portrait_tag (
        tag_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        name_key TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_customer_portrait_tag (
        seller TEXT NOT NULL,
        data_dir TEXT NOT NULL,
        cid INTEGER NOT NULL,
        tag_id TEXT NOT NULL REFERENCES app_portrait_tag(tag_id),
        created_at REAL NOT NULL,
        PRIMARY KEY (seller, data_dir, cid, tag_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS app_customer_portrait_tag_reverse ON app_customer_portrait_tag (seller, data_dir, tag_id, cid)",
    "CREATE INDEX IF NOT EXISTS app_customer_portrait_stage ON app_customer_portrait (seller, data_dir, stage, cid)",
    """
    CREATE TABLE IF NOT EXISTS app_portrait_operation (
        request_id TEXT PRIMARY KEY,
        seller TEXT NOT NULL,
        data_dir TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        results TEXT NOT NULL,
        before TEXT,
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS app_portrait_operation_scope ON app_portrait_operation (seller, data_dir, created_at)",
    """
    CREATE TABLE IF NOT EXISTS app_portrait_scope (
        seller TEXT NOT NULL,
        data_dir TEXT NOT NULL,
        portrait_revision INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (seller, data_dir)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_portrait_tag_meta (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        revision INTEGER NOT NULL DEFAULT 0
    )
    """,
)
