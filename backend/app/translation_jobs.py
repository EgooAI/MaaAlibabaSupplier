"""Async translation jobs running on a dedicated worker queue.

提交侧只入队并立即返回任务快照；LLM 调用由 ``translation`` 专用 worker 执行，
期间不持有 ``account_lock``（长翻译不再阻塞账号路径请求和 GUI 任务）。译文写入
全局 translate 缓存（text_hash 主键，不按账号 scope，跨 epoch 无数据串扰），因此
入队时捕获的账号 epoch 仅用于任务开始前的取消判断，省去账号已切换后的无效 LLM
开销。
"""

from __future__ import annotations

import time

from loguru import logger

from backend.app.shared.backend.account_context import account_lock, get_account_context
from backend.app.shared.crm import request_translations
from backend.app.task_queue import TaskSnapshot, TaskStatus, get_translation_queue

# Each LLM call carries at most this many texts; larger submissions are chunked.
TRANSLATION_CHUNK_SIZE = 50


def submit_translation_job(texts: list[str], *, force: bool, expected_epoch: str | None) -> TaskSnapshot:
    """Queue one translation job covering *texts*.

    Trims, drops empties and dedupes while preserving order. Empty submissions
    return an already-completed snapshot so callers can treat it uniformly.
    """
    ordered = list(dict.fromkeys(text.strip() for text in texts if text and text.strip()))
    if not ordered:
        return _completed_snapshot("没有需要翻译的内容")

    def _run() -> tuple[bool, str]:
        if not _epoch_matches(expected_epoch):
            return False, "账号已切换，翻译任务已取消"
        failed = 0
        for start in range(0, len(ordered), TRANSLATION_CHUNK_SIZE):
            chunk = ordered[start : start + TRANSLATION_CHUNK_SIZE]
            try:
                request_translations(chunk, force=force)
            except Exception:
                # Partial success: chunks already written stay in the cache.
                logger.exception("translation chunk failed ({} texts)", len(chunk))
                failed += len(chunk)
        if failed:
            return False, f"{failed}/{len(ordered)} 条翻译失败，可重试"
        return True, f"已翻译 {len(ordered)} 条"

    return get_translation_queue().enqueue(_run, description="Translation texts")


def translation_job(task_id: str) -> TaskSnapshot | None:
    """Latest snapshot of a translation job, or None if unknown/evicted."""
    if not task_id:
        return None
    return get_translation_queue().get(task_id)


def _epoch_matches(expected: str | None) -> bool:
    if not expected:
        return True
    with account_lock:
        return get_account_context().epoch == expected


def _completed_snapshot(message: str) -> TaskSnapshot:
    now = time.time()
    return TaskSnapshot(
        task_id="",
        description="Translation texts",
        status=TaskStatus.SUCCEEDED,
        message=message,
        result=(True, message),
        created_at=now,
        started_at=now,
        completed_at=now,
    )


__all__ = ["TRANSLATION_CHUNK_SIZE", "submit_translation_job", "translation_job"]
