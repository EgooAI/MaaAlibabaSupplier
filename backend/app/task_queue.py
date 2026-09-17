from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from loguru import logger

TASK_QUEUE_MAXSIZE = 500
TASK_RETENTION_S = 3600.0


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    description: str
    status: TaskStatus
    message: str
    result: tuple[bool, str] | None
    created_at: float
    started_at: float | None
    completed_at: float | None


@dataclass
class _TaskRequest:
    task_id: str
    fn: Callable[[], tuple[bool, str]]
    description: str
    status: TaskStatus
    message: str
    result: tuple[bool, str] | None
    created_at: float
    started_at: float | None
    completed_at: float | None

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot(
            task_id=self.task_id,
            description=self.description,
            status=self.status,
            message=self.message,
            result=self.result,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )


class TaskQueue:
    _instance: TaskQueue | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> TaskQueue:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.Lock()
                instance._condition = threading.Condition(instance._lock)
                instance._requests: dict[str, _TaskRequest] = {}
                instance._queue: deque[str] = deque()
                instance._closing = False
                instance._worker = threading.Thread(
                    target=instance._work,
                    daemon=True,
                    name="maafw-task-queue",
                )
                instance._worker.start()
                cls._instance = instance
            return cls._instance

    def enqueue(self, fn: Callable[[], tuple[bool, str]], *, description: str) -> TaskSnapshot:
        now = time.time()
        with self._condition:
            if self._closing:
                raise RuntimeError("任务队列正在关闭")
            self._evict_locked(now)
            if len(self._queue) >= TASK_QUEUE_MAXSIZE:
                raise OverflowError("任务队列已满，请稍后重试")
            request = _TaskRequest(
                task_id=uuid.uuid4().hex,
                fn=fn,
                description=description,
                status=TaskStatus.PENDING,
                message="等待执行",
                result=None,
                created_at=now,
                started_at=None,
                completed_at=None,
            )
            self._requests[request.task_id] = request
            self._queue.append(request.task_id)
            self._condition.notify_all()
            return request.snapshot()

    def get(self, task_id: str) -> TaskSnapshot | None:
        with self._lock:
            request = self._requests.get(task_id)
            return request.snapshot() if request else None

    def all_snapshots(self) -> list[TaskSnapshot]:
        with self._lock:
            self._evict_locked(time.time())
            return [r.snapshot() for r in self._requests.values()]

    def shutdown(self, timeout: float = 5.0) -> None:
        """Drain accepted work before releasing the singleton and worker."""
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self._worker.join(timeout)
        if self._worker.is_alive():
            raise TimeoutError("任务队列尚未停止")
        with self._instance_lock:
            if type(self)._instance is self:
                type(self)._instance = None

    def _evict_locked(self, now: float) -> None:
        expired = [
            task_id for task_id, req in self._requests.items()
            if req.completed_at is not None and now - req.completed_at > TASK_RETENTION_S
        ]
        for task_id in expired:
            self._requests.pop(task_id, None)

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closing:
                    self._condition.wait()
                if not self._queue:
                    return
                task_id = self._queue.popleft()
                request = self._requests.get(task_id)
                if request is None:
                    continue
                request.status = TaskStatus.RUNNING
                request.message = "正在执行"
                request.started_at = time.time()

            try:
                ok, msg = request.fn()
            except Exception as exc:
                logger.exception("Task '{}' failed", request.description)
                ok, msg = False, str(exc)

            with self._condition:
                request.result = (ok, msg)
                request.completed_at = time.time()
                if ok:
                    request.status = TaskStatus.SUCCEEDED
                    request.message = msg or "执行成功"
                else:
                    request.status = TaskStatus.FAILED
                    request.message = msg or "执行失败"


def get_task_queue() -> TaskQueue:
    return TaskQueue()


def shutdown_task_queue(timeout: float = 5.0) -> None:
    """Drain and release the process-wide queue, if one was ever created."""
    instance = TaskQueue._instance
    if instance is not None:
        instance.shutdown(timeout)
