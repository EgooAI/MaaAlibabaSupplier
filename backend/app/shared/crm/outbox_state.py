"""Single source for outbox statuses, transitions, editable fields and risk rules."""

PENDING_STATUSES = (
    "queued", "navigating", "awaiting_confirmation", "queued_send", "running", "verifying",
)
STATUSES = PENDING_STATUSES + ("observed", "filled", "failed", "unknown", "cancelled")

NON_EXECUTING_STATUSES = frozenset({"queued", "awaiting_confirmation", "queued_send"})
# A GUI action may already have happened (search, input or send).
GUI_RISK_STATUSES = frozenset({"navigating", "running"})
EXECUTING_STATUSES = GUI_RISK_STATUSES | {"verifying"}

# The service may still compensate these statuses after a failure or shutdown.
COMPENSATABLE_STATUSES = NON_EXECUTING_STATUSES | GUI_RISK_STATUSES
# While an attempt is active, awaiting_confirmation is owned by the confirmation flow.
ACTIVE_COMPENSATABLE_STATUSES = (NON_EXECUTING_STATUSES - {"awaiting_confirmation"}) | GUI_RISK_STATUSES

TRANSITIONS = {
    "queued": {"navigating", "failed", "cancelled"},
    "navigating": {"awaiting_confirmation", "failed", "unknown"},
    "awaiting_confirmation": {"queued_send", "failed", "cancelled"},
    "queued_send": {"awaiting_confirmation", "running", "failed", "cancelled"},
    "running": {"verifying", "filled", "failed", "unknown"},
    "verifying": {"observed", "failed", "unknown"},
    "unknown": {"observed"},
}
EDITABLE_FIELDS = frozenset({
    "may_have_sent", "reason", "screenshot_id", "screenshot_at",
    "screenshot_digest", "baseline", "evidence",
})
PAYLOAD_FIELDS = ("conversation_id", "contact_ali_id", "login_id", "content", "action")

# Reasons the API may leak verbatim; everything else is replaced by a generic code.
REASON_CODES = frozenset({
    "cancelled_by_user", "screenshot_expired_or_changed", "shutdown_before_execution",
    "shutdown_execution_uncertain", "restart_execution_uncertain", "restart_before_execution",
    "restart_confirmation_lost", "baseline_invalid", "task_not_verifiable", "snapshot_invalid",
    "snapshot_scope_or_time_mismatch", "message_not_observed", "multiple_candidate_messages",
    "message_already_claimed", "ambiguous_send_attempts", "local_message_observed",
})
# Shared guard/service messages that the API may translate instead of hiding.
SAFE_REASONS = frozenset({
    "Select a seller before operating the client.",
    "GUI session expired; reconnect and retry.",
    "Account context changed; reload and retry.",
    "Client window changed; reconnect and retry.",
    "Client connection lost; reconnect and retry.",
    "Client is not connected; connect first.",
    "Outbox service is stopping",
    "Source baseline unavailable; no text was entered",
})


def is_gui_risk(status: str) -> bool:
    return status in GUI_RISK_STATUSES


def is_compensatable(status: str) -> bool:
    return status in COMPENSATABLE_STATUSES


def is_recovery_uncertain(status: str, may_have_sent: bool) -> bool:
    return may_have_sent or status in EXECUTING_STATUSES
