from __future__ import annotations

from loguru import logger

from app.shared.crm.sync import CRMAdapter
from app.shared.crm.views import CrmConversation
from app.shared.mitm.pool import SelfInfo, UserInfo, get_self_info_pool


def get_self_info() -> SelfInfo | None:
    try:
        info = CRMAdapter().get_self_info()
        if info is not None:
            return info
    except Exception:
        logger.exception("Failed to load SelfInfo from CRM SDK")
    return get_self_info_pool().get()


def get_user_info(identifier: str) -> UserInfo | None:
    if not identifier:
        return None
    try:
        return CRMAdapter().get_user_info(identifier)
    except Exception:
        logger.exception("Failed to load UserInfo from CRM SDK")
        return None


def list_conversations(self_ali_id: str) -> list[CrmConversation]:
    if not self_ali_id:
        return []
    try:
        return CRMAdapter().list_conversations(self_ali_id)
    except Exception:
        logger.exception("Failed to load conversations from CRM SDK")
        return []


__all__ = ["get_self_info", "get_user_info", "list_conversations"]
