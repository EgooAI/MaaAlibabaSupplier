from __future__ import annotations

from dataclasses import dataclass

from backend.app.shared.crm.queries import get_self_info
from backend.app.shared.utils.app_config import get_configured_self_ali_id

REASON_IM_DB_NOT_READY = "im_database_not_ready"
REASON_SELF_IDENTITY_NOT_READY = "self_identity_not_ready"
REASON_SELF_IDENTITY_NOT_SELECTED = "self_identity_not_selected"
REASON_DATA_DIR_NOT_CONFIGURED = "alibaba_data_dir_not_configured"


@dataclass(frozen=True)
class ChatSyncState:
    ready: bool
    self_ali_id: str
    reason: str = ""


def refresh_chat_data(*, wait: bool = False) -> ChatSyncState:
    """Refresh source IM data into CRM without exposing source details to UI."""
    from backend.app.shared.backend.im_db_middleware import get_im_db_middleware

    mw = get_im_db_middleware()
    if mw.data_dir_status()["state"] == "unconfigured":
        return ChatSyncState(ready=False, self_ali_id="", reason=REASON_DATA_DIR_NOT_CONFIGURED)
    if not get_configured_self_ali_id():
        return ChatSyncState(ready=False, self_ali_id="", reason=REASON_SELF_IDENTITY_NOT_SELECTED)
    if mw.get_connection() is None:
        return ChatSyncState(ready=False, self_ali_id="", reason=REASON_IM_DB_NOT_READY)

    mw.sync_to_crm(wait=wait)
    self_info = get_self_info()
    self_ali_id = self_info.ali_id if self_info and self_info.ali_id else ""
    if not self_ali_id:
        return ChatSyncState(ready=False, self_ali_id="", reason=REASON_SELF_IDENTITY_NOT_READY)
    return ChatSyncState(ready=True, self_ali_id=self_ali_id)


__all__ = [
    "ChatSyncState",
    "REASON_DATA_DIR_NOT_CONFIGURED",
    "REASON_IM_DB_NOT_READY",
    "REASON_SELF_IDENTITY_NOT_READY",
    "REASON_SELF_IDENTITY_NOT_SELECTED",
    "refresh_chat_data",
]
