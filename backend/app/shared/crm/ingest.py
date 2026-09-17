from __future__ import annotations

from dataclasses import dataclass

from backend.app.shared.backend.account_context import account_lock, get_account_context
from backend.app.shared.crm.queries import get_self_info

REASON_IM_DB_NOT_READY = "im_database_not_ready"
REASON_SELF_IDENTITY_NOT_READY = "self_identity_not_ready"
REASON_SELF_IDENTITY_NOT_SELECTED = "self_identity_not_selected"
REASON_DATA_DIR_NOT_CONFIGURED = "alibaba_data_dir_not_configured"
REASON_ACCOUNT_CHANGED = "account_changed"
REASON_SYNC_ERROR = "sync_error"


@dataclass(frozen=True)
class ChatSyncState:
    ready: bool
    self_ali_id: str
    reason: str = ""


def refresh_chat_data(*, wait: bool = False) -> ChatSyncState:
    """Refresh source IM data into CRM without exposing source details to UI."""
    from backend.app.shared.backend.im_db_middleware import get_im_db_middleware

    with account_lock:
        context = get_account_context()
        if not context.self_ali_id:
            return ChatSyncState(ready=False, self_ali_id="", reason=REASON_SELF_IDENTITY_NOT_SELECTED)
        mw = get_im_db_middleware()
        if mw.data_dir_status()["state"] == "unconfigured":
            return ChatSyncState(ready=False, self_ali_id="", reason=REASON_DATA_DIR_NOT_CONFIGURED)
        if mw.get_connection() is None:
            return ChatSyncState(ready=False, self_ali_id="", reason=REASON_IM_DB_NOT_READY)
        mw.sync_to_crm(wait=False)
        future = mw._sync_future
        if future is None:
            return ChatSyncState(ready=False, self_ali_id=context.self_ali_id, reason=REASON_SYNC_ERROR)

    # Release our account guard while waiting so other callers can switch.
    if wait or future.done():
        future.result()

    with account_lock:
        if get_account_context() != context:
            return ChatSyncState(ready=False, self_ali_id="", reason=REASON_ACCOUNT_CHANGED)
        self_info = get_self_info(context.self_ali_id)
        if self_info is None or self_info.ali_id != context.self_ali_id:
            return ChatSyncState(ready=False, self_ali_id="", reason=REASON_SELF_IDENTITY_NOT_READY)
        return ChatSyncState(ready=True, self_ali_id=context.self_ali_id)


__all__ = [
    "ChatSyncState",
    "REASON_ACCOUNT_CHANGED",
    "REASON_DATA_DIR_NOT_CONFIGURED",
    "REASON_IM_DB_NOT_READY",
    "REASON_SELF_IDENTITY_NOT_READY",
    "REASON_SELF_IDENTITY_NOT_SELECTED",
    "REASON_SYNC_ERROR",
    "refresh_chat_data",
]
