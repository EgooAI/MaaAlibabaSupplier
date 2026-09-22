import asyncio
from contextlib import suppress
import io
import json
import os
from pathlib import Path
import ssl
import stat
import sys
import threading
import time
import types
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.app import diagnostics as diag
from backend.app.api import main
from backend.tests.auth_client import authenticate
from tools import pull_diagnostics as pull


URL = "/api/app/diagnostics/download"
NATIVE = "[2026-09-20 12:34:56.123][INF][P123][T456] token=private-token payload=private-body\n"
APPLICATION = json.dumps({"schema": 1, "time": "2026-09-20T12:34:56", "level": "ERROR",
                          "event": "test.failure", "message": "private-body Bearer private-token",
                          "context": {"request_id": "abcd1234", "password": "private-password"},
                          "exception": {"type": "ValueError", "message": "private-error",
                                        "frames": [{"file": "test.py", "function": "work", "line": 12,
                                                    "locals": {"secret": "private-local"}}]}}) + "\n"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def unpack(body):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def fixture_sources():
    sources, stage = diag._sources()
    expected = set()
    for source in sources:
        if source.name.startswith("dev-"):
            continue
        filename = source.current[0] if source.current else "maafw.2026-09-20.log"
        text = {"application": APPLICATION, "native": NATIVE,
                "yak": "[!] Forwarding to Python failed: HTTP 503\n[!] payload error: private-yak\n",
                "startup": "Backend startup failed: private-exception\n",
                "result": '{"status":"installed","message":"private-updater","version":"v1.2.3"}'}[source.kind]
        write(source.directory / filename, text)
        expected.add(source.name)
    write(stage / ("a" * 32) / "installer.log", "2026-09-20 12:34:56.123 Installation process succeeded.\n"
          "2026-09-20 12:34:57.123 Command line: private-installer\n")
    expected.add("installer")
    return expected


def test_authenticated_zip_includes_all_sources_without_account_or_raw_data(monkeypatch):
    expected = fixture_sources()
    backend = diag.resolve_backend_root()
    for path in (backend / ".env", backend / "data/auth.sqlite.log", backend / "debug/screenshot.png",
                 backend / "data/logs/clipboard.log", diag._sources()[1] / ("a" * 32) / "request.json"):
        write(path, "private-excluded")
    forbidden = Mock(side_effect=AssertionError("Account or live state accessed"))
    monkeypatch.setattr(main, "get_account_context", forbidden)
    with TestClient(main.create_app()) as client:
        assert client.get(URL).status_code == 401
        assert client.get(URL, headers={"Authorization": "Bearer maa_" + "x" * 43}).status_code == 401
        authenticate(client)
        assert client.get(URL + "?path=.env").status_code == 422
        response = client.get(URL)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "diagnostics-recent.zip" in response.headers["content-disposition"]
        assert "x-account-epoch" not in response.headers
        content = unpack(response.content)
        manifest = json.loads(content["manifest.json"])
        assert {item["source"] for item in manifest["files"] if item["status"] == "included"} == expected
        combined = b"\n".join(content.values())
        assert b"private-" not in combined
        assert str(Path.cwd()).encode() not in combined
        assert b"abcd1234" in combined and b'"ValueError"' in combined
        assert b'"native_record"' in combined and b'"forward_http_error"' in combined
        assert b'"installation_succeeded"' in combined and b'"installed"' in combined
        assert b'"update_result"' in combined and b'"v1.2.3"' in combined
        assert len(content) <= 24
        client.post("/api/auth/logout")
        assert client.get(URL).status_code == 401
    forbidden.assert_not_called()


def test_missing_files_and_rotation_do_not_fail_archive(monkeypatch):
    assert set(unpack(diag.build_bundle())) == {"manifest.json", "runtime.json"}
    path = write(diag.resolve_backend_root() / "data/logs/api.log", APPLICATION)
    original = diag._open_checked

    def rotate(candidate, info):
        if candidate == path:
            candidate.rename(candidate.with_suffix(".old"))
            write(candidate, "private-replacement")
        return original(candidate, info)

    monkeypatch.setattr(diag, "_open_checked", rotate)
    content = unpack(diag.build_bundle())
    assert set(content) == {"manifest.json", "runtime.json"}
    assert json.loads(content["manifest.json"])["files"][0]["status"] == "unavailable_or_rotated"


