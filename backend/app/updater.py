"""Manual Actions updates. Imports and observations never contact GitHub."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from backend.app.api.envelope import AppError
from backend.app.shared.utils.log_context import log_event
from backend.app.shared.utils.settings import resolve_repo_root

APP_ID = "580868F7-B96A-4214-829A-609D552F2C3A"
REPOSITORY_PATTERN = r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.{1,2}$)[A-Za-z0-9_.-]+"
MAX_ZIP = 2 * 1024**3
MAX_UNPACKED = 3 * 1024**3
BUILD_KEYS = ("schema_version", "app_id", "version", "repository", "sha", "run_id", "run_number", "run_attempt")


class UpdateError(Exception):
    """Only fixed, credential-free messages may cross the API boundary."""


def read_json(path: Path) -> dict:
    if path.stat().st_size > 65536:
        raise UpdateError("Update metadata is too large.")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise UpdateError("Invalid update metadata.")
    return value


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def valid_build(value: dict) -> bool:
    return (
        all(key in value for key in BUILD_KEYS)
        and type(value.get("schema_version")) is int and value["schema_version"] == 1
        and value.get("app_id") == APP_ID
        and isinstance(value.get("version"), str) and 0 < len(value["version"]) <= 200
        and (value.get("repository") is None
             or isinstance(value.get("repository"), str)
             and re.fullmatch(REPOSITORY_PATTERN, value["repository"]) is not None)
        and (value.get("sha") is None
             or isinstance(value.get("sha"), str) and re.fullmatch(r"[0-9a-f]{40}", value["sha"]) is not None)
        and all(type(value.get(key)) is int and value[key] >= 0
                for key in ("run_id", "run_number", "run_attempt"))
    )


def resolve_powershell(install: Path) -> str:
    for name in ("pwsh", "powershell"):
        executable = shutil.which(name)
        if executable and not Path(executable).resolve().is_relative_to(install):
            return executable
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if executable.is_file() and not executable.resolve().is_relative_to(install):
        return str(executable)
    raise UpdateError("An external PowerShell runtime is required.")


def staging_base(install: Path) -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local or not Path(local).is_absolute():
        raise UpdateError("LOCALAPPDATA is unavailable.")
    identity = hashlib.sha256(str(install.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
    base = (Path(local) / "MaaAlibabaSupplierUpdater" / identity).resolve()
    if base.is_relative_to(install.resolve()):
        raise UpdateError("Updater staging must be outside the installation.")
    return base


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self) -> None:
        self._token = os.environ.get("MAA_UPDATE_GITHUB_TOKEN", "")
        self._opener = urllib.request.build_opener(NoRedirect())

    def _open(self, url: str, *, api: bool):
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        allowed = host == "api.github.com" if api else (
            host == "api.github.com" or host == "results-receiver.actions.githubusercontent.com"
            or host.endswith(".blob.core.windows.net") or host.endswith(".actions.githubusercontent.com")
            or host == "objects.githubusercontent.com"
        )
        if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            raise UpdateError("GitHub returned an unsupported download location.")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "MaaAlibabaSupplier-Updater"}
        # Never forward credentials, including on redirects back from a blob host.
        if api and self._token:
            headers["Authorization"] = "Bearer " + self._token
        try:
            return self._opener.open(urllib.request.Request(url, headers=headers), timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location")
                exc.close()
                return location
            exc.close()
            raise UpdateError("GitHub request failed; check repository access and Actions availability.") from None
        except (OSError, ValueError):
            raise UpdateError("GitHub connection failed or timed out.") from None

    def json(self, path: str) -> dict:
        response = self._open("https://api.github.com" + path, api=True)
        if isinstance(response, str) or response is None:
            raise UpdateError("Unexpected GitHub API redirect.")
        raw = bytearray()
        deadline = time.monotonic() + 60
        with response:
            while block := response.read1(65536):
                raw.extend(block)
                if len(raw) > 4 * 1024**2 or time.monotonic() > deadline:
                    raise UpdateError("GitHub response exceeded the size or time limit.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise UpdateError("Invalid GitHub response.")
        return value

    def download(self, path: str, target: Path, progress) -> str:
        url = "https://api.github.com" + path
        response = None
        for attempt in range(6):
            response = self._open(url, api=attempt == 0)
            if not isinstance(response, str):
                break
            url = response
        if response is None or isinstance(response, str):
            raise UpdateError("Too many GitHub download redirects.")
        digest = hashlib.sha256()
        size = 0
        deadline = time.monotonic() + 900
        with response, target.open("xb") as stream:
            length = response.headers.get("Content-Length")
            total = int(length) if length else None
            if total is not None and not 0 < total <= MAX_ZIP:
                raise UpdateError("Artifact exceeds the download size limit.")
            while block := response.read1(1024 * 1024):
                size += len(block)
                if size > MAX_ZIP or time.monotonic() > deadline:
                    raise UpdateError("Artifact exceeded the download size or time limit.")
                digest.update(block)
                stream.write(block)
                progress(size, total)
            if not size or total is not None and total != size:
                raise UpdateError("Artifact download was incomplete.")
            stream.flush()
            os.fsync(stream.fileno())
        return digest.hexdigest()


def unpack_verified(archive: Path, stage: Path, expected: dict) -> tuple[Path, dict]:
    """Accept one flat installer from a ZIP whose GitHub digest was verified."""
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) != 1:
            raise UpdateError("Artifact must contain exactly one installer.")
        installer = entries[0]
        name = re.fullmatch(r"MaaAlibabaSupplier-(v[A-Za-z0-9][A-Za-z0-9.+_-]*)-Setup\.exe", installer.filename)
        mode = installer.external_attr >> 16
        if (name is None or installer.orig_filename != installer.filename or installer.is_dir() or installer.flag_bits & 1
                or installer.external_attr & 0x10 or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or not 0 < installer.file_size <= MAX_UNPACKED
                or installer.file_size > max(1, installer.compress_size) * 300):
            raise UpdateError("Artifact contains unsafe ZIP entries.")
        target = stage / installer.filename
        calculated = hashlib.sha256()
        with bundle.open(installer) as source, target.open("xb") as output:
            while block := source.read(1024 * 1024):
                output.write(block)
                calculated.update(block)
            output.flush()
            os.fsync(output.fileno())
        # The filename labels the version; GitHub supplies the independent build
        # identity checked after installation. The local hash binds helper handoff.
        prepared = {key: expected[key] for key in ("repository", "sha", "run_id", "run_number", "run_attempt")}
        prepared.update(schema_version=1, app_id=APP_ID, version=name[1],
                        installer_sha256=calculated.hexdigest())
        return target, prepared


class Updater:
    def __init__(self, install: Path | None = None) -> None:
        self.install = (install or resolve_repo_root()).resolve()
        self.lock = threading.Lock()
        self.handoff = threading.Event()
        self._handoff_id = None
        self._handoff_finished = threading.Event()
        self._handoff_deadline = 0.0
        self._armed = False
        self.runtime = None
        self.build = {}
        self.selected = None
        self.prepared = None
        source = {"repository": os.environ.get("MAA_UPDATE_REPOSITORY", "EgooAI/MaaAlibabaSupplier"),
                  "branch": os.environ.get("MAA_UPDATE_BRANCH", "main"),
                  "workflow": os.environ.get("MAA_UPDATE_WORKFLOW", "install.yml"),
                  "artifact": os.environ.get("MAA_UPDATE_ARTIFACT", "MaaAlibabaSupplier-Setup")}
        try:
            build = read_json(self.install / "build-info.json")
            if valid_build(build):
                self.build = build
        except (OSError, ValueError, UpdateError):
            pass
        self.state = {"supported": False, "reason": "Manual updates require the installed Windows main launcher.",
                      "phase": "idle", "current": {"version": self.build.get("version", "development"),
                                                    "sha": self.build.get("sha"), "run_id": self.build.get("run_id")},
                      "source": source, "candidate": None, "downloaded_bytes": 0, "total_bytes": None,
                      "error": None, "last_result": None}
        try:
            result = read_json(staging_base(self.install) / "last-result.json")
            if (result.get("status") in {"installed", "error", "reboot_required"}
                    and all(isinstance(result.get(key), str) for key in ("status", "message"))
                    and (result.get("version") is None or isinstance(result["version"], str))):
                self.state["last_result"] = {key: result.get(key) for key in ("status", "message", "version")}
            elif result.get("status") == "installing":
                # The previous process did not finish its handoff; expose that in
                # logs without presenting it as an active installation.
                log_event("update.result", status="interrupted", version=result.get("version"))
        except (OSError, ValueError, UpdateError):
            pass

    def register_runtime(self) -> None:
        if (sys.platform != "win32" or not self.build
                or not Path(sys.executable).resolve().is_relative_to(self.install / "backend" / "python")):
            return
        try:
            source = self.state["source"]
            if (re.fullmatch(REPOSITORY_PATTERN, source["repository"]) is None
                    or re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", source["workflow"]) is None
                    or not source["branch"] or not source["artifact"]):
                raise UpdateError("Invalid updater source configuration.")
            from backend.app.update_runtime import WindowsUpdateRuntime

            self.runtime = WindowsUpdateRuntime(self.install, staging_base(self.install), resolve_powershell(self.install))
            self.state.update(supported=True, reason=None)
        except Exception:
            self.state["reason"] = "The independent updater helper or Windows process containment is unavailable."

    def snapshot(self) -> dict:
        self.writes_blocked()
        with self.lock:
            # A live handoff reports its own phase; reading the result file while
            # the broker may write it would only race and show stale attempts.
            if self.runtime is not None and not self.handoff.is_set():
                result = self.runtime.result()
                if result is not None and result != self.state["last_result"]:
                    self.state["last_result"] = result
                    log_event("update.result", status=result["status"], version=result.get("version"))
            return copy.deepcopy(self.state)

    def _cancel_install_locked(self, message: str, reason: str) -> None:
        try:
            self.runtime.cancel()
        except OSError:
            pass
        self._handoff_finished.set()
        self._handoff_id = None
        self.handoff.clear()
        # Before the commit nothing was stopped and the verified installer is
        # untouched, so a cancelled or failed attempt stays installable.
        self.state.update(phase="ready" if self.prepared is not None else "error", error=message)
        log_event("update.handoff", status="cancelled", reason=reason)

    def writes_blocked(self) -> bool:
        """Reconcile from the write gate and watchdog, independently of GET."""
        with self.lock:
            if self.handoff.is_set():
                if self.runtime.failed():
                    self._cancel_install_locked("The updater helper stopped; the application was not stopped.",
                                                "helper_exited")
                elif not self._armed and time.monotonic() >= self._handoff_deadline:
                    self._cancel_install_locked("The update handoff expired; the application was not stopped.",
                                                "handoff_expired")
            return self.handoff.is_set()

    def _admit(self) -> None:
        if not self.state["supported"]:
            raise AppError(self.state["reason"], status_code=409)
        if self.state["phase"] in {"checking", "downloading", "installing"} or self.handoff.is_set():
            raise AppError("An update operation is already active.", status_code=409)

    def _start(self, phase: str, operation) -> dict:
        self.state.update(phase=phase, error=None)

        def run():
            try:
                operation()
            except Exception as exc:
                with self.lock:
                    self.state.update(phase="error", error=str(exc) if isinstance(exc, UpdateError)
                                      else "Update operation failed. Check access, disk space, and artifact availability.")
                log_event("update.failed", phase=phase, status="failed", exception_type=type(exc).__name__)
        response = copy.deepcopy(self.state)
        try:
            threading.Thread(target=run, daemon=True, name="manual-updater").start()
        except Exception:
            self.state.update(phase="error", error="Could not start the update operation.")
            raise AppError(self.state["error"], status_code=503) from None
        return response

    def check(self) -> dict:
        with self.lock:
            self._admit()
            self.selected = self.prepared = None
            self.state.update(candidate=None, downloaded_bytes=0, total_bytes=None)
            return self._start("checking", self._check)

    def _check(self) -> None:
        github = GitHub()
        source = self.state["source"]
        prefix = "/repos/" + source["repository"] + "/actions"
        query = urllib.parse.urlencode({"branch": source["branch"], "event": "push", "status": "success", "per_page": 100})
        runs = github.json(prefix + "/workflows/" + source["workflow"] + "/runs?" + query).get("workflow_runs", [])
        eligible = [run for run in runs if (
            run.get("event") == "push" and run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("head_branch") == source["branch"]
            and (run.get("head_repository") or {}).get("full_name") == source["repository"]
            and run.get("path", "").split("@")[0] == ".github/workflows/" + source["workflow"]
            and all(type(run.get(k)) is int and run[k] > 0 for k in ("id", "run_number", "run_attempt"))
            and isinstance(run.get("head_sha"), str) and re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
        )]
        run = max(eligible, key=lambda r: (r["run_number"], r["run_attempt"]), default=None)
        # run_number is scoped to a workflow. Comparing packaged numbers assumes
        # MAA_UPDATE_WORKFLOW remains the workflow which produced this install.
        # A higher attempt can rebuild dependencies without changing head_sha.
        if run is None or (self.build.get("repository") == source["repository"] and
                (run["run_number"], run["run_attempt"]) <= (self.build["run_number"], self.build["run_attempt"])):
            with self.lock:
                self.state["phase"] = "idle"
            log_event("update.check", status="current", run_id=self.build.get("run_id"))
            return
        artifacts = []
        for page in range(1, 11):
            data = github.json(prefix + f"/runs/{run['id']}/artifacts?per_page=100&page={page}")
            artifacts.extend(data.get("artifacts", []))
            if len(artifacts) >= data.get("total_count", 0):
                break
        else:
            raise UpdateError("The workflow has too many artifacts to select safely.")
        matches = [a for a in artifacts if a.get("name") == source["artifact"] and a.get("expired") is False]
        if len(matches) != 1:
            raise UpdateError("The newest eligible run has no unique unexpired installer artifact.")
        artifact = matches[0]
        if (type(artifact.get("id")) is not int or artifact["id"] <= 0
                or not isinstance(artifact.get("digest"), str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"]) is None):
            raise UpdateError("The artifact has no verifiable GitHub SHA-256 digest.")
        selected = {"repository": source["repository"], "sha": run["head_sha"], "run_id": run["id"],
                    "run_number": run["run_number"], "run_attempt": run["run_attempt"],
                    "artifact_id": artifact["id"], "digest": artifact["digest"][7:]}
        candidate = {"id": uuid.uuid4().hex, "version": f"Build {run['run_number']}.{run['run_attempt']}",
                     "sha": run["head_sha"], "run_id": run["id"], "run_attempt": run["run_attempt"],
                     "created_at": str(run.get("created_at", "")),
                     "url": f"https://github.com/{source['repository']}/actions/runs/{run['id']}"}
        with self.lock:
            self.selected = selected
            self.state.update(phase="available", candidate=candidate)
        log_event("update.check", status="candidate", run_id=run["id"],
                  run_number=run["run_number"], run_attempt=run["run_attempt"])

    def _candidate(self, candidate_id: str) -> None:
        if not self.state["candidate"] or candidate_id != self.state["candidate"]["id"]:
            raise AppError("The update candidate changed; check for updates again.", status_code=409)

    def download(self, candidate_id: str) -> dict:
        with self.lock:
            self._admit()
            self._candidate(candidate_id)
            self.prepared = None
            self.state.update(downloaded_bytes=0, total_bytes=None)
            log_event("update.download", status="started", run_id=self.selected.get("run_id"))
            return self._start("downloading", self._download)

    def _download(self) -> None:
        selected = self.selected.copy()
        stage = staging_base(self.install) / uuid.uuid4().hex
        stage.mkdir(parents=True, exist_ok=False)
        archive = stage / "artifact.zip.part"

        def progress(size, total):
            with self.lock:
                self.state.update(downloaded_bytes=size, total_bytes=total)
        digest = GitHub().download(f"/repos/{selected['repository']}/actions/artifacts/{selected['artifact_id']}/zip", archive, progress)
        if digest != selected["digest"]:
            raise UpdateError("Artifact SHA-256 verification failed.")
        verified = stage / "artifact.zip"
        os.replace(archive, verified)
        installer, prepared = unpack_verified(verified, stage, selected)
        with self.lock:
            self.prepared = (installer, prepared)
            self.state["candidate"]["version"] = prepared["version"]
            self.state["phase"] = "ready"
            count = self.state["downloaded_bytes"]
        log_event("update.download", status="ready", run_id=selected["run_id"], count=count)

    def install_update(self, candidate_id: str) -> str:
        with self.lock:
            self._admit()
            self._candidate(candidate_id)
            if self.state["phase"] != "ready" or self.prepared is None or self.runtime is None:
                raise AppError("Download and verify the update before installing.", status_code=409)
            # A finished attempt must not be mistaken for the one about to start.
            self.runtime.reset()
            self.handoff.set()
            operation_id = self._handoff_id = uuid.uuid4().hex
            finished = self._handoff_finished = threading.Event()
            # Bounded only after the broker accepted; start() bounds the wait.
            self._handoff_deadline = float("inf")
            self._armed = False
            self.state.update(phase="installing", error=None)
            installer, prepared = self.prepared
        # No state/account/GUI lock is held while this attempt's single-use
        # broker starts. Failure restores the ready candidate for an immediate
        # retry: nothing was stopped and the installer file is untouched.
        try:
            self.runtime.start(installer, prepared)
            with self.lock:
                self._handoff_deadline = time.monotonic() + 15.0
            log_event("update.handoff", status="accepted", version=prepared.get("version"))

            def reconcile():
                while not finished.wait(0.1):
                    # A previous response callback cannot affect a newer lease.
                    with self.lock:
                        if self._handoff_id != operation_id:
                            return
                    self.writes_blocked()

            threading.Thread(target=reconcile, daemon=True, name="update-handoff").start()
        except Exception as exc:
            with self.lock:
                if self._handoff_id == operation_id:
                    self._handoff_finished.set()
                    self._handoff_id = None
                    self.handoff.clear()
                    self.state.update(phase="ready", error=None)
            try:
                self.runtime.cancel()
            except OSError:
                pass
            message = (str(exc) if isinstance(exc, UpdateError)
                       else "Update handoff failed; the application was not stopped.")
            log_event("update.handoff", status="rejected", reason="start_failed",
                      exception_type=type(exc).__name__, version=prepared.get("version"))
            raise AppError(message, status_code=503) from None
        return operation_id

    def cancel_install(self, operation_id: str) -> None:
        with self.lock:
            if self._handoff_id == operation_id and not self._armed:
                self._cancel_install_locked("Update handoff was cancelled; the application was not stopped.",
                                            "response_failed")

    def arm(self, operation_id: str) -> None:
        with self.lock:
            if self._handoff_id != operation_id or self._armed:
                return
            try:
                if time.monotonic() >= self._handoff_deadline:
                    raise UpdateError("Update handoff expired.")
                self.runtime.arm()
                self._armed = True
                log_event("update.handoff", status="go")
            except Exception:
                self._cancel_install_locked("Updater handoff failed; the application was not stopped.",
                                            "helper_unavailable")


_instance: Updater | None = None
_instance_lock = threading.Lock()


def get_updater() -> Updater:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Updater()
        return _instance


def register_update_runtime() -> None:
    get_updater().register_runtime()
