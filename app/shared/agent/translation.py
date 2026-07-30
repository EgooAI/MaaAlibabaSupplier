from __future__ import annotations

import json

from loguru import logger

from app.shared.agent.chat_tools import (
    CHAT_TRANSLATION_AGENT_APID,
    build_translation_input,
    run_chat_tool_agent,
)
from app.shared.crm.sdk import load_sdk
from app.shared.crm.translations import text_hash, translation_cached


def _load_translations(raw_text: str) -> dict[str, str | None]:
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("translation agent output must be a JSON object")
    translations = payload.get("translations")
    if not isinstance(translations, dict):
        raise ValueError("translation agent output must contain a translations object")
    result: dict[str, str | None] = {}
    for key, value in translations.items():
        if value is None:
            result[str(key)] = None
        else:
            result[str(key)] = str(value).strip()
    return result


def translate_texts_to_crm(texts: list[str], *, force: bool = False) -> int:
    items = [
        {"text_hash": text_hash(text), "text": text}
        for text in dict.fromkeys(t.strip() for t in texts if t and t.strip())
        if force or not translation_cached(text)
    ]
    if not items:
        return 0

    sdk = load_sdk()
    translations = _load_translations(
        run_chat_tool_agent(CHAT_TRANSLATION_AGENT_APID, build_translation_input(items))
    )
    manager = sdk["TranslateManager"]()
    saved = 0
    for item in items:
        key = item["text_hash"]
        if key not in translations:
            logger.warning("Translation agent omitted text_hash={}", key)
            continue
        manager.upsert_translate(
            sdk["Translate"](text_hash=key, translation=translations[key] or "")
        )
        saved += 1
    return saved