def test_recent_tail_limits_and_fairness_before_rotations(monkeypatch):
    expected = fixture_sources()
    source = diag.resolve_backend_root() / "data/logs"
    for index in range(40):
        write(source / f"api.2026-09-20_{index:02d}.log", APPLICATION * 30)
    monkeypatch.setattr(diag, "MAX_FILE_BYTES", 2048)
    monkeypatch.setattr(diag, "MAX_TOTAL_BYTES", 64 * 1024 + 24 * 1024)
    content = unpack(diag.build_bundle())
    manifest = json.loads(content["manifest.json"])
    included = [item for item in manifest["files"] if item["status"] == "included"]
    assert expected.issubset({item["source"] for item in included})
    assert len(content) == 24
    assert all(item["read_bytes"] <= 2048 and item["exported_bytes"] <= 2048 for item in included)
    assert sum(max(item["read_bytes"], item["exported_bytes"]) for item in included) <= 24 * 1024
    assert any(item["tail_truncated"] for item in included)
    assert all(len(value) <= 2048 for name, value in content.items() if name.startswith("logs/"))


def test_directory_scan_bounded_but_fixed_current_names_checked(monkeypatch):
    path = write(diag.resolve_backend_root() / "data/logs/api.log", APPLICATION)
    consumed = []

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            for index in range(10000):
                consumed.append(index)
                yield SimpleNamespace(name=f"irrelevant-{index}")

    monkeypatch.setattr(diag.os, "scandir", lambda directory: Entries())
    monkeypatch.setattr(diag, "MAX_DIRECTORY_ENTRIES", 3)
    monkeypatch.setattr(diag, "MAX_SCAN_ENTRIES", 5)
    content = unpack(diag.build_bundle())
    assert len(consumed) <= 5
    manifest = json.loads(content["manifest.json"])
    assert manifest["scan_limited"] is True
    assert manifest["files"][0]["source"] == "api"
    assert path.exists()


@pytest.mark.parametrize("kind", ["file", "parent", "hardlink"])
def test_links_cannot_export_other_files(tmp_path, kind):
    outside = write(tmp_path / "outside/api.log", APPLICATION)
    destination = diag.resolve_backend_root() / "data/logs/api.log"
    destination.parent.parent.mkdir(parents=True, exist_ok=True)
    if kind == "parent":
        try:
            destination.parent.symlink_to(outside.parent, target_is_directory=True)
        except OSError:
            pytest.skip("Windows symlink privilege is unavailable; reparse rejection has separate coverage")
    else:
        destination.parent.mkdir()
        if kind == "hardlink":
            os.link(outside, destination)
        else:
            try:
                destination.symlink_to(outside)
            except OSError:
                pytest.skip("Windows symlink privilege is unavailable; reparse rejection has separate coverage")
    assert set(unpack(diag.build_bundle())) == {"manifest.json", "runtime.json"}


def test_reparse_attribute_blocks_before_read(monkeypatch):
    path = write(diag.resolve_backend_root() / "data/logs/api.log", APPLICATION)
    original = Path.lstat

    def marked(candidate):
        info = original(candidate)
        if candidate == path.parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", marked)
    monkeypatch.setattr(diag, "_open_checked", Mock(side_effect=AssertionError("Unsafe path opened")))
    assert set(unpack(diag.build_bundle())) == {"manifest.json", "runtime.json"}


def test_unknown_bodies_oversized_lines_and_stack_locals_are_omitted():
    path = write(diag.resolve_backend_root() / "debug/debug/maa.log",
                 "private-unknown\n" + "x" * (diag.MAX_LINE_BYTES + 1) + "\n" + NATIVE
                 + "  secret = 'private-local'\n")
    data, details = diag._read_recent(path, path.stat(), "native", diag.MAX_FILE_BYTES)
    assert details["omitted_records"] == 3
    assert json.loads(data)["event"] == "native_record"
    assert b"private" not in data
    monkey_record = '{"status":"installed","message":"private-result"}'
    assert "private" not in diag._metadata(monkey_record, "result")


