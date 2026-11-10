"""Cross-end constants shared with the exported frontend.

This module must stay dependency-free. ``tools/generate_frontend_contract.py``
turns these values into ``frontend/domain/contract.generated.ts``.
"""

TRANSLATION_TEXTS_MAX = 500
COMPOSE_TRANSLATION_MAX_CHARS = 4000

PORTRAIT_BATCH_MAX = 50
PORTRAIT_EXPORT_MAX = 100
PORTRAIT_TAG_NAME_MAX = 40
PORTRAIT_TAG_DESCRIPTION_MAX = 200
PORTRAIT_LOST_NOTE_MAX = 500

EXPERIMENT_MAX_VARIANTS = 4
EXPERIMENT_MAX_STEPS = 20
LLM_MAX_LEVEL = 4
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")

OUTBOX_PENDING_STATUSES = (
    "queued",
    "navigating",
    "awaiting_confirmation",
    "queued_send",
    "running",
    "verifying",
)
OUTBOX_STATUSES = OUTBOX_PENDING_STATUSES + (
    "observed",
    "filled",
    "failed",
    "unknown",
    "cancelled",
)

REPLY_NEEDS_REPLY = "needs_reply"
REPLY_WAITING_CUSTOMER = "waiting_customer"
REPLY_HISTORY_PENDING = "history_pending"
REPLY_NONE = "none"
REPLY_UNKNOWN = "unknown"
REPLY_STATES = (
    REPLY_NEEDS_REPLY,
    REPLY_WAITING_CUSTOMER,
    REPLY_HISTORY_PENDING,
    REPLY_NONE,
    REPLY_UNKNOWN,
)

PORTRAIT_STAGES = (
    "unknown",
    "new_inquiry",
    "following_up",
    "negotiating",
    "won",
    "dormant",
    "lost",
)
LOST_REASONS = (
    "unreachable",
    "price",
    "product_mismatch",
    "delivery",
    "competitor",
    "unclear",
)

STEP_MODES = ("buyer", "agent")
SESSION_TYPES = ("test", "playground", "fork")
PINNED_KINDS = ("preset", "draft")

__all__ = [
    "COMPOSE_TRANSLATION_MAX_CHARS",
    "EXPERIMENT_MAX_STEPS",
    "EXPERIMENT_MAX_VARIANTS",
    "LLM_MAX_LEVEL",
    "LOST_REASONS",
    "OUTBOX_PENDING_STATUSES",
    "OUTBOX_STATUSES",
    "PINNED_KINDS",
    "PORTRAIT_BATCH_MAX",
    "PORTRAIT_EXPORT_MAX",
    "PORTRAIT_LOST_NOTE_MAX",
    "PORTRAIT_STAGES",
    "PORTRAIT_TAG_DESCRIPTION_MAX",
    "PORTRAIT_TAG_NAME_MAX",
    "REASONING_EFFORTS",
    "REPLY_HISTORY_PENDING",
    "REPLY_NEEDS_REPLY",
    "REPLY_NONE",
    "REPLY_STATES",
    "REPLY_UNKNOWN",
    "REPLY_WAITING_CUSTOMER",
    "SESSION_TYPES",
    "STEP_MODES",
    "TRANSLATION_TEXTS_MAX",
]
