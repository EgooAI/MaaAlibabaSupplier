from __future__ import annotations

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.api.envelope import AppError, api_error, ok, user_message
from backend.app.api.account_scope import AccountRoute, request_epoch
from backend.app.shared.crm import get_translation, request_translations
from backend.app.task_queue import TaskSnapshot
from backend.app.translation_jobs import submit_translation_job, translation_job

router = APIRouter(route_class=AccountRoute)

TRANSLATION_TEXTS_MAX = 500


class RequestTranslationsInput(BaseModel):
    texts: list[str] = Field(default_factory=list, max_length=TRANSLATION_TEXTS_MAX)
    force: bool = False


class TranslationQueryInput(BaseModel):
    texts: list[str] = Field(default_factory=list, max_length=TRANSLATION_TEXTS_MAX)


class TranslateInput(BaseModel):
    conversationId: str = ""
    messageId: str = ""
    targetLanguage: str = "zh-CN"
    text: str = Field(default="", max_length=5000)


def _job_payload(snapshot: TaskSnapshot) -> dict:
    return {
        "task_id": snapshot.task_id,
        "status": str(snapshot.status),
        "message": user_message(snapshot.message),
    }


@router.post("/api/messages/translations")
def post_translations(body: RequestTranslationsInput) -> dict:
    """Queue an async translation job; returns its snapshot immediately.

    The LLM work runs on the dedicated translation worker without holding
    ``account_lock``; results land in the shared translate cache and are read
    back through the query endpoint or job polling.
    """
    expected = request_epoch.get()
    try:
        snapshot = submit_translation_job(body.texts, force=bool(body.force), expected_epoch=expected)
    except AppError:
        raise
    except OverflowError:
        return api_error("翻译任务队列已满，请稍后重试", status_code=429)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(_job_payload(snapshot))


@router.post("/api/messages/translations/query")
def query_translations(body: TranslationQueryInput) -> dict:
    """Batch cache lookup: text -> translation (null when not cached yet)."""
    translations: dict[str, str | None] = {}
    try:
        for raw in body.texts:
            text = (raw or "").strip()
            if not text or text in translations:
                continue
            translations[text] = get_translation(text)
    except AppError:
        raise
    except Exception:
        logger.exception("translation query failed")
        return api_error("服务器内部错误", status_code=500)
    return ok({"translations": translations})


@router.get("/api/messages/translations/jobs/{task_id}")
def get_translation_job(task_id: str) -> dict:
    snapshot = translation_job(task_id)
    if snapshot is None:
        raise AppError("翻译任务不存在或已过期", status_code=404)
    return ok(_job_payload(snapshot))


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