def test_interrupted_update_result_exports_status_and_safe_version():
    record = json.loads(diag._metadata(
        '{"status":"installing","message":"private-message","version":"v0.0.0-ci.260921-7f5bbc9"}', "result"))
    assert record == {"event": "update_result", "status": "installing", "version": "v0.0.0-ci.260921-7f5bbc9"}
    assert "version" not in json.loads(diag._metadata(
        '{"status":"installing","message":"private-message","version":"../private/escape"}', "result"))
    assert diag._metadata('{"status":"whatever","message":"private-message"}', "result") is None


def test_native_backup_names_and_actual_four_mib_tail():
    directory = diag.resolve_backend_root() / "debug/debug"
    for name in ("maa.bak.log", "maafw.bak.log"):
        write(directory / name, NATIVE)
    path = directory / "maa.bak.log"
    with path.open("wb") as stream:
        stream.write(b"private-prefix\n")
        stream.write(b"x" * diag.MAX_FILE_BYTES)
        stream.write(b"\n" + NATIVE.encode())
    data, details = diag._read_recent(path, path.stat(), "native", diag.MAX_FILE_BYTES)
    assert details["read_bytes"] == 4 * 1024**2 and details["tail_truncated"]
    assert json.loads(data)["event"] == "native_record"
    content = unpack(diag.build_bundle())
    sources = {item["source"] for item in json.loads(content["manifest.json"])["files"]}
    assert sources == {"native-backend-nested-maa", "native-backend-nested-maafw"}


def test_new_process_json_logs_are_resanitized_and_installer_rotations_share_one_source():
    backend = diag.resolve_backend_root()
    for name in ("maafw_cli.log", "yak_mitm.log"):
        write(backend / "data/logs" / name, APPLICATION)
    stage = diag._sources()[1]
    for index in range(4):
        write(stage / (str(index) * 32) / "installer.log", "2026-09-20 12:34:56.123 Installation process succeeded.\n")
    content = unpack(diag.build_bundle())
    manifest = json.loads(content["manifest.json"])
    assert [item for item in manifest["sources"] if item["source"] == "installer"] == [{"source": "installer", "candidates": 4}]
    assert [item["source"] for item in manifest["files"][:3]] == ["yak", "cli", "installer"]
    for item in manifest["files"][:2]:
        record = json.loads(content[item["file"]])
        assert record["event"] == "test.failure" and "message" not in record
    assert b"private" not in b"\n".join(content.values())


@pytest.mark.parametrize("known_active", [False, True])
def test_owned_native_sessions_include_active_snapshot_without_rotation(monkeypatch, known_active):
    root = diag.resolve_backend_root() / "data/logs/native"
    for index in range(6):
        session = root / ("session-" + str(index) * 32)
        write(session / ".owner", "maa-native-session-v1\n")
        write(session / ".closed", "")
        os.utime(session / ".closed", (100 + index, 100 + index))
        write(session / "maa.log", NATIVE)
        write(session / "debug/maafw.log", NATIVE)
        write(session / "screenshot.png", "private-image")
    active = root / ("session-" + "a" * 32)
    write(active / ".owner", "maa-native-session-v1\n")
    write(active / "maa.log", APPLICATION)
    unowned = root / ("session-" + "b" * 32)
    write(unowned / ".closed", "")
    write(unowned / ".owner", "wrong-owner")
    write(unowned / "maa.log", APPLICATION)
    monkeypatch.setattr(diag, "runtime_observations", lambda: {"native_active_session": active.name} if known_active else {})
    content = unpack(diag.build_bundle())
    manifest = json.loads(content["manifest.json"])
    assert [item for item in manifest["sources"] if item["source"].startswith("native-session-")] == [
        {"source": "native-session-maa", "candidates": 4},
        {"source": "native-session-maafw", "candidates": 3},
    ]
    assert len(manifest["files"]) == 7
    first = manifest["files"][0]
    assert first["session_state"] == ("active" if known_active else "open_or_unclosed")
    assert first["live_snapshot"] is True
    assert b"test.failure" in content[first["file"]]
    assert not (active / ".closed").exists()
    retention = json.loads(content["runtime.json"])["native_retention"]
    assert retention["active_session_known"] == known_active
    assert retention["inventory_complete"] is False
    assert retention["rotated_or_pruned"] is False
    assert manifest["omitted_native_sessions"] == 3
    assert b"private" not in b"\n".join(content.values())


