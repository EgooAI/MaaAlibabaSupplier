import io
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app import maafw_process
from backend.app.shared.utils import logging, process_logs


def record(message):
    return json.dumps({"schema": 1, "level": "INFO", "event": "child.status", "message": message}).encode() + b"\n"


def test_drain_discards_whole_oversized_lines_and_continues_sanitizing(tmp_path):
    # The secret tail of an oversized line must not become a fresh record.
    raw = b"x" * (process_logs.MAX_LINE_BYTES * 20) + record("secret tail")
    raw += b"unrecognized customer body and credential\n" + record("token=private-token") + record("completed")
    path = tmp_path / "child.log"
    drain = process_logs.ProcessLogDrain(io.BytesIO(raw), path, source="yak").start()
    assert drain.join(3)
    output = path.read_text(encoding="utf-8")
    assert "private-token" not in output and "secret tail" not in output and "customer body" not in output
    assert "completed" in output
    assert all(json.loads(line)["source"] == "yak" for line in output.splitlines())
    assert drain.dropped_records >= 2 and drain.error_type is None


@pytest.mark.parametrize("failure", ["disk", "sanitizer"])
def test_pipe_is_continuously_drained_even_when_capture_fails(monkeypatch, tmp_path, failure):
    def fail(*args, **kwargs):
        raise OSError("sensitive exception value")

    if failure == "disk":
        monkeypatch.setattr(process_logs.RotatingDiagnosticFile, "write", fail)
    else:
        monkeypatch.setattr(logging, "sanitize_diagnostic_record", fail)
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    writer = os.fdopen(write_fd, "wb", buffering=0)
    completed = threading.Event()
    payload = record("completed") * 10000

    def feed():
        try:
            view = memoryview(payload)
            while view:
                view = view[writer.write(view):]
            completed.set()
        finally:
            writer.close()

    drain = process_logs.ProcessLogDrain(reader, tmp_path / "child.log", source="maafw_cli").start()
    producer = threading.Thread(target=feed, daemon=True)
    producer.start()
    try:
        assert completed.wait(5), "output larger than a pipe buffer must not block its producer"
        assert drain.join(5)
        assert drain.dropped_records == 10000
        assert drain.error_type == "OSError"
        assert "sensitive" not in repr(drain.status())
    finally:
        producer.join(1)


def test_rotation_caps_bytes_and_archives_and_expires_only_known_files(monkeypatch, tmp_path):
    monkeypatch.setattr(process_logs.time, "time", lambda: 1_000_000)
    path = tmp_path / "child.log"
    unrelated = tmp_path / "other.log"
    unrelated.write_text("retain")
    old = tmp_path / "child.log.2"
    old.write_text("expired")
    os.utime(old, (1, 1))
    oversized = tmp_path / "child.log.3"
    oversized.write_text("legacy unbounded output" * 100)
    writer = process_logs.RotatingDiagnosticFile(path, max_bytes=100, archives=5)
    writer.write("begin")
    assert not old.exists()
    assert not oversized.exists()
    for index in range(20):
        writer.write(f"{index}:" + "x" * 60)
    files = [path for path in tmp_path.glob("child.log*") if path.suffix != ".age"]
    assert len(files) == 6
    assert all(file.stat().st_size <= 100 for file in files)
    assert path.read_text().startswith("19:")
    assert (tmp_path / "child.log.5").read_text().startswith("14:")
    assert unrelated.read_text() == "retain"
    writer.write("oversized" * 100)
    assert path.stat().st_size <= 100


@pytest.fixture
def log_clock(monkeypatch):
    clock = SimpleNamespace(wall=1_000_000.0, elapsed=0.0)
    monkeypatch.setattr(process_logs.time, "time", lambda: clock.wall + clock.elapsed)
    monkeypatch.setattr(process_logs.time, "monotonic", lambda: clock.elapsed)
    return clock


def test_small_appends_rotate_daily_and_expire_from_first_record(log_clock, tmp_path):
    path = tmp_path / "child.log"
    # Keep enough archives to distinguish age expiry from archive-count eviction.
    writer = process_logs.RotatingDiagnosticFile(path, archives=10)
    writer.write("oldest")
    for half_day in range(1, 14):
        log_clock.elapsed = half_day * 43200
        writer.write(f"small append {half_day}")
        os.utime(path, (log_clock.wall + log_clock.elapsed,) * 2)
    assert (tmp_path / "child.log.6").read_text() == "oldest\nsmall append 1\n"
    assert "oldest" not in path.read_text()
    # A recent archive mtime and a prune less than a minute ago cannot extend age.
    oldest = tmp_path / "child.log.6"
    log_clock.elapsed = process_logs.LOG_MAX_AGE_S - 30
    os.utime(oldest, (log_clock.wall + log_clock.elapsed,) * 2)
    writer.write("just before expiry")
    assert "oldest" in oldest.read_text()
    log_clock.elapsed += 30
    writer.write("new day")
    logs = [file for file in tmp_path.glob("child.log*") if file.suffix != ".age"]
    assert all("oldest" not in file.read_text() for file in logs)
    assert path.read_text() == "new day\n"


