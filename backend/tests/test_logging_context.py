import asyncio
import io
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from backend.app.shared.utils import logging as transport
from backend.app.shared.utils.log_context import bind_log_context, capture_log_context, log_event


@pytest.fixture
def logs(monkeypatch, tmp_path):
    stderr = io.StringIO()
    monkeypatch.setattr(transport.sys, "stderr", stderr)
    monkeypatch.setattr(transport, "resolve_backend_root", lambda: tmp_path)
    monkeypatch.setattr(transport, "_CONFIGURED", False)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    transport.configure_logging()
    records = []
    logger.add(lambda message: records.append(json.loads(str(message))),
               format=lambda record: "{extra[_safe_json]}\n", diagnose=False, backtrace=False)
    try:
        yield records, stderr, tmp_path / "data" / "logs"
    finally:
        logger.complete()
        logger.remove()
        logger.configure(patcher=None, extra={})
        logger.add(sys.__stderr__, diagnose=False, backtrace=False)
        root.handlers, root.level = handlers, level


def test_file_stderr_export_keep_events_without_payloads_or_exception_values(logs):
    records, stderr, directory = logs
    secret = "EXCEPTION_CANARY_PRIVATE_MESSAGE"
    with bind_log_context(request_id="req-one", account_epoch="epoch-one", password="HIDDEN"):
        log_event("outbox.started", outbox_id="outbox-one", content="BODY_CANARY", token="TOKEN_CANARY")
        try:
            raise ValueError(secret)
        except ValueError:
            logger.exception("Outbox operation failed")
        logging.getLogger("httpx").info("HTTP GET https://private.example/buyer?token=URL_CANARY")
        logger.info("Credentials authorization=Bearer AUTH_CANARY")
    logger.complete()
    file_text = (directory / "api.log").read_text(encoding="utf-8")
    for output in (file_text, stderr.getvalue(), *[transport.sanitize_diagnostic_record(line)
                                                for line in file_text.splitlines()]):
        assert output
        for value in (secret, "BODY_CANARY", "TOKEN_CANARY", "URL_CANARY", "AUTH_CANARY", "HIDDEN"):
            assert value not in output
    assert "\x1b" not in stderr.getvalue() and "\x1b" not in file_text
    assert records[0]["context"]["request_id"] == "req-one"
    assert records[0]["context"]["outbox_id"] == "outbox-one"
    failure = records[1]["exception"]
    assert failure["type"] == "ValueError"
    assert failure["frames"][-1]["function"] == "test_file_stderr_export_keep_events_without_payloads_or_exception_values"
    assert all(set(frame) == {"file", "function", "line"} for frame in failure["frames"])
    assert capture_log_context()["request_id"] is None


@pytest.mark.parametrize("line", [
    "body from native OCR containing PRIVATE",
    "  File \"C:/private/user.py\", line 4",
    "ValueError: PRIVATE", '{"message":"PRIVATE"}',
    '{"schema":1,"context":[],"exception":{"type":"ValueError","frames":null}}',
])
def test_export_untrusted_continuations_and_malformed_fields_do_not_leak(line):
    result = transport.sanitize_diagnostic_record(line, "native")
    assert result is None or "PRIVATE" not in result


def test_legacy_export_retains_location_without_unlabeled_business_prose():
    line = "2026-09-20 12:00:00.123 | WARNING | backend.worker:run:14 - Translation omitted text='PRIVATE text'"
    result = json.loads(transport.sanitize_diagnostic_record(line))
    assert result["event"] == "legacy.message" and result["level"] == "WARNING"
    assert (result["logger"], result["function"], result["line"]) == ("backend.worker", "run", 14)
    assert "PRIVATE" not in json.dumps(result)
    assert "PRIVATE_UNLABELED" not in transport.sanitize_diagnostic_record(json.dumps({
        "schema": 1, "event": "application.message", "message": "PRIVATE_UNLABELED",
    }))


def test_async_scopes_restore_after_failure_and_do_not_share_mutable_snapshots():
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def first():
            with bind_log_context(request_id="first"):
                snapshot = capture_log_context()
                snapshot["request_id"] = "mutated"
                entered.set()
                await release.wait()
                assert capture_log_context()["request_id"] == "first"
                raise RuntimeError("expected")

        task = asyncio.create_task(first())
        await entered.wait()
        with bind_log_context(request_id="second"):
            release.set()
            with pytest.raises(RuntimeError):
                await task
            assert capture_log_context()["request_id"] == "second"
        assert capture_log_context()["request_id"] is None

    asyncio.run(run())


