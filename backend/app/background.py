"""Owns application background work without knowing accounts, GUI or storage.

Workers run cooperative activities, schedules coalesce timer/manual wakeups, and
pools drain accepted calls. A readiness predicate can defer scheduled work until
an external policy (for example system idleness) admits it.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from contextvars import Context, copy_context
from queue import Queue
from threading import Event, RLock, Thread
from time import monotonic, time
from typing import Callable

from loguru import logger


class WorkBlocked(RuntimeError):
    """Rejects new work while installation owns admission."""


class _Activity:
    def __init__(self, runtime, resets_idle):
        self.runtime = runtime
        self.resets_idle = resets_idle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        with self.runtime._lock:
            if self not in self.runtime._activities:
                return
            self.runtime._activities.remove(self)
            if self.resets_idle and not any(item.resets_idle for item in self.runtime._activities):
                self.runtime._idle_since = self.runtime._clock()


class _Observation:
    """Maintains scalar snapshots under a lock never held across application calls."""

    def __init__(self, runtime, name, kind):
        self.lock = runtime._observation_lock
        self.triggered = False
        self.data = {
            "name": name, "kind": kind, "state": "starting",
            "active": 0, "pending": 0, "completed": 0, "failed": 0,
            "started_at": None, "last_started_at": None,
            "last_completed_at": None, "next_run_at": None, "last_error": None,
        }

    def _state(self, state):
        if self.data["state"] != "stopping":
            self.data["state"] = state

    def begin(self):
        with self.lock:
            self.data["active"] += 1
            self.data["last_started_at"] = time()
            self.data["next_run_at"] = None
            if self.data["kind"] == "schedule":
                self.data["pending"] = int(self.triggered)
            self._state("running")

    def fail(self, error):
        with self.lock:
            self.data["failed"] += 1
            self.data["last_error"] = error

    def finish(self, error=None):
        with self.lock:
            self.data["active"] -= 1
            self.data["last_completed_at"] = time()
            if error is None:
                self.data["completed"] += 1
            else:
                self.fail(error)
            self._state("running" if self.data["active"] else "waiting")

    def wait(self, delay, *, blocked=False):
        with self.lock:
            self._state("blocked" if blocked else "waiting")
            self.data["pending"] = int(blocked or self.triggered)
            self.data["next_run_at"] = (
                None if delay is None or self.data["state"] == "stopping" else time() + delay
            )

    def stop(self):
        with self.lock:
            if self.data["state"] not in {"stopped", "completed", "failed"}:
                self.data["state"] = "stopping"
                self.data["next_run_at"] = None

    def exit(self, state=None):
        with self.lock:
            self.data["state"] = state or (
                "stopped" if self.data["state"] == "stopping" else "completed"
            )
            self.data.update(active=0, next_run_at=None)
            # Pool pending is decremented as workers dequeue, so a nonzero value
            # after an abnormal exit still reports undrained work.
            if self.data["kind"] != "pool":
                self.data["pending"] = 0


class Worker:
    def __init__(self, runtime, name, run, *, on_stop=None, wake=None,
                 managed=True, daemon=True, kind="worker", resident=False, control=False):
        self.runtime = runtime
        self.name = name
        self.stop_event = Event()
        self.wake = wake if wake is not None else Event()
        self._run = run
        self._on_stop = on_stop
        self._managed = managed
        self._daemon = daemon
        self._resident = resident
        self._control = control
        self._activity = None
        self._lock = RLock()
        self._thread: Thread | None = None
        self._observation = _Observation(runtime, name, kind)

    def start(self):
        with self._lock:
            if self.stop_event.is_set():
                raise RuntimeError(f"Worker {self.name} has already stopped")
            if self._thread is not None:
                if not self._thread.is_alive():
                    raise RuntimeError(f"Worker {self.name} has already stopped")
                return self
            if self._managed:
                self.runtime._admit(self)
            # Long-lived workers must not inherit the first caller's account.
            context = Context()
            self._thread = Thread(target=lambda: context.run(self._execute), name=self.name, daemon=self._daemon)
            try:
                self._thread.start()
            except BaseException as exc:
                self._thread = None
                self._observation.fail(type(exc).__name__)
                self._observation.exit("failed")
                if self._managed:
                    self.runtime._release(self)
                raise
        return self

    def _execute(self):
        observation = self._observation
        is_worker = observation.data["kind"] == "worker"
        error = None
        if is_worker:
            observation.begin()
        try:
            self._run(self.stop_event)
        except BaseException as exc:
            error = type(exc).__name__
            if not is_worker:
                observation.fail(error)
            if not isinstance(exc, Exception):
                raise
            logger.exception("Background worker '{}' failed", self.name)
        finally:
            if is_worker:
                observation.finish(error)
            observation.exit("failed" if error is not None else None)
            with self._lock:
                if self._activity is not None:
                    self._activity.close()
                    self._activity = None
            if self._managed:
                self.runtime._release(self)

    def trigger(self):
        with self._lock:
            if (self.stop_event.is_set() or self.runtime.closing
                    or self._thread is not None and not self._thread.is_alive()):
                raise RuntimeError(f"Worker {self.name} is stopping")
            observation = self._observation
            if observation.data["kind"] == "schedule":
                if not self._control and self._activity is None:
                    self._activity = self.runtime.activity(resets_idle=not self._resident)
                with observation.lock:
                    observation.triggered = True
                    observation.data["pending"] = 1
            self.wake.set()

    def request_stop(self):
        with self._lock:
            first = not self.stop_event.is_set()
            self._observation.stop()
            self.stop_event.set()
            self.wake.set()
            if self._thread is None and self._activity is not None:
                self._activity.close()
                self._activity = None
        if first and self._on_stop is not None:
            self._on_stop()

    def join(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.is_alive()

    def stop(self, timeout=5.0):
        try:
            self.request_stop()
        finally:
            if not self.join(timeout):
                raise TimeoutError(f"Worker {self.name} has not stopped")

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()


class ManagedPool:
    def __init__(self, runtime, name, max_workers):
        if max_workers <= 0:
            raise ValueError("Pool size must be positive")
        self.runtime = runtime
        self.name = name
        self._lock = RLock()
        self._max_workers = max_workers
        self._workers: list[Worker] = []
        self._queue = Queue()
        self._pending: set[Future] = set()
        self._closing = False
        self._live_workers = 0
        self._observation = _Observation(runtime, name, "pool")

    def submit(self, fn, /, *args, **kwargs):
        with self._lock:
            if self._closing:
                raise RuntimeError(f"Pool {self.name} is stopping")
            activity = self.runtime.activity()
            try:
                self.runtime._admit(self)
                if len(self._workers) < self._max_workers:
                    # Pool workers drain accepted writes even after a shutdown timeout.
                    self._live_workers += 1
                    try:
                        worker = Worker(self.runtime, f"{self.name}-{len(self._workers)}",
                                        lambda stop: self._work(), managed=False, daemon=False).start()
                    except BaseException as exc:
                        self._live_workers -= 1
                        self._observation.fail(type(exc).__name__)
                        if not self._live_workers:
                            self._observation.exit("failed")
                        raise
                    self._workers.append(worker)
                future = Future()
                self._pending.add(future)
                with self._observation.lock:
                    self._observation.data["pending"] += 1
                    if not self._observation.data["active"] and self._live_workers:
                        self._observation._state("waiting")
                self._queue.put((future, copy_context(), fn, args, kwargs, activity))
                return future
            except BaseException:
                activity.close()
                raise

    def _work(self):
        observation = self._observation
        try:
            while (item := self._queue.get()) is not None:
                future, context, fn, args, kwargs, activity = item
                with self._lock:
                    self._pending.discard(future)
                    with observation.lock:
                        observation.data["pending"] -= 1
                if not future.set_running_or_notify_cancel():
                    activity.close()
                    continue
                observation.begin()
                error = None

                def execute():
                    nonlocal error
                    try:
                        result = fn(*args, **kwargs)
                    except BaseException as exc:
                        error = type(exc).__name__
                        future.set_exception(exc)
                    else:
                        future.set_result(result)

                try:
                    context.run(execute)
                except BaseException as exc:
                    error = type(exc).__name__
                    raise
                finally:
                    # Future completion precedes callbacks; the worker is still
                    # occupied until all synchronous callbacks have returned.
                    observation.finish(error)
                    activity.close()
        finally:
            with self._lock:
                self._live_workers -= 1
                if not self._live_workers:
                    observation.exit("stopped" if self._closing else "failed")

    def request_stop(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
            self._observation.stop()
            if not self._live_workers and self._observation.data["state"] != "failed":
                self._observation.exit("stopped")
            for _ in self._workers:
                self._queue.put(None)

    def join(self, timeout=None):
        self.request_stop()
        deadline = None if timeout is None else monotonic() + timeout
        for worker in self._workers:
            remaining = None if deadline is None else max(0, deadline - monotonic())
            if not worker.join(remaining):
                return False
        self.runtime._release(self)
        return True

    def shutdown(self, wait=True, *, cancel_futures=False):
        self.request_stop()
        with self._lock:
            pending = tuple(self._pending) if cancel_futures else ()
        for future in pending:
            future.cancel()
        if wait:
            self.join()


class BackgroundRuntime:
    """Closes admission, runs ordered business cleanup, then stops and joins work."""

    def __init__(self, *, clock=monotonic):
        self._lock = RLock()
        self._shutdown_lock = RLock()
        self._resources = {}
        self._cleanups = {}
        self._observation_lock = RLock()
        self._observations = {}
        self._history = deque(maxlen=50)
        self.closing = False
        self._clock = clock
        self._activities = set()
        self._idle_since = clock()
        self._installing = False

    def activity(self, *, resets_idle=True):
        """Reserves work through completion, including queueing and callbacks."""
        with self._lock:
            if self.closing:
                raise RuntimeError("Background runtime is stopping")
            if self._installing:
                raise WorkBlocked("Application update installation is active")
            activity = _Activity(self, resets_idle)
            self._activities.add(activity)
            return activity

    def begin_install(self, *, idle_for=0.0, ready_since=0.0):
        """Atomically checks continuous idleness and closes work admission.

        Manual installation uses zero delay but still requires no admitted work.
        Resident polling reserves execution but does not reset the idle clock.
        """
        with self._lock:
            if self.closing or self._installing:
                return False
            if self._activities or self._clock() - max(self._idle_since, ready_since) < idle_for:
                return False
            self._installing = True
            return True

    def end_install(self):
        """Reopens admission after an uncommitted or failed installation."""
        with self._lock:
            self._installing = False
            self._idle_since = self._clock()

    def _admit(self, resource):
        with self._lock:
            if self.closing:
                raise RuntimeError("Background runtime is stopping")
            if self._installing and not getattr(resource, "_control", False):
                raise WorkBlocked("Application update installation is active")
            if (isinstance(resource, Worker) and resource._observation.data["kind"] == "worker"
                    and not resource._resident and not resource._control):
                resource._activity = self.activity()
            self._resources[resource] = None
            observation = resource._observation
            with self._observation_lock:
                if observation not in self._observations:
                    observation.data.update(state="starting", started_at=time())
                    self._observations[observation] = None

    def _release(self, resource):
        with self._lock:
            if isinstance(resource, Worker) and resource._activity is not None:
                resource._activity.close()
                resource._activity = None
            self._resources.pop(resource, None)
            with self._observation_lock:
                observation = resource._observation
                if observation in self._observations:
                    self._observations.pop(observation)
                    self._history.append(observation.data.copy())

    def observation(self):
        """Returns passive snapshots, including the last 50 released resources.

        Counts describe runtime calls, not business success. Pending pool calls
        include canceled entries until dequeued; completion includes callbacks.
        Errors retain only their type names, including after a successful retry.
        """
        with self._observation_lock:
            return {
                "closing": self.closing,
                "observed_at": time(),
                "resources": [item.copy() for item in self._history] + [
                    observation.data.copy() for observation in self._observations
                ],
            }

    def worker(self, name, run, *, on_stop=None, resident=False, control=False):
        return Worker(self, name, run, on_stop=on_stop, resident=resident, control=control)

    def pool(self, name, *, max_workers=1):
        return ManagedPool(self, name, max_workers)

    def schedule(self, name, action, *, interval=None, delay=0.0, wake=None, ready=None,
                 resident=False, control=False):
        """Runs non-overlapping calls on a fixed-delay timer or coalesced triggers.

        None disables the timer. A returned delay overrides the next interval.
        A false readiness predicate retains the pending call and checks again on
        trigger or after one second. It never cancels or replays accepted work.
        """
        if interval is not None and interval <= 0:
            raise ValueError("Scheduled interval must be positive")
        if delay is not None and delay < 0:
            raise ValueError("Initial delay must not be negative")

        def run(stop):
            observation = worker._observation
            next_delay = delay
            blocked = False
            while not stop.is_set():
                observation.wait(next_delay, blocked=blocked)
                worker.wake.wait(next_delay)
                with worker._lock:
                    worker.wake.clear()
                    with observation.lock:
                        observation.triggered = False
                        observation.data.update(pending=1, next_run_at=None)
                        observation._state("blocked" if ready is not None else "waiting")
                if stop.is_set():
                    return
                with worker._lock:
                    if not control and worker._activity is None:
                        try:
                            worker._activity = self.activity(resets_idle=not resident)
                        except WorkBlocked:
                            next_delay = 1.0
                            blocked = True
                            continue
                        except RuntimeError:
                            if self.closing:
                                return
                            raise
                try:
                    admitted = ready is None or ready()
                except Exception as exc:
                    observation.fail(type(exc).__name__)
                    logger.exception("Readiness check for '{}' failed", name)
                    admitted = False
                if not admitted:
                    next_delay = 1.0
                    blocked = True
                    continue
                if stop.is_set():
                    return
                with worker._lock:
                    activity = worker._activity
                    worker._activity = None
                observation.begin()
                error = None
                try:
                    override = action()
                except Exception as exc:
                    error = type(exc).__name__
                    logger.exception("Scheduled activity '{}' failed", name)
                    override = None
                finally:
                    observation.finish(error)
                    if activity is not None:
                        with worker._lock:
                            # A trigger accepted during readiness belongs to the
                            # follow-up call and must keep admission reserved.
                            if observation.triggered and worker._activity is None:
                                worker._activity = activity
                            else:
                                activity.close()
                next_delay = interval if override is None else override
                blocked = False

        worker = Worker(self, name, run, wake=wake, kind="schedule", resident=resident, control=control)
        return worker

    def on_shutdown(self, name: str, callback: Callable[[], None], *, priority=0):
        """Registers cleanup; higher priorities run first, ties unwind registration."""
        with self._lock:
            if self.closing:
                raise RuntimeError("Background runtime is stopping")
            self._cleanups[name] = (priority, callback)

    def shutdown(self, timeout=5.0):
        with self._shutdown_lock:
            with self._lock:
                with self._observation_lock:
                    self.closing = True
                cleanups = sorted(reversed(list(self._cleanups.values())), key=lambda item: -item[0])
                self._cleanups.clear()
            errors = []
            for _, callback in cleanups:
                try:
                    callback()
                except Exception as exc:
                    errors.append(exc)
            with self._lock:
                resources = list(self._resources)
            for resource in resources:
                try:
                    resource.request_stop()
                except Exception as exc:
                    errors.append(exc)
            deadline = None if timeout is None else monotonic() + timeout
            for resource in resources:
                try:
                    remaining = None if deadline is None else max(0, deadline - monotonic())
                    if not resource.join(remaining):
                        raise TimeoutError(f"Background work {resource.name} has not stopped")
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise ExceptionGroup("Background shutdown failed", errors)


_runtime = BackgroundRuntime()


def get_background_runtime():
    return _runtime


def shutdown_background(timeout=5.0):
    _runtime.shutdown(timeout)
