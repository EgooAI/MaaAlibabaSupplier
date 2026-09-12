from __future__ import annotations

"""Translate 缓存的底层访问，供 crm 与 agent 层共同使用。"""

import hashlib

from app.shared.crm.sdk import TranslateManager


def text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _manager():
    return TranslateManager()


def translation_cached(text: str, manager=None) -> bool:
    if not text:
        return False
    if manager is None:
        manager = _manager()
    return manager.get_translate(text_hash(text)) is not None


def get_translation(text: str, manager=None) -> str | None:
    if not text:
        return None
    if manager is None:
        manager = _manager()
    record = manager.get_translate(text_hash(text))
    if record is None:
        return None
    return record.translation or None


__all__ = ["get_translation", "text_hash", "translation_cached"]