@pytest.mark.parametrize("source,line,event", [
    ("native", "[2026-09-20 12:00:00.123][ERR] OCR=PRIVATE_CUSTOMER_BODY", "native.record"),
    ("maafw_cli", "[2026-09-20 12:00:00][INF] text=PRIVATE_CUSTOMER_BODY", "native.record"),
    ("yak", "[ERROR] 12:00:00 PRIVATE_CUSTOMER_BODY", "yak.record"),
    ("yak", "[!] payload error: PRIVATE_CUSTOMER_BODY", "yak.payload_failed"),
])
def test_native_and_child_output_retains_only_recognized_metadata(source, line, event):
    result = transport.sanitize_diagnostic_record(line, source)
    assert json.loads(result)["event"] == event
    assert "PRIVATE" not in result


@pytest.mark.parametrize("color", ["\x1b[31m", "\x1b[1m\x1b[38;2;255;0;0m"])
def test_colored_native_error_survives_collection_and_export_without_payloads(tmp_path, color):
    from backend.app.shared.utils.process_logs import ProcessLogDrain
    from backend.app.diagnostics import _metadata

    line = (color + "[2026-09-20 12:34:56.123][ERR][Px123][Tx456]"
            r"[C:\PRIVATE_DIRECTORY\Tasker.cpp:42][Tasker::run][handle=0x987][task_id=7]"
            " [entry=ChatInput_SendOnly] job_id=8 timeout: PRIVATE_BODY token=PRIVATE_TOKEN"
            "\x1b[0m\r\n")
    path = tmp_path / "cli.log"
    drain = ProcessLogDrain(io.BytesIO(line.encode()), path, source="maafw_cli").start()
    assert drain.join(3)
    assert drain.dropped_records == 0 and drain.error_type is None
    collected = path.read_text(encoding="utf-8")
    record = json.loads(collected)
    expected = {"pid": 0x123, "tid": 0x456, "source_file": "Tasker.cpp", "line": 42,
                "task_id": 7, "job_id": 8, "entry": "ChatInput_SendOnly", "error_category": "timeout"}
    assert record["level"] == "ERR" and record["source"] == "maafw_cli"
    assert record["context"] == expected
    assert json.loads(transport.sanitize_diagnostic_record(collected, "native"))["context"] == expected
    assert json.loads(_metadata(collected, "native"))["context"] == expected
    assert "PRIVATE" not in collected and "\\u001b" not in collected and "\x1b" not in collected


@pytest.mark.parametrize("prefix", ["\x1b[31m" * 33, "\x1b[" + "1" * 65 + "m", "\x1b]0;PRIVATE\x07", "\x1b[2J"])
def test_native_color_stripping_rejects_excessive_or_non_sgr_controls(prefix):
    line = prefix + "[2026-09-20 12:34:56.123][ERR][Px123] PRIVATE"
    assert transport.sanitize_diagnostic_record(line, "maafw_cli") is None


@pytest.mark.parametrize("tail", [
    'OCR PRIVATE text task_id=999 timeout',
    '[PRIVATE_BODY][task_id=999] timeout',
    '{"task_id":999,"message":"timeout PRIVATE"}',
])
def test_native_metadata_parser_stops_before_ocr_and_payload_fields(tail):
    line = "\x1b[31m[2026-09-20 12:34:56.123][ERR][Px123] " + tail + "\x1b[0m"
    record = json.loads(transport.sanitize_diagnostic_record(line, "native"))
    assert record["context"] == {"pid": 0x123}
    assert "PRIVATE" not in json.dumps(record)


def test_startup_event_retains_caller_and_typed_process_fields(logs):
    with bind_log_context(origin_request_id="request-origin", native_log_session_id="session-123"):
        log_event("process.started", component="maafw_cli", phase="startup", pid=123,
                  port=8084, exit_code=-1, tcp_reachable=False, body="PRIVATE_BODY")
    logger.info("MITM receiver thread started on {}:{}", "127.0.0.1", 8084)
    record = logs[0][0]
    assert record["logger"] == __name__ and record["file"] == "test_logging_context.py"
    assert record["function"] == "test_startup_event_retains_caller_and_typed_process_fields"
    assert record["message"] == record["event"] == "process.started"
    expected = {"component": "maafw_cli", "phase": "startup", "pid": 123, "port": 8084,
                "exit_code": -1, "tcp_reachable": False, "origin_request_id": "request-origin",
                "native_log_session_id": "session-123"}
    assert expected.items() <= record["context"].items()
    exported = json.loads(transport.sanitize_diagnostic_record(json.dumps(record)))
    assert expected.items() <= exported["context"].items()
    assert logs[0][1]["message"] == "MITM receiver thread started"
    assert "PRIVATE" not in json.dumps(logs[0])
    assert transport.safe_fields({"pid": True, "exit_code": "PRIVATE", "port": 65536,
                                  "tcp_reachable": "PRIVATE", "error_category": "PRIVATE"}) == {}


