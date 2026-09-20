import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.shared.utils import native_logs, process_logs


@pytest.fixture(autouse=True)
def release_synthetic_writers(monkeypatch):
    managers = []
    original = native_logs.NativeLogSessions.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        managers.append(self)

    monkeypatch.setattr(native_logs.NativeLogSessions, "__init__", initialize)
    yield
    for manager in managers:
        manager.release_after_shutdown(native_stopped=True)


def test_switch_requires_quiescence_and_success_before_closing_previous(tmp_path):
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    setter = Mock(return_value=True)
    with pytest.raises(RuntimeError, match="completed native operations"):
        sessions.prepare_session(setter, quiescent=False)
    assert not sessions.root.exists()
    setter.assert_not_called()
    first = sessions.prepare_session(setter, quiescent=True)
    assert sessions.prepare_session(setter, quiescent=True) == first
    assert setter.call_count == 1
    assert not (first / ".closed").exists()
    setter.return_value = False
    with pytest.raises(RuntimeError, match="switch failed"):
        sessions.prepare_session(setter, quiescent=True, force=True)
    assert not (first / ".closed").exists()
    assert sessions.active == first
    assert "native_log_switch_uncertain" in sessions.status()["warnings"]
    # A failed setter might have changed the native writer: force a real retry.
    setter.return_value = True
    second = sessions.prepare_session(setter, quiescent=True)
    assert second != first and setter.call_count == 3
    assert (first / ".closed").is_file()
    assert "native_active_writer_unbounded" in sessions.status()["warnings"]
    assert "native_unclosed_sessions_retained" not in sessions.status()["warnings"]


def test_retention_prunes_oldest_closed_sessions_but_never_active_or_unknown(tmp_path):
    sessions = native_logs.NativeLogSessions(tmp_path / "native", max_bytes=500)
    setter = lambda path: True
    first = sessions.prepare_session(setter, quiescent=True)
    (first / "maa.log").write_bytes(b"a" * 200)
    second = sessions.prepare_session(setter, quiescent=True, force=True)
    (second / "maa.log").write_bytes(b"b" * 200)
    third = sessions.prepare_session(setter, quiescent=True, force=True)
    (third / "maa.log").write_bytes(b"c" * 200)
    os.utime(first / ".closed", (100, 100))
    unknown = sessions.root / "legacy-debug"
    unknown.mkdir()
    (unknown / "maa.log").write_bytes(b"preserve unknown data")
    result = sessions.prune()
    assert result["deleted"] == [first.name]
    assert second.exists() and third.exists() and unknown.exists()
    assert sessions.status()["bytes"] <= 500
    # Closed metadata cannot authorize removing this manager's active writer.
    (third / ".closed").touch()
    (third / "maa.log").write_bytes(b"active" * 200)
    sessions.prune()
    assert not second.exists()
    assert third.exists()
    assert "native_retention_budget_exceeded" in sessions.status()["warnings"]


def test_restart_preserves_unclosed_sessions_and_expires_closed_by_age(tmp_path):
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    first = sessions.prepare_session(lambda path: True, quiescent=True)
    active = sessions.prepare_session(lambda path: True, quiescent=True, force=True)
    old = 1
    os.utime(first / ".closed", (old, old))
    os.utime(active, (old, old))
    restarted = native_logs.NativeLogSessions(sessions.root)
    restarted.prune()
    assert not first.exists() and active.exists()
    assert "native_unclosed_sessions_retained" in restarted.status()["warnings"]


def test_reparse_tree_is_not_pruned_or_exported(monkeypatch, tmp_path):
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    first = sessions.prepare_session(lambda path: True, quiescent=True)
    sessions.prepare_session(lambda path: True, quiescent=True, force=True)
    nested = first / "vision"
    nested.mkdir()
    retained = nested / "customer.png"
    retained.write_bytes(b"private image")
    os.utime(first / ".closed", (1, 1))
    original = native_logs.is_reparse
    monkeypatch.setattr(native_logs, "is_reparse", lambda path: path == nested or original(path))
    assert sessions.prune()["skipped_unsafe_or_unreadable"]
    assert retained.read_bytes() == b"private image"
    with pytest.raises(ValueError, match="reparse"):
        sessions.export_sanitized(first.name)
    assert "native_unsafe_or_unreadable_sessions_skipped" in sessions.status()["warnings"]
    junction = Mock()
    junction.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
    assert process_logs.is_reparse(junction)


