"""Application-owned IM sync scheduling; CRM owns transactions and revisions."""

from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, RLock, Thread
import time
from types import MappingProxyType
from collections.abc import Mapping
from uuid import uuid4

from loguru import logger

from backend.app.shared.backend.account_context import AccountContext
from backend.app.shared.crm.sync_store import canonical_source_dir
from backend.app.shared.utils.log_context import bind_log_context, capture_log_context, log_event


@dataclass
class SyncTarget:
    context: AccountContext
    path: Path
    source_revision: int
    waiters: list[Future] = field(default_factory=lambda: [Future()])
    future: Future | None = None
    log_context: Mapping = field(default_factory=lambda: MappingProxyType(capture_log_context()))


class SyncCoordinator:
    def __init__(self, submit, *, clock=time.time):
        self._submit = submit
        self._clock = clock
        self._lock = RLock()
        self.wake = Event()
        self._completed = SimpleQueue()
        self._active: dict[str, SyncTarget] = {}
        self._pending: SyncTarget | None = None
        self._context: AccountContext | None = None
        self._failures = 0
        self._state = {}
        self._last_target: SyncTarget | None = None

    def select(self, context, restored=None):
        with self._lock:
            restored = dict(restored or {})
            if context == self._context and self._state["revision"] > restored.get("revision", 0):
                # A completion may be published while the archive read is in flight.
                restored.update(revision=self._state["revision"], last_success=self._state["last_success"],
                                **self._state["counts"])
            self._cancel_pending()
            self._context = context
            self._last_target = None
            self._failures = 0
            self._state = {
                "self_ali_id": context.self_ali_id, "epoch": context.epoch,
                "revision": restored.get("revision", 0),
                "applied_source_revision": restored.get("applied_source_revision", 0),
                "last_success": restored.get("last_success"),
                "counts": {key: restored.get(key, 0) for key in ("inserted", "updated", "unchanged")},
                "last_attempt": None, "retry_at": None, "last_error": "",
                "phase": "idle", "pending": False, "syncing": False,
            }

    def _cancel_pending(self):
        if self._pending is not None:
            for waiter in self._pending.waiters:
                waiter.cancel()
            self._pending = None

    def clear_retry(self):
        with self._lock:
            self._failures = 0
            self._state.update(retry_at=None, last_error="")

    def snapshot(self):
        with self._lock:
            return {**self._state, "counts": dict(self._state.get("counts", {}))}

    def pinned_paths(self):
        with self._lock:
            return {job.path for job in self._active.values()} | (
                {self._pending.path} if self._pending else set()
            )

    def request(self, context, path, source_revision):
        with self._lock:
            if context != self._context:
                raise RuntimeError("Account context changed")
            target = self._last_target
            if target and target.source_revision == source_revision and target.path == path:
                # An unchanged committed or active source never needs another scan.
                if not target.waiters[-1].done() or not self._state["last_error"]:
                    return target.waiters[-1]
            with bind_log_context(account_epoch=context.epoch, source_revision=source_revision, sync_id=uuid4().hex):
                target = SyncTarget(context, path, source_revision)
            if self._pending is not None:
                target.waiters = self._pending.waiters + target.waiters
            self._pending = self._last_target = target
            self._state["pending"] = True
            self._dispatch()
            self.wake.set()
            return target.waiters[-1]

    def _dispatch(self):
        target = self._pending
        if target is None or target.context.epoch in self._active:
            return
        if self._clock() < (self._state["retry_at"] or 0):
            return
        self._pending = None
        self._active[target.context.epoch] = target
        self._state.update(pending=False, syncing=True, phase="syncing", last_attempt=self._clock())
        try:
            with bind_log_context(**target.log_context):
                log_event("sync.dispatched")
                future = self._submit(target)
            if not isinstance(future, Future):
                raise TypeError("CRM sync must return a Future")
        except Exception as exc:
            future = Future()
            future.set_exception(exc)
        target.future = future
        # The CRM executor must never acquire middleware/account/coordinator locks.
        future.add_done_callback(lambda done: self._complete(target))

    def _complete(self, target):
        with bind_log_context(**target.log_context):
            log_event("sync.callback")
            self._completed.put(target)
            self.wake.set()

    def _publish_archive_result(self, context, result):
        current = self._context
        if (current is None or context.self_ali_id != current.self_ali_id
                or canonical_source_dir(context.data_dir) != canonical_source_dir(current.data_dir)
                or result["revision"] <= self._state["revision"]):
            return
        # These describe the latest committed archive, including an older epoch.
        # applied_source_revision and readiness belong to the current source validation.
        self._state.update(
            revision=result["revision"], last_success=result["last_success"],
            counts={key: result[key] for key in ("inserted", "updated", "unchanged")},
        )

    def tick(self):
        with self._lock:
            while True:
                try:
                    target = self._completed.get_nowait()
                except Empty:
                    break
                if self._active.get(target.context.epoch) is not target:
                    continue
                self._active.pop(target.context.epoch)
                try:
                    result = target.future.result()
                    required = ("revision", "applied_source_revision", "last_success", "inserted", "updated", "unchanged")
                    if not isinstance(result, dict) or any(key not in result for key in required):
                        raise ValueError("CRM sync returned no committed state")
                    if (type(result["revision"]) is not int or result["revision"] <= 0
                            or not isinstance(result["last_success"], (int, float))):
                        raise ValueError("CRM sync returned invalid committed state")
                    if result["applied_source_revision"] < target.source_revision:
                        raise ValueError("CRM sync did not apply the requested source revision")
                    error = None
                except Exception as exc:
                    error = exc
                if error is None:
                    self._publish_archive_result(target.context, result)
                if target.context == self._context:
                    self._state["syncing"] = False
                    if error:
                        self._failures += 1
                        self._state.update(
                            phase="error", last_error=str(error) or type(error).__name__,
                            retry_at=self._clock() + min(3 * 2 ** min(self._failures - 1, 4), 30),
                        )
                        if self._pending is None:
                            self._pending = SyncTarget(target.context, target.path, target.source_revision,
                                                       log_context=target.log_context)
                            self._last_target = self._pending
                        self._state["pending"] = True
                    else:
                        self._failures = 0
                        self._state.update(
                            applied_source_revision=result["applied_source_revision"],
                            phase="ready", last_error="", retry_at=None,
                        )
                for waiter in target.waiters:
                    if not waiter.done():
                        with bind_log_context(**target.log_context):
                            if error:
                                waiter.set_exception(error)
                            else:
                                waiter.set_result(result)
            self._dispatch()

    def wait(self, target):
        while not target.done():
            self.wake.clear()
            self.tick()
            if not target.done():
                self.wake.wait(0.1)
        return target.result()

    def shutdown(self):
        with self._lock:
            self._cancel_pending()
            active = list(self._active.values())
            self._context = None
        for target in active:
            try:
                target.future.result()
            except Exception:
                pass
            # result() can wake before the executor has invoked its callback.
            self._complete(target)
        self.tick()