def test_card_sweep_and_mitm_fields_survive_the_export_allowlist():
    fields = transport.safe_fields({
        "sid": 12, "key_kind": "ali_id", "unresolved": 2, "resolved": 1, "navigated": True,
        "body_bytes": 4096, "count": 1, "targets": 3, "visited": 3, "enriched": 1, "failed": 2,
        "eligible": 13, "backoff": 2, "seen": 5, "matched": 2, "private": "PRIVATE",
    })
    assert fields == {"sid": 12, "key_kind": "ali_id", "unresolved": 2, "resolved": 1, "navigated": True,
                      "body_bytes": 4096, "count": 1, "targets": 3, "visited": 3, "enriched": 1,
                      "failed": 2, "eligible": 13, "backoff": 2, "seen": 5, "matched": 2}


def test_audited_operation_messages_survive_export_and_free_form_does_not():
    audited = json.dumps({"schema": 1, "time": "2026-09-20T12:00:00", "level": "INFO",
                          "event": "application.message", "message": "SelfInfo parsed for selected account",
                          "context": {}})
    assert json.loads(transport.sanitize_diagnostic_record(audited))["message"] == "SelfInfo parsed for selected account"
    free_form = json.dumps({"schema": 1, "time": "2026-09-20T12:00:00", "level": "INFO",
                            "event": "application.message", "message": "buyer said PRIVATE",
                            "context": {}})
    assert "message" not in json.loads(transport.sanitize_diagnostic_record(free_form))


def test_yak_traffic_tick_exports_only_bounded_counts():
    record = json.loads(transport.sanitize_diagnostic_record(
        "[*] Yak MITM tick seen=17 matched=3 PRIVATE trailing body", "yak"))
    assert record == {"schema": 1, "source": "yak", "event": "yak.traffic_tick",
                      "context": {"seen": 17, "matched": 3}}


def test_file_rotation_and_retention_keep_a_bounded_number_of_parseable_files(monkeypatch, logs):
    _, _, directory = logs
    assert transport.LOG_MAX_BYTES == 10 * 1024 * 1024 and transport.LOG_ARCHIVES == 5
    # Exercise the real policy under a smaller byte budget.
    monkeypatch.setattr(transport, "LOG_MAX_BYTES", 4096)
    monkeypatch.setattr(transport, "LOG_ARCHIVE_BYTES", 5 * 4096)
    for index in range(100):
        log_event("operation.completed", count=index)
    logger.complete()
    files = list(directory.glob("api*.log"))
    assert len(files) == 6
    assert all(path.stat().st_size <= 4096 for path in files)
    assert all(json.loads(line)["schema"] == 1 for path in files for line in path.read_text().splitlines())
    assert json.loads((directory / "api.log").read_text().splitlines()[-1])["context"]["count"] == 99


def test_archive_age_and_aggregate_budget_preserve_active_and_unrelated_files(monkeypatch, tmp_path):
    now = 1_000_000
    monkeypatch.setattr(transport.time, "time", lambda: now)
    monkeypatch.setattr(transport, "LOG_MAX_BYTES", 100)
    monkeypatch.setattr(transport, "LOG_ARCHIVE_BYTES", 180)
    assert transport.LOG_MAX_AGE_S == 7 * 24 * 60 * 60
    files = []
    for name, size, age in (("api.1.log", 90, 1), ("api.2.log", 90, 2), ("api.3.log", 90, 3),
                            ("api.4.log", 10, 8 * 24 * 60 * 60), ("api.5.log", 101, 0),
                            ("api.log", 200, 8 * 24 * 60 * 60), ("unrelated.log", 200, 8 * 24 * 60 * 60)):
        path = tmp_path / name
        path.write_bytes(b"x" * size)
        os.utime(path, (now - age, now - age))
        files.append(str(path))
    transport._retain_logs(files)
    assert {path.name for path in tmp_path.iterdir()} == {"api.1.log", "api.2.log", "api.log", "unrelated.log"}


