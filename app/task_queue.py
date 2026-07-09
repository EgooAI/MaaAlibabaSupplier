from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from loguru import logger


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
                instance._queue: list[str] = []
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
            return [r.snapshot() for r in self._requests.values()]

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._queue:
                    self._condition.wait()
                task_id = self._queue.pop(0)
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