class SyncService:
    def __init__(self, middleware, interval=1.0, *, clock=time.time):
        self.middleware = middleware
        self.interval = interval
        self._stop = Event()
        self._lifecycle_lock = RLock()
        self._thread = None
        self._clock = clock
        self._observation_lock = RLock()
        self._observation = {
            "started_at": None, "heartbeat_at": None, "last_progress_at": None,
            "phase": "not_started", "phase_started_at": None,
            "completed_iterations": 0, "last_error": None,
        }

    def _observe(self, phase, **changes):
        with self._observation_lock:
            now = self._clock()
            if phase != self._observation["phase"]:
                self._observation.update(phase=phase, phase_started_at=now)
            self._observation.update(heartbeat_at=now, **changes)

    def observation(self):
        # Never acquire the lifecycle, account or middleware locks while polling.
        with self._observation_lock:
            state = dict(self._observation)
        sync = self.middleware._coordinator.snapshot()
        now = self._clock()
        return {
            **state, "started": state["started_at"] is not None,
            "alive": self._thread is not None and self._thread.is_alive(),
            "stopping": self._stop.is_set(), "observed_at": now,
            "phase_age_s": max(0, now - state["phase_started_at"]) if state["phase_started_at"] is not None else None,
            "pending": int(sync["pending"]), "active": int(sync["syncing"]),
            "pending_observed_at": now, "progress_unit": "service_iterations",
        }

    def start(self):
        with self._lifecycle_lock:
            if self._thread is not None and not self._stop.is_set():
                return
            self._stop.clear()
            self._observe("starting", started_at=self._clock(), last_error=None)
            self.middleware.prepare_startup_validation()
            self._thread = Thread(target=self._run, name="im-source-check", daemon=True)
            self._thread.start()

    def tick(self):
        self.middleware.sync_tick(stop=self._stop)

    def _run(self):
        try:
            while not self._stop.is_set():
                self.middleware._coordinator.wake.clear()
                self._observe("checking_source")
                try:
                    self.tick()
                except Exception as exc:
                    self._observe("waiting", last_error=type(exc).__name__)
                    logger.exception("IM sync service tick failed")
                else:
                    with self._observation_lock:
                        self._observation["completed_iterations"] += 1
                    self._observe("waiting", last_progress_at=self._clock(), last_error=None)
                self.middleware._coordinator.wake.wait(self.interval)
        finally:
            self._observe("stopped")

    def stop(self):
        with self._lifecycle_lock:
            if self._thread is None or self._stop.is_set():
                return
            self._stop.set()
            with self.middleware._lock:
                self.middleware._cancel_startup_validation()
                self.middleware._publish_source_status()
            self.middleware._coordinator.wake.set()
            self._thread.join()
            self.middleware._coordinator.shutdown()
            with self.middleware._lock:
                self.middleware._reset_runtime_state()
                self.middleware._select_sync_context()


_service: SyncService | None = None
_service_lock = RLock()


def observe_sync_service():
    # The registry lock is held during shutdown; reading its published reference
    # keeps observation available while a source read or CRM commit is blocked.
    service = _service
    return service.observation() if service is not None else None


def start_sync_service():
    global _service
    from backend.app.shared.backend.im_db_middleware import get_im_db_middleware

    with _service_lock:
        if _service is None:
            _service = SyncService(get_im_db_middleware())
            _service.start()
        return _service


def stop_sync_service():
    global _service
    with _service_lock:
        if _service is not None:
            _service.stop()
            _service = None
