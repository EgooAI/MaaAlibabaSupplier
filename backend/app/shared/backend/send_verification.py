"""Pure local-source matching, never a delivery confirmation or a send retry.

Persist baseline as a JSON object before the send action::

    {"origin": snapshot["origin"], "ids": [m["id"] for m in snapshot["messages"]],
     "checked_at": snapshot["checked_at"], "sent_at": send_started_at}

Only a valid snapshot may become a baseline. sent_at is the immutable send-phase
start time; if absent, record.created_at is the conservative fallback. Never use
updated_at, which changes during verification/recovery. Source timestamps allow
five seconds of clock skew before baseline.checked_at, and at most 120 seconds
after send start. Late ingestion is allowed within that occurrence-time window.

Callers must pass all possible sends in the scope, including unknown tasks for
late reconciliation and observed tasks for claims (not a paginated UI listing).
Persist observed through the store's atomic transition/unique message claim.
Missing/invalid baselines remain unknown and must not be automatically retried.
Screenshot confirmation and GUI authorization belong to the sending service.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from backend.app.shared.crm.identities import self_sender_id


def _timestamp(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _baseline(record: dict) -> dict | None:
    baseline = record.get("baseline")
    if not isinstance(baseline, dict):
        return None
    origin, ids = baseline.get("origin"), baseline.get("ids")
    if (not isinstance(origin, dict)
            or any(not isinstance(origin.get(key), str) or not origin[key]
                   for key in ("seller", "data_dir", "source_path"))
            or origin["seller"] != record.get("seller") or origin["data_dir"] != record.get("data_dir")
            or not isinstance(ids, list) or any(not isinstance(mid, str) or not mid for mid in ids)
            or not _timestamp(baseline.get("checked_at"))
            or not _timestamp(baseline.get("sent_at", record.get("created_at")))):
        return None
    return baseline


def _candidates(record: dict, baseline: dict, messages: list[dict]) -> list[dict]:
    old_ids = set(baseline["ids"])
    seller, contact = record.get("seller"), record.get("contact_ali_id")
    cids = (f"{seller}-{contact}", f"{contact}-{seller}")
    sent_at = baseline.get("sent_at", record.get("created_at"))
    return [message for message in messages if (
        isinstance(message, dict) and isinstance(message.get("id"), str) and message["id"]
        and message["id"] not in old_ids
        and message.get("sender_id") == self_sender_id(seller)
        and type(message.get("type")) is int and message["type"] == 0
        and message.get("text") == record.get("content")
        and message.get("is_system") is False and message.get("is_auto_reply") is False
        and message.get("extension_valid") is True
        and isinstance(message.get("cid"), str) and message["cid"].split("#", 1)[0] in cids
        and _timestamp(message.get("created_at"))
        and baseline["checked_at"] - 5 <= message["created_at"] <= sent_at + 120
    )]


def match_outbox(record: dict, snapshot: dict, other_records: Iterable[dict]) -> dict:
    """Return {status, reason, evidence, matched_message_id?} without side effects.

    Status is verifying, observed, or unknown. Stable origin, not process epoch,
    scopes local evidence after restart. An unknown task never becomes verifying.
    """
    result = {"status": "unknown", "reason": "baseline_invalid", "evidence": {}}
    baseline = _baseline(record)
    if baseline is None:
        return result
    if (record.get("action") != "send" or record.get("may_have_sent") is not True
            or record.get("status") not in ("running", "verifying", "unknown")
            or not isinstance(record.get("content"), str) or not record["content"]
            or not isinstance(record.get("contact_ali_id"), str) or not record["contact_ali_id"]):
        result["reason"] = "task_not_verifiable"
        return result
    sent_at = baseline.get("sent_at", record.get("created_at"))
    checked_at = snapshot.get("checked_at")
    waiting = "unknown" if record["status"] == "unknown" else "verifying"
    if _timestamp(checked_at) and checked_at > sent_at + 120:
        waiting = "unknown"
    if snapshot.get("valid") is not True:
        result.update(status=waiting, reason="snapshot_invalid")
        return result
    if (snapshot.get("origin") != baseline["origin"] or not _timestamp(checked_at)
            or checked_at < baseline["checked_at"] or not isinstance(snapshot.get("messages"), list)):
        result["reason"] = "snapshot_scope_or_time_mismatch"
        return result
    candidates = _candidates(record, baseline, snapshot["messages"])
    result["evidence"] = {
        "kind": "local_source_message", "origin": dict(snapshot["origin"]),
        "source_revision": snapshot.get("source_revision"), "checked_at": checked_at,
        "baseline_checked_at": baseline["checked_at"], "sent_at": sent_at,
        "candidate_ids": [message["id"] for message in candidates],
    }
    if not candidates:
        result.update(status=waiting, reason="message_not_observed")
        return result
    if len(candidates) != 1:
        result["reason"] = "multiple_candidate_messages"
        return result
    candidate = candidates[0]
    for other in other_records:
        if (other.get("id") == record.get("id")
                or other.get("seller") != record["seller"] or other.get("data_dir") != record["data_dir"]):
            continue
        if other.get("matched_message_id") == candidate["id"]:
            result["reason"] = "message_already_claimed"
            return result
        if (other.get("action") != "send" or other.get("may_have_sent") is not True
                or other.get("content") != record["content"]
                or other.get("contact_ali_id") != record["contact_ali_id"]):
            continue
        other_baseline = _baseline(other)
        # A possible send with missing evidence cannot be excluded as the cause.
        if other_baseline is None or (other_baseline["origin"] == baseline["origin"]
                                     and _candidates(other, other_baseline, [candidate])):
            result["reason"] = "ambiguous_send_attempts"
            return result
    result.update(status="observed", reason="local_message_observed", matched_message_id=candidate["id"])
    result["evidence"]["message"] = dict(candidate)
    return result
