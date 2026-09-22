"""Pure matching regressions: observation is not delivery confirmation."""

from copy import deepcopy
import json

import pytest

from backend.app.shared.backend.send_verification import match_outbox


@pytest.fixture
def case():
    origin = {"seller": "10001", "data_dir": "normalized-directory", "source_path": "normalized-source"}
    record = {
        "id": "task-one", "seller": "10001", "data_dir": origin["data_dir"],
        "contact_ali_id": "20002", "content": "hello", "action": "send",
        "status": "verifying", "may_have_sent": True, "created_at": 900.0, "updated_at": 5000.0,
        "baseline": {"origin": origin, "ids": ["msg_a:old"], "checked_at": 1000.0, "sent_at": 1002.0},
    }
    message = {"id": "msg_a:new", "sender_id": "10001@icbu", "cid": "20002-10001#suffix",
               "created_at": 1003.0, "type": 0, "text": "hello", "is_system": False,
               "is_auto_reply": False, "extension_valid": True}
    snapshot = {"valid": True, "reason": "fresh", "origin": deepcopy(origin),
                "source_revision": 2, "checked_at": 1005.0, "messages": [message]}
    return record, snapshot


def test_unique_new_message_is_observed_without_mutation(case):
    record, snapshot = case
    before = deepcopy(case)
    result = match_outbox(record, snapshot, [])
    assert result["status"] == "observed" and result["matched_message_id"] == "msg_a:new"
    assert result["evidence"]["kind"] == "local_source_message"
    assert result["evidence"]["message"] == snapshot["messages"][0]
    assert case == before
    assert json.loads(json.dumps(result)) == result


def test_instance_profile_sender_and_cid_still_observe_the_send(case):
    record, snapshot = case
    message = snapshot["messages"][0]
    message["sender_id"] = "10001@icbu_1"
    message["cid"] = "10001@icbu_1-20002#suffix"
    result = match_outbox(record, snapshot, [])
    assert result["status"] == "observed" and result["matched_message_id"] == "msg_a:new"


def test_repeated_text_already_in_baseline_is_not_new(case):
    record, snapshot = case
    snapshot["messages"][0]["id"] = "msg_a:old"
    assert match_outbox(record, snapshot, [])["status"] == "verifying"


@pytest.mark.parametrize("field,value", [
    ("sender_id", "20002@icbu"), ("sender_id", "10001"), ("type", 1), ("type", False),
    ("text", "hello "), ("text", "Hello"), ("is_system", True), ("is_auto_reply", True),
    ("extension_valid", False), ("cid", "10001-200020"), ("cid", "10001-20002-30003"),
    ("created_at", 994.99), ("created_at", 1122.01), ("created_at", float("nan")),
])
def test_nonmatching_or_unsafe_messages_cannot_observe(case, field, value):
    record, snapshot = case
    snapshot["messages"][0][field] = value
    result = match_outbox(record, snapshot, [])
    assert result["status"] == "verifying" and "matched_message_id" not in result


@pytest.mark.parametrize("occurred_at", [995.0, 1122.0])
def test_occurrence_window_inclusive_and_late_ingestion_allowed(case, occurred_at):
    record, snapshot = case
    snapshot["messages"][0]["created_at"] = occurred_at
    snapshot["checked_at"] = 9000.0
    assert match_outbox(record, snapshot, [])["status"] == "observed"


def test_send_time_falls_back_to_created_at_not_updated_at(case):
    record, snapshot = case
    del record["baseline"]["sent_at"]
    record["created_at"] = 1001.0
    snapshot["messages"][0]["created_at"] = 2000.0
    snapshot["checked_at"] = 6000.0
    assert match_outbox(record, snapshot, [])["status"] == "unknown"


def test_multiple_new_equal_text_messages_are_ambiguous(case):
    record, snapshot = case
    snapshot["messages"].append({**snapshot["messages"][0], "id": "msg_b:second"})
    assert match_outbox(record, snapshot, [])["reason"] == "multiple_candidate_messages"


@pytest.mark.parametrize("other_status", ["running", "verifying", "unknown"])
def test_two_possible_attempts_both_unknown_independent_of_order(case, other_status):
    record, snapshot = case
    other = {**deepcopy(record), "id": "task-two", "status": other_status}
    for task, competitors in ((record, [record, other]), (other, [other, record])):
        result = match_outbox(task, snapshot, competitors)
        assert result["status"] == "unknown" and result["reason"] == "ambiguous_send_attempts"
        assert "matched_message_id" not in result


def test_other_attempt_can_be_excluded_by_baseline_or_time(case):
    record, snapshot = case
    other = {**deepcopy(record), "id": "task-two", "status": "unknown"}
    other["baseline"]["ids"].append("msg_a:new")
    assert match_outbox(record, snapshot, [other])["status"] == "observed"
    other["baseline"]["ids"] = []
    other["baseline"]["checked_at"] = 2000.0
    other["baseline"]["sent_at"] = 2001.0
    assert match_outbox(record, snapshot, [other])["status"] == "observed"


def test_possible_competitor_missing_baseline_is_ambiguous(case):
    record, snapshot = case
    other = {**record, "id": "task-two", "baseline": None, "status": "unknown"}
    assert match_outbox(record, snapshot, [other])["reason"] == "ambiguous_send_attempts"


def test_claim_is_checked_even_for_other_content(case):
    record, snapshot = case
    other = {**record, "id": "task-two", "content": "different", "status": "observed",
             "matched_message_id": "msg_a:new"}
    assert match_outbox(record, snapshot, [other])["reason"] == "message_already_claimed"


@pytest.mark.parametrize("field", ["seller", "data_dir", "source_path"])
def test_origin_mismatch_rejected(case, field):
    record, snapshot = case
    snapshot["origin"][field] = "different"
    assert match_outbox(record, snapshot, [])["status"] == "unknown"


def test_unknown_after_restart_can_observe_same_scope(case):
    record, snapshot = case
    record.update(status="unknown", reason="restart_execution_uncertain", updated_at=8000.0)
    snapshot["checked_at"] = 9000.0
    snapshot["source_revision"] = 1
    assert match_outbox(record, snapshot, [])["status"] == "observed"


@pytest.mark.parametrize("baseline", [None, "{broken", "{}", [], {}, {"ids": []}])
def test_missing_or_malformed_baseline_stays_unknown(case, baseline):
    record, snapshot = case
    record["baseline"] = baseline
    result = match_outbox(record, snapshot, [])
    assert result["status"] == "unknown" and result["reason"] == "baseline_invalid"


@pytest.mark.parametrize("status,valid,checked_at,expected", [
    ("verifying", True, 1005.0, "verifying"), ("verifying", False, 1005.0, "verifying"),
    ("verifying", True, 2000.0, "unknown"), ("verifying", False, 2000.0, "unknown"),
    ("unknown", True, 1005.0, "unknown"), ("unknown", False, 1005.0, "unknown"),
])
def test_waiting_and_unknown_are_preserved_for_later_observation(case, status, valid, checked_at, expected):
    record, snapshot = case
    record["status"] = status
    snapshot.update(valid=valid, checked_at=checked_at, messages=[])
    assert match_outbox(record, snapshot, [])["status"] == expected


@pytest.mark.parametrize("changes", [{"action": "test"}, {"may_have_sent": False}, {"status": "queued_send"}])
def test_non_send_or_unstarted_task_never_observed(case, changes):
    record, snapshot = case
    record.update(changes)
    assert match_outbox(record, snapshot, [])["reason"] == "task_not_verifiable"
