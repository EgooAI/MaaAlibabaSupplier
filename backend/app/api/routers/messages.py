from __future__ import annotations

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.api.envelope import AppError, api_error, ok
from backend.app.api.account_scope import AccountRoute
from backend.app.shared.crm import get_translation, request_translations

router = APIRouter(route_class=AccountRoute)


class RequestTranslationsInput(BaseModel):
    texts: list[str] = Field(default_factory=list, max_length=50)
    force: bool = False


class TranslateInput(BaseModel):
    conversationId: str = ""
    messageId: str = ""
    targetLanguage: str = "zh-CN"
    text: str = Field(default="", max_length=5000)


@router.post("/api/messages/translations")
def post_translations(body: RequestTranslationsInput) -> dict:
    try:
        saved = request_translations(list(body.texts or []), force=bool(body.force))
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
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
