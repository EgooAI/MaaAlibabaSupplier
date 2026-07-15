from __future__ import annotations

import hashlib

from app.shared.crm.sdk import load_sdk


def text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _manager():
    return load_sdk()["TranslateManager"]()


def translation_cached(text: str) -> bool:
    if not text:
        return False
    return _manager().get_translate(text_hash(text)) is not None


def get_translation(text: str) -> str | None:
    if not text:
        return None
    record = _manager().get_translate(text_hash(text))
    if record is None:
        return None
    return record.translation or None


def request_translations(texts: list[str], *, force: bool = False) -> int:
    from app.shared.agent.translation import translate_texts_to_crm

    return translate_texts_to_crm(texts, force=force)


__all__ = ["get_translation", "request_translations", "text_hash", "translation_cached"]
