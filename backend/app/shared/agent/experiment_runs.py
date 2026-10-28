"""In-process registry for experiment runs.

A run fans out over the variants of one step. The registry keeps enough state
for the UI to poll, and it enforces one active run per (experiment, step) so a
double click cannot bill the same step twice. Runs do not survive a restart:
`reconcile_interrupted` reports those as interrupted instead of pretending they
are still working.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional

from agent_pipeline.cancel import CancellationToken
from agent_pipeline.experiment_runner import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_INTERRUPTED = "interrupted"

TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})


@dataclass
class VariantRunState:
    variant_id: str
    status: str = STATUS_QUEUED
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class RunState:
    run_id: str
    experiment_id: int
    step_index: int
    status: str = STATUS_RUNNING
    variants: dict[str, VariantRunState] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    token: CancellationToken = field(default_factory=CancellationToken)
    _lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def active(self) -> bool:
        return self.status not in TERMINAL_STATUSES and self.status != STATUS_INTERRUPTED

    def update(self, variant_id: str, **changes: Any) -> None:
        with self._lock:
            state = self.variants.get(variant_id)
            if state is None:
                return
            for key, value in changes.items():
                setattr(state, key, value)

    def finish(self) -> None:
        with self._lock:
            if any(state.status == STATUS_CANCELLED for state in self.variants.values()):
                self.status = STATUS_CANCELLED
            elif any(state.status == STATUS_FAILED for state in self.variants.values()):
                self.status = STATUS_FAILED
            else:
                self.status = STATUS_COMPLETED
            self.finished_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "runId": self.run_id,
                "experimentId": str(self.experiment_id),
                "stepIndex": self.step_index,
                "status": self.status,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "variants": [
                    {
                        "variantId": state.variant_id,
                        "status": state.status,
                        "error": state.error,
                        "durationMs": round(state.duration_ms, 1),
                    }
                    for state in self.variants.values()
                ],
            }


class RunRegistry:
    """Thread-safe in-process map of run id to state."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[str, RunState] = {}
        # Runs are started for one account; a later request from another account
        # must not read or cancel them. Kept beside the states so the state
        # dataclass stays a pure scheduling record.
        self._owners_lock = Lock()
        self._owners: dict[str, Any] = {}

    def start(self, experiment_id: int, step_index: int, variant_ids: list[str]) -> RunState:
        """Create a run, or raise ``RunConflict`` if this step is already running."""
        with self._lock:
            for existing in self._runs.values():
                if (
                    existing.experiment_id == experiment_id
                    and existing.step_index == step_index
                    and existing.active
                ):
                    raise RunConflict(existing.run_id)
            state = RunState(
                run_id=uuid.uuid4().hex,
                experiment_id=experiment_id,
                step_index=step_index,
                variants={variant_id: VariantRunState(variant_id) for variant_id in variant_ids},
            )
            self._runs[state.run_id] = state
            return state

    def bind_owner(self, run_id: str, owner: Any) -> None:
        """Record which account started the run."""
        with self._owners_lock:
            self._owners[run_id] = owner

    def owner_of(self, run_id: str) -> Any:
        with self._owners_lock:
            return self._owners.get(run_id)

    def get(self, run_id: str) -> Optional[RunState]:
        with self._lock:
            return self._runs.get(run_id)

    def active_for(self, experiment_id: int) -> Optional[RunState]:
        with self._lock:
            for state in self._runs.values():
                if state.experiment_id == experiment_id and state.active:
                    return state
        return None

    def forget(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
        with self._owners_lock:
            self._owners.pop(run_id, None)

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
        with self._owners_lock:
            self._owners.clear()


class RunConflict(RuntimeError):
    def __init__(self, run_id: str) -> None:
        super().__init__("该步骤已有运行中的任务")
        self.run_id = run_id


runs = RunRegistry()


__all__ = [
    "RunConflict",
    "RunRegistry",
    "RunState",
    "STATUS_INTERRUPTED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "TERMINAL_STATUSES",
    "VariantRunState",
    "runs",
]
