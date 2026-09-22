import os
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app import main


@pytest.fixture
def startup(monkeypatch):
    mocks = {}
    for name in ("load_workdir_env", "configure_logging", "ensure_system_agents_seeded",
                 "ensure_default_llm_levels_seeded", "_register_agent_runtime", "MaaFWProcess",
                 "_start_mitm_receiver", "_start_yak_mitm", "start_sync_service", "stop_sync_service",
                 "start_outbox_service", "stop_outbox_service", "run_api"):
        mocks[name] = Mock()
        monkeypatch.setattr(main, name, mocks[name])
    monkeypatch.setattr("backend.app.task_queue.shutdown_task_queue", Mock())
    mocks["_start_yak_mitm"].return_value.poll.return_value = 0
    return mocks


@pytest.mark.parametrize("secret", [None, "", "bad", "g" * 64])
def test_invalid_auth_stops_before_logging_seeding_or_services(monkeypatch, startup, secret):
    if secret is None:
        monkeypatch.delenv("MAA_AUTH_SECRET_SHA256", raising=False)
    else:
        monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", secret)
    with pytest.raises(ValueError, match="MAA_AUTH_SECRET_SHA256"):
        main.main()
    startup["load_workdir_env"].assert_called_once()
    for name, mock in startup.items():
        if name != "load_workdir_env":
            mock.assert_not_called()


@pytest.mark.parametrize("key,value", [("MITM_RECEIVER_HOST", "0.0.0.0"), ("MITM_RECEIVER_HOST", "::"),
                                       ("MITM_RECEIVER_PORT", "0"), ("MITM_RECEIVER_PORT", "bad")])
