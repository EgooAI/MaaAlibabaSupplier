from __future__ import annotations

from loguru import logger

from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.crm.views import CrmConversation
from backend.app.shared.mitm.pool import SelfInfo, UserInfo


def get_self_info(self_ali_id: str | None = None) -> SelfInfo | None:
    try:
        return CRMAdapter().get_self_info(self_ali_id)
    except Exception:
        logger.exception("Failed to load SelfInfo from CRM SDK")
        return None


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
