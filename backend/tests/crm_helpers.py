"""Production-path conversation reads for test assertions (no ORM list helpers)."""

from backend.app.shared.crm.identities import session_key_prefix
from backend.app.shared.crm.views import CrmConversation, coerce_epoch


def conversations_for(adapter, self_ali_id: str) -> list[CrmConversation]:
    prefix = session_key_prefix(self_ali_id)
    conversations = []
    for meta in adapter.sessions.list_session_meta():
        if not meta.sid or not str(meta.key or "").startswith(prefix):
            continue
        conversation = adapter.get_conversation_detail(self_ali_id, meta.sid)
        if conversation is not None:
            conversations.append(conversation)
    conversations.sort(key=lambda conversation: coerce_epoch(conversation.last_created_at), reverse=True)
    return conversations
