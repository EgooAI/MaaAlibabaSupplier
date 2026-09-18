"""Business adapter layer for syncing app data into the CRM SDK."""

from backend.app.shared.crm.ingest import (
    REASON_DATA_DIR_NOT_CONFIGURED,
    REASON_IM_DB_NOT_READY,
    REASON_SELF_IDENTITY_NOT_READY,
    REASON_SELF_IDENTITY_NOT_SELECTED,
    ChatSyncState,
    refresh_chat_data,
)
from backend.app.shared.crm.queries import get_conversation_detail, get_self_info, get_user_info, list_conversations
from backend.app.shared.crm.sync import sync_im_database, sync_self_info, sync_user_info
from backend.app.shared.crm.translation_cache import get_translation, translation_cached
from backend.app.shared.crm.views import CrmConversation, CrmMessage, CrmResolver

__all__ = [
    "ChatSyncState",
    "CrmConversation",
    "CrmMessage",
    "CrmResolver",
    "REASON_DATA_DIR_NOT_CONFIGURED",
    "REASON_IM_DB_NOT_READY",
    "REASON_SELF_IDENTITY_NOT_READY",
    "REASON_SELF_IDENTITY_NOT_SELECTED",
    "get_conversation_detail",
    "get_self_info",
    "get_translation",
    "get_user_info",
    "list_conversations",
    "refresh_chat_data",
    "sync_im_database",
    "sync_self_info",
    "sync_user_info",
    "translation_cached",
]