@pytest.mark.parametrize("kind,header", [
    ("native", "[2026-09-20 12:34:56.123][ERR][P123][T456][C:\\private\\Tasker.cpp:98][Tasker::run]"),
    ("native", "[2026-09-20 12:34:56.123][ERR][Px123][Tx456][Tasker.cpp][L98][Tasker::run][handle=0x1234]"),
    ("yak", "[ERROR] 2026-09-20 12:34:56.123 [proxy.go:98][forward]"),
])
def test_native_yak_correlation_stops_before_sensitive_body(kind, header):
    record = json.loads(diag._metadata(header + ' [task_id=12][job_id=34][node_id=56][entry=ChatInput_SendOnly] '
                                     'recognition failed: OCR private-body node_id=999 [job_id=888]', kind))
    context = record["context"]
    assert context["task_id"] == 12 and context["job_id"] == 34 and context["node_id"] == 56
    assert context["entry"] == "ChatInput_SendOnly" and context["line"] == 98
    assert context["source_file"] in ("Tasker.cpp", "proxy.go")
    assert context["function"] in ("Tasker::run", "forward")
    assert context["error_category"] == "recognition_failed"
    assert "private" not in json.dumps(record)
    if kind == "native":
        assert context["pid"] == 123 and context["tid"] == 456
    for body in ('OCR text: task_id=987 [job_id=654]', 'payload={"entry":"ChatInput"}',
                 '[node=private-recipient] [body=private-body]', '[text=private-ocr] [task_id=987]'):
        record = json.loads(diag._metadata(header + " " + body, kind))
        assert not {"entry", "node", "task_id", "job_id"}.intersection(record["context"])
        assert "private" not in json.dumps(record)


def test_structured_native_numeric_ids_survive_without_nested_payloads():
    raw = {"schema": 1, "event": "native.task", "message": "private-ocr", "context": {
        "task_id": 12, "job_id": "34", "node_id": 56, "entry": "ContactSearch",
        "file": "C:/private/Tasker.cpp", "function": "Tasker::run", "line": 78,
        "error_category": "timeout", "body": {"job_id": 999}, "ocr": "private-ocr"}}
    context = json.loads(diag._metadata(json.dumps(raw), "native"))["context"]
    assert context["task_id"] == 12 and context["job_id"] == 34 and context["node_id"] == 56
    assert context["entry"] == "ContactSearch" and context["source_file"] == "Tasker.cpp"
    assert context["error_category"] == "timeout" and context["line"] == 78
    assert "private" not in json.dumps(context) and "body" not in context


@pytest.mark.parametrize("encoding,bom", [("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")])
@pytest.mark.parametrize("tail", [False, True])
def test_installer_bom_encodings_and_tail_byte_budget(tmp_path, encoding, bom, tail):
    path = tmp_path / "installer.log"
    line = "2026-09-20 12:34:56.123 Installation process succeeded.\r\n"
    raw = bom + (("private-\u4e2d\U0001f600\n" * 100 if tail else "") + line).encode(encoding)
    path.write_bytes(raw)
    budget = 257
    output, details = diag._read_recent(path, path.stat(), "installer", budget)
    records = [json.loads(line) for line in output.splitlines()]
    assert records[-1]["event"] == "installation_succeeded"
    assert details["read_bytes"] <= budget and len(output) <= budget
    assert details["tail_truncated"] == tail and details["encoding"] == encoding
    assert "private" not in output.decode()
    assert details["unprocessed_read_bytes"] == 0


def test_live_snapshot_excludes_appends_and_reports_incomplete_records(tmp_path, monkeypatch):
    path = write(tmp_path / "maa.log", NATIVE + "[2026-09-20 12:34:56.123][INF][task_id=42]")
    before = path.stat()
    original = diag._open_checked

    def append(candidate, info):
        with candidate.open("ab") as writer:
            writer.write(b" [node_id=private-append]\n" + NATIVE.encode())
        return original(candidate, info)

    monkeypatch.setattr(diag, "_open_checked", append)
    output, details = diag._read_recent(path, before, "native", 4096)
    assert len(output.splitlines()) == 1
    assert details["snapshot_bytes"] == before.st_size and details["snapshot_changed"]
    assert details["omission_reasons"] == {"incomplete_last_record": 1}
    assert "42" not in output.decode()


