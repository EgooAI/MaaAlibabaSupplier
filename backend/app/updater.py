"""Scheduled Actions updates with manual installation. Import never contacts GitHub."""

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
from collections.abc import Collection
from concurrent.futures import Future
from pathlib import Path

from backend.app.api.envelope import AppError
from backend.app.background import Worker, get_background_runtime
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


_TERMINAL_RESULTS = {"installed", "error", "reboot_required"}


def terminal_result(result: dict) -> dict | None:
    """Project one helper-written result file; None unless terminal and well-formed."""
    if (result.get("status") in _TERMINAL_RESULTS
            and isinstance(result.get("message"), str)
            and (result.get("version") is None or isinstance(result["version"], str))):
        return {key: result.get(key) for key in ("status", "message", "version")}
    return None


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


_STAGE_NAME = re.compile(r"[0-9a-f]{32}")
_INSTALLER_NAME = r"MaaAlibabaSupplier-(v[A-Za-z0-9][A-Za-z0-9.+_-]*)-Setup\.exe"
_INSTALLER_VERSION = re.compile(_INSTALLER_NAME)
_ARTIFACT_NAME = re.compile(_INSTALLER_NAME + r"|artifact\.zip(\.part)?")
_STAGE_KEEP = 4
AUTO_CHECK_INTERVAL = 300.0
AUTO_FIRST_DELAY = 60.0
AUTO_BACKOFF_MAX = 6 * 3600.0
DISK_MARGIN = 512 * 1024**2


def stage_directories(base: Path) -> list[Path]:
    """Lists uuid stage directories, best effort."""
    try:
        entries = list(base.iterdir())
    except OSError:
        return []
    stages = []
    for entry in entries:
        try:
            if (not _STAGE_NAME.fullmatch(entry.name) or not entry.is_dir()
                    or entry.is_symlink() or entry.is_junction()):
                continue
        except OSError:
            continue
        stages.append(entry)
    return stages


def purge_stale_artifacts(base: Path, protected: Collection[Path]) -> int:
    """Delete downloaded installers and archives outside `protected` stages, best effort.

    Logs and helper files stay for diagnostics; the count-based prune bounds them.
    """
    removed = 0
    for stage in stage_directories(base):
        try:
            if stage.resolve() in protected:
                continue
            for item in stage.iterdir():
                if item.is_file() and not item.is_symlink() and _ARTIFACT_NAME.fullmatch(item.name):
                    item.unlink()
                    removed += 1
        except OSError:
            continue
    if removed:
        log_event("update.prune", status="artifacts", removed=removed)
    return removed


