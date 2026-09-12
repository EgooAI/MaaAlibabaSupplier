from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.api.envelope import ok
from backend.app.shared.crm import get_translation, request_translations

router = APIRouter()


class RequestTranslationsInput(BaseModel):
    texts: list[str] = []
    force: bool = False


class TranslateInput(BaseModel):
    conversationId: str = ""
    messageId: str = ""
    targetLanguage: str = "zh-CN"
    text: str = ""


@router.post("/api/messages/translations")
def post_translations(body: RequestTranslationsInput) -> dict:
    saved = request_translations(list(body.texts or []), force=bool(body.force))
    return ok({"saved_count": saved, "translated_text": None, "cached": False})


@router.get("/api/messages/translations/{text}")
def fetch_translation(text: str) -> dict:
    return ok(get_translation(text))


def _single_translate(body: TranslateInput, *, force: bool) -> dict:
    text = (body.text or "").strip()
    if not text:
        return {"messageId": body.messageId, "translatedContent": None}
    request_translations([text], force=force)
    return {"messageId": body.messageId or text, "translatedContent": get_translation(text)}


@router.post("/api/messages/translate")
def translate_message(body: TranslateInput) -> dict:
    return ok(_single_translate(body, force=False))


@router.post("/api/messages/retranslate")
def retranslate_message(body: TranslateInput) -> dict:
    return ok(_single_translate(body, force=True))