def test_runtime_build_and_observation_allowlists_are_noninitializing(monkeypatch):
    write(diag.resolve_repo_root() / "build-info.json", json.dumps({
        "version": "v1.2.3", "sha": "a" * 40, "run_id": 123, "run_number": 4,
        "token": "private-token", "repository": "private-repository", "path": "private-path"}))
    monkeypatch.setattr(diag, "runtime_observations", lambda: {
        "gui_initialized": False, "shutdown_requested": True, "native_switch_uncertain": True,
        "password": "private-secret", "sync_initialized": "private-value"})
    runtime = json.loads(unpack(diag.build_bundle())["runtime.json"])
    assert runtime["build"] == {"status": "available", "version": "v1.2.3", "sha": "a" * 40, "run_id": 123, "run_number": 4}
    assert runtime["observations"] == {"gui_initialized": False, "shutdown_requested": True, "native_switch_uncertain": True}
    assert "private" not in json.dumps(runtime)


def test_runtime_collects_cached_workers_and_active_native_session_without_payloads(monkeypatch):
    from backend.app.shared.backend import maafw_runner, sync_coordinator, outbox_service
    from backend.app.task_queue import TaskQueue

    session = "session-" + "b" * 32
    monkeypatch.setattr(maafw_runner, "get_native_log_status", lambda: {
        "initialized": True, "active_session": session, "bytes": 2048, "observed_at": 123.0,
        "outstanding_jobs": 1, "post_uncertain": False,
        "warnings": ["native_log_policy_unavailable", "native_log_switch_uncertain"],
    })
    state = {"started": True, "alive": True, "phase": "checking_source", "pending": 2,
             "last_progress_at": 122.0, "last_error": "private-error",
             "context": {"seller": "private-seller", "data_dir": "private-path"}}
    monkeypatch.setattr(sync_coordinator, "observe_sync_service", lambda: state)
    monkeypatch.setattr(outbox_service, "observe_outbox_verifier", lambda: state)
    monkeypatch.setattr(TaskQueue, "observations", lambda name: {
        "initialized": False, "alive": False, "pending": 0, "current_started": None,
    })
    runtime = json.loads(unpack(diag.build_bundle())["runtime.json"])
    assert runtime["observations"]["native_active_session"] == session
    assert runtime["native_retention"]["policy_unavailable"] is True
    assert runtime["native_retention"]["switch_uncertain"] is True
    assert runtime["native_retention"]["retained_bytes"] == 2048
    assert runtime["workers"]["sync"]["pending"] == 2
    assert runtime["workers"]["outbox"]["has_error"] is True
    assert runtime["workers"]["translation"]["initialized"] is False
    assert "private" not in json.dumps(runtime)


def test_runtime_reports_passive_updater_state_without_free_text(monkeypatch):
    updater = types.ModuleType("backend.app.updater")
    updater._instance = SimpleNamespace(snapshot=lambda: {
        "supported": True, "phase": "installing", "error": "private-path",
        "candidate": {"run_id": 42, "version": "private-candidate"},
        "last_result": {"status": "installing", "message": "private-message", "version": "v1.2.3"}})
    monkeypatch.setitem(sys.modules, "backend.app.updater", updater)
    runtime = diag._runtime_summary(time.monotonic() + 5)
    assert runtime["updater"] == {"initialized": True, "supported": True, "handoff": True, "has_error": True,
                                  "phase": "installing", "candidate_run_id": 42,
                                  "last_result_status": "installing", "last_result_version": "v1.2.3"}
    assert "private" not in json.dumps(runtime)


def test_runtime_does_not_initialize_an_absent_updater(monkeypatch):
    updater = types.ModuleType("backend.app.updater")
    updater._instance = None
    monkeypatch.setitem(sys.modules, "backend.app.updater", updater)
    assert diag._runtime_summary(time.monotonic() + 5)["updater"] == {"initialized": False}


def test_legacy_records_deduplicate_by_origin_and_keep_distinct_sources():
    path = write(diag.resolve_backend_root() / "data/logs/api.log",
                 "2026-09-20 12:00:00 | INFO | vendor.client:send:477 - first\n"
                 "2026-09-20 12:00:01 | INFO | vendor.client:send:477 - second\n"
                 "2026-09-20 12:00:02 | WARNING | vendor.client:drop:9 - third\n")
    data, details = diag._read_recent(path, path.stat(), "application", diag.MAX_FILE_BYTES)
    records = [json.loads(line) for line in data.decode().splitlines()]
    assert [(item["level"], item["function"]) for item in records] == [("INFO", "send"), ("WARNING", "drop")]
    assert details["omitted_records"] == 1 and details["omission_reasons"] == {"duplicate": 1}


