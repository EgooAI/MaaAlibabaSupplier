from __future__ import annotations

from loguru import logger

from backend.app.shared.agent.inputs import build_translation_input
from backend.app.shared.agent.runner import run_chat_tool_agent
from backend.app.shared.agent.system_agents import CHAT_TRANSLATION_AGENT_APID
from backend.app.shared.agent.output_normalizers import parse_translation_payload
from backend.app.shared.crm.sdk import Translate, TranslateManager
from backend.app.shared.crm.translation_cache import text_hash, translation_cached


def translate_texts_to_crm(
    texts: list[str],
    *,
    force: bool = False,
    conversation: list[tuple[str, str, str]] | None = None,
) -> int:
    """Translate buyer texts via the translation agent and upsert CRM Translate rows.

    *conversation* is optional ``(timestamp, speaker, text)`` context (same shape as
    reply-suggestion input). When provided, seller/system lines help disambiguate,
    matching main-branch behavior while still keying results by text_hash.
    """
    items = [
        {"text_hash": text_hash(text), "text": text}
        for text in dict.fromkeys(t.strip() for t in texts if t and t.strip())
        if force or not translation_cached(text)
    ]
    if not items:
        return 0

    user_input = build_translation_input(items, conversation=conversation)
    if not user_input:
        return 0

    translations = parse_translation_payload(
        run_chat_tool_agent(CHAT_TRANSLATION_AGENT_APID, user_input)
    )
    manager = TranslateManager()
    saved = 0
    for item in items:
        key = item["text_hash"]
        if key not in translations:
            logger.warning("Translation agent omitted text_hash={}", key)
            continue
        manager.upsert_translate(
            Translate(text_hash=key, translation=translations[key] or "")
        )
        saved += 1
    return saved
