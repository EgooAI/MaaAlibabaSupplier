"""Remote Python execution for on-machine diagnostics.

The feature is intentionally powerful: a submitted script runs as the backend's
own Windows user with the bundled interpreter. Containment here is about
lifecycle, not privilege separation. Every backend start begins disabled, and
closing the switch terminates the active process tree.

Run metadata lives in the installation's data directory instead of ordinary
logs so that captured stdout/stderr stay complete. Ordinary telemetry records
only the fixed lifecycle facts.
"""

from __future__ import annotations

import codecs
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from typing import BinaryIO

from backend.app.shared.utils import paths, settings
from backend.app.shared.utils.log_context import log_event

MAX_SCRIPT_BYTES = 64 * 1024
MAX_OUTPUT_CHARS = 1024 * 1024
MAX_RUNS = 20
MAX_AGE_S = 24 * 3600
MIN_TIMEOUT_S = 1
DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 300
OUTPUT_CHUNK = 64 * 1024
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timeout", "interrupted"})
_RECORD_FIELDS = (
    "id", "status", "created_at", "started_at", "finished_at", "timeout_s", "exit_code",
    "cancel_requested", "script_sha256", "script_bytes", "error",
    "stdout_chars", "stderr_chars", "stdout_truncated", "stderr_truncated",
)