@pytest.mark.parametrize("message, category", [
    ("Unsafe diagnostic path", "unsafe_path"),
    ("Not a private regular file", "nonregular"),
    ("Diagnostic file rotated", "rotated"),
    ("Diagnostic path changed", "rotated"),
    ("unexpected failure", "unavailable"),
])
def test_candidate_skip_reasons_are_fixed_categories(message, category):
    assert diag._skip_reason(OSError(message)) == category


def test_empty_sources_keep_manifest_entries_without_zip_payloads():
    write(diag.resolve_backend_root() / "debug/maafw.log", "unrecognized private body\n")
    content = unpack(diag.build_bundle())
    manifest = json.loads(content["manifest.json"])
    entry = next(item for item in manifest["files"] if item["source"] == "cli-legacy")
    assert entry["status"] == "included" and entry["exported_bytes"] == 0
    assert entry["file"] not in content
    assert "private" not in json.dumps(manifest)


def test_cooperative_deadline_preserves_zip_and_reports_unprocessed_records(monkeypatch):
    write(diag.resolve_backend_root() / "debug/maafw.log", NATIVE * 10)
    write(diag.resolve_backend_root() / "debug/maa.log", NATIVE)
    clock = [100.0]
    monkeypatch.setattr(diag.time, "monotonic", lambda: clock[0])
    original = diag._metadata

    def expire(line, kind):
        safe = original(line, kind)
        clock[0] += 11
        return safe

    monkeypatch.setattr(diag, "_metadata", expire)
    content = unpack(diag.build_bundle())
    manifest = json.loads(content["manifest.json"])
    first, second = manifest["files"]
    assert manifest["deadline_exceeded"] and first["deadline_exceeded"]
    assert first["unprocessed_read_bytes"] > 0 and not first["omitted_records_exact"]
    assert second["status"] == "omitted_deadline" and second["file"] not in content
    assert len(content[first["file"]].splitlines()) == 1


def test_output_limit_and_discovery_deadline_are_explicit(tmp_path, monkeypatch):
    path = write(tmp_path / "maa.log", "[2026-09-20 12:34:56][INF]\n" * 5)
    output, details = diag._read_recent(path, path.stat(), "native", path.stat().st_size)
    assert len(output) <= path.stat().st_size
    assert details["output_truncated"] and details["unprocessed_read_bytes"] > 0
    assert not details["omitted_records_exact"]
    monkeypatch.setattr(diag, "COLLECTION_SECONDS", 0)
    monkeypatch.setattr(diag, "_safe_stat", Mock(side_effect=AssertionError("Read after deadline")))
    content = unpack(diag.build_bundle())
    manifest = json.loads(content["manifest.json"])
    assert manifest["deadline_exceeded"] and manifest["discovery_incomplete"]
    assert json.loads(content["runtime.json"])["build"]["status"] == "omitted_deadline"


def test_deadline_stops_between_read_chunks_and_closes_snapshot(tmp_path, monkeypatch):
    path = write(tmp_path / "maa.log", NATIVE * 2000)
    original = diag._open_checked
    clock = [100.0]
    opened = []

    class InterruptedRead:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size):
            data = self.stream.read(size)
            if size > 3:
                clock[0] = 111.0
            return data

    def open_snapshot(candidate, info):
        stream = original(candidate, info)
        opened.append(stream)
        return InterruptedRead(stream)

    monkeypatch.setattr(diag, "_open_checked", open_snapshot)
    monkeypatch.setattr(diag.time, "monotonic", lambda: clock[0])
    output, details = diag._read_recent(path, path.stat(), "native", diag.MAX_FILE_BYTES, deadline=110.0)
    assert not output and details["deadline_exceeded"] and details["snapshot_read_incomplete"]
    assert details["read_bytes"] == 65536 + 3
    assert details["unprocessed_read_bytes"] == details["read_bytes"]
    assert all(stream.closed for stream in opened)


