"""Business adapter layer for syncing app data into the CRM SDK."""

from backend.app.shared.crm.ingest import (
    REASON_IM_DB_NOT_READY,
    REASON_SELF_IDENTITY_NOT_READY,
    ChatSyncState,
    refresh_chat_data,
)
from backend.app.shared.crm.queries import get_self_info, get_user_info, list_conversations
from backend.app.shared.crm.sync import sync_im_database, sync_self_info, sync_user_info
from backend.app.shared.crm.translations import get_translation, request_translations, text_hash, translation_cached
from backend.app.shared.crm.views import CrmConversation, CrmMessage, CrmResolver

__all__ = [
    "ChatSyncState",
    "CrmConversation",
    "CrmMessage",
    "CrmResolver",
    "REASON_IM_DB_NOT_READY",
    "REASON_SELF_IDENTITY_NOT_READY",
    "get_self_info",
    "get_user_info",
    "list_conversations",
    "refresh_chat_data",
    "get_translation",
    "request_translations",
    "sync_im_database",
    "sync_self_info",
    "sync_user_info",
    "text_hash",
    "translation_cached",
]
