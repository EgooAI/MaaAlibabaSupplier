"""Strict observations of an already verified IM cache, without refresh or sync.

An invalid result is never an empty successful baseline. Callers must check
``valid`` before saving origin/ids/checked_at. CRC reads may be slow even though
cache lifecycle locks cover only pin acquisition/release. GUI guards must account
for that cost. A stale baseline requires refresh before sending; a verifier can
observe again after the existing source worker has refreshed the cache.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from backend.app.shared.backend.account_context import AccountContext
from backend.app.shared.backend.im_chat_db import list_msg_tables, open_readonly
from backend.app.shared.backend.im_db_middleware import get_im_db_middleware
from backend.app.shared.utils.crm_time import coerce_epoch


def _extension_flags(raw: object) -> tuple[bool, bool, bool]:
    try:
        outer = json.loads(raw)
        if not isinstance(outer, dict):
            return False, False, False
        basic = outer.get("basicMessageInfo", {})
        if isinstance(basic, str):
            basic = json.loads(basic)
        if not isinstance(basic, dict):
            return False, False, False
        flags = ("systemMessage", "autoReply")
        if any(name in obj and type(obj[name]) is not bool for obj in (outer, basic) for name in flags):
            return False, False, False
        return (any(obj.get("systemMessage") is True for obj in (outer, basic)),
                any(obj.get("autoReply") is True for obj in (outer, basic)), True)
    except (TypeError, ValueError, RecursionError):
        return False, False, False


def read_source_messages(context: AccountContext, contact_ali_id: str) -> dict:
    """Return valid/reason/origin/source_revision/checked_at/messages in seconds.

    Only the current selected seller/directory and an enabled, verified cache are
    eligible. ``context.epoch`` is not a read permission or GUI authorization.
    Failure always returns valid=False with a reason and discards partial rows.
    Missing source text/sender are empty strings; unknown types use -1 so they
    cannot be mistaken for a plain-text send. All source IDs remain in the view.
    """
    result = {"valid": False, "reason": "source_unavailable", "origin": {
        "seller": context.self_ali_id, "data_dir": context.data_dir, "source_path": "",
    }, "source_revision": 0, "checked_at": time.time(), "messages": []}
    if (not isinstance(contact_ali_id, str) or not contact_ali_id
            or any(char in contact_ali_id for char in "-#")
            or contact_ali_id == context.self_ali_id):
        result["reason"] = "invalid_contact"
        return result
    try:
        mw = get_im_db_middleware()
        with mw.pin_source_reader(context) as pinned:
            result.update(origin=pinned["origin"], source_revision=pinned["source_revision"])
            source = Path(pinned["origin"]["source_path"])
            captured = mw._capture_source(source)
            if captured is None:
                result["reason"] = "source_unreadable"
                return result
            if captured != pinned["signature"]:
                result["reason"] = "source_stale"
                return result
            # Record the start of the observation, not the end of a slow scan.
            result["checked_at"] = time.time()
            cids = (f"{context.self_ali_id}-{contact_ali_id}", f"{contact_ali_id}-{context.self_ali_id}")
            messages = []
            with closing(open_readonly(pinned["path"])) as conn:
                conn.execute("BEGIN")
                for table in list_msg_tables(conn):
                    quoted = '"' + table.replace('"', '""') + '"'
                    # substr is literal (unlike LIKE's %/_), and includes # suffixes.
                    rows = conn.execute(
                        f"SELECT mid, sender_id, cid, created_at, user_content_type, content_label, extension "
                        f"FROM {quoted} WHERE cid IN (?, ?) "
                        "OR substr(cid, 1, instr(cid, '#') - 1) IN (?, ?)", (*cids, *cids),
                    )
                    for row in rows:
                        system, auto, valid = _extension_flags(row["extension"])
                        stamp = coerce_epoch(row["created_at"])
                        if not math.isfinite(stamp):
                            raise ValueError("source_invalid_timestamp")
                        if row["mid"] is None or not str(row["mid"]) or ":" in table:
                            raise ValueError("source_invalid_message_id")
                        messages.append({
                            "id": f"{table}:{row['mid']}",
                            "sender_id": row["sender_id"] if isinstance(row["sender_id"], str) else "",
                            "cid": row["cid"], "created_at": stamp,
                            "type": row["user_content_type"] if type(row["user_content_type"]) is int else -1,
                            "text": row["content_label"] if isinstance(row["content_label"], str) else "",
                            "is_system": system, "is_auto_reply": auto, "extension_valid": valid,
                        })
            after = mw._capture_source(source)
            if after is None or after != captured:
                result["reason"] = "source_unreadable" if after is None else "source_stale"
                return result
            # A reset/account switch during the read revokes current eligibility.
            with mw.pin_source_reader(context) as current:
                if current["origin"] != pinned["origin"] or current["signature"] != captured:
                    result["reason"] = "source_stale"
                    return result
            result.update(valid=True, reason="fresh", messages=messages)
    except ValueError as exc:
        result["reason"] = str(exc) or "source_invalid"
    except (OSError, sqlite3.Error):
        result["reason"] = "source_read_failed"
    return result
