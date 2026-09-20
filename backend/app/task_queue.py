from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from collections.abc import Mapping

from loguru import logger
from backend.app.shared.utils.log_context import bind_log_context, capture_log_context, log_event

TASK_QUEUE_MAXSIZE = 500
TASK_RETENTION_S = 3600.0

DEFAULT_QUEUE_NAME = "maafw"
TRANSLATION_QUEUE_NAME = "translation"


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
    log_context: Mapping

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
    _instances: dict[str, TaskQueue] = {}
    _instance_lock = threading.Lock()

    def __new__(cls, name: str = DEFAULT_QUEUE_NAME) -> TaskQueue:
        with cls._instance_lock:
            instance = cls._instances.get(name)
            if instance is None:
                instance = super().__new__(cls)
                instance._name = name
                instance._lock = threading.Lock()
                instance._condition = threading.Condition(instance._lock)
                instance._requests: dict[str, _TaskRequest] = {}
                instance._queue: deque[str] = deque()
                instance._closing = False
                instance._current_started: float | None = None
                instance._last_completed: float | None = None
                instance._worker = threading.Thread(
                    target=instance._work,
                    daemon=True,
                    name=f"{name}-task-queue",
                )
                instance._worker.start()
                cls._instances[name] = instance
            return instance

    @classmethod
    def observations(cls, name: str = DEFAULT_QUEUE_NAME) -> dict:
        """Observe an existing worker; never construct a queue or evict tasks.

        pending excludes the running job. Timestamps are Unix seconds and are
        retained independently of task-history eviction for this worker lifetime.
        """
        with cls._instance_lock:
            instance = cls._instances.get(name)
            if instance is None:
                return {"initialized": False, "alive": False, "pending": 0,
                        "current_started": None, "last_completed": None}
            with instance._lock:
                return {"initialized": True, "alive": instance._worker.is_alive(),
                        "pending": len(instance._queue), "current_started": instance._current_started,
                        "last_completed": instance._last_completed}

    def enqueue(self, fn: Callable[[], tuple[bool, str]], *, description: str) -> TaskSnapshot:
        now = time.time()
        with self._condition:
            if self._closing:
                raise RuntimeError("任务队列正在关闭")
            self._evict_locked(now)
            if len(self._queue) >= TASK_QUEUE_MAXSIZE:
                raise OverflowError("任务队列已满，请稍后重试")
            task_id = uuid.uuid4().hex
            request = _TaskRequest(
                task_id=task_id,
                fn=fn,
                description=description,
                status=TaskStatus.PENDING,
                message="等待执行",
                result=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                log_context=MappingProxyType({**capture_log_context(), "queue_task_id": task_id, "queue": self._name}),
            )
            self._requests[request.task_id] = request
            self._queue.append(request.task_id)
            with bind_log_context(**request.log_context):
                log_event("queue.enqueued")
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
        """Drain accepted work before releasing the instance and worker."""
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self._worker.join(timeout)
        if self._worker.is_alive():
            raise TimeoutError("任务队列尚未停止")
        with type(self)._instance_lock:
            type(self)._instances.pop(self._name, None)

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
                self._current_started = request.started_at

            with bind_log_context(**request.log_context):
                log_event("queue.started")
                try:
                    ok, msg = request.fn()
                except Exception as exc:
                    logger.exception("Queued task failed")
                    ok, msg = False, str(exc)
                with self._condition:
                    request.result = (ok, msg)
                    request.completed_at = time.time()
                    self._last_completed = request.completed_at
                    self._current_started = None
                    if ok:
                        request.status = TaskStatus.SUCCEEDED
                        request.message = msg or "执行成功"
                    else:
                        request.status = TaskStatus.FAILED
                        request.message = msg or "执行失败"
                log_event("queue.succeeded" if ok else "queue.failed",
                          duration_ms=(request.completed_at - request.started_at) * 1000)


def observe_task_queues() -> dict[str, dict]:
    """Include both standard queues even when no workers have been initialized."""
    with TaskQueue._instance_lock:
        names = dict.fromkeys((DEFAULT_QUEUE_NAME, TRANSLATION_QUEUE_NAME, *TaskQueue._instances))
    return {name: TaskQueue.observations(name) for name in names}


def get_task_queue() -> TaskQueue:
    return TaskQueue()


def get_translation_queue() -> TaskQueue:
    """Dedicated worker so long LLM translation jobs never block GUI tasks."""
    return TaskQueue(TRANSLATION_QUEUE_NAME)


def shutdown_task_queue(timeout: float = 5.0) -> None:
    """Drain and release every named queue created in this process."""
    with TaskQueue._instance_lock:
        instances = list(TaskQueue._instances.values())
    for instance in instances:
        try:
            instance.shutdown(timeout)
        except TimeoutError:
            logger.warning("Task queue '{}' did not stop within {}s", instance._name, timeout)
