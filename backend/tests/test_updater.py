import asyncio
import hashlib
import io
import json
import stat
import threading
import urllib.error
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
    manager = updates.Updater(install)
    manager.state.update(supported=True, reason=None)
    manager.runtime = Mock(result=Mock(return_value=None), failed=Mock(return_value=False))
    monkeypatch.setattr(updates, "_instance", manager)
    yield manager
    manager._handoff_finished.set()


def remote(monkeypatch, runs=None, artifacts=None):
    github = Mock()
    github.json.side_effect = [{"workflow_runs": runs if runs is not None else [run()]},
                              {"artifacts": artifacts if artifacts is not None else [artifact()], "total_count": 1}]
    monkeypatch.setattr(updates, "GitHub", lambda: github)
    return github


def archive(tmp_path, *, name="MaaAlibabaSupplier-v1.2.3-Setup.exe", installer_data=b"installer test payload"):
    path = tmp_path / "download.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(name, installer_data)
    return path


def wait_operation(manager):
    # Synchronize with a worker completion without relying on a fixed sleep.
    for thread in threading.enumerate():
        if thread.name == "manual-updater":
            thread.join(3)
    assert manager.snapshot()["phase"] not in {"checking", "downloading"}


def test_observation_is_offline_and_development_unsupported(monkeypatch, tmp_path):
    network = Mock(side_effect=AssertionError("No observation network"))
    monkeypatch.setattr(updates, "GitHub", network)
    manager = updates.Updater(tmp_path)
    manager.register_runtime()
    state = manager.snapshot()
    assert state["supported"] is False and state["reason"]
    assert state["phase"] == "idle" and state["current"] == {"version": "development", "sha": None}
    assert state["source"]["branch"] == "main"
    with pytest.raises(AppError):
        manager.check()
    network.assert_not_called()


@pytest.mark.parametrize("change", [{"schema_version": True}, {"app_id": "wrong"}, {"run_id": "1"},
                                   {"run_attempt": False}, {"sha": "not-sha"}, {"repository": "../evil"}])
def test_local_build_metadata_is_validated(change):
    assert not updates.valid_build(build(**change))


def test_check_selects_newest_trusted_run_and_opaque_candidate(manager, monkeypatch):
    github = remote(monkeypatch, runs=[run(run_number=14, id=22), run()])
    state = manager.check()
    assert state["phase"] == "checking"
    wait_operation(manager)
    candidate = manager.snapshot()["candidate"]
    assert candidate["run_id"] == 22 and len(candidate["id"]) == 32
    assert candidate["url"] == "https://github.com/EgooAI/MaaAlibabaSupplier/actions/runs/22"
    assert "/runs/22/artifacts?" in github.json.call_args.args[0]
    assert manager.selected["artifact_id"] == 99
    assert "digest" not in candidate


@pytest.mark.parametrize("change", [
    {"event": "pull_request"}, {"event": "workflow_dispatch"}, {"head_branch": "other"},
    {"head_repository": {"full_name": "attacker/MaaAlibabaSupplier"}}, {"conclusion": "failure"},
    {"status": "in_progress"}, {"path": ".github/workflows/other.yml"}, {"run_id": True, "id": True},
    {"head_sha": "untrusted"},
])
def test_check_excludes_untrusted_runs(manager, monkeypatch, change):
    github = remote(monkeypatch, runs=[run(**change)])
    manager._check()
    assert manager.snapshot()["phase"] == "idle"
    assert manager.snapshot()["candidate"] is None
    assert github.json.call_count == 1


@pytest.mark.parametrize("change", [{"run_number": 11}, {"run_number": 12}])
def test_check_does_not_reinstall_same_or_older_build(manager, monkeypatch, change):
    github = remote(monkeypatch, runs=[run(**change)])
    manager._check()
    assert manager.snapshot()["phase"] == "idle"
    assert github.json.call_count == 1


@pytest.mark.parametrize("change", [{"run_number": 13}, {"id": 20, "run_number": 12, "run_attempt": 2}])
def test_newer_run_or_attempt_with_same_sha_is_available(manager, monkeypatch, change):
    remote(monkeypatch, runs=[run(head_sha="a" * 40, **change)])
    manager._check()
    assert manager.snapshot()["phase"] == "available"
    assert manager.state["candidate"]["sha"] == manager.build["sha"]


@pytest.mark.parametrize("artifacts", [[], [artifact(expired=True)], [artifact(name="other")],
                                     [artifact(), artifact(id=100)], [artifact(digest=None)]])
def test_missing_ambiguous_or_unverifiable_artifact_never_stops_app(manager, monkeypatch, artifacts):
    remote(monkeypatch, artifacts=artifacts)
    manager.check()
    wait_operation(manager)
    assert manager.snapshot()["phase"] == "error"
    manager.runtime.prepare.assert_not_called()
    assert not manager.handoff.is_set()


