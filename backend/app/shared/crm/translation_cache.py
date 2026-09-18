from __future__ import annotations

"""Translate 缓存的底层访问，供 crm 与 agent 层共同使用。"""

import hashlib

from backend.app.shared.crm.sdk import TranslateManager


def md5_text_key(text: str) -> str:
    """32 位 MD5 主键；与 LLM 输入中的 ≤5 字符短哈希标记是两个概念。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def translation_cached(text: str) -> bool:
    if not text:
        return False
    return TranslateManager().get_translate(md5_text_key(text)) is not None


def get_translation(text: str) -> str | None:
    """None 表示未缓存；空串是 NO_NEED 哨兵（已缓存、无需翻译）。"""
    if not text:
        return None
    record = TranslateManager().get_translate(md5_text_key(text))
    if record is None:
        return None
    return record.translation


__all__ = ["get_translation", "md5_text_key", "translation_cached"]