def test_archive_ceiling_and_temporary_file_cleanup(monkeypatch):
    original = diag.tempfile.TemporaryFile
    handles = []

    def temporary():
        handle = original()
        handles.append(handle)
        return handle

    monkeypatch.setattr(diag.tempfile, "TemporaryFile", temporary)
    monkeypatch.setattr(diag, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(ValueError, match="limit"):
        diag.build_bundle()
    assert handles and all(handle.closed for handle in handles)


def test_opened_path_replacement_is_rejected_and_handle_closed(tmp_path, monkeypatch):
    candidate = write(diag.resolve_backend_root() / "data/logs/api.log", APPLICATION)
    outside = write(tmp_path / "outside.log", "private-outside")
    original = os.open
    descriptors = []

    def replaced(path, flags, *args, **kwargs):
        descriptor = original(outside, flags) if str(path) in (str(candidate), candidate.name) else original(path, flags, *args, **kwargs)
        if str(path) in (str(candidate), candidate.name):
            descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(diag.os, "open", replaced)
    with pytest.raises(OSError):
        diag._open_checked(candidate, candidate.stat())
    assert descriptors
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


async def asgi_request(app, token, *, send_hook=None, disconnect=None):
    first = True
    messages = []
    never = asyncio.Event()

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await (disconnect or never).wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if send_hook:
            await send_hook(message)

    await app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
               "method": "GET", "scheme": "http", "path": URL, "raw_path": URL.encode(),
               "query_string": b"", "headers": [(b"authorization", f"Bearer {token}".encode())],
               "client": ("test", 1), "server": ("test", 80)}, receive, send)
    return messages


@pytest.mark.parametrize("outcome", ["complete", "send_failure", "cancel", "disconnect"])
def test_admission_held_until_outer_send_cleanup(monkeypatch, outcome):
    app = main.create_app()
    token = app.state.auth_store.issue()
    real_build = diag.build_bundle
    threads = []

    def build():
        threads.append(threading.get_ident())
        return real_build()

    monkeypatch.setattr(diag, "build_bundle", build)

    async def scenario():
        blocked, release, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                blocked.set()
                await release.wait()
                if outcome == "send_failure":
                    raise OSError("private-send-error")

        request = asyncio.create_task(asgi_request(app, token, send_hook=send, disconnect=disconnected))
        try:
            await asyncio.wait_for(blocked.wait(), 5)
            busy = await asyncio.wait_for(asgi_request(app, token), 5)
            assert busy[0]["status"] == 429
            assert len(threads) == 1 and threads[0] != threading.get_ident()
            if outcome == "cancel":
                request.cancel()
            elif outcome == "disconnect":
                disconnected.set()
            release.set()
            with suppress(asyncio.CancelledError, OSError):
                await asyncio.wait_for(request, 5)
            following = await asyncio.wait_for(asgi_request(app, token), 5)
            assert following[0]["status"] == 200
            assert len(threads) == 2
        finally:
            release.set()
            request.cancel()
            with suppress(asyncio.CancelledError, OSError):
                await request

    asyncio.run(scenario())


def test_cancelled_build_holds_admission_until_worker_exits(monkeypatch):
    app = main.create_app()
    token = app.state.auth_store.issue()
    release = threading.Event()
    real_build = diag.build_bundle

    async def scenario():
        started, exited = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()

        def build():
            loop.call_soon_threadsafe(started.set)
            try:
                assert release.wait(5)
                return real_build()
            finally:
                loop.call_soon_threadsafe(exited.set)

        monkeypatch.setattr(diag, "build_bundle", build)
        request = asyncio.create_task(asgi_request(app, token))
        try:
            await asyncio.wait_for(started.wait(), 5)
            request.cancel()
            with suppress(asyncio.CancelledError):
                await request
            busy = await asyncio.wait_for(asgi_request(app, token), 5)
            assert busy[0]["status"] == 429
        finally:
            release.set()
            await asyncio.wait_for(exited.wait(), 5)
            with suppress(asyncio.CancelledError):
                await request
        # Join the actual worker completion, not an elapsed-time assumption.
        await asyncio.get_running_loop().shutdown_default_executor()
        assert diag._ADMISSION.acquire(blocking=False)
        diag._ADMISSION.release()

    asyncio.run(scenario())


