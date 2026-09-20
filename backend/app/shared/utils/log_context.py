"""Explicit, scalar-only correlation snapshots for async and worker boundaries."""

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from loguru import logger

from backend.app.shared.utils.logging import safe_fields

_RUN_ID = uuid4().hex
_context: ContextVar[dict] = ContextVar("log_context", default={})


def capture_log_context() -> dict:
    """Return an independent snapshot; never read the current account implicitly."""
    # An absent origin must also mask a request/account active at replay time.
    return {"run_id": _RUN_ID, "request_id": None, "account_epoch": None, **_context.get()}


@contextmanager
def bind_log_context(**fields):
    """Overlay safe scalar fields for this scope, restoring them even on failure."""
    token = _context.set({**_context.get(), **safe_fields(fields)})
    try:
        yield
    finally:
        _context.reset(token)


def log_event(event: str, **fields) -> None:
    """Best-effort structured event. Telemetry must never change execution results.

    Only the scalar field allowlist is emitted. Pass exception_type, never an
    exception string, payload, credential, identity, screenshot or filesystem path.
    """
    try:
        from backend.app.shared.utils.logging import _LABEL

        if not isinstance(event, str) or not _LABEL.fullmatch(event):
            return
        context = safe_fields({**capture_log_context(), **fields})
        logger.bind(event=event, context=context).opt(depth=1).info(event)
    except Exception:
        pass
