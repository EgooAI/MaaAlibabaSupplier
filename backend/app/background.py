"""Owns application background work without knowing accounts, GUI or storage.

Workers run cooperative activities, schedules coalesce timer/manual wakeups, and
pools drain accepted calls. A readiness predicate can defer scheduled work until
an external policy (for example system idleness) admits it.
"""

from __future__ import annotations

from concurrent.futures import Future
from contextvars import Context, copy_context
from queue import Queue
from threading import Event, RLock, Thread
from time import monotonic
from typing import Callable

from loguru import logger


class Worker:
    def __init__(self, runtime, name, run, *, on_stop=None, wake=None, managed=True, daemon=True):
        self.runtime = runtime
        self.name = name
        self.stop_event = Event()
        self.wake = wake if wake is not None else Event()
        self._run = run
        self._on_stop = on_stop
        self._managed = managed
        self._daemon = daemon
        self._lock = RLock()
        self._thread: Thread | None = None

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
            except BaseException:
                self._thread = None
                if self._managed:
                    self.runtime._release(self)
                raise
        return self

    def _execute(self):
        try:
            self._run(self.stop_event)
        except Exception:
            logger.exception("Background worker '{}' failed", self.name)
        finally:
            if self._managed:
                self.runtime._release(self)

    def trigger(self):
        with self._lock:
            if (self.stop_event.is_set() or self.runtime.closing
                    or self._thread is not None and not self._thread.is_alive()):
                raise RuntimeError(f"Worker {self.name} is stopping")
            self.wake.set()

    def request_stop(self):
        with self._lock:
            first = not self.stop_event.is_set()
            self.stop_event.set()
            self.wake.set()
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

    def submit(self, fn, /, *args, **kwargs):
        with self._lock:
            if self._closing:
                raise RuntimeError(f"Pool {self.name} is stopping")
            self.runtime._admit(self)
            if len(self._workers) < self._max_workers:
                # The pool owns these workers and must finish accepted writes even
                # when the application's bounded shutdown wait expires.
                worker = Worker(self.runtime, f"{self.name}-{len(self._workers)}", self._work,
                                managed=False, daemon=False).start()
                self._workers.append(worker)
            future = Future()
            self._pending.add(future)
            self._queue.put((future, copy_context(), fn, args, kwargs))
            return future

    def _work(self, stop):
        while (item := self._queue.get()) is not None:
            future, context, fn, args, kwargs = item
            with self._lock:
                self._pending.discard(future)
            if not future.set_running_or_notify_cancel():
                continue
            def execute():
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)

            context.run(execute)

    def request_stop(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
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

    def __init__(self):
        self._lock = RLock()
        self._shutdown_lock = RLock()
        self._resources = {}
        self._cleanups = {}
        self.closing = False

    def _admit(self, resource):
        with self._lock:
            if self.closing:
                raise RuntimeError("Background runtime is stopping")
            self._resources[resource] = None

    def _release(self, resource):
        with self._lock:
            self._resources.pop(resource, None)

    def worker(self, name, run, *, on_stop=None):
        return Worker(self, name, run, on_stop=on_stop)

    def pool(self, name, *, max_workers=1):
        return ManagedPool(self, name, max_workers)

    def schedule(self, name, action, *, interval=None, delay=0.0, wake=None, ready=None):
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
            next_delay = delay
            while not stop.is_set():
                worker.wake.wait(next_delay)
                worker.wake.clear()
                if stop.is_set():
                    return
                try:
                    admitted = ready is None or ready()
                except Exception:
                    logger.exception("Readiness check for '{}' failed", name)
                    admitted = False
                if not admitted:
                    next_delay = 1.0
                    continue
                if stop.is_set():
                    return
                try:
                    override = action()
                except Exception:
                    logger.exception("Scheduled activity '{}' failed", name)
                    override = None
                next_delay = interval if override is None else override

        worker = Worker(self, name, run, wake=wake)
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
