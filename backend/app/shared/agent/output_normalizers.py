"""Business output normalizers for the four system chat agents.

系统 Agent 的输出归一化属于 AlibabaSupplier 业务逻辑，注册到 SDK 的
通用输出归一化注册表（``agent_pipeline.registry.output_normalizer_registry``）中，
SDK 本身不包含任何业务内容。
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.app.shared.agent.system_agents import (
    CHAT_CUSTOMER_INTENT_AGENT_APID,
    CHAT_CUSTOMER_STAGE_AGENT_APID,
    CHAT_TRANSLATION_AGENT_APID,
)


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def _load_json_object(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(_strip_json_fence(raw_text))
    except json.JSONDecodeError as exc:
        raise ValueError("system agent output must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("system agent output must be a JSON object")
    return payload


def _normalize_text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def parse_translation_payload(raw_text: str) -> dict[str, str | None]:
    payload = _load_json_object(raw_text)
    translations = payload.get("translations")
    if not isinstance(translations, dict):
        raise ValueError("translations must be an object under the translations key")

    normalized: dict[str, str | None] = {}
    for key, value in translations.items():
        if value is None:
            normalized[str(key)] = None
        else:
            normalized[str(key)] = str(value).strip()
    return normalized


def _normalize_translation(raw_text: str) -> str:
    return json.dumps({"translations": parse_translation_payload(raw_text)}, ensure_ascii=False)


def _normalize_intent(raw_text: str) -> str:
    payload = _load_json_object(raw_text)
    intent = payload.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("intent must be a non-empty string")
    return json.dumps(
        {
            "intent": intent.strip(),
            "evidence": _normalize_text_list(payload.get("evidence"), "evidence"),
            "concerns": _normalize_text_list(payload.get("concerns"), "concerns"),
            "next_actions": _normalize_text_list(payload.get("next_actions"), "next_actions"),
        },
        ensure_ascii=False,
    )


def _normalize_stage(raw_text: str) -> str:
    payload = _load_json_object(raw_text)
    stage = payload.get("stage")
    if not isinstance(stage, str) or not stage.strip():
        raise ValueError("stage must be a non-empty string")
    confidence = payload.get("confidence", "")
    if confidence is None:
        confidence = ""
    if not isinstance(confidence, str):
        raise ValueError("confidence must be a string")
    return json.dumps(
        {
            "stage": stage.strip(),
            "evidence": _normalize_text_list(payload.get("evidence"), "evidence"),
            "next_actions": _normalize_text_list(payload.get("next_actions"), "next_actions"),
            "confidence": confidence.strip(),
        },
        ensure_ascii=False,
    )


def register_system_output_normalizers() -> None:
    """Register output normalizers for the agents that still need text parsing.

    The reply-suggestion agent is now tool-driven: its replies come from
    ``reply_customer`` intents, so it has no normalizer. Output normalizers are
    the legacy mechanism for agents that must return structured text; new agents
    should use effect tools instead.
    """
    from backend.app.shared.crm.sdk import ensure_sdk_path
    from agent_pipeline.registry import register_output_normalizer

    ensure_sdk_path()
    register_output_normalizer(CHAT_TRANSLATION_AGENT_APID, _normalize_translation)
    register_output_normalizer(CHAT_CUSTOMER_INTENT_AGENT_APID, _normalize_intent)
    register_output_normalizer(CHAT_CUSTOMER_STAGE_AGENT_APID, _normalize_stage)


__all__ = ["register_system_output_normalizers", "parse_translation_payload"]