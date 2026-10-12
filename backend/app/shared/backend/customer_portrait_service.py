"""Customer portrait export: workspace-scoped CSV for selected conversations.

Targets are re-resolved server-side inside the caller's workspace; the client
cannot export another workspace's portraits by guessing SIDs. Output is
de-duplicated by ``cid`` and reports SIDs that are missing, invisible or have
an unresolved identity instead of guessing.
"""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from backend.app.shared.crm.customer_portrait_store import (
    MAX_EXPORT_TARGETS,
    PortraitNotReady,
    inbox_scope_ready,
    load_portrait_bundle,
    public_portrait_fields,
    resolve_scope_contacts,
)
from backend.app.shared.crm.inbox_store import InboxStore
from backend.app.shared.crm.sync_store import canonical_source_dir
from backend.app.shared.crm.views import format_created_at

EXPORT_HEADERS = (
    "cid", "ali_id", "name", "company", "country", "stage",
    "business_tags", "lost_reason", "lost_note", "updated_at",
)


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@") or text[:1] in ("\t", "\r"):
        return f"'{text}"
    return text


def export_customer_portraits(store: InboxStore, *, seller: str, data_dir: str, sids: list[int]) -> dict:
    if not sids:
        raise ValueError("至少选择一个会话")
    if len(sids) > MAX_EXPORT_TARGETS:
        raise ValueError(f"单次最多导出 {MAX_EXPORT_TARGETS} 个会话")
    if len(set(sids)) != len(sids):
        raise ValueError("会话不能重复提交")
    if not store.database_path.is_file():
        raise PortraitNotReady("CRM 数据库不存在，画像功能未就绪")
    directory = canonical_source_dir(data_dir)
    key = {"seller": seller, "data_dir": directory}
    with store._connection() as conn:
        if not inbox_scope_ready(conn):
            raise PortraitNotReady("聊天数据未就绪，暂不能导出客户资料")
        _, contacts = resolve_scope_contacts(conn, key, sids)
        cids = sorted({cid for cid, _ in contacts.values()})
        customers: dict[int, dict] = {}
        if cids:
            for row in conn.execute(
                "SELECT * FROM customer WHERE cid IN (SELECT value FROM json_each(:cids))",
                {"cids": json.dumps(cids)},
            ):
                customers[row["cid"]] = dict(row)
        portraits = load_portrait_bundle(conn, seller, directory, cids)

    rows: dict[int, dict] = {}
    missing: list[int] = []
    for sid in sids:
        resolved = contacts.get(sid)
        if resolved is None:
            missing.append(sid)
            continue
        cid, account = resolved
        customer = customers.get(cid) or {}
        extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        portrait = portraits.get(cid) or {}
        fields = public_portrait_fields(cid, portrait)
        rows[cid] = {
            "cid": cid,
            "ali_id": extra.get("ali_id") or account.get("account") or "",
            "name": customer.get("name") or account.get("nickname") or "",
            "company": extra.get("company_name") or "",
            "country": extra.get("country_code") or customer.get("region") or "",
            "stage": fields["business_stage"],
            "business_tags": "; ".join(tag["name"] for tag in fields["business_tags"]),
            "lost_reason": fields["lost_reason"] or "",
            "lost_note": fields["lost_note"] or "",
            "updated_at": format_created_at(portrait.get("updated_at")),
        }

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_HEADERS)
    for cid in sorted(rows):
        writer.writerow([_csv_cell(rows[cid][header]) for header in EXPORT_HEADERS])
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return {
        "filename": f"customer-portraits-{timestamp}.csv",
        "content": "\ufeff" + buffer.getvalue(),
        "exported": len(rows),
        "missing": missing,
    }
