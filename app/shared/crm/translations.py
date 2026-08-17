from __future__ import annotations

from app.shared.crm.translation_cache import get_translation, text_hash, translation_cached  # noqa: F401


def request_translations(
    texts: list[str],
    *,
    force: bool = False,
    conversation: list[tuple[str, str, str]] | None = None,
) -> int:
    from app.shared.agent.translation import translate_texts_to_crm

    return translate_texts_to_crm(texts, force=force, conversation=conversation)


__all__ = ["get_translation", "request_translations", "text_hash", "translation_cached"]
