from __future__ import annotations

import hashlib

from loguru import logger

from backend.app.shared.agent.inputs import build_translation_input
from backend.app.shared.agent.runner import run_chat_tool_agent
from backend.app.shared.agent.system_agents import CHAT_TRANSLATION_AGENT_APID
from backend.app.shared.agent.output_normalizers import parse_translation_payload
from backend.app.shared.crm.sdk import Translate, TranslateManager
from backend.app.shared.crm.translation_cache import text_hash, translation_cached

# Agent 输出协议常量：区分“无需翻译”与“非常规消息”两种无译文情形。
NO_NEED_TO_TRANSLATE = "NO_NEED_TO_TRANSLATE"
ABNORMAL_MESSAGE = "ABNORMAL_MESSAGE"

# Agent I/O 使用 ≤5 字符的短哈希；数据库主键仍为 32 位 MD5，避免短哈希碰撞串库。
SHORT_HASH_LENGTH = 5
_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def short_text_hash(text: str, *, salt: int = 0) -> str:
    """Deterministic ≤5-char base62 hash derived from the text's MD5."""
    digest = hashlib.md5(f"{text}#{salt}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    chars: list[str] = []
    for _ in range(SHORT_HASH_LENGTH):
        value, remainder = divmod(value, 62)
        chars.append(_BASE62[remainder])
    return "".join(reversed(chars))


def assign_short_hashes(texts: list[str]) -> dict[str, str]:
    """Deterministic text -> short-hash map; in-set collisions get salted re-derivation."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for text in dict.fromkeys(texts):
        salt = 0
        candidate = short_text_hash(text)
        while candidate in used:
            salt += 1
            candidate = short_text_hash(text, salt=salt)
        mapping[text] = candidate
        used.add(candidate)
    return mapping


def translate_texts_to_crm(
    texts: list[str],
    *,
    force: bool = False,
    conversation: list[tuple[str, str, str]] | None = None,
    annotate: dict[str, str] | None = None,
) -> int:
    """Translate chat texts via the translation agent and upsert CRM Translate rows.

    *conversation* is the full ``(timestamp, speaker, text)`` transcript used as LLM
    context: every historical line is included, translated or not. *annotate* maps the
    job-level target texts to short hashes — transcript lines carrying ``text_hash=``
    are pending translation, lines without the marker are context only. When omitted it
    is derived from *texts* (e.g. the single-message endpoints).

    Already-cached texts are skipped unless *force* (retranslation is the exception).
    Agent protocol values: translation text / NO_NEED_TO_TRANSLATE (already Chinese,
    cached as an empty sentinel) / ABNORMAL_MESSAGE (non-textual noise, never cached).
    """
    cleaned = list(dict.fromkeys(text.strip() for text in texts if text and text.strip()))
    targets = [text for text in cleaned if force or not translation_cached(text)]
    if not targets:
        return 0

    if annotate is None:
        mapping = assign_short_hashes(targets)
    else:
        # Top up missing keys (e.g. texts the caller did not pre-assign) collision-safe.
        mapping = dict(annotate)
        used = set(mapping.values())
        for text in targets:
            if text in mapping:
                continue
            mapping[text] = _fresh_short_hash(text, used)
    items = [{"text_hash": mapping[text], "text": text} for text in targets]

    user_input = build_translation_input(items, conversation=conversation, annotate=mapping)
    if not user_input:
        return 0

    translations = parse_translation_payload(
        run_chat_tool_agent(CHAT_TRANSLATION_AGENT_APID, user_input)
    )
    manager = TranslateManager()
    saved = 0
    for item in items:
        short = item["text_hash"]
        if short not in translations:
            logger.warning("Translation agent omitted text_hash={}", short)
            continue
        raw = translations[short]
        value = NO_NEED_TO_TRANSLATE if raw is None else raw.strip()
        normalized = value.upper()
        if normalized == ABNORMAL_MESSAGE:
            # 非常规消息不写缓存，保持未翻译态，下次仍由 Agent 判定。
            continue
        no_need = normalized == NO_NEED_TO_TRANSLATE
        manager.upsert_translate(
            Translate(text_hash=text_hash(item["text"]), translation="" if no_need else value)
        )
        saved += 1
    return saved


def _fresh_short_hash(text: str, used: set[str]) -> str:
    salt = 0
    candidate = short_text_hash(text)
    while candidate in used:
        salt += 1
        candidate = short_text_hash(text, salt=salt)
    used.add(candidate)
    return candidate


__all__ = [
    "ABNORMAL_MESSAGE",
    "NO_NEED_TO_TRANSLATE",
    "SHORT_HASH_LENGTH",
    "assign_short_hashes",
    "short_text_hash",
    "translate_texts_to_crm",
]
