from __future__ import annotations

from backend.app.shared.crm.ports import run_translation_service
from backend.app.shared.crm.translation_cache import get_translation, text_hash, translation_cached  # noqa: F401


def request_translations(
    texts: list[str],
    *,
    force: bool = False,
    conversation: list[tuple[str, str, str]] | None = None,
    annotate: dict[str, str] | None = None,
) -> int:
    return run_translation_service(texts, force=force, conversation=conversation, annotate=annotate)


__all__ = ["get_translation", "request_translations", "text_hash", "translation_cached"]
