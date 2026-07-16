from __future__ import annotations

import json
import re
from dataclasses import dataclass

from loguru import logger

from app.shared.agent.chat_tools import (
    CHAT_TRANSLATION_AGENT_APID,
    build_translation_input,
    run_chat_tool_agent,
)
from app.shared.crm.sdk import load_sdk
from app.shared.crm.translations import text_hash, translation_cached


@dataclass(frozen=True)
class TranslationRequest:
    text_hash: str
    text: str


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def _load_translation_payload(raw_text: str) -> dict[str, str | None]:
    payload = json.loads(_strip_json_fence(raw_text))
    if not isinstance(payload, dict):
        raise ValueError("translation agent output must be a JSON object")
    translations = payload.get("translations", payload)
    if not isinstance(translations, dict):
        raise ValueError("translation agent output must contain a translations object")
    result: dict[str, str | None] = {}
    for key, value in translations.items():
        if value is None:
            result[str(key)] = None
        elif isinstance(value, str):
            result[str(key)] = value.strip()
        else:
            result[str(key)] = str(value).strip()
    return result


def translate_texts_to_crm(texts: list[str], *, force: bool = False) -> int:
    """Translate texts with the database chat translation agent and persist results.

    Empty translation strings are stored for messages that are already Chinese.
    The business layer should read translation results from the translate table.
    """
    unique_texts = list(dict.fromkeys(text.strip() for text in texts if text and text.strip()))
    requests = [
        TranslationRequest(text_hash=text_hash(text), text=text)
        for text in unique_texts
        if force or not translation_cached(text)
    ]
    if not requests:
        return 0

    sdk = load_sdk()
    raw_text = run_chat_tool_agent(
        CHAT_TRANSLATION_AGENT_APID,
        build_translation_input(
            [
                {"text_hash": request.text_hash, "text": request.text}
                for request in requests
            ]
        ),
    )
    translations = _load_translation_payload(raw_text)

    manager = sdk["TranslateManager"]()
    saved = 0
    for request in requests:
        if request.text_hash not in translations:
            logger.warning("Translation agent omitted text_hash={}", request.text_hash)
            continue
        translated = translations[request.text_hash]
        manager.upsert_translate(
            sdk["Translate"](
                text_hash=request.text_hash,
                translation=translated or "",
            )
        )
        saved += 1
    return saved