def test_restart_keeps_original_rotation_and_archive_expiry_epoch(log_clock, tmp_path):
    path = tmp_path / "child.log"
    process_logs.RotatingDiagnosticFile(path).write("before restart")
    log_clock.elapsed = 18 * 3600
    restarted = process_logs.RotatingDiagnosticFile(path)
    restarted.write("after restart")
    log_clock.elapsed = 24 * 3600
    restarted.write("next generation")
    archive = tmp_path / "child.log.1"
    assert archive.read_text() == "before restart\nafter restart\n"
    assert path.read_text() == "next generation\n"
    log_clock.elapsed = process_logs.LOG_MAX_AGE_S
    os.utime(archive, (log_clock.wall + log_clock.elapsed,) * 2)
    process_logs.RotatingDiagnosticFile(path).write("another restart")
    assert archive.read_text() == "next generation\n"
    assert path.read_text() == "another restart\n"
    assert all("before restart" not in file.read_text() for file in tmp_path.glob("child.log*"))


@pytest.mark.parametrize("filename", ["child.log", "child.log.1"])
def test_legacy_log_age_uses_creation_not_recent_append(monkeypatch, log_clock, tmp_path, filename):
    legacy = tmp_path / filename
    legacy.write_text("old legacy content")
    os.utime(legacy, (log_clock.wall,) * 2)
    original = Path.stat

    def stat_with_old_birth(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == legacy:
            return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size, st_mtime=info.st_mtime,
                                   st_ctime=info.st_ctime, st_birthtime=log_clock.wall - 8 * 86400)
        return info

    monkeypatch.setattr(Path, "stat", stat_with_old_birth)
    process_logs.RotatingDiagnosticFile(tmp_path / "child.log").write("fresh")
    assert (tmp_path / "child.log").read_text() == "fresh\n"
    assert all("old legacy content" not in file.read_text() for file in tmp_path.glob("child.log*"))


def test_bounded_reader_preserves_utf8_and_omits_budget_fragment():
    payload = ("a" * 8191 + "\u4e2d\n").encode()
    assert list(process_logs.bounded_lines(io.BytesIO(payload))) == ["a" * 8191 + "\u4e2d"]
    assert list(process_logs.bounded_lines(io.BytesIO(b"whole\npartial-secret"), max_read_bytes=10)) == ["whole"]


def test_colorless_child_env_overrides_user_environment_without_mutation(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "0")
    monkeypatch.setenv("TERM", "xterm-256color")
    base = {"PATH": "keep", "CLICOLOR_FORCE": "1"}
    child = process_logs.colorless_child_env(base)
    assert child == {"PATH": "keep", "NO_COLOR": "1", "TERM": "dumb",
                     "CLICOLOR": "0", "CLICOLOR_FORCE": "0"}
    assert base == {"PATH": "keep", "CLICOLOR_FORCE": "1"}
    assert process_logs.colorless_child_env()["NO_COLOR"] == "1"


def test_emergency_failure_contains_type_and_frames_but_no_values(monkeypatch, tmp_path):
    stderr = io.StringIO()
    monkeypatch.setattr(process_logs.sys, "stderr", stderr)
    monkeypatch.setattr("backend.app.shared.utils.settings.resolve_backend_root", lambda: tmp_path)
    try:
        secret_local = "private-token-secret"
        raise ValueError(secret_local)
    except ValueError as exc:
        process_logs.report_startup_failure(exc)
    output = (tmp_path / "data" / "logs" / "startup_failure.log").read_text()
    assert output == stderr.getvalue()
    assert "ValueError" in output and "test_process_logs.py:" in output
    assert "private-token-secret" not in output and "raise ValueError" not in output
    monkeypatch.setattr(process_logs.sys, "stderr", None)
    process_logs.report_startup_failure(ValueError("still private"))
    assert "still private" not in (tmp_path / "data" / "logs" / "startup_failure.log").read_text()


@pytest.mark.parametrize("immediate", [True, False])
def test_cli_process_start_is_recorded_once_without_readiness_claim(monkeypatch, tmp_path, immediate):
    executable = tmp_path / "deps" / "bin" / "MaaPiCli.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    (tmp_path / "assets").mkdir()
    exited = threading.Event()
    process = SimpleNamespace(pid=123, stdout=io.BytesIO(record("completed")))
    process.poll = lambda: 7 if immediate or exited.is_set() else None

    def wait(timeout=None):
        assert exited.wait(3)
        return 7

    process.wait = wait
    process.terminate = exited.set
    process.kill = exited.set
    popen = Mock(return_value=process)
    monkeypatch.setattr(maafw_process.subprocess, "Popen", popen)
    events = []
    monkeypatch.setattr(maafw_process, "log_event",
                        lambda event, **fields: events.append((event, fields)))
    manager = maafw_process.MaaFWProcess(tmp_path)
    try:
        if immediate:
            with pytest.raises(maafw_process.MaaFWProcessError, match="exited during startup.*readiness was not verified"):
                manager.start()
            assert events == []
        else:
            manager.start()
            # A normal early exit is not a warning; only the start is recorded.
            assert events == [("process.started", {"component": "maafw_cli", "pid": 123})]
            exited.set()
        assert popen.call_args.kwargs["stdout"] == maafw_process.subprocess.PIPE
        assert popen.call_args.kwargs["bufsize"] == 0
        child = popen.call_args.kwargs["env"]
        assert child["NO_COLOR"] == "1" and child["TERM"] == "dumb"
        assert child["CLICOLOR"] == "0" and child["CLICOLOR_FORCE"] == "0"
        assert process_logs.finish_process_log(process)
        assert (tmp_path / "data" / "logs" / "maafw_cli.log").exists()
    finally:
        exited.set()
        manager.stop()
