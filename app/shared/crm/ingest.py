from __future__ import annotations

from dataclasses import dataclass

from app.shared.crm.queries import get_self_info


@dataclass(frozen=True)
class ChatSyncState:
    ready: bool
    self_ali_id: str
    reason: str = ""


def refresh_chat_data(*, wait: bool = False) -> ChatSyncState:
    """Refresh source IM data into CRM without exposing source details to UI."""
    from app.shared.backend.im_db_middleware import get_im_db_middleware

    mw = get_im_db_middleware()
    if mw.get_connection() is None:
        return ChatSyncState(ready=False, self_ali_id="", reason="im_database_not_ready")

    mw.sync_to_crm(wait=wait)
    self_info = get_self_info()
    self_ali_id = self_info.ali_id if self_info and self_info.ali_id else ""
    if not self_ali_id:
        return ChatSyncState(ready=False, self_ali_id="", reason="self_identity_not_ready")
    return ChatSyncState(ready=True, self_ali_id=self_ali_id)


__all__ = ["ChatSyncState", "refresh_chat_data"]
