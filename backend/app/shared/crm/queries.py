from __future__ import annotations

from loguru import logger

from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.crm.views import CrmConversation
from backend.app.shared.mitm.pool import SelfInfo, UserInfo, get_self_info_pool


def get_self_info() -> SelfInfo | None:
    try:
        info = CRMAdapter().get_self_info()
        if info is not None:
            return info
    except Exception:
        logger.exception("Failed to load SelfInfo from CRM SDK, falling back to pool")
    return get_self_info_pool().get()


def get_user_info(identifier: str) -> UserInfo | None:
    if not identifier:
        return None
    return CRMAdapter().get_user_info(identifier)


def list_conversations(self_ali_id: str) -> list[CrmConversation]:
    if not self_ali_id:
        return []
    return CRMAdapter().list_conversations(self_ali_id)


def get_conversation_detail(self_ali_id: str, sid: int) -> CrmConversation | None:
    if not self_ali_id or not sid:
        return None
    return CRMAdapter().get_conversation_detail(self_ali_id, sid)


__all__ = ["get_conversation_detail", "get_self_info", "get_user_info", "list_conversations"]