def test_small_active_file_rotates_daily(monkeypatch, tmp_path):
    clock = [1_000_000]
    monkeypatch.setattr(transport.time, "time", lambda: clock[0])
    path = tmp_path / "api.log"
    path.write_text("previous")
    os.utime(path, (clock[0], clock[0]))
    rotate = transport._log_rotation()
    with path.open("a", encoding="utf-8") as file:
        assert not rotate("next", file)
        clock[0] += 24 * 60 * 60
        assert rotate("next", file)


def test_configuration_prunes_expired_owned_archives_before_first_write(monkeypatch, logs):
    _, _, directory = logs
    logger.remove()
    expired = directory / "api.2000-01-01.log"
    other_writer = directory / "agent.2000-01-01.log"
    for path in (expired, other_writer):
        path.write_text("old")
        os.utime(path, (1, 1))
    monkeypatch.setattr(transport, "_CONFIGURED", False)
    transport.configure_logging()
    assert not expired.exists()
    assert other_writer.read_text() == "old"


def test_runtime_omits_unlabeled_interpolations_and_preserves_stdlib_origin(logs):
    class SecretArgument:
        def __str__(self):
            raise AssertionError("stdlib argument must not be formatted")

    logger.info("Unrecognized payload {}", "PRIVATE_UNLABELED")
    logger.warning("Outbox terminal write deferred for {}", "PRIVATE_ID")
    # Pytest installs an additional raw logging handler after fixture setup;
    # exercise our transport directly so its safety isn't delegated to pytest.
    transport._InterceptHandler().emit(logging.LogRecord(
        "vendor.client", logging.ERROR, __file__, 1, "response %s", (SecretArgument(),), None, "dispatch",
    ))
    record = logging.LogRecord("vendor.client", logging.WARNING, "/private/client/transport.py", 123,
                               "PRIVATE_THIRDPARTY", (), None, "dispatch")
    transport._InterceptHandler().emit(record)
    with bind_log_context(native_log_session_id="session-123"):
        log_event("maa.submitted", native_job_id=42)
    logging.getLogger("uvicorn.error").info("Application startup complete.")
    records, stderr, directory = logs
    logger.complete()
    assert records[0]["message"] == "Log message omitted"
    assert records[1]["message"] == "Outbox terminal write deferred"
    assert {key: records[3][key] for key in ("logger", "module", "file", "function", "line")} == {
        "logger": "vendor.client", "module": "transport", "file": "transport.py", "function": "dispatch", "line": 123,
    }
    assert records[4]["context"]["native_log_session_id"] == "session-123"
    assert records[5]["message"] == "Application startup complete."
    exported = transport.sanitize_diagnostic_record(json.dumps(records[3]))
    assert json.loads(exported)["file"] == "transport.py"
    assert json.loads(transport.sanitize_diagnostic_record(json.dumps(records[4])))["context"]["native_log_session_id"] == "session-123"
    for output in (stderr.getvalue(), (directory / "api.log").read_text(), json.dumps(records), exported):
        assert "PRIVATE" not in output and "/private" not in output


@pytest.mark.parametrize("relation", ["cause", "context", "suppressed"])
def test_exception_chains_keep_types_and_frames_without_values(logs, relation):
    try:
        try:
            raise ValueError("PRIVATE_INNER")
        except ValueError as cause:
            if relation == "cause":
                raise RuntimeError("PRIVATE_OUTER") from cause
            if relation == "suppressed":
                raise RuntimeError("PRIVATE_OUTER") from None
            raise RuntimeError("PRIVATE_OUTER")
    except RuntimeError:
        logger.exception("Outbox operation failed")
    exception = logs[0][-1]["exception"]
    assert exception["type"] == "RuntimeError"
    if relation == "suppressed":
        assert "chain" not in exception
    else:
        cause = exception["chain"][0]
        assert cause["type"] == "ValueError" and cause["relation"] == relation and cause["frames"]
    assert "PRIVATE" not in json.dumps(logs[0])
    exported = json.loads(transport.sanitize_diagnostic_record(json.dumps(logs[0][-1])))
    assert exported["exception"] == exception


