from __future__ import annotations

import json
import re
from dataclasses import dataclass

from loguru import logger

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


def _build_agent_input(requests: list[TranslationRequest]) -> str:
    return json.dumps(
        {
            "task": "translate_buyer_messages_to_simplified_chinese",
            "instructions": [
                "Translate each item.text into Simplified Chinese.",
                "Return JSON only: {\"translations\": {\"<text_hash>\": \"<translation or null>\"}}.",
                "If an item is already Simplified Chinese, return null for that text_hash.",
                "Do not omit any text_hash.",
            ],
            "items": [
                {"text_hash": request.text_hash, "text": request.text}
                for request in requests
            ],
        },
        ensure_ascii=False,
    )


def translate_texts_to_crm(texts: list[str], *, force: bool = False) -> int:
    """Translate texts with crm_sdk level=0 agent and persist results into translate table.

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
    from agent_pipeline import AgentPipeline, AgentPipelineInput
    from agent_pipeline.llm import OpenAICompatibleLLMClient
    from agent_pipeline.llm_api import register_default_llms
    from core import llm_registry

    register_default_llms()
    llm_config = llm_registry.require(0)
    client = OpenAICompatibleLLMClient(llm_config)
    preset = sdk["AgentPreset"](
        apid="builtin-translation-agent",
        name="Built-in Translation Agent",
        description="Translate buyer messages and persist translations.",
        prompt=(
            "You are a translation agent for Alibaba supplier chat. "
            "Translate buyer messages into Simplified Chinese. "
            "You must output JSON only and follow the user's requested schema exactly."
        ),
        intelevel=0,
        tools=[],
    )
    pipeline = AgentPipeline(llm_client=client)
    result = pipeline.run(AgentPipelineInput(user_input=_build_agent_input(requests), agent_preset=preset))
    translations = _load_translation_payload(result.output_text)

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