def test_single_async_operation_and_immediate_snapshot(manager, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def check():
        entered.set()
        assert release.wait(3)
        with manager.lock:
            manager.state["phase"] = "idle"

    monkeypatch.setattr(manager, "_check", check)
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
    manager._check()

    def download(path, target, progress):
        data = source.read_bytes()
        target.write_bytes(data)
        progress(len(data), len(data))
        return hashlib.sha256(data).hexdigest()

    github.download.side_effect = download
    state = manager.download(manager.state["candidate"]["id"])
    assert state["phase"] == "downloading"
    wait_operation(manager)
    state = manager.snapshot()
    assert state["phase"] == "ready" and state["candidate"]["version"] == "v9.8.7"
    assert state["downloaded_bytes"] == source.stat().st_size
    installer, expected = manager.prepared
    assert installer.is_file() and not installer.is_relative_to(manager.install)
    assert expected == build(version="v9.8.7", sha=run()["head_sha"], run_id=run()["id"],
                             run_number=run()["run_number"], run_attempt=run()["run_attempt"],
                             installer_sha256=hashlib.sha256(installer.read_bytes()).hexdigest())
    assert {path.name for path in installer.parent.iterdir()} == {"artifact.zip", installer.name}
    manager.runtime.prepare_long.assert_called_once_with(installer, expected)
    assert not manager.handoff.is_set()


def test_bad_zip_digest_blocks_extraction(manager, monkeypatch, tmp_path):
    source = archive(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    github = remote(monkeypatch, artifacts=[artifact(digest="sha256:" + digest)])
    manager._check()
    archive(tmp_path, installer_data=b"replaced installer")

    def download(path, target, progress):
        data = source.read_bytes()
        target.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    github.download.side_effect = download
    unpack = Mock(side_effect=AssertionError("Must not extract"))
    monkeypatch.setattr(updates, "unpack_verified", unpack)
    manager.download(manager.state["candidate"]["id"])
    wait_operation(manager)
    assert manager.snapshot()["phase"] == "error"
    unpack.assert_not_called()
    assert manager.prepared is None
    manager.runtime.prepare_long.assert_not_called()


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
    manager.state.update(phase="ready", candidate={"id": "opaque", "version": "v1.2.3"})
    manager.prepared = (Path("verified.exe"), build())


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
        manager.runtime.prepare.assert_called_once_with(*manager.prepared)
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
    manager.runtime.prepare.assert_not_called()


def test_stale_candidate_and_handshake_failure_leave_app_running(manager):
    make_ready(manager)
    with pytest.raises(AppError) as exc:
        manager.install_update("stale")
    assert exc.value.status_code == 409 and not manager.handoff.is_set()
    manager.runtime.prepare.side_effect = RuntimeError("secret/path")
    with pytest.raises(AppError) as exc:
        manager.install_update("opaque")
    assert exc.value.status_code == 503 and "secret" not in exc.value.message
    manager.runtime.cancel.assert_called_once()
    manager.runtime.arm.assert_not_called()
    assert not manager.handoff.is_set()


def test_second_install_rejected_during_handshake_without_blocking_observation(manager):
    make_ready(manager)
    entered, release = threading.Event(), threading.Event()

    def prepare(*args):
        entered.set()
        assert release.wait(3)

    manager.runtime.prepare.side_effect = prepare
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
    assert manager.state["phase"] == "error"
    assert not manager.handoff.is_set()


def test_durable_result_loaded_without_network(manager):
    path = updates.staging_base(manager.install)
    path.mkdir(parents=True)
    result = {"status": "error", "message": "Installer failed", "version": "v1.2.3"}
    updates.atomic_json(path / "last-result.json", result)
    fresh = updates.Updater(manager.install)
    assert fresh.snapshot()["last_result"] == result


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
    manager.runtime.prepare.assert_called_once()
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
    manager.runtime.prepare.assert_called_once()
    manager.runtime.arm.assert_not_called()
    manager.runtime.cancel.assert_called_once()
    assert not manager.handoff.is_set()
    assert manager.state["phase"] == "error"


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
    manager.runtime.prepare.assert_called_once()
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
    manager.runtime.prepare.assert_called_once()
    manager.runtime.arm.assert_not_called()
    manager.runtime.cancel.assert_called_once()
    assert not manager.handoff.is_set()


def test_watchdog_reconciles_helper_failure_without_any_http_request(manager):
    make_ready(manager)
    manager.install_update("opaque")
    manager.runtime.failed.return_value = True
    assert manager._handoff_finished.wait(2)
    assert not manager.handoff.is_set()
    assert manager.state["phase"] == "error"


def test_response_finishing_after_pending_lease_expiry_cannot_arm(manager):
    make_ready(manager)

    async def send(message):
        if message["type"] == "http.response.body":
            with manager.lock:
                manager._handoff_deadline = 0
            assert await asyncio.to_thread(manager._handoff_finished.wait, 2)
            assert not manager.handoff.is_set()

    asyncio.run(asgi_install(main.create_app(), send))
    manager.runtime.prepare.assert_called_once()
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
def test_download_remains_active_and_writes_available_during_helper_hash(manager, monkeypatch, tmp_path, fail):
    source = archive(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    github = remote(monkeypatch, artifacts=[artifact(digest="sha256:" + digest)])
    manager._check()

    def download(path, target, progress):
        target.write_bytes(source.read_bytes())
        return digest

    entered, release = threading.Event(), threading.Event()

    def prepare_long(*args):
        entered.set()
        assert release.wait(3)
        if fail:
            raise updates.UpdateError("Simulated hash failure")

    github.download.side_effect = download
    manager.runtime.prepare_long.side_effect = prepare_long
    manager.download(manager.state["candidate"]["id"])
    try:
        assert entered.wait(2)
        assert manager.snapshot()["phase"] == "downloading"
        assert not manager.writes_blocked()
        manager.runtime.prepare.assert_not_called()
    finally:
        release.set()
        wait_operation(manager)
    assert manager.state["phase"] == ("error" if fail else "ready")
    if fail:
        assert manager.prepared is None
        assert manager.runtime.cancel.call_count == 2  # prior preparation, failed preparation


@pytest.mark.parametrize("operation", ["check", "download"])
def test_new_operation_cancels_previous_helper_file_lock(manager, monkeypatch, operation):
    make_ready(manager)
    monkeypatch.setattr(manager, "_start", lambda *args: manager.state.copy())
    if operation == "check":
        manager.check()
    else:
        manager.download("opaque")
    manager.runtime.cancel.assert_called_once()
    assert manager.prepared is None