class ExecutionRejected(Exception):
    """Carries a stable reason code; the API adds the user-facing wording."""

    def __init__(self, reason: str, *, status_code: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class _Output:
    """Writes decoded text to a run file, draining input past the cap."""

    def __init__(self, path: Path, limit: int) -> None:
        self.path = path
        self.limit = limit
        self.written = 0
        self.truncated = False
        self._lock = threading.Lock()
        self._stream = path.open("w", encoding="utf-8", newline="")

    def append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            stream = self._stream
            if stream is None or stream.closed:
                return
            remaining = self.limit - self.written
            if remaining <= 0:
                self.truncated = True
                return
            if len(text) > remaining:
                text = text[:remaining]
                self.truncated = True
            stream.write(text)
            stream.flush()
            self.written += len(text)

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            if stream is not None and not stream.closed:
                with suppress(OSError):
                    stream.close()


def _pump(stream: BinaryIO, output: _Output) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while True:
            block = stream.read(OUTPUT_CHUNK)
            if not block:
                break
            output.append(decoder.decode(block))
        output.append(decoder.decode(b"", final=True))
    except (OSError, ValueError):
        pass


@dataclass
class _Spawned:
    process: object
    job: int | None = None

    def wait(self, timeout: float | None = None) -> int | None:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def terminate_tree(self) -> None:
        if self.job:
            with suppress(Exception):
                _kernel32().TerminateJobObject(self.job, 1)
                return
        with suppress(Exception):
            self.process.kill()

    def close(self) -> None:
        if self.job:
            with suppress(Exception):
                _kernel32().CloseHandle(self.job)
            self.job = None
        for stream in (getattr(self.process, "stdout", None), getattr(self.process, "stderr", None)):
            if stream is not None:
                with suppress(Exception):
                    stream.close()


_KERNEL = None
KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_INFORMATION = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


def _kernel32():
    global _KERNEL
    if _KERNEL is None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        _KERNEL = kernel
    return _KERNEL


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _assign_job(pid: int) -> int | None:
    """Best-effort kill-on-close containment for the script and its children."""
    if os.name != "nt":
        return None
    kernel = None
    handle = None
    try:
        kernel = _kernel32()
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, JOB_OBJECT_LIMIT_INFORMATION,
                                              ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel.CloseHandle(job)
            return None
        handle = kernel.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not handle or not kernel.AssignProcessToJobObject(job, handle):
            kernel.CloseHandle(job)
            return None
        return job
    except Exception:
        return None
    finally:
        if handle:
            with suppress(Exception):
                _kernel32().CloseHandle(handle)


def _interpreter() -> Path:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        # The packaged backend starts under pythonw; a console interpreter is
        # required to observe the script's own exit status and piped output.
        candidate = executable.with_name("python.exe")
        if candidate.is_file():
            return candidate
    return executable


def _spawn(script_path: Path, cwd: str, env: dict[str, str]) -> _Spawned:
    """Starts one script; tests replace this seam instead of using a real process."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(_interpreter()), "-I", "-u", "-X", "utf8", str(script_path)],
        cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        close_fds=True, creationflags=flags,
    )
    return _Spawned(process, _assign_job(process.pid))


def _script_hash(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


@dataclass
class _Active:
    run_id: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    process: _Spawned | None = None


class ExecutionService:
    """Owns the switch, the single active run and persisted run history."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._start_token = uuid.uuid4().hex
        self._generation = 1
        self._enabled = False
        self._stopping = False
        self._loaded = False
        self._storage_ready = False
        self._runs: "OrderedDict[str, dict]" = OrderedDict()
        self._active: _Active | None = None
        self._worker = None

    # -- setup -----------------------------------------------------------

    @property
    def directory(self) -> Path:
        return paths.execution_dir()

    def initialize(self) -> None:
        """Loads history and marks previous-process runs before serving requests."""
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
            except OSError:
                self._storage_ready = False
                return
            self._storage_ready = True
            index = self.directory / "runs.json"
            try:
                raw = json.loads(index.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = []
            now = time.time()
            records = []
            for item in raw if isinstance(raw, list) else []:
                record = self._validated(item, now)
                if record is not None:
                    records.append(record)
            if any(record["status"] in {"queued", "running"} for record in records):
                for record in records:
                    if record["status"] in {"queued", "running"}:
                        record.update(status="interrupted", finished_at=now,
                                      error="restart_interrupted")
                self._write_index_locked(records)
            for record in records:
                self._runs[record["id"]] = record
            self._prune_locked(now)

    def _validated(self, item, now: float) -> dict | None:
        if not isinstance(item, dict):
            return None
        run_id = item.get("id")
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            return None
        status = item.get("status")
        if status not in _TERMINAL | {"queued", "running"}:
            return None
        record = {name: item.get(name) for name in _RECORD_FIELDS}
        for name in ("created_at", "started_at", "finished_at"):
            value = record.get(name)
            if value is not None and (type(value) not in (int, float) or not 0 <= value < 2**63):
                return None
        timeout = record.get("timeout_s")
        if type(timeout) is not int or not MIN_TIMEOUT_S <= timeout <= MAX_TIMEOUT_S:
            return None
        for name in ("stdout_chars", "stderr_chars", "script_bytes"):
            value = record.get(name)
            if type(value) is not int or not 0 <= value <= MAX_OUTPUT_CHARS * 4:
                return None
        for name in ("cancel_requested", "stdout_truncated", "stderr_truncated"):
            if type(record.get(name)) is not bool:
                return None
        if not isinstance(record.get("script_sha256"), str):
            return None
        if record.get("error") is not None and not isinstance(record.get("error"), str):
            return None
        return record

    def _prune_locked(self, now: float) -> None:
        stale = [run_id for run_id, record in self._runs.items()
                 if record["status"] in _TERMINAL and now - record["created_at"] > MAX_AGE_S]
        for run_id in stale:
            self._runs.pop(run_id, None)
        while len(self._runs) > MAX_RUNS:
            self._runs.popitem(last=False)
        keep = set(self._runs)
        with suppress(OSError):
            for path in self.directory.iterdir():
                if path.is_file() and path.suffix in {".py", ".txt"} and path.stem.split(".")[0] not in keep:
                    with suppress(OSError):
                        path.unlink()
        self._write_index_locked(list(self._runs.values()))

    def _write_index_locked(self, records) -> None:
        if not self._storage_ready:
            return
        index = self.directory / "runs.json"
        temporary = index.with_name(index.name + ".tmp")
        try:
            temporary.write_text(json.dumps(records, ensure_ascii=True, separators=(",", ":")),
                                  encoding="utf-8")
            os.replace(temporary, index)
        except OSError:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            log_event("execution.persistence_failed")

    # -- observation -----------------------------------------------------

    def observe(self) -> dict:
        """Passive summary for the diagnostics bundle; never initializes storage."""
        with self._lock:
            return {
                "enabled": self._enabled,
                "active": self._active is not None,
                "runs": len(self._runs),
                "succeeded": sum(1 for record in self._runs.values() if record["status"] == "succeeded"),
                "failed": sum(1 for record in self._runs.values() if record["status"] == "failed"),
                "interrupted": sum(1 for record in self._runs.values() if record["status"] == "interrupted"),
            }

    def state(self) -> dict:
        self._ensure_loaded()
        interpreter = _interpreter()
        with self._lock:
            return {
                "enabled": self._enabled,
                "generation": self._generation,
                "start_token": self._start_token,
                "interpreter": str(interpreter),
                "interpreter_available": interpreter.is_file(),
                "cwd": str(settings.resolve_repo_root()),
                "active_run_id": self._active.run_id if self._active else None,
                "limits": {
                    "max_script_bytes": MAX_SCRIPT_BYTES,
                    "max_output_chars": MAX_OUTPUT_CHARS,
                    "min_timeout_s": MIN_TIMEOUT_S,
                    "default_timeout_s": DEFAULT_TIMEOUT_S,
                    "max_timeout_s": MAX_TIMEOUT_S,
                    "max_runs": MAX_RUNS,
                    "max_age_s": MAX_AGE_S,
                },
                "runs": self._summaries_locked(),
            }

    def _summaries_locked(self) -> list[dict]:
        return [dict(record) for record in reversed(self._runs.values())]

    def list_runs(self, limit: int = MAX_RUNS) -> list[dict]:
        self._ensure_loaded()
        with self._lock:
            return self._summaries_locked()[:max(1, min(limit, MAX_RUNS))]

    def get(self, run_id: str, *, stdout_offset: int = 0, stderr_offset: int = 0) -> dict:
        self._ensure_loaded()
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise ExecutionRejected("execution_run_not_found", status_code=404)
            detail = dict(record)
        for name, offset in (("stdout", stdout_offset), ("stderr", stderr_offset)):
            text, total = self._read_output(run_id, name)
            start = max(0, min(offset, len(text)))
            detail[name] = text[start:]
            detail[name + "_offset"] = start
            detail[name + "_total"] = total
        script_path = self.directory / f"{run_id}.py"
        try:
            detail["script"] = script_path.read_text(encoding="utf-8")
        except OSError:
            detail["script"] = ""
        return detail

    def _read_output(self, run_id: str, name: str) -> tuple[str, int]:
        path = self.directory / f"{run_id}.{name}.txt"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return "", 0
        return text, len(text)

    # -- switch ----------------------------------------------------------

    def set_enabled(self, enabled: bool, *, generation: int) -> dict:
        self._ensure_loaded()
        with self._lock:
            if self._stopping:
                raise ExecutionRejected("execution_stopping")
            if type(enabled) is not bool or generation != self._generation:
                raise ExecutionRejected("execution_generation_changed")
            if self._enabled == enabled:
                return self._state_locked()
            self._generation += 1
            self._enabled = enabled
            active = self._active if not enabled else None
        if active is not None:
            self._terminate(active, cancel_requested=True)
        log_event("execution.switch", status="enabled" if enabled else "disabled")
        return self.state()

    def _state_locked(self) -> dict:
        return {"enabled": self._enabled, "generation": self._generation,
                "active_run_id": self._active.run_id if self._active else None}

    # -- submission ------------------------------------------------------

    def submit(self, *, run_id: str, start_token: str, generation: int,
               script: str, timeout_s: int) -> dict:
        self._ensure_loaded()
        if not isinstance(script, str) or not script.strip():
            raise ExecutionRejected("execution_script_invalid", status_code=422)
        encoded = len(script.encode("utf-8"))
        if encoded > MAX_SCRIPT_BYTES:
            raise ExecutionRejected("execution_script_invalid", status_code=422)
        if type(timeout_s) is not int or not MIN_TIMEOUT_S <= timeout_s <= MAX_TIMEOUT_S:
            raise ExecutionRejected("execution_timeout_invalid", status_code=422)
        interpreter = _interpreter()
        with self._lock:
            if not self._storage_ready:
                raise ExecutionRejected("execution_storage_unavailable", status_code=503)
            if self._stopping:
                raise ExecutionRejected("execution_stopping")
            if start_token != self._start_token:
                raise ExecutionRejected("execution_start_token_changed")
            if generation != self._generation:
                raise ExecutionRejected("execution_generation_changed")
            if not self._enabled:
                raise ExecutionRejected("execution_disabled")
            if not interpreter.is_file():
                raise ExecutionRejected("execution_interpreter_unavailable", status_code=503)
            digest, script_bytes = _script_hash(script), encoded
            existing = self._runs.get(run_id)
            if existing is not None:
                if existing["script_sha256"] != digest or existing["timeout_s"] != timeout_s:
                    raise ExecutionRejected("execution_duplicate_conflict")
                return dict(existing)
            if self._active is not None:
                raise ExecutionRejected("execution_busy")
            now = time.time()
            record = {
                "id": run_id, "status": "queued", "created_at": now, "started_at": None,
                "finished_at": None, "timeout_s": timeout_s, "exit_code": None,
                "cancel_requested": False, "script_sha256": digest, "script_bytes": script_bytes,
                "error": None, "stdout_chars": 0, "stderr_chars": 0,
                "stdout_truncated": False, "stderr_truncated": False,
            }
            try:
                (self.directory / f"{run_id}.py").write_text(script, encoding="utf-8")
            except OSError:
                raise ExecutionRejected("execution_storage_unavailable", status_code=503) from None
            self._runs[run_id] = record
            active = _Active(run_id)
            self._active = active
            self._prune_locked(now)
            self._write_index_locked(list(self._runs.values()))
        try:
            from backend.app.background import get_background_runtime

            worker = get_background_runtime().worker(
                f"execution-{run_id[:8]}",
                lambda stop: self._execute(active, script, timeout_s, stop),
            )
            worker.start()
        except Exception:
            with self._lock:
                if self._active is active:
                    self._active = None
                record = self._runs.get(run_id)
                if record is not None and record["status"] not in _TERMINAL:
                    record.update(status="failed", finished_at=time.time(), error="start_failed")
                    self._write_index_locked(list(self._runs.values()))
            log_event("execution.finished", execution_id=run_id, status="failed")
            raise ExecutionRejected("execution_start_failed", status_code=503) from None
        with self._lock:
            if self._active is active:
                self._worker = worker
        log_event("execution.submitted", execution_id=run_id)
        return self.get(run_id)

    # -- cancellation ----------------------------------------------------

    def cancel(self, run_id: str) -> dict:
        self._ensure_loaded()
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise ExecutionRejected("execution_run_not_found", status_code=404)
            if record["status"] in _TERMINAL:
                summary = dict(record)
                active = None
            else:
                active = self._active if self._active is not None and self._active.run_id == run_id else None
                record["cancel_requested"] = True
                self._write_index_locked(list(self._runs.values()))
                summary = dict(record)
        if active is not None:
            self._terminate(active, cancel_requested=True)
        return summary

    def _terminate(self, active: _Active, *, cancel_requested: bool) -> None:
        with self._lock:
            if cancel_requested:
                active.cancelled.set()
            process = active.process
        if process is not None:
            process.terminate_tree()

    # -- execution -------------------------------------------------------

    def _execute(self, active: _Active, script: str, timeout_s: int, stop_event) -> None:
        run_id = active.run_id
        started = time.time()
        with self._lock:
            record = self._runs.get(run_id)
            if record is None or record["status"] in _TERMINAL:
                if self._active is active:
                    self._active = None
                    self._worker = None
                return
            record.update(status="running", started_at=started)
            self._write_index_locked(list(self._runs.values()))
        out = err = spawned = None
        pumps: list[threading.Thread] = []
        status, error, exit_code = "failed", "spawn_failed", None
        unconfirmed = False
        try:
            out = _Output(self.directory / f"{run_id}.stdout.txt", MAX_OUTPUT_CHARS)
            err = _Output(self.directory / f"{run_id}.stderr.txt", MAX_OUTPUT_CHARS)
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            spawned = _spawn(self.directory / f"{run_id}.py", str(settings.resolve_repo_root()), env)
            with self._lock:
                active.process = spawned
            for stream, sink in ((spawned.process.stdout, out), (spawned.process.stderr, err)):
                pump = threading.Thread(target=_pump, args=(stream, sink), daemon=True)
                pump.start()
                pumps.append(pump)
            deadline = self._clock() + timeout_s
            cancelled = False
            timed_out = False
            while True:
                code = spawned.wait(0.05)
                # A termination request wins over the exit code it produced.
                if active.cancelled.is_set() or stop_event.is_set():
                    cancelled = True
                elif self._clock() >= deadline:
                    timed_out = True
                if cancelled or timed_out:
                    spawned.terminate_tree()
                    exit_code = spawned.wait(5.0)
                    unconfirmed = exit_code is None
                    break
                if code is not None:
                    exit_code = code
                    break
            for pump in pumps:
                pump.join(2.0)
            if unconfirmed:
                # Never report a stop that the process tree did not confirm.
                status, error = "failed", "termination_unconfirmed"
            elif cancelled:
                status, error = "cancelled", "cancelled"
            elif timed_out:
                status, error = "timeout", "timeout"
            elif exit_code == 0:
                status, error = "succeeded", None
            else:
                status, error = "failed", "nonzero_exit"
        except Exception as exc:
            status, error = "failed", type(exc).__name__
        finally:
            if spawned is not None:
                spawned.close()
            for sink in (out, err):
                if sink is not None:
                    sink.close()
            finished = time.time()
            with self._lock:
                record = self._runs.get(run_id)
                if record is not None:
                    record.update(
                        status=status, finished_at=finished, exit_code=exit_code, error=error,
                        stdout_chars=out.written if out else 0, stderr_chars=err.written if err else 0,
                        stdout_truncated=bool(out and out.truncated),
                        stderr_truncated=bool(err and err.truncated),
                        cancel_requested=active.cancelled.is_set(),
                    )
                    self._write_index_locked(list(self._runs.values()))
                if self._active is active and not unconfirmed:
                    self._active = None
                    self._worker = None
            log_event("execution.finished", execution_id=run_id, status=status,
                      duration_ms=max(0.0, (finished - started) * 1000),
                      exit_code=exit_code, reason=error)

    # -- shutdown --------------------------------------------------------

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            self._enabled = False
            active = self._active
            worker = self._worker
        if active is not None:
            self._terminate(active, cancel_requested=True)
        if worker is not None and not worker.join(5.0):
            # A blocked native call keeps its handle; the runtime reports the timeout.
            return


_INSTANCE: ExecutionService | None = None
_INSTANCE_LOCK = threading.Lock()


def observe_execution() -> dict | None:
    """Returns a bounded summary without creating storage or starting a run."""
    service = _INSTANCE
    return None if service is None else service.observe()


def get_execution_service() -> ExecutionService:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = ExecutionService()
        return _INSTANCE