def prune_stage_directories(base: Path, keep: int = _STAGE_KEEP, protected: Collection[Path] = ()) -> int:
    """Delete uuid stage directories beyond the newest `keep`, best effort.

    Protected directories are neither counted nor removed.
    """
    candidates = []
    for entry in stage_directories(base):
        try:
            if entry.resolve() in protected:
                continue
            candidates.append((entry.stat().st_mtime, entry.name, entry))
        except OSError:
            continue
    # The name tie-break keeps the removal set stable for equal timestamps.
    candidates.sort()
    removed = 0
    for _, _, entry in candidates[:max(0, len(candidates) - keep)]:
        try:
            shutil.rmtree(entry)
            removed += 1
        except OSError:
            continue
    if removed:
        log_event("update.prune", status="purged", removed=removed)
    return removed


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
        length = response.headers.get("Content-Length")
        total = int(length) if length else None
        if total is not None and not 0 < total <= MAX_ZIP:
            raise UpdateError("Artifact exceeds the download size limit.")
        # The archive and the extracted installer coexist until unpacking ends.
        if total is not None and shutil.disk_usage(target.parent).free < 2 * total + DISK_MARGIN:
            raise UpdateError("Not enough free disk space for the update.")
        with response, target.open("xb") as stream:
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
        name = _INSTALLER_VERSION.fullmatch(installer.filename)
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
        self.prepared = None
        self._busy = False
        self._stage = None
        self._background = get_background_runtime()
        self._workers: list[Worker] = []
        self._stopping = False
        self._schedule_delay = AUTO_FIRST_DELAY
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
                      "error": None, "last_result": None, "auto": False}
        try:
            result = read_json(staging_base(self.install) / "last-result.json")
            terminal = terminal_result(result)
            if terminal is not None:
                self.state["last_result"] = terminal
                self._note_stale(terminal)
            elif result.get("status") == "installing":
                # The previous process did not finish its handoff; expose that in
                # logs without presenting it as an active installation.
                log_event("update.result", status="interrupted", version=result.get("version"))
        except (OSError, ValueError, UpdateError):
            pass

    def _note_stale(self, result: dict) -> None:
        """Report an `installed` result that does not name the running build.

        Only success is expected to match; a mismatch means the helper's own
        terminal write was lost, not that the installation failed.
        """
        if (result["status"] == "installed" and result["version"]
                and self.build.get("version") and result["version"] != self.build["version"]):
            log_event("update.result", status="stale", version=result["version"])

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

            resolve_powershell(self.install)  # attempts re-resolve; here it disables updates when no shell exists
            self.runtime = WindowsUpdateRuntime(self.install, staging_base(self.install))
            self.state.update(supported=True, reason=None, auto=os.environ.get("MAA_UPDATE_AUTO", "1") != "0")
            # Nothing references earlier downloads after a restart.
            self.reconcile_staging()
            if self.state["auto"]:
                self._start_scheduler()
        except UpdateError as exc:
            self.state["reason"] = str(exc)
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
                    self._note_stale(result)
            return copy.deepcopy(self.state)

    def observation(self) -> dict:
        """Reads published progress without probing the helper or waiting for an operation."""
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError("Updater observation is busy")
        try:
            return {key: self.state[key] for key in (
                "supported", "reason", "phase", "auto", "downloaded_bytes", "total_bytes", "error",
            )}
        finally:
            self.lock.release()

    def _clear_handoff_locked(self) -> None:
        self._handoff_finished.set()
        self._handoff_id = None
        self.handoff.clear()

    def _cancel_install_locked(self, message: str, reason: str) -> None:
        try:
            self.runtime.cancel()
        except OSError:
            pass
        self._clear_handoff_locked()
        # Before the commit nothing was stopped and the verified installer is
        # untouched, so a cancelled or failed attempt stays installable.
        self.state.update(phase="ready" if self.prepared is not None else "error", error=message)
        log_event("update.handoff", status="cancelled", reason=reason)

    def writes_blocked(self, operation_id: str | None = None) -> bool:
        """Reconcile from the write gate and watchdog, independently of GET."""
        with self.lock:
            if operation_id is not None and self._handoff_id != operation_id:
                return False
            if self.handoff.is_set():
                if self.runtime.failed():
                    self._cancel_install_locked("The updater helper stopped; the application was not stopped.",
                                                "helper_exited")
                elif not self._armed and time.monotonic() >= self._handoff_deadline:
                    self._cancel_install_locked("The update handoff expired; the application was not stopped.",
                                                "handoff_expired")
            return self.handoff.is_set()

    def _admit(self) -> None:
        if self._stopping:
            raise AppError("The updater is stopping.", status_code=503)
        if not self.state["supported"]:
            raise AppError(self.state["reason"], status_code=409)
        if self.state["phase"] in {"checking", "downloading", "installing"} or self.handoff.is_set():
            raise AppError("An update operation is already active.", status_code=409)

    def _admit_operation(self) -> None:
        """Admits a check or download; caller holds the lock. `_run_guarded` releases it."""
        self._admit()
        if self._busy:
            raise AppError("An update operation is already active.", status_code=409)
        self._busy = True

    def _run_guarded(self, phase: str, operation) -> bool:
        """Runs one operation to its end, then deletes downloads nothing references.

        Returns whether the operation succeeded. The caller has set `_busy`.
        """
        succeeded = True
        try:
            try:
                operation()
            except Exception as exc:
                succeeded = False
                with self.lock:
                    if self.state["phase"] in {"checking", "downloading"}:
                        self.state.update(phase="ready" if self.prepared is not None else "error",
                                          error=self._message(exc))
                log_event("update.failed", phase=phase, status="failed", exception_type=type(exc).__name__)
            self.reconcile_staging()
        finally:
            with self.lock:
                self._busy = False
        return succeeded

    def _start(self, phase: str, operation) -> dict:
        self.state.update(phase=phase, error=None)
        response = copy.deepcopy(self.state)
        try:
            self._start_worker_locked(self._background.worker(
                "manual-updater", lambda stop: self._run_guarded(phase, operation),
            ))
        except Exception:
            self._busy = False
            self.state.update(phase="error", error="Could not start the update operation.")
            raise AppError(self.state["error"], status_code=503) from None
        return response

    def _start_worker_locked(self, worker: Worker) -> None:
        """Starts and tracks work while serialized with shutdown admission."""
        if self._stopping:
            raise RuntimeError("The updater is stopping")
        self._background.on_shutdown(f"updater-{id(self)}", self.stop, priority=50)
        worker.start()
        self._workers = [active for active in self._workers if active.is_alive()]
        self._workers.append(worker)

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stops timers and handoff monitoring, then drains accepted operations."""
        with self.lock:
            self._stopping = True
            self._handoff_finished.set()
            workers = tuple(self._workers)
        for worker in workers:
            worker.request_stop()
        deadline = None if timeout is None else time.monotonic() + timeout
        for worker in workers:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not worker.join(remaining):
                raise TimeoutError(f"Updater worker {worker.name} has not stopped")
        with self.lock:
            if self.handoff.is_set() and not self._armed:
                self._cancel_install_locked("Update handoff was cancelled during shutdown.", "shutdown")

    def check(self) -> dict:
        with self.lock:
            self._admit_operation()
            return self._start("checking", lambda: self._cycle(announce=True))

    def _scheduled_cycle(self) -> bool:
        """Runs one background cycle unless another operation is active.

        Returns whether it succeeded or was skipped because it could not run now.
        """
        with self.lock:
            if (self._stopping or not self.state["supported"] or self.state["phase"] not in {"idle", "ready", "error"}
                    or self.handoff.is_set() or self._busy):
                return True
            self._busy = True
        return self._run_guarded("auto", lambda: self._cycle(announce=False))

    def _schedule(self) -> float:
        if self._scheduled_cycle():
            self._schedule_delay = AUTO_CHECK_INTERVAL
        else:
            self._schedule_delay = min(max(self._schedule_delay, AUTO_CHECK_INTERVAL) * 2, AUTO_BACKOFF_MAX)
        return self._schedule_delay

    def _start_scheduler(self) -> None:
        with self.lock:
            self._start_worker_locked(self._background.schedule(
                "auto-updater", self._schedule, interval=AUTO_CHECK_INTERVAL, delay=AUTO_FIRST_DELAY,
            ))

    @staticmethod
    def _message(exc: Exception) -> str:
        return str(exc) if isinstance(exc, UpdateError) else \
            "Update operation failed. Check access, disk space, and artifact availability."

    def _protected_locked(self) -> set[Path]:
        """Stage directories still referenced by a verified or running download."""
        protected = set()
        if self.prepared is not None:
            protected.add(self.prepared[0].parent.resolve())
        if self._stage is not None:
            protected.add(self._stage.resolve())
        return protected

    def reconcile_staging(self) -> int:
        """Deletes downloaded installers and archives that no verified candidate references."""
        with self.lock:
            if self.handoff.is_set() or self.state["phase"] == "installing":
                return 0
            try:
                return purge_stale_artifacts(staging_base(self.install), self._protected_locked())
            except UpdateError:
                return 0

    def _holds(self, run: dict) -> bool:
        """Whether this run's installer is already downloaded and verified. Caller holds the lock."""
        prepared = self.prepared
        return (prepared is not None
                and (prepared[1]["run_id"], prepared[1]["run_attempt"]) == (run["id"], run["run_attempt"]))

    def _cycle(self, announce: bool) -> None:
        """Checks GitHub and downloads a newer build.

        A build whose installer is already verified is not downloaded again.
        Scheduled cycles (`announce` false) leave the state alone unless they download.
        """
        github = GitHub()
        run = self._latest_run(github)
        with self.lock:
            if run is None or self._holds(run):
                if announce:
                    if self.prepared is None:
                        self.state.update(phase="idle", candidate=None, downloaded_bytes=0, total_bytes=None)
                    else:
                        self.state["phase"] = "ready"
                    log_event("update.check", status="current", run_id=self.build.get("run_id"))
                return
        selected, candidate = self._resolve(github, run)
        log_event("update.check", status="candidate", run_id=run["id"],
                  run_number=run["run_number"], run_attempt=run["run_attempt"])
        self._download_candidate(selected, candidate)

    def _download_candidate(self, selected: dict, candidate: dict) -> None:
        with self.lock:
            if self.handoff.is_set() or self.state["phase"] == "installing":
                return
            # The installable candidate stays visible until its replacement is verified.
            self.state.update(phase="downloading", downloaded_bytes=0, total_bytes=None, error=None)
        log_event("update.download", status="started", run_id=selected["run_id"])
        try:
            installer, prepared = self._download(selected)
        except Exception as exc:
            with self.lock:
                self.state.update(phase="ready" if self.prepared is not None else "error", error=self._message(exc))
            raise
        with self.lock:
            self.prepared = (installer, prepared)
            self.state.update(phase="ready", candidate={**candidate, "version": prepared["version"]})
            count = self.state["downloaded_bytes"]
        log_event("update.download", status="ready", run_id=selected["run_id"], count=count)

    def _latest_run(self, github: GitHub) -> dict | None:
        """Returns the newest eligible run when it is newer than the running build."""
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
            return None
        return run

    def _resolve(self, github: GitHub, run: dict) -> tuple[dict, dict]:
        """Selects the unique installer artifact of a run and builds its candidate."""
        source = self.state["source"]
        prefix = "/repos/" + source["repository"] + "/actions"
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
        return selected, candidate

    def _candidate(self, candidate_id: str) -> None:
        if not self.state["candidate"] or candidate_id != self.state["candidate"]["id"]:
            raise AppError("The update candidate changed; check for updates again.", status_code=409)

    def _download(self, selected: dict) -> tuple[Path, dict]:
        base = staging_base(self.install)
        stage = base / uuid.uuid4().hex
        with self.lock:
            self._stage = stage
            protected = self._protected_locked()
        try:
            stage.mkdir(parents=True, exist_ok=False)
            # Pruning is safe against an armed handoff because _admit blocks
            # further attempts until it clears.
            prune_stage_directories(base, protected=protected)
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
            # The verified archive has no consumer after extraction.
            verified.unlink(missing_ok=True)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            with self.lock:
                self._stage = None
            raise
        with self.lock:
            self._stage = None
        return installer, prepared

    def install_update(self, candidate_id: str) -> str:
        with self.lock:
            self._admit()
            self._candidate(candidate_id)
            if self.state["phase"] != "ready" or self.prepared is None or self.runtime is None:
                raise AppError("Download and verify the update before installing.", status_code=409)
            if not self.prepared[0].is_file():
                # Security software or a cleanup may remove a long-held installer.
                self.prepared = None
                self.state.update(phase="idle", candidate=None, downloaded_bytes=0, total_bytes=None)
                raise AppError("The downloaded installer is missing; check for updates again.", status_code=409)
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
            accepted = Future()

            def handoff(stop):
                # The request waits for acceptance, but shutdown owns the whole
                # handshake and can drain it before releasing application resources.
                try:
                    self.runtime.start(installer, prepared)
                    with self.lock:
                        if self._stopping or self._handoff_id != operation_id:
                            raise UpdateError("The update handoff was stopped.")
                        self._handoff_deadline = time.monotonic() + 15.0
                    log_event("update.handoff", status="accepted", version=prepared.get("version"))
                except BaseException as exc:
                    accepted.set_exception(exc)
                    return
                accepted.set_result(None)
                while not stop.wait(0.1) and not finished.is_set():
                    if not self.writes_blocked(operation_id):
                        return

        try:
            with self.lock:
                self._start_worker_locked(self._background.worker("update-handoff", handoff))
            accepted.result()
        except Exception as exc:
            with self.lock:
                if self._handoff_id == operation_id:
                    try:
                        self.runtime.cancel()
                    except OSError:
                        pass
                    self._clear_handoff_locked()
                    self.state.update(phase="ready", error=None)
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
            if self._stopping or self._handoff_id != operation_id or self._armed:
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


def observe_updater() -> dict | None:
    instance = _instance
    return instance.observation() if instance is not None else None


def register_update_runtime() -> None:
    get_updater().register_runtime()


def stop_update_scheduler() -> None:
    """Stops updater work without creating an updater outside a supported run."""
    instance = _instance
    if instance is not None:
        instance.stop()
