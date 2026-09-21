from __future__ import annotations

"""Translate 缓存的底层访问，供 crm 与 agent 层共同使用。"""

import hashlib
from typing import Iterable

from backend.app.shared.crm.sdk import TranslateManager


def md5_text_key(text: str) -> str:
    """32 位 MD5 主键；与 LLM 输入中的 ≤5 字符短哈希标记是两个概念。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def get_translation(text: str) -> str | None:
    """None 表示未缓存；空串是 NO_NEED 哨兵（已缓存、无需翻译）。"""
    if not text:
        return None
    record = TranslateManager().get_translate(md5_text_key(text))
    if record is None:
        return None
    return record.translation


def get_translations(texts: Iterable[str]) -> dict[str, str | None]:
    """Batch lookup over one manager; same three-value contract as get_translation."""
    manager = TranslateManager()
    results: dict[str, str | None] = {}
    for text in texts:
        if text in results:
            continue
        if not text:
            results[text] = None
            continue
        record = manager.get_translate(md5_text_key(text))
        results[text] = record.translation if record is not None else None
    return results


def translation_cached(text: str) -> bool:
    return get_translation(text) is not None


__all__ = ["get_translation", "get_translations", "md5_text_key", "translation_cached"]