def test_build_failure_releases_admission_and_does_not_echo(monkeypatch):
    app = main.create_app()
    with TestClient(app) as client:
        authenticate(client)
        with monkeypatch.context() as patch:
            patch.setattr(diag, "build_bundle", Mock(side_effect=OSError("private-path private-token")))
            response = client.get(URL)
            assert response.status_code == 503 and "private" not in response.text
        assert client.get(URL).status_code == 200


@pytest.mark.parametrize("url", ["http://example.com", "https://user:private@example.com", "https://example.com/?token=private",
                                "https://example.com/#private", "file:///tmp/a", "http://127.0.0.1/other"])
def test_pull_rejects_unsafe_origin_before_network(url):
    with pytest.raises(ValueError):
        pull.endpoint(url)


@pytest.mark.parametrize("exc, expected", [
    (urllib.error.HTTPError("https://private.example", 401, "private-reason", {}, None), "HTTP 401"),
    (urllib.error.HTTPError("https://private.example", 429, "private-reason", {}, None), "HTTP 429"),
    (urllib.error.HTTPError("https://private.example", 503, "private-reason", {}, None), "HTTP 503"),
    (urllib.error.URLError(ssl.SSLCertVerificationError("private-certificate")), "TLS certificate"),
    (urllib.error.URLError(ConnectionRefusedError()), "Could not connect"),
    (TimeoutError("private-timeout"), "timed out"),
    (zipfile.BadZipFile("private-archive"), "ZIP"),
    (ValueError("private-value"), "rejected"),
])
def test_pull_failure_categories_are_fixed_and_never_echo(exc, expected):
    message = pull.classify_failure(exc)
    assert expected in message
    assert "private" not in message


@pytest.mark.parametrize("failure", [None, "invalid_zip", "truncated", "oversized", "occupied_partial", "interrupted"])
def test_pull_validates_before_publication_and_cleans_partial(tmp_path, monkeypatch, failure):
    body = diag.build_bundle() if failure != "invalid_zip" else b"private-server-error"
    target = tmp_path / "diagnostics.zip"
    partial = tmp_path / "diagnostics.zip.part"
    if failure == "occupied_partial":
        partial.write_bytes(b"preserve-other-download")
    if failure == "oversized":
        monkeypatch.setattr(pull, "MAX_ARCHIVE_BYTES", 10)
    from email.message import Message

    headers = Message()
    headers["Content-Type"] = "application/zip"
    headers["Content-Length"] = str(len(body) + (1 if failure == "truncated" else 0))
    response = io.BytesIO(body)
    response.status, response.headers = 200, headers
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        if failure == "interrupted":
            raise KeyboardInterrupt
        return response

    monkeypatch.setattr(pull.urllib.request, "build_opener", lambda *handlers: SimpleNamespace(open=open_request))
    token = "maa_" + "a" * 43
    if failure:
        with pytest.raises((ValueError, zipfile.BadZipFile, FileExistsError, KeyboardInterrupt)):
            pull.download("http://127.0.0.1:8000", token, target)
        assert not target.exists()
    else:
        pull.download("http://127.0.0.1:8000", token, target)
        pull.validate_archive(target)
        assert requests[0].headers["Authorization"] == f"Bearer {token}"
        assert token not in requests[0].full_url
    assert partial.exists() == (failure == "occupied_partial")
    if partial.exists():
        assert partial.read_bytes() == b"preserve-other-download"


def test_pull_refuses_overwrite_and_redirects_without_echo(tmp_path, monkeypatch, capsys):
    target = tmp_path / "existing.zip"
    target.write_bytes(b"preserve")
    monkeypatch.setattr(pull.urllib.request, "build_opener", Mock(side_effect=AssertionError("Network called")))
    with pytest.raises(ValueError):
        pull.download("https://example.com", "maa_" + "a" * 43, target)
    assert target.read_bytes() == b"preserve"
    assert pull.NoRedirect().redirect_request(None, None, 302, None, None, "https://other.example") is None
    monkeypatch.setattr(pull, "download", Mock(side_effect=ValueError("private-token private-server-body")))
    monkeypatch.setattr(pull.getpass, "getpass", lambda prompt: "private-token")
    monkeypatch.setattr(pull.os.sys, "argv", ["pull_diagnostics.py", str(tmp_path / "new.zip")])
    with pytest.raises(SystemExit) as error:
        pull.main()
    assert error.value.code == 1
    assert "private" not in capsys.readouterr().err