def test_native_export_is_closed_only_sanitized_and_bounded(tmp_path):
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    first = sessions.prepare_session(lambda path: True, quiescent=True)
    records = [
        "unknown native content with private-token",
        json.dumps({"schema": 1, "level": "INFO", "message": "token=private-token"}),
        json.dumps({"schema": 1, "level": "INFO", "message": "completed"}),
    ]
    (first / "maa.log").write_text("\n".join(records) + "\n", encoding="utf-8")
    (first / "vision.png").write_bytes(b"private image")
    with pytest.raises(ValueError, match="closed"):
        sessions.export_sanitized(first.name)
    sessions.prepare_session(lambda path: True, quiescent=True, force=True)
    exported = sessions.export_sanitized(first.name)
    assert "private-token" not in exported and "private image" not in exported
    assert "completed" in exported
    assert all(json.loads(line)["source"] == "native" for line in exported.splitlines())
    assert len(sessions.export_sanitized(first.name, max_bytes=100).encode()) <= 100
    assert sessions.export_sanitized(first.name, max_read_bytes=10) == ""
    with pytest.raises(ValueError, match="identifier"):
        sessions.export_sanitized("../maa.log")
    # Retaining native logs means the local original remains byte-for-byte raw.
    assert "private-token" in (first / "maa.log").read_text()


@pytest.mark.parametrize("boundary", ["age", "size"])
def test_native_switch_after_session_boundary(monkeypatch, tmp_path, boundary):
    clock = [1_000_000.0]
    monkeypatch.setattr(native_logs.time, "time", lambda: clock[0])
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    setter = Mock(return_value=True)
    first = sessions.prepare_session(setter, quiescent=True)
    if boundary == "age":
        clock[0] += 86401
    else:
        monkeypatch.setattr(native_logs, "LOG_MAX_BYTES", 100)
        (first / "maa.log").write_bytes(b"x" * 101)
    second = sessions.prepare_session(setter, quiescent=True)
    assert second != first and (first / ".closed").exists()


def test_failed_switch_reuses_candidate_and_live_leases_prevent_reclamation(tmp_path):
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    first = sessions.prepare_session(lambda path: True, quiescent=True)
    for _ in range(5):
        with pytest.raises(RuntimeError, match="switch failed"):
            sessions.prepare_session(lambda path: False, quiescent=True, force=True)
    assert len(list(sessions.root.iterdir())) == 2
    observer = native_logs.NativeLogSessions(sessions.root, max_bytes=1)
    # A stale/forged closed marker cannot override a live writer's lease.
    (first / ".closed").touch()
    assert observer.prune()["deleted"] == []
    assert first.exists() and sessions._candidate.exists()
    with pytest.raises(RuntimeError, match="shutdown"):
        sessions.release_after_shutdown(native_stopped=False)


def test_exited_owner_sessions_become_reclaimable_without_pid_guessing(tmp_path):
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    active = sessions.prepare_session(lambda path: True, quiescent=True)
    (active / "maa.log").write_bytes(b"retained native output")
    observer = native_logs.NativeLogSessions(sessions.root, max_bytes=1)
    assert observer.prune()["deleted"] == []
    # Simulate OS exit releasing descriptors, without writing a closed marker.
    for descriptor in sessions._leases.values():
        os.close(descriptor)
    sessions._leases.clear()
    sessions.active = None
    assert not (active / ".closed").exists()
    assert observer.prune()["deleted"] == [active.name]
    assert not active.exists()


def test_session_count_is_bounded_and_missing_lease_is_not_proof_of_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(native_logs, "NATIVE_MAX_SESSIONS", 3)
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    for _ in range(8):
        sessions.prepare_session(lambda path: True, quiescent=True, force=True)
    assert len(list(sessions.root.iterdir())) == 3
    unknown = sessions.root / ("session-" + "a" * 32)
    unknown.mkdir()
    (unknown / ".owner").write_bytes(native_logs._OWNER)
    sessions.max_bytes = 1
    sessions.prune()
    assert unknown.exists() and not (unknown / ".closed").exists()
    assert sessions.active.exists()


def test_closed_session_cleanup_recovers_after_partial_lease_removal(tmp_path):
    sessions = native_logs.NativeLogSessions(tmp_path / "native")
    closed = sessions.prepare_session(lambda path: True, quiescent=True)
    active = sessions.prepare_session(lambda path: True, quiescent=True, force=True)
    (closed / ".lease").unlink()
    sessions.max_bytes = 1
    assert sessions.prune()["deleted"] == [closed.name]
    assert active.exists()