@pytest.mark.parametrize("cycle", [False, True])
def test_exception_chain_limits_and_cycles_are_bounded(logs, cycle):
    def raise_nested(depth):
        if depth:
            raise_nested(depth - 1)
        else:
            raise ValueError("PRIVATE")

    errors = []
    for _ in range(12):
        try:
            raise_nested(8)
        except ValueError as exc:
            errors.append(exc)
    for outer, inner in zip(errors, errors[1:]):
        outer.__cause__ = inner
    if cycle:
        errors[2].__cause__ = errors[0]
    try:
        raise errors[0]
    except ValueError:
        logger.exception("Outbox operation failed")
    exception = logs[0][-1]["exception"]
    chain = [exception, *exception["chain"]]
    assert len(chain) == (3 if cycle else 8)
    assert chain[-1]["truncated"]
    assert sum(len(item["frames"]) for item in chain) <= 32
    if not cycle:
        assert sum(len(item["frames"]) for item in chain) == 32
    assert "PRIVATE" not in json.dumps(exception)


def test_patcher_failure_is_constant_safe_output_and_never_interrupts_caller(monkeypatch, logs):
    def fail(exception):
        raise RuntimeError("PRIVATE_SANITIZER_FAILURE")

    monkeypatch.setattr(transport, "_safe_exception", fail)
    try:
        raise ValueError("PRIVATE_ORIGINAL_EXCEPTION")
    except ValueError:
        logger.bind(payload="PRIVATE_EXTRA").exception("PRIVATE_MESSAGE")
    logger.complete()
    records, stderr, directory = logs
    assert records[-1] == json.loads(transport._SANITIZATION_FAILURE)
    for output in (stderr.getvalue(), (directory / "api.log").read_text()):
        assert "logging.sanitization_failed" in output and "PRIVATE" not in output


def test_queue_observations_do_not_initialize_and_retain_progress_after_history_eviction(monkeypatch):
    from backend.app import task_queue
    from backend.app.task_queue import TaskQueue, observe_task_queues, TASK_RETENTION_S

    assert not TaskQueue._instances
    observed = observe_task_queues()
    assert set(observed) == {"maafw", "translation"}
    assert all(not item["initialized"] and not item["alive"] for item in observed.values())
    assert not TaskQueue._instances
    first_started, first_release, second_started, second_release = (Event() for _ in range(4))
    finished = Event()
    original_event = task_queue.log_event

    def completion_event(event, **fields):
        original_event(event, **fields)
        if event == "queue.succeeded":
            finished.set()

    monkeypatch.setattr(task_queue, "log_event", completion_event)
    queue = TaskQueue("observations")

    def first():
        first_started.set()
        assert first_release.wait(5)
        raise RuntimeError("expected failure")

    def second():
        second_started.set()
        assert second_release.wait(5)
        return True, "completed"

    try:
        first_task = queue.enqueue(first, description="first")
        assert first_started.wait(5)
        second_task = queue.enqueue(second, description="second")
        running = TaskQueue.observations("observations")
        assert running == {"initialized": True, "alive": True, "pending": 1,
                           "current_started": queue.get(first_task.task_id).started_at, "last_completed": None}
        first_release.set()
        assert second_started.wait(5)
        completed_at = queue.get(first_task.task_id).completed_at
        with queue._condition:
            queue._evict_locked(completed_at + TASK_RETENTION_S + 1)
        assert queue.get(first_task.task_id) is None
        running = observe_task_queues()["observations"]
        assert running["pending"] == 0 and running["last_completed"] == completed_at
        assert running["current_started"] == queue.get(second_task.task_id).started_at
        second_release.set()
        assert finished.wait(5)
        idle = TaskQueue.observations("observations")
        assert idle["alive"] and idle["current_started"] is None and idle["pending"] == 0
        assert idle["last_completed"] == queue.get(second_task.task_id).completed_at
    finally:
        first_release.set()
        second_release.set()
        queue.shutdown()
    assert TaskQueue.observations("observations")["alive"] is False


