"""Agent-layer services used by the business layer."""

from backend.app.shared.agent.suggestions import generate_reply_suggestions
from backend.app.shared.agent.translation import translate_texts_to_crm

__all__ = ["generate_reply_suggestions", "translate_texts_to_crm"]