def test_invalid_receiver_config_stops_before_seeding_or_services(monkeypatch, startup, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        main.main()
    for name, mock in startup.items():
        if name != "load_workdir_env":
            mock.assert_not_called()


def test_each_launch_passes_fresh_private_credential_to_both_services(monkeypatch, startup):
    monkeypatch.setenv("MAA_MITM_INTERNAL_TOKEN", "stale-inherited-token")
    monkeypatch.setenv("MITM_RECEIVER_HOST", "[::1]")
    monkeypatch.setenv("MITM_RECEIVER_PORT", "9015")
    tokens = []
    for _ in range(2):
        main.main()
        receiver = startup["_start_mitm_receiver"].call_args.kwargs
        yak = startup["_start_yak_mitm"].call_args.kwargs
        assert receiver == yak
        assert receiver["host"] == "::1" and receiver["port"] == 9015
        token = receiver["internal_token"]
        assert token.startswith("mitm_") and len(token) == 48
        tokens.append(token)
    assert tokens[0] != tokens[1]
    assert os.environ["MAA_MITM_INTERNAL_TOKEN"] == "stale-inherited-token"
    assert startup["_start_mitm_receiver"].return_value.shutdown.call_count == 2


def test_auth_validation_follows_dotenv_loading(monkeypatch, startup):
    monkeypatch.delenv("MAA_AUTH_SECRET_SHA256", raising=False)
    startup["load_workdir_env"].side_effect = lambda: monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", "ab" * 32)
    main.main()
    startup["run_api"].assert_called_once()


def test_receiver_bind_failure_prevents_gui_and_yak_start(startup):
    startup["_start_mitm_receiver"].side_effect = OSError("address already in use")
    with pytest.raises(OSError, match="address already in use"):
        main.main()
    startup["MaaFWProcess"].return_value.start.assert_not_called()
    startup["_start_yak_mitm"].assert_not_called()
    startup["run_api"].assert_not_called()


def test_receiver_binds_before_thread_start_and_propagates_failure(monkeypatch):
    create = Mock(side_effect=OSError("address already in use"))
    thread = Mock()
    monkeypatch.setattr(main, "create_receiver", create)
    monkeypatch.setattr(main.threading, "Thread", thread)
    with pytest.raises(OSError, match="address already in use"):
        main._start_mitm_receiver(host="127.0.0.1", port=8085, internal_token="private-token")
    thread.assert_not_called()


def test_receiver_thread_closes_server(monkeypatch):
    server = Mock()
    create = Mock(return_value=server)
    monkeypatch.setattr(main, "create_receiver", create)
    thread = Mock()
    monkeypatch.setattr(main.threading, "Thread", thread)
    assert main._start_mitm_receiver(host="::1", port=9005, internal_token="private-token") is server
    create.assert_called_once_with("::1", 9005, internal_token="private-token")
    thread.return_value.start.assert_called_once()
    thread.call_args.kwargs["target"]()
    server.serve_forever.assert_called_once()
    server.server_close.assert_called_once()


def test_yak_receives_token_only_in_child_environment(monkeypatch, tmp_path):
    (tmp_path / "yak_mitm.yak").write_text("// test script", encoding="utf-8")
    monkeypatch.setenv("MAA_MITM_INTERNAL_TOKEN", "inherited-token")
    monkeypatch.setenv("NO_COLOR", "0")
    monkeypatch.setenv("MITM_RECEIVER_HOST", "127.0.0.1")
    monkeypatch.setenv("MITM_RECEIVER_PORT", "8085")
    monkeypatch.setattr(main, "_resolve_yak_executable", lambda root: "yak.exe")
    monkeypatch.setattr(main, "_wait_for_port", lambda *args, **kwargs: True)
    popen = Mock(return_value=SimpleNamespace(pid=123, stdout=BytesIO(), poll=lambda: None))
    monkeypatch.setattr(main.subprocess, "Popen", popen)
    logger = Mock()
    monkeypatch.setattr(main, "logger", logger)
    main._start_yak_mitm(tmp_path, host="[::1]", port=9015, internal_token="new-private-token")
    assert popen.call_args.args == (["yak.exe", str(tmp_path / "yak_mitm.yak")],)
    child = popen.call_args.kwargs["env"]
    assert child["MAA_MITM_INTERNAL_TOKEN"] == "new-private-token"
    assert child["MITM_RECEIVER_HOST"] == "::1" and child["MITM_RECEIVER_PORT"] == "9015"
    assert child["NO_COLOR"] == "1" and child["TERM"] == "dumb"
    assert child["CLICOLOR"] == "0" and child["CLICOLOR_FORCE"] == "0"
    assert os.environ["MAA_MITM_INTERNAL_TOKEN"] == "inherited-token"
    assert "new-private-token" not in repr(logger.mock_calls)
    assert main.finish_process_log(popen.return_value)


@pytest.mark.parametrize("reachable", [False, True])
def test_yak_early_exit_is_not_readiness_even_when_port_is_open(monkeypatch, tmp_path, reachable):
    (tmp_path / "yak_mitm.yak").write_text("// test script", encoding="utf-8")
    monkeypatch.setattr(main, "_wait_for_port", lambda *args, **kwargs: reachable)
    process = SimpleNamespace(pid=123, stdout=BytesIO(), poll=lambda: 17)
    monkeypatch.setattr(main.subprocess, "Popen", Mock(return_value=process))
    logger = Mock()
    monkeypatch.setattr(main, "logger", logger)
    assert main._start_yak_mitm(tmp_path, host="127.0.0.1", port=8085, internal_token="private-token") is None
    assert "exited during startup" in logger.error.call_args.args[0]
    logger.info.assert_not_called()
    assert process.diagnostic_log.finished.is_set()


def test_yak_cannot_spawn_without_private_token(monkeypatch, tmp_path):
    popen = Mock()
    monkeypatch.setattr(main.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="MAA_MITM_INTERNAL_TOKEN"):
        main._start_yak_mitm(tmp_path, host="127.0.0.1", port=8085, internal_token="")
    popen.assert_not_called()
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("failures", [
    {"outbox"}, {"queue"}, {"sync"}, {"maafw"}, {"yak"}, {"receiver"},
    {"outbox", "queue", "sync", "maafw", "yak", "receiver"},
])
def test_cleanup_attempts_all_services_including_receiver(monkeypatch, startup, failures):
    calls = []

    def cleanup(name):
        def run(*args, **kwargs):
            calls.append(name)
            if name in failures:
                raise RuntimeError(f"{name} cleanup failed")
        return run

    startup["stop_outbox_service"].side_effect = cleanup("outbox")
    monkeypatch.setattr("backend.app.task_queue.shutdown_task_queue", cleanup("queue"))
    startup["stop_sync_service"].side_effect = cleanup("sync")
    startup["MaaFWProcess"].return_value.stop.side_effect = cleanup("maafw")
    yak = startup["_start_yak_mitm"].return_value
    yak.poll.return_value = None
    yak.terminate.side_effect = cleanup("yak")
    startup["_start_mitm_receiver"].return_value.shutdown.side_effect = cleanup("receiver")
    startup["run_api"].side_effect = RuntimeError("original API failure")
    logger = Mock()
    monkeypatch.setattr(main, "logger", logger)
    with pytest.raises(RuntimeError, match="original API failure"):
        main.main()
    assert calls == ["outbox", "queue", "sync", "maafw", "yak", "receiver"]
    assert logger.exception.call_count == len(failures)
