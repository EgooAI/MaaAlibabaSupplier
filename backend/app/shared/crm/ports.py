from __future__ import annotations

from typing import Callable, Protocol


class TranslationService(Protocol):
    def __call__(
        self,
        texts: list[str],
        *,
        force: bool = False,
        conversation: list[tuple[str, str, str]] | None = None,
        annotate: dict[str, str] | None = None,
    ) -> int: ...


_impl: Callable[..., int] | None = None


def register_translation_service(impl: Callable[..., int]) -> None:
    global _impl
    _impl = impl


def run_translation_service(
    texts: list[str],
    *,
    force: bool = False,
    conversation: list[tuple[str, str, str]] | None = None,
    annotate: dict[str, str] | None = None,
) -> int:
    if _impl is None:
        from backend.app.shared.agent.translation import translate_texts_to_crm

        register_translation_service(translate_texts_to_crm)
    assert _impl is not None
    return _impl(texts, force=force, conversation=conversation, annotate=annotate)
