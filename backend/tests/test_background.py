"""Exercises background lifecycle and scheduling with controlled completion."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from queue import Queue
from threading import Condition, Event

import pytest

from backend.app.background import BackgroundRuntime


WAIT = 5


def resource_observation(runtime, name):
    return next(item for item in runtime.observation()["resources"] if item["name"] == name)


class ControlledWake:
    """Exposes timer waits and advances them without wall-clock sleeps."""

    def __init__(self):
        self.waits = Queue()
        self._condition = Condition()
        self._set = False
        self._elapsed = False

    def set(self):
        with self._condition:
            self._set = True
            self._condition.notify_all()

    def clear(self):
        with self._condition:
            self._set = False

    def elapse(self):
        with self._condition:
            self._elapsed = True
            self._condition.notify_all()

    def wait(self, timeout=None):
        with self._condition:
            self.waits.put((timeout, self._set))
            assert self._condition.wait_for(lambda: self._set or self._elapsed, WAIT)
            self._elapsed = False
            return self._set


@pytest.fixture
def runtime():
    runtime = BackgroundRuntime()
    yield runtime
    runtime.shutdown(timeout=WAIT)


def test_timer_and_triggers_serialize_and_coalesce_one_followup(runtime):
    wake = ControlledWake()
    entered = Queue()
    release = Event()
    calls = []

    def action():
        calls.append(len(calls) + 1)
        entered.put(calls[-1])
        if len(calls) == 1:
            assert release.wait(WAIT)

    worker = runtime.schedule("coalesced", action, interval=30, delay=10, wake=wake).start()
    try:
        assert wake.waits.get(timeout=WAIT) == (10, False)
        wake.elapse()
        assert entered.get(timeout=WAIT) == 1
        for _ in range(4):
            worker.trigger()
        assert wake.waits.empty()
        assert calls == [1]
        release.set()
        assert wake.waits.get(timeout=WAIT) == (30, True)
        assert entered.get(timeout=WAIT) == 2
        assert wake.waits.get(timeout=WAIT) == (30, False)
        wake.elapse()
        assert entered.get(timeout=WAIT) == 3
        assert wake.waits.get(timeout=WAIT) == (30, False)
    finally:
        release.set()
        worker.stop(timeout=WAIT)
    assert calls == [1, 2, 3]


def test_trigger_during_readiness_keeps_followup_reserved_for_install_gate(runtime):
    probes = []

    class ProbeWake(ControlledWake):
        def wait(self, timeout=None):
            if timeout == 30:
                probes.append(runtime.begin_install())
            return super().wait(timeout)

    wake = ProbeWake()
    calls = []

    def ready():
        if calls:
            return False
        worker.trigger()
        return True

    worker = runtime.schedule("reserved-followup", lambda: calls.append("first"),
                              interval=30, delay=None, wake=wake, ready=ready).start()
    try:
        assert wake.waits.get(timeout=WAIT) == (None, False)
        worker.trigger()
        assert wake.waits.get(timeout=WAIT) == (30, True)
        assert wake.waits.get(timeout=WAIT) == (1, False)
        assert probes == [False]
        assert calls == ["first"]
    finally:
        worker.stop(WAIT)
        runtime.end_install()


def test_stopping_an_unstarted_schedule_releases_its_accepted_trigger(runtime):
    worker = runtime.schedule("not-started", lambda: None, delay=None)
    worker.trigger()
    assert not runtime.begin_install()
    worker.stop(WAIT)
    assert runtime.begin_install()
    runtime.end_install()


@pytest.mark.parametrize("stop_pending", [False, True])
def test_readiness_retains_trigger_until_gate_opens_or_stop(runtime, stop_pending):
    wake = ControlledWake()
    gate = Event()
    calls = []
    worker = runtime.schedule(
        "gated", lambda: calls.append("run"), delay=None, wake=wake, ready=gate.is_set,
    ).start()
    try:
        assert wake.waits.get(timeout=WAIT) == (None, False)
        worker.trigger()
        assert wake.waits.get(timeout=WAIT) == (1, False)
        blocked = resource_observation(runtime, "gated")
        assert (blocked["state"], blocked["pending"], blocked["active"]) == ("blocked", 1, 0)
        assert blocked["next_run_at"] is not None
        assert calls == []
        wake.elapse()
        assert wake.waits.get(timeout=WAIT) == (1, False)
        assert calls == []
        gate.set()
        if stop_pending:
            worker.request_stop()
        else:
            # The original trigger survives gate retries without another trigger.
            wake.elapse()
            assert wake.waits.get(timeout=WAIT) == (None, False)
            waiting = resource_observation(runtime, "gated")
            assert (waiting["state"], waiting["pending"], waiting["completed"]) == ("waiting", 0, 1)
            assert waiting["next_run_at"] is None
    finally:
        worker.stop(timeout=WAIT)
    assert calls == ([] if stop_pending else ["run"])


def test_stop_during_readiness_check_does_not_start_action(runtime):
    checking = Event()
    release = Event()
    calls = []

    def ready():
        checking.set()
        assert release.wait(WAIT * 2)
        return True

    worker = runtime.schedule("stopping-gate", lambda: calls.append("run"), delay=None, ready=ready).start()
    try:
        worker.trigger()
        assert checking.wait(WAIT)
        with ThreadPoolExecutor(max_workers=1) as caller:
            try:
                observed = caller.submit(runtime.observation).result(timeout=WAIT)
                blocked, = observed["resources"]
                assert (blocked["state"], blocked["active"], blocked["pending"]) == ("blocked", 0, 1)
                assert blocked["next_run_at"] is None
                worker.request_stop()
                stopping = caller.submit(runtime.observation).result(timeout=WAIT)["resources"][0]
                assert stopping["state"] == "stopping"
                assert not release.is_set()
            finally:
                release.set()
    finally:
        release.set()
        worker.stop(timeout=WAIT)
    assert calls == []


def test_schedule_recovers_after_failure_and_honors_next_delay(runtime, monkeypatch):
    from backend.app import background

    clock = [100.0]
    monkeypatch.setattr(background, "time", lambda: clock[0])
    wake = ControlledWake()
    calls = []

    def action():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise ValueError("transient action failure")
        if len(calls) == 3:
            return False
        return 7 if len(calls) == 2 else None

    worker = runtime.schedule("retry", action, interval=30, delay=10, wake=wake).start()
    try:
        assert wake.waits.get(timeout=WAIT) == (10, False)
        initial = resource_observation(runtime, "retry")
        assert (initial["state"], initial["next_run_at"], initial["started_at"]) == ("waiting", 110.0, 100.0)
        for index, next_delay in enumerate((30, 7, False, 30)):
            clock[0] += 10
            wake.elapse()
            assert wake.waits.get(timeout=WAIT) == (next_delay, False)
            observed = resource_observation(runtime, "retry")
            assert (observed["state"], observed["active"], observed["pending"]) == ("waiting", 0, 0)
            assert (observed["completed"], observed["failed"], observed["last_error"]) == (index, 1, "ValueError")
            assert observed["last_started_at"] == observed["last_completed_at"] == clock[0]
            assert observed["next_run_at"] == clock[0] + next_delay
    finally:
        worker.stop(timeout=WAIT)
    assert calls == [1, 2, 3, 4]
    stopped = resource_observation(runtime, "retry")
    assert stopped["state"] == "stopped" and stopped["next_run_at"] is None


def test_stopping_schedule_drains_current_action_without_running_pending_trigger(runtime):
    entered = Event()
    release = Event()
    finished = Event()
    calls = []

    def action():
        calls.append("run")
        entered.set()
        assert release.wait(WAIT)
        finished.set()

    worker = runtime.schedule("stop-pending", action, delay=None).start()
    try:
        worker.trigger()
        assert entered.wait(WAIT)
        worker.trigger()
        observed = resource_observation(runtime, "stop-pending")
        assert (observed["state"], observed["active"], observed["pending"]) == ("running", 1, 1)
        with pytest.raises(TimeoutError):
            worker.stop(timeout=0)
        stopping = resource_observation(runtime, "stop-pending")
        assert stopping["state"] == "stopping" and stopping["completed"] == 0
        assert not finished.is_set()
        with pytest.raises(RuntimeError):
            worker.trigger()
    finally:
        release.set()
        worker.stop(timeout=WAIT)
    assert finished.is_set()
    assert calls == ["run"]


def test_worker_has_clean_context_and_stop_timeout_preserves_liveness(runtime):
    context = ContextVar("worker-origin", default="creation")
    entered = Event()
    release = Event()
    cancelled = []
    observations = []

    def run(stop):
        entered.set()
        assert release.wait(WAIT)
        observations.append((context.get(), stop.is_set()))

    worker = runtime.worker("context-worker", run, on_stop=lambda: cancelled.append("cancel"))
    token = context.set("origin")
    try:
        assert worker.start() is worker
        context.set("new-workspace")
        assert entered.wait(WAIT)
        assert worker.start() is worker
        with pytest.raises(TimeoutError):
            worker.stop(timeout=0)
        assert worker.is_alive()
        stopping = resource_observation(runtime, "context-worker")
        assert (stopping["state"], stopping["active"], stopping["completed"]) == ("stopping", 1, 0)
        assert worker.join(timeout=0) is False
        with pytest.raises(RuntimeError):
            worker.trigger()
        with pytest.raises(RuntimeError):
            worker.start()
        worker.request_stop()
        assert cancelled == ["cancel"]
    finally:
        release.set()
        worker.stop(timeout=WAIT)
        context.reset(token)
    assert worker.join(timeout=0) is True
    assert observations == [("creation", True)]
    stopped = resource_observation(runtime, "context-worker")
    assert (stopped["state"], stopped["active"], stopped["completed"]) == ("stopped", 0, 1)
    with pytest.raises(RuntimeError):
        worker.start()


def test_stopped_worker_cannot_be_started_for_the_first_time(runtime):
    calls = []
    worker = runtime.worker("stopped-before-start", lambda stop: calls.append("run"))
    worker.request_stop()
    try:
        with pytest.raises(RuntimeError):
            worker.start()
    finally:
        worker.stop(timeout=WAIT)
    assert calls == []


def test_completed_worker_rejects_triggers(runtime):
    calls = []
    worker = runtime.worker("completed", lambda stop: calls.append("run")).start()
    assert worker.join(timeout=WAIT)
    assert calls == ["run"]
    with pytest.raises(RuntimeError):
        worker.trigger()


def test_observation_is_passive_and_independent_of_lifecycle_locks(runtime, monkeypatch):
    from backend.app import background

    clock = [100.0]
    monkeypatch.setattr(background, "time", lambda: clock[0])
    entered = Event()
    release = Event()
    calls = []
    runtime.worker("unstarted", lambda stop: calls.append("worker"))
    runtime.schedule("unstarted-schedule", lambda: calls.append("action"),
                     ready=lambda: calls.append("ready"))
    pool = runtime.pool("unused")
    assert runtime.observation() == {"closing": False, "observed_at": 100.0, "resources": []}

    def run(stop):
        with runtime._lock, worker._lock, pool._lock:
            entered.set()
            assert release.wait(WAIT * 2)
        return False

    worker = runtime.worker("observed", run)
    worker.start()
    try:
        assert entered.wait(WAIT)
        with ThreadPoolExecutor(max_workers=1) as caller:
            try:
                snapshot = caller.submit(runtime.observation).result(timeout=WAIT)
                assert snapshot == {
                    "closing": False, "observed_at": 100.0,
                    "resources": [{
                        "name": "observed", "kind": "worker", "state": "running",
                        "active": 1, "pending": 0, "completed": 0, "failed": 0,
                        "started_at": 100.0, "last_started_at": 100.0,
                        "last_completed_at": None, "next_run_at": None, "last_error": None,
                    }],
                }
                snapshot["resources"][0]["active"] = 999
                fresh = caller.submit(runtime.observation).result(timeout=WAIT)
                assert fresh["resources"][0]["active"] == 1
                assert calls == [] and not release.is_set()
                clock[0] = 105.0
            finally:
                release.set()
    finally:
        release.set()
        assert worker.join(timeout=WAIT)
    completed = resource_observation(runtime, "observed")
    assert (completed["state"], completed["completed"], completed["failed"]) == ("completed", 1, 0)
    assert completed["last_completed_at"] == 105.0
    completed["state"] = "failed"
    assert resource_observation(runtime, "observed")["state"] == "completed"


@pytest.mark.parametrize("fail_start", [False, True])
def test_worker_failure_retains_only_exception_type(runtime, monkeypatch, fail_start):
    from backend.app import background

    def fail(*args):
        raise ValueError("private task content and arguments")

    worker = runtime.worker("failed", fail)
    if fail_start:
        monkeypatch.setattr(background.Thread, "start", fail)
        with pytest.raises(ValueError):
            worker.start()
    else:
        worker.start()
        assert worker.join(timeout=WAIT)
    observed = resource_observation(runtime, "failed")
    assert (observed["state"], observed["failed"], observed["completed"]) == ("failed", 1, 0)
    assert (observed["active"], observed["pending"], observed["last_error"]) == (0, 0, "ValueError")
    worker.request_stop()
    assert resource_observation(runtime, "failed") == observed
    assert "private" not in str(runtime.observation())


def test_stop_callback_failure_still_joins_the_running_worker(runtime, monkeypatch):
    entered = Event()
    release = Event()
    finished = Event()
    steps = Queue()
    cancellation_error = ValueError("cancel hook failed")

    def run(stop):
        entered.set()
        assert release.wait(WAIT)
        finished.set()

    def cancel():
        raise cancellation_error

    worker = runtime.worker("failed-cancel", run, on_stop=cancel).start()
    join = worker.join

    def observe_join(timeout=None):
        steps.put("joining")
        return join(timeout)

    monkeypatch.setattr(worker, "join", observe_join)
    try:
        assert entered.wait(WAIT)
        with ThreadPoolExecutor(max_workers=1) as caller:
            stopping = caller.submit(worker.stop, timeout=WAIT)
            stopping.add_done_callback(lambda future: steps.put("returned"))
            try:
                assert steps.get(timeout=WAIT) == "joining"
                assert not stopping.done()
            finally:
                release.set()
            with pytest.raises(ValueError) as failure:
                stopping.result(timeout=WAIT)
            assert failure.value is cancellation_error
        assert finished.is_set() and not worker.is_alive()
    finally:
        release.set()
        assert join(timeout=WAIT)


def test_pool_drains_fifo_after_stop_and_captures_each_submission_context(runtime):
    context = ContextVar("pool-origin", default="outside")
    entered = Event()
    release = Event()
    calls = []
    pool = runtime.pool("fifo", max_workers=1)

    def action(number, *, fail=False):
        if number == 1:
            entered.set()
            assert release.wait(WAIT)
        origin = context.get()
        calls.append((number, origin))
        context.set("mutated-in-worker")
        if fail:
            raise ValueError("one failed task")
        return number, origin

    token = context.set("first")
    try:
        first = pool.submit(action, 1)
        assert entered.wait(WAIT)
        context.set("second")
        second = pool.submit(action, 2, fail=True)
        context.set("third")
        third = pool.submit(action, 3)
        context.set("new-workspace")
        pool.request_stop()
        with pytest.raises(RuntimeError):
            pool.submit(action, 4)
        assert pool.join(timeout=0) is False
        assert not second.done() and not third.done()
    finally:
        release.set()
        assert pool.join(timeout=WAIT)
        context.reset(token)
    assert first.result(timeout=WAIT) == (1, "first")
    with pytest.raises(ValueError, match="one failed task"):
        second.result(timeout=WAIT)
    assert third.result(timeout=WAIT) == (3, "third")
    assert calls == [(1, "first"), (2, "second"), (3, "third")]
    assert context.get() == "outside"


def test_shutdown_timeout_keeps_running_worker_owned_for_repeated_shutdown(runtime, monkeypatch):
    entered = Event()
    release = Event()
    joining = Event()
    finished = Event()
    cancelled = []

    def run(stop):
        entered.set()
        assert release.wait(WAIT)
        finished.set()

    worker = runtime.worker("slow", run, on_stop=lambda: cancelled.append("cancel")).start()
    join = worker.join

    def observe_join(timeout=None):
        joining.set()
        return join(timeout)

    try:
        assert entered.wait(WAIT)
        with pytest.raises(ExceptionGroup) as failure:
            runtime.shutdown(timeout=0)
        assert len(failure.value.exceptions) == 1
        assert isinstance(failure.value.exceptions[0], TimeoutError)
        assert worker.is_alive() and not finished.is_set()
        monkeypatch.setattr(worker, "join", observe_join)
        with ThreadPoolExecutor(max_workers=1) as caller:
            shutdown = caller.submit(runtime.shutdown, timeout=WAIT)
            try:
                assert joining.wait(WAIT)
                assert not shutdown.done()
            finally:
                release.set()
            shutdown.result(timeout=WAIT)
        assert finished.is_set() and not worker.is_alive()
        runtime.shutdown(timeout=0)
        assert cancelled == ["cancel"]
    finally:
        release.set()
        worker.stop(timeout=WAIT)


def test_pool_callback_is_running_work_and_respects_shutdown_timeout(runtime):
    finish_action = Event()
    callback_entered = Event()
    finish_callback = Event()
    callback_finished = Event()
    pool = runtime.pool("callback-drain")

    def action():
        assert finish_action.wait(WAIT)

    def callback(future):
        callback_entered.set()
        assert finish_callback.wait(WAIT * 2)
        callback_finished.set()

    future = pool.submit(action)
    future.add_done_callback(callback)
    try:
        finish_action.set()
        assert callback_entered.wait(WAIT)
        assert future.done() and not callback_finished.is_set()
        with ThreadPoolExecutor(max_workers=1) as caller:
            shutdown = caller.submit(runtime.shutdown, timeout=0)
            try:
                with pytest.raises(ExceptionGroup) as failure:
                    shutdown.result(timeout=WAIT)
                assert any(isinstance(exc, TimeoutError) for exc in failure.value.exceptions)
                assert not callback_finished.is_set()
            finally:
                finish_callback.set()
        runtime.shutdown(timeout=WAIT)
        assert callback_finished.is_set()
    finally:
        finish_action.set()
        finish_callback.set()
        assert pool.join(timeout=WAIT)


def test_pool_observation_counts_callbacks_queue_failures_and_cancellation(runtime):
    entered = Queue()
    finish_actions = Event()
    callbacks = Queue()
    finish_callbacks = Event()
    ran = []
    pool = runtime.pool("observed-pool", max_workers=2)

    def action(number):
        entered.put(number)
        assert finish_actions.wait(WAIT * 3)
        if number == 0:
            raise ValueError("private submission payload")
        return False

    def callback(future):
        callbacks.put(future)
        assert finish_callbacks.wait(WAIT * 3)

    first = pool.submit(action, 0)
    second = pool.submit(action, 1)
    first.add_done_callback(callback)
    second.add_done_callback(callback)
    try:
        assert {entered.get(timeout=WAIT), entered.get(timeout=WAIT)} == {0, 1}
        canceled = pool.submit(lambda: ran.append("canceled"))
        last = pool.submit(lambda: ran.append("last"))
        assert canceled.cancel()
        finish_actions.set()
        assert {callbacks.get(timeout=WAIT), callbacks.get(timeout=WAIT)} == {first, second}
        assert first.done() and second.done()
        with ThreadPoolExecutor(max_workers=1) as caller:
            try:
                with runtime._lock, pool._lock:
                    snapshot = caller.submit(runtime.observation).result(timeout=WAIT)
                observed, = snapshot["resources"]
                assert (observed["kind"], observed["state"]) == ("pool", "running")
                assert (observed["active"], observed["pending"]) == (2, 2)
                assert (observed["completed"], observed["failed"], observed["last_completed_at"]) == (0, 0, None)
                assert not last.done() and not finish_callbacks.is_set()
                with pytest.raises(ExceptionGroup):
                    runtime.shutdown(timeout=0)
                closing = caller.submit(runtime.observation).result(timeout=WAIT)
                assert closing["closing"] is True
                stopping, = closing["resources"]
                assert (stopping["state"], stopping["active"], stopping["pending"]) == ("stopping", 2, 2)
            finally:
                finish_callbacks.set()
    finally:
        finish_actions.set()
        finish_callbacks.set()
        assert pool.join(timeout=WAIT)
    assert canceled.cancelled() and ran == ["last"]
    assert isinstance(first.exception(timeout=WAIT), ValueError)
    assert second.result(timeout=WAIT) is False and last.result(timeout=WAIT) is None
    stopped = resource_observation(runtime, "observed-pool")
    assert (stopped["state"], stopped["active"], stopped["pending"]) == ("stopped", 0, 0)
    assert (stopped["completed"], stopped["failed"], stopped["last_error"]) == (2, 1, "ValueError")
    assert stopped["last_completed_at"] >= stopped["last_started_at"] >= stopped["started_at"]


def test_released_history_is_bounded_and_does_not_retain_work(runtime):
    import gc
    import weakref

    class Action:
        def __init__(self, index):
            self.index = index

        def __call__(self, stop):
            if self.index == 54:
                raise ValueError("private short-lived failure")
            return False

    entered = Event()

    def running(stop):
        entered.set()
        stop.wait()

    anchor = runtime.worker("still-running", running).start()
    references = []
    try:
        assert entered.wait(WAIT)
        for index in range(55):
            action = Action(index)
            references.append(weakref.ref(action))
            worker = runtime.worker(f"short-{index}", action).start()
            assert worker.join(timeout=WAIT)
            del worker, action
        gc.collect()
        assert all(reference() is None for reference in references)
        resources = runtime.observation()["resources"]
        assert len(resources) == 51
        history = [item for item in resources if item["name"] != "still-running"]
        assert [item["name"] for item in history] == [f"short-{index}" for index in range(5, 55)]
        assert all(item["state"] == "completed" and item["completed"] == 1 for item in history[:-1])
        assert (history[-1]["state"], history[-1]["last_error"]) == ("failed", "ValueError")
        assert resource_observation(runtime, "still-running")["state"] == "running"
    finally:
        anchor.stop(timeout=WAIT)
    assert len(runtime.observation()["resources"]) == 50


def test_shutdown_closes_admission_and_continues_ordered_cleanup_after_errors(runtime):
    cleanup_order = []
    cancellations = []
    entered = [Event(), Event()]
    cancelled = [Event(), Event()]
    completed = Queue()
    cleanup_error = ValueError("business cleanup failed")
    cancellation_error = RuntimeError("cancellation failed")

    def run(index):
        entered[index].set()
        assert cancelled[index].wait(WAIT)
        completed.put(index)

    def cancel(index):
        cancellations.append(index)
        cancelled[index].set()
        if index == 0:
            raise cancellation_error

    workers = [
        runtime.worker(f"cleanup-{index}", lambda stop, i=index: run(i), on_stop=lambda i=index: cancel(i)).start()
        for index in range(2)
    ]
    late_worker = runtime.worker("late", lambda stop: None)
    late_pool = runtime.pool("late-pool")

    def high_priority_cleanup():
        cleanup_order.append("high")
        for attempt in (
            late_worker.start,
            lambda: late_pool.submit(lambda: None),
            workers[0].trigger,
            lambda: runtime.on_shutdown("late", lambda: None),
        ):
            with pytest.raises(RuntimeError):
                attempt()
        raise cleanup_error

    runtime.on_shutdown("old", lambda: cleanup_order.append("old"))
    runtime.on_shutdown("low", lambda: cleanup_order.append("low"), priority=-1)
    runtime.on_shutdown("high", high_priority_cleanup, priority=10)
    runtime.on_shutdown("new", lambda: cleanup_order.append("new"))
    try:
        assert all(event.wait(WAIT) for event in entered)
        with pytest.raises(ExceptionGroup) as failure:
            runtime.shutdown(timeout=WAIT)
        assert failure.value.exceptions == (cleanup_error, cancellation_error)
        assert cleanup_order == ["high", "new", "old", "low"]
        assert cancellations == [0, 1]
        assert {completed.get(timeout=WAIT), completed.get(timeout=WAIT)} == {0, 1}
        assert all(not worker.is_alive() for worker in workers)
        runtime.shutdown(timeout=0)
        assert cleanup_order == ["high", "new", "old", "low"]
        assert cancellations == [0, 1]
    finally:
        for event in cancelled:
            event.set()
        for worker in workers:
            worker.request_stop()
            assert worker.join(timeout=WAIT)
        late_worker.stop(timeout=WAIT)
        late_pool.shutdown()


def test_readiness_exception_retains_pending_work_for_retry(runtime):
    wake = ControlledWake()
    checks = []
    calls = []

    def ready():
        checks.append("check")
        if len(checks) == 1:
            raise OSError("readiness temporarily unavailable")
        return True

    worker = runtime.schedule(
        "failing-gate", lambda: calls.append("run"), delay=None, wake=wake, ready=ready,
    ).start()
    try:
        assert wake.waits.get(timeout=WAIT) == (None, False)
        worker.trigger()
        assert wake.waits.get(timeout=WAIT) == (1, False)
        assert calls == []
        observed = resource_observation(runtime, "failing-gate")
        assert (observed["state"], observed["pending"], observed["failed"]) == ("blocked", 1, 1)
        assert observed["last_error"] == "OSError"
        wake.elapse()
        assert wake.waits.get(timeout=WAIT) == (None, False)
        assert calls == ["run"]
    finally:
        worker.stop(timeout=WAIT)


def test_pool_continuation_uses_completed_submission_account_not_first_submitter(runtime):
    from backend.app.shared.backend.account_context import AccountContext, bind_account_context, get_account_context

    first_account = AccountContext("first", "source-first", "epoch-first")
    second_account = AccountContext("second", "source-second", "epoch-second")
    release = Event()
    continuation = Queue()
    pool = runtime.pool("account-continuation")

    def second_action():
        assert release.wait(WAIT)
        return get_account_context()

    try:
        with bind_account_context(first_account):
            assert pool.submit(get_account_context).result(timeout=WAIT) == first_account
        with bind_account_context(second_account):
            second = pool.submit(second_action)
            second.add_done_callback(lambda done: continuation.put(pool.submit(get_account_context)))
        release.set()
        assert second.result(timeout=WAIT) == second_account
        assert continuation.get(timeout=WAIT).result(timeout=WAIT) == second_account
    finally:
        release.set()
        assert pool.join(timeout=WAIT)


def test_shared_task_queue_does_not_keep_creator_account_in_gui_guard(runtime, monkeypatch):
    from backend.app import task_queue
    from backend.app.shared.backend import account_context, gui_session

    first = account_context.AccountContext("first", "source-first", "epoch-first")
    current = account_context.AccountContext("second", "source-second", "epoch-second")
    monkeypatch.setattr(task_queue, "get_background_runtime", lambda: runtime)
    monkeypatch.setattr(account_context, "_context", current)
    monkeypatch.setattr(account_context, "read_app_config", lambda: {
        "self_ali_id": current.self_ali_id, "alibaba_data_dir": current.data_dir,
    })
    monkeypatch.setattr(gui_session.runner, "_probe_binding", lambda: (True, "connected"))
    monkeypatch.setattr(gui_session.runner, "_window_generation", "window")
    monkeypatch.setattr(gui_session.runner, "_prepare_native_logs", lambda: None)
    old_token = gui_session.GuiSessionToken(first.self_ali_id, first.data_dir, first.epoch, "window")
    calls = []

    with account_context.bind_account_context(first):
        queue = task_queue.TaskQueue("account-isolation-review")
    try:
        assert account_context.get_account_context() == current
        submitted = queue.enqueue(
            lambda: gui_session.run_guarded(old_token, lambda: (calls.append("gui") is None, "done")),
            description="stale account authorization",
        )
    finally:
        queue.shutdown(timeout=WAIT)
    assert queue.get(submitted.task_id).result[0] is False
    assert calls == []


def test_outbox_stop_during_start_cannot_miss_verifier(runtime, monkeypatch, tmp_path):
    from backend.app.api.envelope import AppError
    from backend.app.shared.backend import outbox_service
    from backend.app.shared.crm.outbox_store import OutboxStore

    monkeypatch.setattr(outbox_service, "get_background_runtime", lambda: runtime)
    store = OutboxStore(tmp_path / "outbox-start-stop.sqlite")
    service = outbox_service.OutboxService(store)
    initializing = Event()
    release = Event()
    initialize = store.initialize

    def paused_initialize():
        initializing.set()
        assert release.wait(WAIT)
        initialize()

    monkeypatch.setattr(store, "initialize", paused_initialize)
    try:
        with ThreadPoolExecutor(max_workers=2) as caller:
            starting = caller.submit(service.start)
            try:
                assert initializing.wait(WAIT)
                stopping = caller.submit(service.stop, timeout=0)
                assert service._stopping.wait(WAIT)
            finally:
                release.set()
            failure = starting.exception(timeout=WAIT)
            assert failure is None or isinstance(failure, AppError)
            stopping.result(timeout=WAIT)
        worker = service._thread
        assert worker is None or worker.stop_event.is_set()
        assert worker is None or worker.join(timeout=WAIT)
    finally:
        release.set()
        if service._thread is not None:
            service._thread.stop(timeout=WAIT)
        service.stop(timeout=WAIT)


def test_yak_startup_interruption_does_not_leave_unowned_process(runtime, monkeypatch, tmp_path):
    from io import BytesIO
    from unittest.mock import Mock

    from backend.app import background, main

    (tmp_path / "yak_mitm.yak").write_text("// synthetic startup test", encoding="utf-8")
    process = Mock(pid=123, stdout=BytesIO())
    process.poll.return_value = None
    process.wait.return_value = 0
    monkeypatch.setattr(background, "_runtime", runtime)
    monkeypatch.setattr(main.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(main, "_resolve_yak_executable", lambda root: "synthetic-yak.exe")

    def interrupt_readiness(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "_wait_for_port", interrupt_readiness)
    with pytest.raises(KeyboardInterrupt):
        main._start_yak_mitm(tmp_path, host="127.0.0.1", port=9015, internal_token="synthetic-token")
    # _main has not received the process, so its stop_yak closure still sees None.
    runtime.shutdown(timeout=WAIT)
    process.terminate.assert_called_once()
    process.wait.assert_called()
