from __future__ import annotations

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.api.envelope import AppError, api_error, ok, user_message
from backend.app.api.account_scope import AccountRoute, request_epoch
from backend.app.shared.crm import get_translation
from backend.app.shared.crm.translation_cache import get_translations
from backend.app.task_queue import TaskSnapshot
from backend.app.translation_jobs import submit_translation_job, translation_job

router = APIRouter(route_class=AccountRoute)

TRANSLATION_TEXTS_MAX = 500


class RequestTranslationsInput(BaseModel):
    texts: list[str] = Field(default_factory=list, max_length=TRANSLATION_TEXTS_MAX)
    force: bool = False
    # 提供会话 ID 时，后端加载全量历史作为翻译上下文（已翻译消息也纳入）。
    conversationId: int | None = None


class TranslationQueryInput(BaseModel):
    texts: list[str] = Field(default_factory=list, max_length=TRANSLATION_TEXTS_MAX)


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
        snapshot = submit_translation_job(
            body.texts,
            force=bool(body.force),
            expected_epoch=expected,
            conversation_id=body.conversationId,
        )
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
    """Batch cache lookup: text -> translation.

    null 表示尚未缓存（或 Agent 判定为非常规消息）；空串是 NO_NEED 哨兵
    （已缓存、无需翻译）。
    """
    translations: dict[str, str | None] = {}
    try:
        texts: list[str] = []
        seen: set[str] = set()
        for raw in body.texts:
            text = (raw or "").strip()
            if text and text not in seen:
                seen.add(text)
                texts.append(text)
        translations = get_translations(texts)
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
    return ok(get_translation((text or "").strip()))