def test_queue_snapshots_survive_caller_change_and_worker_failure(logs):
    from backend.app.task_queue import TaskQueue, TaskStatus

    queue = TaskQueue("correlation-test")
    entered, release = Event(), Event()
    contexts = []

    def first():
        entered.set()
        assert release.wait(5)
        contexts.append(capture_log_context())
        raise RuntimeError("job failed")

    def second():
        contexts.append(capture_log_context())
        return True, "complete"

    try:
        with bind_log_context(request_id="request-a", account_epoch="epoch-a"):
            one = queue.enqueue(first, description="PRIVATE_DESCRIPTION")
        assert entered.wait(5)
        with bind_log_context(request_id="request-b", account_epoch="epoch-b"):
            two = queue.enqueue(second, description="PRIVATE_DESCRIPTION")
        release.set()
        queue.shutdown()
        assert queue.get(one.task_id).status == TaskStatus.FAILED
        assert queue.get(two.task_id).status == TaskStatus.SUCCEEDED
        assert [(c["request_id"], c["account_epoch"], c["queue_task_id"]) for c in contexts] == [
            ("request-a", "epoch-a", one.task_id), ("request-b", "epoch-b", two.task_id),
        ]
        events = [r for r in logs[0] if r["event"].startswith("queue.")]
        assert {r["event"] for r in events} == {"queue.enqueued", "queue.started", "queue.failed", "queue.succeeded"}
        assert "PRIVATE_DESCRIPTION" not in json.dumps(logs[0])
    finally:
        release.set()
        queue.shutdown()


