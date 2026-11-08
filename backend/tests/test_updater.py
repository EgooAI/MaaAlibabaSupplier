import asyncio
import hashlib
import io
import json
import os
import stat
import threading
import urllib.error
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app import updater as updates
from backend.app.api import main
from backend.app.api.envelope import AppError
from backend.tests.auth_client import authenticate


def build(**changes):
    return {"schema_version": 1, "app_id": updates.APP_ID, "version": "v1.2.3",
            "repository": "EgooAI/MaaAlibabaSupplier", "sha": "a" * 40,
            "run_id": 20, "run_number": 12, "run_attempt": 1, **changes}


def run(**changes):
    return {"id": 21, "run_number": 13, "run_attempt": 1, "head_sha": "b" * 40,
            "event": "push", "status": "completed", "conclusion": "success", "head_branch": "main",
            "head_repository": {"full_name": "EgooAI/MaaAlibabaSupplier"},
            "path": ".github/workflows/install.yml", "created_at": "2026-09-18T12:00:00Z", **changes}


def artifact(**changes):
    return {"id": 99, "name": "MaaAlibabaSupplier-Setup", "expired": False,
            "digest": "sha256:" + "c" * 64, **changes}


@pytest.fixture
def manager(monkeypatch, tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    (install / "build-info.json").write_text(json.dumps(build()), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    manager = updates.Updater(install)
    manager.state.update(supported=True, reason=None)
    manager.runtime = Mock(result=Mock(return_value=None), failed=Mock(return_value=False))
    monkeypatch.setattr(updates, "_instance", manager)
    yield manager
    manager.stop(timeout=None)


def remote(monkeypatch, runs=None, artifacts=None):
    """Fakes GitHub; `github.runs` and `github.artifacts` may be reassigned between cycles."""
    github = Mock()
    github.runs = runs if runs is not None else [run()]
    github.artifacts = artifacts if artifacts is not None else [artifact()]

    def fetch(path):
        if "/artifacts" in path:
            return {"artifacts": github.artifacts, "total_count": len(github.artifacts)}
        return {"workflow_runs": github.runs}

    github.json.side_effect = fetch
    monkeypatch.setattr(updates, "GitHub", lambda: github)
    return github


def serve(monkeypatch, tmp_path, *, runs=None, mismatch=False):
    """Fakes GitHub with a downloadable installer archive; `mismatch` corrupts the reported digest."""
    source = archive(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    github = remote(monkeypatch, runs=runs, artifacts=[artifact(digest="sha256:" + digest)])

    def download(path, target, progress):
        data = source.read_bytes()
        target.write_bytes(data)
        progress(len(data), len(data))
        return "0" * 64 if mismatch else digest

    github.download.side_effect = download
    return github


def stage_dirs(manager):
    base = updates.staging_base(manager.install)
    return {entry for entry in base.iterdir() if updates._STAGE_NAME.fullmatch(entry.name)}


def archive(tmp_path, *, name="MaaAlibabaSupplier-v1.2.3-Setup.exe", installer_data=b"installer test payload"):
    path = tmp_path / "download.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(name, installer_data)
    return path


def wait_operation(manager):
    for worker in manager._workers:
        if worker.name == "manual-updater":
            assert worker.join(3)
    assert manager.snapshot()["phase"] not in {"checking", "downloading"}


def test_observation_is_offline_and_development_unsupported(monkeypatch, tmp_path):
    network = Mock(side_effect=AssertionError("No observation network"))
    monkeypatch.setattr(updates, "GitHub", network)
    manager = updates.Updater(tmp_path)
    scheduler = Mock()
    monkeypatch.setattr(manager, "_start_scheduler", scheduler)
    manager.register_runtime()
    scheduler.assert_not_called()
    state = manager.snapshot()
    assert state["supported"] is False and state["reason"]
    assert state["phase"] == "idle" and state["current"] == {"version": "development", "sha": None, "run_id": None}
    assert state["source"]["branch"] == "main"
    with pytest.raises(AppError):
        manager.check()
    network.assert_not_called()


@pytest.mark.parametrize("change", [{"schema_version": True}, {"app_id": "wrong"}, {"run_id": "1"},
                                   {"run_attempt": False}, {"sha": "not-sha"}, {"repository": "../evil"}])
def test_local_build_metadata_is_validated(change):
    assert not updates.valid_build(build(**change))


def test_check_selects_newest_trusted_run_and_downloads_it(manager, monkeypatch, tmp_path):
    github = serve(monkeypatch, tmp_path, runs=[run(run_number=14, id=22), run()])
    state = manager.check()
    assert state["phase"] == "checking"
    wait_operation(manager)
    snapshot = manager.snapshot()
    candidate = snapshot["candidate"]
    assert snapshot["phase"] == "ready"
    assert candidate["run_id"] == 22 and len(candidate["id"]) == 32
    assert candidate["url"] == "https://github.com/EgooAI/MaaAlibabaSupplier/actions/runs/22"
    assert any("/runs/22/artifacts?" in call.args[0] for call in github.json.call_args_list)
    assert "/actions/artifacts/99/zip" in github.download.call_args.args[0]
    assert "digest" not in candidate


@pytest.mark.parametrize("change", [
    {"event": "pull_request"}, {"event": "workflow_dispatch"}, {"head_branch": "other"},
    {"head_repository": {"full_name": "attacker/MaaAlibabaSupplier"}}, {"conclusion": "failure"},
    {"status": "in_progress"}, {"path": ".github/workflows/other.yml"}, {"run_id": True, "id": True},
    {"head_sha": "untrusted"},
])
def test_check_excludes_untrusted_runs(manager, monkeypatch, change):
    github = remote(monkeypatch, runs=[run(**change)])
    manager._cycle(announce=True)
    assert manager.snapshot()["phase"] == "idle"
    assert manager.snapshot()["candidate"] is None
    assert github.json.call_count == 1
    github.download.assert_not_called()


@pytest.mark.parametrize("change", [{"run_number": 11}, {"run_number": 12}])
def test_check_does_not_reinstall_same_or_older_build(manager, monkeypatch, change):
    github = remote(monkeypatch, runs=[run(**change)])
    manager._cycle(announce=True)
    assert manager.snapshot()["phase"] == "idle"
    assert github.json.call_count == 1
    github.download.assert_not_called()


@pytest.mark.parametrize("change", [{"run_number": 13}, {"id": 20, "run_number": 12, "run_attempt": 2}])
def test_newer_run_or_attempt_with_same_sha_is_downloaded(manager, monkeypatch, tmp_path, change):
    serve(monkeypatch, tmp_path, runs=[run(head_sha="a" * 40, **change)])
    manager._cycle(announce=True)
    assert manager.snapshot()["phase"] == "ready"
    assert manager.state["candidate"]["sha"] == manager.build["sha"]


@pytest.mark.parametrize("artifacts", [[], [artifact(expired=True)], [artifact(name="other")],
                                     [artifact(), artifact(id=100)], [artifact(digest=None)]])
def test_missing_ambiguous_or_unverifiable_artifact_never_stops_app(manager, monkeypatch, artifacts):
    remote(monkeypatch, artifacts=artifacts)
    manager.check()
    wait_operation(manager)
    assert manager.snapshot()["phase"] == "error"
    manager.runtime.start.assert_not_called()
    assert not manager.handoff.is_set()


def test_single_async_operation_and_immediate_snapshot(manager, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def check(announce):
        entered.set()
        assert release.wait(3)
        with manager.lock:
            manager.state["phase"] = "idle"

    monkeypatch.setattr(manager, "_cycle", check)
    try:
        assert manager.check()["phase"] == "checking"
        assert entered.wait(1)
        with pytest.raises(AppError) as exc:
            manager.check()
        assert exc.value.status_code == 409
        assert manager.snapshot()["phase"] == "checking"
    finally:
        release.set()
        wait_operation(manager)


def test_verified_download_uses_github_identity_and_filename_version(manager, monkeypatch, tmp_path):
    source = archive(tmp_path, name="MaaAlibabaSupplier-v9.8.7-Setup.exe")
    github = remote(monkeypatch, artifacts=[artifact(digest="sha256:" + hashlib.sha256(source.read_bytes()).hexdigest())])

    def download(path, target, progress):
        data = source.read_bytes()
        target.write_bytes(data)
        progress(len(data), len(data))
        return hashlib.sha256(data).hexdigest()

    github.download.side_effect = download
    manager.check()
    wait_operation(manager)
    state = manager.snapshot()
    assert state["phase"] == "ready" and state["candidate"]["version"] == "v9.8.7"
    assert state["downloaded_bytes"] == source.stat().st_size
    installer, expected = manager.prepared
    assert installer.is_file() and not installer.is_relative_to(manager.install)
    assert expected == build(version="v9.8.7", sha=run()["head_sha"], run_id=run()["id"],
                             run_number=run()["run_number"], run_attempt=run()["run_attempt"],
                             installer_sha256=hashlib.sha256(installer.read_bytes()).hexdigest())
    assert {path.name for path in installer.parent.iterdir()} == {installer.name}
    manager.runtime.start.assert_not_called()
    assert not manager.handoff.is_set()


def test_bad_zip_digest_blocks_extraction(manager, monkeypatch, tmp_path):
    source = archive(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    github = remote(monkeypatch, artifacts=[artifact(digest="sha256:" + digest)])
    archive(tmp_path, installer_data=b"replaced installer")

    def download(path, target, progress):
        data = source.read_bytes()
        target.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    github.download.side_effect = download
    unpack = Mock(side_effect=AssertionError("Must not extract"))
    monkeypatch.setattr(updates, "unpack_verified", unpack)
    manager.check()
    wait_operation(manager)
    assert manager.snapshot()["phase"] == "error"
    unpack.assert_not_called()
    assert manager.prepared is None
    assert stage_dirs(manager) == set()
    manager.runtime.start.assert_not_called()


@pytest.mark.parametrize("name", ["../escape.exe", "dir/setup.exe", "dir\\setup.exe", "C:setup.exe",
                                 "/absolute.exe", "SETUP.EXE:stream", "update-manifest.json",
                                 "MaaAlibabaSupplier-Setup.exe", "Other-v1.2.3-Setup.exe",
                                 "MaaAlibabaSupplier-v-Setup.exe", "MaaAlibabaSupplier-v../escape-Setup.exe",
                                 "MaaAlibabaSupplier-v1 2-Setup.exe", "MaaAlibabaSupplier-v.1-Setup.exe",
                                 "MaaAlibabaSupplier-v1.2.3-Setup.EXE"])
def test_zip_rejects_unsafe_or_unexpected_names(tmp_path, name):
    path = archive(tmp_path, name=name)
    with pytest.raises(updates.UpdateError):
        updates.unpack_verified(path, tmp_path, build())
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO, stat.S_IFCHR])
def test_zip_rejects_nonregular_entries(tmp_path, mode):
    entry = zipfile.ZipInfo("MaaAlibabaSupplier-v1.2.3-Setup.exe")
    entry.create_system = 3
    entry.external_attr = (mode | 0o777) << 16
    path = archive(tmp_path, name=entry)
    with pytest.raises(updates.UpdateError, match="unsafe"):
        updates.unpack_verified(path, tmp_path, build())


@pytest.mark.parametrize("version", ["v1.2.3+build.4", "v0.0.0-local", "v20260918_rc-1", "vv1.2.3"])
def test_packaged_safe_version_filename_is_accepted(tmp_path, version):
    name = f"MaaAlibabaSupplier-{version}-Setup.exe"
    path = archive(tmp_path, name=name)
    installer, prepared = updates.unpack_verified(path, tmp_path, build())
    assert installer.name == name
    assert prepared["version"] == version
    assert prepared["installer_sha256"] == hashlib.sha256(installer.read_bytes()).hexdigest()


@pytest.mark.parametrize("extra", ["update-manifest.json", "other.json", "MaaAlibabaSupplier-v1.2.3-Setup.exe",
                                  "MaaAlibabaSupplier-v9.9.9-Setup.exe", "folder/"])
def test_zip_rejects_extra_files_including_manifest_and_duplicate_installers(tmp_path, extra):
    path = archive(tmp_path)
    with pytest.warns(UserWarning) if extra == "MaaAlibabaSupplier-v1.2.3-Setup.exe" else nullcontext():
        with zipfile.ZipFile(path, "a") as bundle:
            bundle.writestr(extra, json.dumps(build()))
    with pytest.raises(updates.UpdateError, match="exactly one"):
        updates.unpack_verified(path, tmp_path, build())
    assert list(tmp_path.iterdir()) == [path]


def test_zip_rejects_empty_installer_or_archive(tmp_path):
    path = archive(tmp_path, installer_data=b"")
    with pytest.raises(updates.UpdateError, match="unsafe"):
        updates.unpack_verified(path, tmp_path, build())
    with zipfile.ZipFile(path, "w"):
        pass
    with pytest.raises(updates.UpdateError, match="exactly one"):
        updates.unpack_verified(path, tmp_path, build())


def test_zip_size_and_compression_bounds(tmp_path, monkeypatch):
    path = archive(tmp_path)
    monkeypatch.setattr(updates, "MAX_UNPACKED", 10)
    with pytest.raises(updates.UpdateError):
        updates.unpack_verified(path, tmp_path, build())


def test_zip_rejects_excessive_compression(tmp_path):
    path = tmp_path / "download.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("MaaAlibabaSupplier-v1.2.3-Setup.exe", b"a" * 1000000)
    with pytest.raises(updates.UpdateError, match="unsafe"):
        updates.unpack_verified(path, tmp_path, build())


class Response(io.BytesIO):
    def __init__(self, data=b"payload", headers=None):
        super().__init__(data)
        self.headers = headers or {}


def test_download_credentials_only_on_initial_api_request(monkeypatch, tmp_path):
    monkeypatch.setenv("MAA_UPDATE_GITHUB_TOKEN", "private-test-token")
    github = updates.GitHub()
    redirect = urllib.error.HTTPError("api", 302, "redirect", {"Location": "https://example.blob.core.windows.net/artifact?sig=secret"}, None)
    github._opener = Mock()
    github._opener.open.side_effect = [redirect, Response()]
    digest = github.download("/repos/org/repo/actions/artifacts/1/zip", tmp_path / "download", Mock())
    assert digest == hashlib.sha256(b"payload").hexdigest()
    requests = [call.args[0] for call in github._opener.open.call_args_list]
    assert requests[0].get_header("Authorization") == "Bearer private-test-token"
    assert requests[1].get_header("Authorization") is None


@pytest.mark.parametrize("url", ["http://objects.githubusercontent.com/a", "https://evil.example/a",
                                "https://api.github.com.evil.example/a", "https://blob.core.windows.net.evil.example/a",
                                "https://user:secret@api.github.com/a", "https://api.github.com:8443/a"])
def test_rejects_unsafe_redirect_locations(url):
    github = updates.GitHub()
    github._opener = Mock()
    with pytest.raises(updates.UpdateError):
        github._open(url, api=False)
    github._opener.open.assert_not_called()


def test_api_redirect_is_not_followed_or_leaked():
    github = updates.GitHub()
    github._opener = Mock()
    github._opener.open.side_effect = urllib.error.HTTPError("api", 302, "redirect", {"Location": "https://evil.example/secret"}, None)
    with pytest.raises(updates.UpdateError) as exc:
        github.json("/repos/org/repo")
    assert "secret" not in str(exc.value)


def test_stream_bounds_and_incomplete_response(tmp_path, monkeypatch):
    github = updates.GitHub()
    github._opener = Mock()
    github._opener.open.return_value = Response(b"payload", {"Content-Length": "100"})
    with pytest.raises(updates.UpdateError, match="incomplete"):
        github.download("/download", tmp_path / "short", Mock())
    monkeypatch.setattr(updates, "MAX_ZIP", 3)
    github._opener.open.return_value = Response(b"payload")
    with pytest.raises(updates.UpdateError, match="size"):
        github.download("/download", tmp_path / "long", Mock())


def make_ready(manager):
    installer = manager.install / "verified.exe"
    installer.write_bytes(b"installer")
    manager.state.update(phase="ready", candidate={"id": "opaque", "version": "v1.2.3"})
    manager.prepared = (installer, build())
    manager._ready_since = manager._clock()


def test_install_auth_gate_no_account_lock_and_arms_after_response(manager, monkeypatch):
    make_ready(manager)
    forbidden = Mock(side_effect=AssertionError("Account context must not be consulted"))
    monkeypatch.setattr(main, "get_account_context", forbidden)
    delivered = threading.Event()
    app = main.create_app()

    async def observed_app(scope, receive, send):
        async def observed_send(message):
            await send(message)
            if (scope.get("path") == "/api/app/update/install" and message["type"] == "http.response.body"
                    and not message.get("more_body", False)):
                delivered.set()
        await app(scope, receive, observed_send)

    def arm():
        assert delivered.is_set(), "The helper must not be armed before the complete response is sent"

    manager.runtime.arm.side_effect = arm
    with TestClient(observed_app) as client:
        authenticate(client)
        response = client.post("/api/app/update/install", json={"candidate_id": "opaque", "confirm": True})
        assert response.status_code == 200 and response.json()["data"] == {"accepted": True}
        manager.runtime.start.assert_called_once_with(*manager.prepared)
        manager.runtime.arm.assert_called_once()
        assert manager.handoff.is_set()
        assert client.get("/api/app/update").status_code == 200
        blocked = client.post("/api/messages", content="malformed")
        assert blocked.status_code == 409 and blocked.json()["code"] != 0
        client.headers.pop("Authorization")
        assert client.post("/api/app/update/check", json={}).status_code == 401
    forbidden.assert_not_called()


@pytest.mark.parametrize("body", [{"candidate_id": "opaque", "confirm": False}, {"candidate_id": "opaque"},
                                 {"candidate_id": "opaque", "confirm": 1},
                                 {"candidate_id": "opaque", "confirm": True, "pid": 42},
                                 {"candidate_id": "opaque", "confirm": True, "url": "https://evil.example"}])
def test_install_rejects_missing_confirmation_and_browser_targets(manager, body):
    make_ready(manager)
    with TestClient(main.create_app()) as client:
        authenticate(client)
        assert client.post("/api/app/update/install", json=body).status_code == 422
    manager.runtime.start.assert_not_called()


def test_stale_candidate_and_handshake_failure_leave_app_running(manager):
    make_ready(manager)
    with pytest.raises(AppError) as exc:
        manager.install_update("stale")
    assert exc.value.status_code == 409 and not manager.handoff.is_set()
    manager.runtime.start.side_effect = RuntimeError("secret/path")
    with pytest.raises(AppError) as exc:
        manager.install_update("opaque")
    assert exc.value.status_code == 503 and "secret" not in exc.value.message
    manager.runtime.cancel.assert_called_once()
    manager.runtime.arm.assert_not_called()
    assert not manager.handoff.is_set()
    assert manager.state["phase"] == "ready" and manager.prepared is not None


def test_second_install_rejected_during_handshake_without_blocking_observation(manager):
    make_ready(manager)
    entered, release = threading.Event(), threading.Event()

    def prepare(*args):
        entered.set()
        assert release.wait(3)

    manager.runtime.start.side_effect = prepare
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.install_update, "opaque")
        try:
            assert entered.wait(1)
            assert manager.snapshot()["phase"] == "installing"
            with pytest.raises(AppError):
                manager.install_update("opaque")
        finally:
            release.set()
        future.result()


def test_cancelled_handoff_unblocks_next_write_without_observation(manager):
    make_ready(manager)
    manager.install_update("opaque")
    manager.runtime.failed.return_value = True
    assert not manager.writes_blocked()
    assert manager.state["phase"] == "ready"
    assert not manager.handoff.is_set()
    assert manager.prepared is not None


def test_durable_result_loaded_without_network(manager):
    path = updates.staging_base(manager.install)
    path.mkdir(parents=True)
    result = {"status": "error", "message": "Installer failed", "version": "v1.2.3"}
    updates.atomic_json(path / "last-result.json", result)
    fresh = updates.Updater(manager.install)
    assert fresh.snapshot()["last_result"] == result


def test_interrupted_installing_result_is_ignored(manager):
    path = updates.staging_base(manager.install)
    path.mkdir(parents=True)
    updates.atomic_json(path / "last-result.json",
                        {"status": "installing", "message": "pending", "version": "v1.2.3"})
    fresh = updates.Updater(manager.install)
    assert fresh.snapshot()["last_result"] is None


def test_interrupted_installing_result_emits_startup_event(manager, monkeypatch):
    path = updates.staging_base(manager.install)
    path.mkdir(parents=True)
    updates.atomic_json(path / "last-result.json",
                        {"status": "installing", "message": "pending", "version": "v1.2.3"})
    events = []
    monkeypatch.setattr(updates, "log_event", lambda event, **fields: events.append((event, fields)))
    updates.Updater(manager.install)
    assert events == [("update.result", {"status": "interrupted", "version": "v1.2.3"})]


def test_snapshot_emits_result_event_only_when_the_terminal_result_changes(manager, monkeypatch):
    events = []
    monkeypatch.setattr(updates, "log_event", lambda event, **fields: events.append((event, fields)))
    manager.runtime.result.return_value = {"status": "installed", "message": "ok", "version": "v1.2.3"}
    manager.snapshot()
    manager.snapshot()
    assert events == [("update.result", {"status": "installed", "version": "v1.2.3"})]


def test_snapshot_reports_the_stale_result_of_an_older_version(manager, monkeypatch):
    events = []
    monkeypatch.setattr(updates, "log_event", lambda event, **fields: events.append((event, fields)))
    manager.runtime.result.return_value = {"status": "installed", "message": "ok", "version": "v0.9.0"}
    manager.snapshot()
    assert events == [("update.result", {"status": "installed", "version": "v0.9.0"}),
                      ("update.result", {"status": "stale", "version": "v0.9.0"})]


def test_a_result_older_than_the_running_build_is_reported_as_stale_at_startup(manager, monkeypatch):
    events = []
    monkeypatch.setattr(updates, "log_event", lambda event, **fields: events.append((event, fields)))
    path = updates.staging_base(manager.install)
    path.mkdir(parents=True)
    updates.atomic_json(path / "last-result.json",
                        {"status": "installed", "message": "ok", "version": "v0.9.0"})
    updates.Updater(manager.install)
    assert ("update.result", {"status": "stale", "version": "v0.9.0"}) in events

    events.clear()
    updates.atomic_json(path / "last-result.json",
                        {"status": "installed", "message": "ok", "version": "v1.2.3"})
    assert updates.Updater(manager.install).state["last_result"]["version"] == "v1.2.3"
    assert events == []


def test_update_lifecycle_events_cover_check_download_and_handoff(manager, monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(updates, "log_event", lambda event, **fields: events.append((event, fields)))
    source = archive(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    github = remote(monkeypatch, artifacts=[artifact(digest="sha256:" + digest)])

    def download(path, target, progress):
        data = source.read_bytes()
        target.write_bytes(data)
        progress(len(data), len(data))
        return digest

    github.download.side_effect = download
    manager.check()
    wait_operation(manager)
    assert ("update.check", {"status": "candidate", "run_id": 21, "run_number": 13, "run_attempt": 1}) in events
    assert ("update.download", {"status": "started", "run_id": 21}) in events
    ready = [fields for event, fields in events if event == "update.download" and fields["status"] == "ready"]
    assert ready == [{"status": "ready", "run_id": 21, "count": source.stat().st_size}]

    manager.install_update(manager.state["candidate"]["id"])
    assert ("update.handoff", {"status": "accepted", "version": "v1.2.3"}) in events
    manager.arm(manager._handoff_id)
    assert ("update.handoff", {"status": "go"}) in events


def test_current_build_check_is_recorded_without_a_candidate(manager, monkeypatch):
    events = []
    monkeypatch.setattr(updates, "log_event", lambda event, **fields: events.append((event, fields)))
    remote(monkeypatch, runs=[run(run_number=12, id=20)])
    manager._cycle(announce=True)
    assert events == [("update.check", {"status": "current", "run_id": 20})]


def test_cancelled_and_rejected_handoffs_record_their_reason(manager, monkeypatch):
    events = []
    monkeypatch.setattr(updates, "log_event", lambda event, **fields: events.append((event, fields)))
    make_ready(manager)
    manager.runtime.start.side_effect = updates.UpdateError("The updater helper did not accept the installation.")
    with pytest.raises(AppError):
        manager.install_update("opaque")
    assert ("update.handoff", {"status": "rejected", "reason": "start_failed",
                               "exception_type": "UpdateError", "version": "v1.2.3"}) in events

    manager.runtime.start.side_effect = None
    make_ready(manager)
    manager.install_update("opaque")
    manager.runtime.failed.return_value = True
    assert not manager.writes_blocked()
    assert ("update.handoff", {"status": "cancelled", "reason": "helper_exited"}) in events

    make_ready(manager)
    operation = manager.install_update("opaque")
    manager.cancel_install(operation)
    assert ("update.handoff", {"status": "cancelled", "reason": "response_failed"}) in events


def test_snapshot_does_not_read_result_file_during_handoff(manager):
    make_ready(manager)
    manager.install_update("opaque")
    manager.snapshot()
    manager.runtime.result.assert_not_called()
    manager.cancel_install(manager._handoff_id)


def test_powershell_preference_and_command_fallback(monkeypatch, tmp_path):
    paths = {"pwsh": str(tmp_path / "pwsh.exe"), "powershell": str(tmp_path / "powershell.exe")}
    monkeypatch.setattr(updates.shutil, "which", paths.get)
    assert updates.resolve_powershell(tmp_path / "install") == paths["pwsh"]
    paths.pop("pwsh")
    assert updates.resolve_powershell(tmp_path / "install") == paths["powershell"]


def test_powershell_system_fallback_and_no_install_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(updates.shutil, "which", lambda name: str(tmp_path / "install" / name))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "windows"))
    executable = tmp_path / "windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    assert updates.resolve_powershell(tmp_path / "install") == str(executable)


def test_staging_cannot_be_inside_install(manager, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(manager.install / "local"))
    with pytest.raises(updates.UpdateError):
        updates.staging_base(manager.install)


def make_stages(base: Path, count: int, populate=None) -> list[Path]:
    """Create uuid-named stage directories pinned to ascending old timestamps."""
    stages = [base / uuid.uuid4().hex for _ in range(count)]
    for index, stage in enumerate(stages):
        stage.mkdir()
        if populate is not None:
            populate(stage)
        stamp = 1_700_000_000 + index
        os.utime(stage, (stamp, stamp))
    return stages


def test_stage_pruning_keeps_recent_and_never_base_files(tmp_path):
    base = tmp_path / "staging"
    base.mkdir()
    stages = make_stages(base, 6)
    (base / "last-result.json").write_text("{}", encoding="utf-8")
    (base / "last-result.json.tmp").write_text("{}", encoding="utf-8")
    (base / "not-uuid").mkdir()

    removed = updates.prune_stage_directories(base, keep=4)

    assert removed == 2
    kept = {stages[-4].name, stages[-3].name, stages[-2].name, stages[-1].name}
    live = {entry.name for entry in base.iterdir()}
    assert live == kept | {"last-result.json", "last-result.json.tmp", "not-uuid"}


def test_prune_continues_when_a_directory_is_locked(monkeypatch, tmp_path):
    base = tmp_path / "staging"
    base.mkdir()
    stages = make_stages(base, 5, lambda stage: (stage / "installer.log").write_text("x", encoding="utf-8"))
    locked = stages[0]
    real = updates.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if Path(path) == locked:
            raise OSError("sharing violation")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(updates.shutil, "rmtree", rmtree)
    removed = updates.prune_stage_directories(base, keep=2)
    assert removed == 2
    assert locked.is_dir() and (locked / "installer.log").is_file()
    assert {entry.name for entry in base.iterdir()} == {locked.name, stages[3].name, stages[4].name}


def test_prune_ignores_a_missing_base(tmp_path):
    assert updates.prune_stage_directories(tmp_path / "absent") == 0


def test_download_prunes_old_stages_around_the_new_one(manager, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    base = updates.staging_base(manager.install)
    base.mkdir(parents=True)
    stages = make_stages(base, 6)
    serve(monkeypatch, tmp_path)
    manager.check()
    wait_operation(manager)
    assert manager.snapshot()["phase"] == "ready"
    stage_names = {entry.name for entry in base.iterdir() if updates._STAGE_NAME.fullmatch(entry.name)}
    # The new stage is protected and does not count toward the retained four.
    assert stage_names == {stage.name for stage in stages[2:]} | {manager.prepared[0].parent.name}


def test_local_packaging_null_identity_is_supported_metadata():
    metadata = build(repository=None, sha=None, run_id=0, run_number=0, run_attempt=0)
    assert updates.valid_build(metadata)


async def asgi_install(app, send, *, disconnect=False):
    body = json.dumps({"candidate_id": "opaque", "confirm": True}).encode()
    token = app.state.auth_store.issue()
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        if disconnect:
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()

    await app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
               "http_version": "1.1", "method": "POST", "scheme": "http",
               "path": "/api/app/update/install", "raw_path": b"/api/app/update/install",
               "root_path": "", "query_string": b"", "client": ("test", 1), "server": ("test", 80),
               "headers": [(b"authorization", ("Bearer " + token).encode()),
                           (b"content-type", b"application/json")]}, receive, send)


def test_real_asgi_delayed_outer_body_send_finishes_before_arm(manager):
    make_ready(manager)
    delivered = []

    async def send(message):
        if message["type"] == "http.response.body":
            await asyncio.sleep(0.1)
            manager.runtime.arm.assert_not_called()
            if not message.get("more_body", False):
                delivered.append(True)

    manager.runtime.arm.side_effect = lambda: delivered.append("armed")
    asyncio.run(asgi_install(main.create_app(), send))
    assert delivered == [True, "armed"]
    manager.runtime.start.assert_called_once()
    manager.runtime.cancel.assert_not_called()


@pytest.mark.parametrize("failure", ["headers", "body", "cancel"])
def test_real_asgi_send_failure_or_cancellation_releases_uncommitted_handoff(manager, failure):
    make_ready(manager)

    async def send(message):
        if ((failure == "headers" and message["type"] == "http.response.start")
                or (failure != "headers" and message["type"] == "http.response.body")):
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise OSError("simulated transport failure")

    with pytest.raises((Exception, asyncio.CancelledError)):
        asyncio.run(asgi_install(main.create_app(), send))
    manager.runtime.start.assert_called_once()
    manager.runtime.arm.assert_not_called()
    manager.runtime.cancel.assert_called_once()
    assert not manager.handoff.is_set()
    assert manager.state["phase"] == "ready"


@pytest.mark.parametrize("failure", ["exception", "error_status"])
def test_real_basehttp_failure_after_prepare_cancels_before_next_write(manager, failure):
    make_ready(manager)

    class FailResponse(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path == "/api/app/update/install":
                if failure == "exception":
                    raise RuntimeError("injected after endpoint preparation")
                response.status_code = 503
            return response

    app = main.create_app()
    # Auth remains outside the injected BaseHTTP middleware and actual router.
    app.user_middleware.insert(1, Middleware(FailResponse))

    @app.post("/api/write-probe")
    def write_probe():
        return {"accepted": True}

    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(asgi_install(app, send))
    assert messages[0]["status"] in {500, 503}
    manager.runtime.start.assert_called_once()
    manager.runtime.cancel.assert_called_once()
    manager.runtime.arm.assert_not_called()
    assert not manager.handoff.is_set()
    with TestClient(app) as client:
        authenticate(client)
        assert client.post("/api/write-probe").status_code == 200


def test_real_asgi_disconnect_without_send_error_cancels(manager):
    make_ready(manager)

    class ObserveDisconnect:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            async def response(message):
                if message["type"] == "http.response.start":
                    assert (await receive())["type"] == "http.disconnect"
                await send(message)
            await self.app(scope, receive, response)

    app = main.create_app()
    app.user_middleware.insert(1, Middleware(ObserveDisconnect))

    async def send(message):
        if message["type"] == "http.response.body":
            await asyncio.sleep(0.05)

    asyncio.run(asgi_install(app, send, disconnect=True))
    manager.runtime.start.assert_called_once()
    manager.runtime.arm.assert_not_called()
    manager.runtime.cancel.assert_called_once()
    assert not manager.handoff.is_set()


def test_watchdog_reconciles_helper_failure_without_any_http_request(manager):
    make_ready(manager)
    manager.install_update("opaque")
    manager.runtime.failed.return_value = True
    assert manager._handoff_finished.wait(2)
    assert not manager.handoff.is_set()
    assert manager.state["phase"] == "ready"


def test_response_finishing_after_pending_lease_expiry_cannot_arm(manager):
    make_ready(manager)

    async def send(message):
        if message["type"] == "http.response.body":
            with manager.lock:
                manager._handoff_deadline = 0
            assert await asyncio.to_thread(manager._handoff_finished.wait, 2)
            assert not manager.handoff.is_set()

    asyncio.run(asgi_install(main.create_app(), send))
    manager.runtime.start.assert_called_once()
    manager.runtime.arm.assert_not_called()
    manager.runtime.cancel.assert_called_once()


def test_write_gate_reconciles_helper_failure_without_get_or_watchdog(manager):
    make_ready(manager)
    manager.install_update("opaque")
    manager._handoff_finished.set()
    manager.runtime.failed.return_value = True
    app = main.create_app()

    @app.post("/api/write-probe")
    def write_probe():
        return {"accepted": True}

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + app.state.auth_store.issue()
        assert client.post("/api/write-probe").status_code == 200
    assert not manager.handoff.is_set()


def test_stale_response_callbacks_cannot_arm_or_cancel_new_handoff(manager):
    make_ready(manager)
    previous = manager.install_update("opaque")
    manager.cancel_install(previous)
    make_ready(manager)
    current = manager.install_update("opaque")
    manager.arm(previous)
    manager.cancel_install(previous)
    manager.runtime.arm.assert_not_called()
    assert manager.handoff.is_set()
    manager.arm(current)
    manager.runtime.arm.assert_called_once()


@pytest.mark.parametrize("fail", [False, True])
def test_download_verifies_without_starting_a_broker(manager, monkeypatch, tmp_path, fail):
    serve(monkeypatch, tmp_path, mismatch=fail)
    manager.check()
    wait_operation(manager)
    assert manager.state["phase"] == ("error" if fail else "ready")
    assert manager.prepared is None if fail else manager.prepared is not None
    manager.runtime.start.assert_not_called()
    assert not manager.writes_blocked()


def test_failed_broker_start_keeps_candidate_ready_for_retry(manager):
    make_ready(manager)
    manager.runtime.start.side_effect = updates.UpdateError("The updater helper did not accept the installation.")
    with pytest.raises(AppError) as exc:
        manager.install_update("opaque")
    assert exc.value.status_code == 503
    assert not manager.handoff.is_set()
    assert manager.state["phase"] == "ready" and manager.prepared is not None

    manager.runtime.start.side_effect = None
    manager.install_update("opaque")
    assert manager.handoff.is_set()
    assert manager.runtime.start.call_count == 2


def test_same_build_is_not_downloaded_again_once_ready(manager, monkeypatch, tmp_path):
    github = serve(monkeypatch, tmp_path)
    manager.check()
    wait_operation(manager)
    installer = manager.prepared[0]
    assert github.download.call_count == 1

    assert manager._scheduled_cycle() is True
    manager.check()
    wait_operation(manager)

    assert github.download.call_count == 1
    assert manager.prepared[0] == installer and installer.is_file()
    assert manager.snapshot()["phase"] == "ready"


def test_newer_build_replaces_the_installer_and_deletes_the_old_one(manager, monkeypatch, tmp_path):
    github = serve(monkeypatch, tmp_path)
    manager.check()
    wait_operation(manager)
    old = manager.prepared[0]

    github.runs = [run(id=22, run_number=14)]
    manager.check()
    wait_operation(manager)

    new = manager.prepared[0]
    assert new != old and new.is_file() and not old.exists()
    assert manager.snapshot()["candidate"]["run_id"] == 22 and manager.snapshot()["phase"] == "ready"
    assert list(updates.staging_base(manager.install).rglob("MaaAlibabaSupplier-v*-Setup.exe")) == [new]


def test_failed_replacement_keeps_the_verified_installer_installable(manager, monkeypatch, tmp_path):
    github = serve(monkeypatch, tmp_path)
    manager.check()
    wait_operation(manager)
    installer = manager.prepared[0]
    candidate = manager.snapshot()["candidate"]["id"]

    github.runs = [run(id=22, run_number=14)]
    github.download.side_effect = updates.UpdateError("offline")
    manager.check()
    wait_operation(manager)

    state = manager.snapshot()
    assert state["phase"] == "ready" and state["candidate"]["id"] == candidate and state["error"] == "offline"
    assert installer.is_file() and stage_dirs(manager) == {installer.parent}
    manager.install_update(candidate)
    manager.runtime.start.assert_called_once_with(*manager.prepared)


@pytest.mark.parametrize("active", ["checking", "downloading", "installing", "busy", "handoff", "unsupported"])
def test_scheduled_cycle_never_overlaps_another_operation(manager, monkeypatch, active):
    github = remote(monkeypatch)
    if active == "busy":
        manager._busy = True
    elif active == "handoff":
        manager.handoff.set()
    elif active == "unsupported":
        manager.state["supported"] = False
    else:
        manager.state["phase"] = active
    assert manager._scheduled_cycle() is True
    github.json.assert_not_called()


def test_scheduled_cycle_leaves_the_state_alone_when_current_or_offline(manager, monkeypatch):
    github = remote(monkeypatch, runs=[run(run_number=12, id=20)])
    before = manager.snapshot()
    assert manager._scheduled_cycle() is True
    assert manager.snapshot() == before

    github.json.side_effect = updates.UpdateError("offline")
    assert manager._scheduled_cycle() is False
    assert manager.snapshot() == before
    assert manager._busy is False


def test_guarded_operation_releases_busy_when_interrupted(manager):
    manager._busy = True

    def stop():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        manager._run_guarded("checking", stop)
    assert manager._busy is False


def test_schedule_preserves_initial_delay_backoff_cap_and_recovery(manager, monkeypatch):
    schedule = Mock(wraps=manager._background.schedule)
    monkeypatch.setattr(manager._background, "schedule", schedule)
    results = iter([False, False, True] + [False] * 10)
    monkeypatch.setattr(manager, "_scheduled_cycle", lambda: next(results))
    manager._start_scheduler()
    assert schedule.call_args.kwargs == {"interval": 300, "delay": 60, "control": True}
    action = schedule.call_args.args[1]
    assert [action() for _ in range(3)] == [600, 1200, 300]
    assert [action() for _ in range(10)][-1] == updates.AUTO_BACKOFF_MAX


@pytest.mark.parametrize("automatic", [False, True])
def test_shutdown_drains_inflight_checks_and_rejects_new_work(manager, monkeypatch, tmp_path, automatic):
    entered, release = threading.Event(), threading.Event()
    written = tmp_path / "operation-finished"

    def cycle(announce):
        entered.set()
        assert release.wait(3)
        written.write_text("finished", encoding="utf-8")
        with manager.lock:
            manager.state["phase"] = "idle"

    monkeypatch.setattr(manager, "_cycle", cycle)
    if automatic:
        manager._start_scheduler()
        manager._workers[-1].trigger()
    else:
        manager.check()
    try:
        assert entered.wait(3)
        with pytest.raises(TimeoutError):
            manager.stop(timeout=0)
        assert not written.exists()
        with pytest.raises(AppError) as exc:
            manager.check()
        assert exc.value.status_code == 503
    finally:
        release.set()
        manager.stop(timeout=3)
    assert written.read_text(encoding="utf-8") == "finished"
    assert not manager._busy
    assert all(not worker.is_alive() for worker in manager._workers)


@pytest.mark.parametrize("armed", [False, True])
def test_shutdown_joins_watchdog_and_only_cancels_uncommitted_handoff(manager, armed):
    make_ready(manager)
    operation = manager.install_update("opaque")
    if armed:
        manager.arm(operation)
    updates.stop_update_scheduler()
    assert all(not worker.is_alive() for worker in manager._workers)
    assert manager._handoff_finished.is_set()
    assert manager.runtime.cancel.call_count == (0 if armed else 1)
    manager.arm(operation)
    assert manager.runtime.arm.call_count == (1 if armed else 0)
    with pytest.raises(AppError) as exc:
        manager.install_update("opaque")
    assert exc.value.status_code == 503


def test_shutdown_waits_for_helper_acceptance_and_prevents_late_handoff(manager):
    make_ready(manager)
    entered, release = threading.Event(), threading.Event()

    def prepare(*args):
        entered.set()
        assert release.wait(3)

    manager.runtime.start.side_effect = prepare
    with ThreadPoolExecutor(max_workers=1) as pool:
        installing = pool.submit(manager.install_update, "opaque")
        try:
            assert entered.wait(3)
            with pytest.raises(TimeoutError):
                manager.stop(timeout=0)
        finally:
            release.set()
        with pytest.raises(AppError) as exc:
            installing.result(timeout=3)
        assert exc.value.status_code == 503
    manager.stop(timeout=3)
    manager.runtime.cancel.assert_called_once()
    manager.runtime.arm.assert_not_called()
    assert not manager.handoff.is_set()
    assert all(not worker.is_alive() for worker in manager._workers)


def test_purge_removes_unreferenced_installers_but_keeps_logs_and_protected_stage(tmp_path):
    base = tmp_path / "staging"
    base.mkdir()

    def populate(stage):
        for name in ("MaaAlibabaSupplier-v1.2.3-Setup.exe", "artifact.zip", "artifact.zip.part", "installer.log"):
            (stage / name).write_bytes(b"x")

    old, live = make_stages(base, 2, populate)
    (base / "last-result.json").write_text("{}", encoding="utf-8")
    assert updates.purge_stale_artifacts(base, {live.resolve()}) == 3
    assert {entry.name for entry in old.iterdir()} == {"installer.log"}
    assert len(list(live.iterdir())) == 4
    assert (base / "last-result.json").is_file()


def test_reconcile_deletes_stale_installers_unless_installing_or_downloading(manager):
    base = updates.staging_base(manager.install)
    base.mkdir(parents=True)
    (stale,) = make_stages(base, 1, lambda stage: (stage / "MaaAlibabaSupplier-v1.2.3-Setup.exe").write_bytes(b"x"))
    leftover = stale / "MaaAlibabaSupplier-v1.2.3-Setup.exe"

    manager.state["phase"] = "installing"
    assert manager.reconcile_staging() == 0 and leftover.is_file()

    manager.state["phase"] = "idle"
    manager._stage = stale
    assert manager.reconcile_staging() == 0 and leftover.is_file()

    manager._stage = None
    assert manager.reconcile_staging() == 1 and not leftover.exists()


def test_install_rejects_a_vanished_installer_and_asks_for_a_new_check(manager):
    make_ready(manager)
    manager.prepared[0].unlink()
    with pytest.raises(AppError) as exc:
        manager.install_update("opaque")
    assert exc.value.status_code == 409
    assert manager.state["phase"] == "idle" and manager.prepared is None
    manager.runtime.start.assert_not_called()


def test_download_fails_before_writing_when_the_disk_is_short(tmp_path, monkeypatch):
    github = updates.GitHub()
    github._opener = Mock()
    github._opener.open.return_value = Response(b"payload", {"Content-Length": "7"})
    monkeypatch.setattr(updates.shutil, "disk_usage", lambda path: Mock(free=updates.DISK_MARGIN))
    progress = Mock()
    with pytest.raises(updates.UpdateError, match="disk space"):
        github.download("/download", tmp_path / "archive", progress)
    assert not (tmp_path / "archive").exists()
    progress.assert_not_called()
