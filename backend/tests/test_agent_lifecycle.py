"""Exercises Agent shutdown ownership without native services or real signals."""

import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app import background


@pytest.fixture
def agent(monkeypatch):
    from maa.library import Library

    # Prevents maa.agent imports from switching DLLs or registering native nodes.
    monkeypatch.setattr(Library, "open", Mock())
    from maa.agent.agent_server import AgentServer

    monkeypatch.setattr(AgentServer, "custom_action", lambda name: lambda cls: cls)
    monkeypatch.setattr(AgentServer, "custom_recognition", lambda name: lambda cls: cls)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    entry = importlib.import_module("backend.app.agent.main")
    monkeypatch.setattr(entry.sys, "argv", ["agent", "test-socket"])
    monkeypatch.setattr(entry, "load_workdir_env", Mock())
    monkeypatch.setattr(entry, "configure_logging", Mock())
    monkeypatch.setattr(entry, "log_event", Mock())
    monkeypatch.setattr(entry, "logger", Mock())
    for name in ("start_up", "shut_down", "join"):
        monkeypatch.setattr(AgentServer, name, Mock(return_value=True if name == "start_up" else None))

    signals = {}
    monkeypatch.setattr(entry.signal, "signal", lambda signum, handler: signals.__setitem__(signum, handler))
    runtime = background.BackgroundRuntime()
    monkeypatch.setattr(background, "_runtime", runtime)
    workers = []
    create_worker = runtime.worker

    def worker(*args, **kwargs):
        handle = create_worker(*args, **kwargs)
        workers.append(handle)
        return handle

    monkeypatch.setattr(runtime, "worker", worker)
    try:
        yield SimpleNamespace(entry=entry, server=AgentServer, runtime=runtime, workers=workers, signals=signals)
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("first_completed", ["shutdown", "join"])
def test_signal_shutdown_waits_for_native_shutdown_and_join(agent, first_completed):
    joining, shutting_down, joined, shut_down, cleaning = (Event() for _ in range(5))
    release_join, release_shutdown = Event(), Event()

    def join():
        joining.set()
        assert release_join.wait(5)
        joined.set()

    def shutdown():
        shutting_down.set()
        assert release_shutdown.wait(5)
        shut_down.set()

    agent.server.join.side_effect = join
    agent.server.shut_down.side_effect = shutdown
    agent.runtime.on_shutdown("observe-agent-cleanup", cleaning.set, priority=-1)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(agent.entry.main)
        try:
            assert joining.wait(5)
            handler = agent.signals[agent.entry.signal.SIGINT]
            handler(agent.entry.signal.SIGINT, None)
            assert shutting_down.wait(5)
            assert not running.done()
            with pytest.raises(KeyboardInterrupt):
                handler(agent.entry.signal.SIGINT, None)

            if first_completed == "shutdown":
                release_shutdown.set()
                assert shut_down.wait(5)
                assert not joined.is_set() and not running.done()
                release_join.set()
            else:
                release_join.set()
                assert joined.wait(5)
                assert cleaning.wait(5)
                assert not shut_down.is_set() and not running.done()
                release_shutdown.set()
            running.result(timeout=5)
        finally:
            release_join.set()
            release_shutdown.set()
            running.result(timeout=5)

    assert joined.is_set() and shut_down.is_set() and cleaning.is_set()
    assert agent.workers and all(not worker.is_alive() for worker in agent.workers)
    agent.runtime.shutdown()
    agent.server.start_up.assert_called_once_with("test-socket")
    agent.server.shut_down.assert_called_once_with()
    agent.server.join.assert_called_once_with()


def test_startup_failure_cleans_registered_lifecycle_and_workers(agent):
    entered, stopped = Event(), Event()

    def registered_work(stop):
        entered.set()
        assert stop.wait(5)
        stopped.set()

    worker = agent.runtime.worker("already-registered", registered_work).start()
    try:
        assert entered.wait(5)
        agent.server.start_up.side_effect = RuntimeError("Agent startup failed")

        with pytest.raises(RuntimeError, match="Agent startup failed"):
            agent.entry.main()

        assert stopped.is_set()
        assert all(not handle.is_alive() for handle in agent.workers)
        agent.runtime.shutdown()
        agent.server.start_up.assert_called_once_with("test-socket")
        agent.server.shut_down.assert_called_once_with()
        agent.server.join.assert_not_called()
    finally:
        worker.stop()