def test_auth_access_uses_template_and_same_request_context_on_success_and_rejection(logs, tmp_path):
    from backend.app.api.auth import AuthConfig, AuthMiddleware, LoginLimiter, SessionStore

    app = FastAPI()
    store = SessionStore(AuthConfig(b"a" * 32, tmp_path / "auth.sqlite"))
    token = store.issue()

    @app.get("/api/probe/{customer}")
    def probe(customer: str):
        log_event("probe.executed")
        return capture_log_context()

    app.add_middleware(AuthMiddleware, store=store, limiter=LoginLimiter())
    with TestClient(app) as client:
        response = client.get("/api/probe/PRIVATE_ID?token=PRIVATE_QUERY", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        rejected = client.get("/api/probe/PRIVATE_ID?token=PRIVATE_QUERY")
        assert rejected.status_code == 401
    # A successful fast read emits no access record; its request context is
    # observable through the route's own event.
    executed = [r for r in logs[0] if r["event"] == "probe.executed"]
    assert executed[0]["context"]["request_id"] == response.headers["X-Request-ID"]
    events = [r for r in logs[0] if r["event"] == "http.access"]
    assert [r["context"]["route"] for r in events] == ["unmatched"]
    assert events[0]["context"]["status"] == 401
    assert events[0]["context"]["request_id"] == rejected.headers["X-Request-ID"]
    assert [r for r in logs[0] if r["event"] == "http.response"] == []
    assert "PRIVATE" not in json.dumps(logs[0])
    assert token not in json.dumps(logs[0])


def test_http_access_records_writes_and_errors_but_not_fast_reads(logs, tmp_path):
    from backend.app.api.auth import AuthConfig, AuthMiddleware, LoginLimiter, SessionStore

    app = FastAPI()
    store = SessionStore(AuthConfig(b"a" * 32, tmp_path / "auth.sqlite"))
    token = store.issue()

    @app.get("/api/probe")
    def probe():
        return {"ok": True}

    @app.post("/api/probe")
    def write():
        return {"ok": True}

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("expected failure")

    app.add_middleware(AuthMiddleware, store=store, limiter=LoginLimiter())
    auth_module = transport.sys.modules["backend.app.api.auth"]
    before = auth_module.http_observations()
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/probe", headers=headers).status_code == 200
        assert client.post("/api/probe", headers=headers).status_code == 200
        assert client.get("/api/boom", headers=headers).status_code == 500
    accesses = [r for r in logs[0] if r["event"] == "http.access"]
    assert sorted((r["context"]["method"], r["context"]["status"]) for r in accesses) == [("GET", 500), ("POST", 200)]
    assert [r for r in logs[0] if r["event"] == "http.response"] == []
    counters = auth_module.http_observations()
    delta = tuple(counters[key] - before[key] for key in ("requests", "reads", "writes", "errors"))
    assert delta == (3, 2, 1, 1)


def test_slow_successful_reads_are_recorded(logs, tmp_path, monkeypatch):
    from backend.app.api import auth as auth_module
    from backend.app.api.auth import AuthConfig, AuthMiddleware, LoginLimiter, SessionStore

    monkeypatch.setattr(auth_module, "SLOW_REQUEST_MS", 0.0)
    app = FastAPI()
    store = SessionStore(AuthConfig(b"a" * 32, tmp_path / "auth.sqlite"))
    token = store.issue()

    @app.get("/api/probe")
    def probe():
        return {"ok": True}

    app.add_middleware(AuthMiddleware, store=store, limiter=LoginLimiter())
    before = auth_module.http_observations()
    with TestClient(app) as client:
        assert client.get("/api/probe", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    accesses = [r for r in logs[0] if r["event"] == "http.access"]
    assert [(r["context"]["method"], r["context"]["status"]) for r in accesses] == [("GET", 200)]
    assert accesses[0]["context"]["duration_ms"] >= 0
    assert auth_module.http_observations()["slow"] - before["slow"] == 1


@pytest.mark.parametrize("fails", [False, True])
def test_crm_executor_and_completion_callback_retain_submission_context(monkeypatch, logs, fails):
    from backend.app.shared.crm import sync

    entered, release, completed = Event(), Event(), Event()
    observed = []
    original = sync._log_sync_failure

    def work():
        entered.set()
        assert release.wait(5)
        observed.append(capture_log_context())
        if fails:
            raise ValueError("PRIVATE_CRM_PAYLOAD")
        return {"revision": 3}

    def callback(future, duration_ms=None):
        observed.append(capture_log_context())
        original(future, duration_ms)
        completed.set()

    monkeypatch.setattr(sync, "_log_sync_failure", callback)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(sync, "_SYNC_EXECUTOR", pool)
        try:
            with bind_log_context(request_id="crm-request", account_epoch="old-epoch"):
                future = sync._submit_sync(work)
            assert entered.wait(5)
            with bind_log_context(request_id="later-request", account_epoch="new-epoch"):
                release.set()
                assert completed.wait(5)
            assert len(observed) == 2
            assert all(c["request_id"] == "crm-request" and c["account_epoch"] == "old-epoch" for c in observed)
            assert observed[0]["sync_id"] == observed[1]["sync_id"]
            assert (future.exception() is not None) is fails
            assert "PRIVATE_CRM_PAYLOAD" not in json.dumps(logs[0])
        finally:
            release.set()


def test_account_context_change_emits_new_epoch_event(logs):
    from backend.app.shared.backend import account_context

    previous = account_context.get_account_context().epoch
    account_context.invalidate_account_context()
    events = [r for r in logs[0] if r["event"] == "account.changed"]
    assert len(events) == 1
    current = account_context.get_account_context()
    assert events[0]["context"]["account_epoch"] == current.epoch != previous
    assert "PRIVATE" not in json.dumps(events[0])


def test_failure_report_due_reports_first_changed_and_tenth_attempts():
    from backend.app.shared.utils.log_context import failure_report_due

    assert failure_report_due(1)
    assert not failure_report_due(2)
    assert not failure_report_due(9)
    assert failure_report_due(10)
    assert failure_report_due(5, changed=True)


def test_application_log_call_sites_are_audited_against_the_allowlist():
    import ast
    from pathlib import Path

    from backend.app.shared.utils.logging import _RUNTIME_MESSAGES

    def logger_base(node):
        while isinstance(node, (ast.Attribute, ast.Call)):
            node = node.value if isinstance(node, ast.Attribute) else node.func
        return getattr(node, "id", "")

    levels = {"info", "success", "warning", "error", "exception", "critical"}
    uncovered, dead = [], []
    # The real source tree, not resolve_repo_root(): the isolation fixture
    # repoints that constant at an empty temp root and silently disables this audit.
    source_root = Path(__file__).resolve().parents[1] / "app"
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            level = node.func.attr
            if logger_base(node.func) != "logger" or level not in levels | {"debug", "trace"} or not node.args:
                continue
            if level in {"debug", "trace"}:
                dead.append(f"{path.name}:{node.lineno}")
                continue
            argument = node.args[0]
            if isinstance(argument, ast.JoinedStr):
                head = next((part.value for part in argument.values
                             if isinstance(part, ast.Constant) and isinstance(part.value, str)), "")
            elif isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                head = argument.value
            else:
                continue
            if head and not any(head.startswith(message) for message in _RUNTIME_MESSAGES):
                uncovered.append(f"{path.name}:{node.lineno}: {head!r}")
    # The INFO sink makes debug/trace calls unreachable; new messages must be
    # either allowlisted or emitted as a structured log_event instead.
    assert not dead, f"unreachable debug logging: {dead}"
    assert not uncovered, f"messages missing from _RUNTIME_MESSAGES: {uncovered}"
