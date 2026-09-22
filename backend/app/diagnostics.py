"""Bounded, metadata-only diagnostic exports; never traverse application data."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from starlette.responses import JSONResponse, Response

from backend.app.api.envelope import err
from backend.app.shared.utils.logging import ERROR_CATEGORIES, NATIVE_ENTRIES
from backend.app.shared.utils.settings import resolve_backend_root, resolve_repo_root

MAX_FILES = 24  # Includes the two generated records.
MAX_FILE_BYTES = 4 * 1024**2
MAX_TOTAL_BYTES = 32 * 1024**2
MAX_ARCHIVE_BYTES = 34 * 1024**2
MAX_DIRECTORY_ENTRIES = 256
MAX_SCAN_ENTRIES = 4096
MAX_LINE_BYTES = 64 * 1024
COLLECTION_SECONDS = 10.0
_ADMISSION = threading.BoundedSemaphore(1)
_DATE = r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?"
_NATIVE = re.compile(rf"^\[({_DATE})\]\s*\[(TRC|DBG|INF|WRN|ERR|FTL|TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\]", re.I)
_YAK = re.compile(rf"^\[(TRACE|TRAC|DEBUG|DEBU|INFO|WARN|WARNING|ERROR|ERRO|FATAL|FATA)\]\s+\[?({_DATE}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{2}}:\d{{2}}:\d{{2}})\]?", re.I)
_INSTALLER = re.compile(rf"^({_DATE})\s+(.*)$")


@dataclass(frozen=True)
class Source:
    name: str
    directory: Path
    pattern: str
    current: tuple[str, ...]
    kind: str
    session_state: str | None = None


def _correlation(fields: dict) -> dict:
    """Only typed protocol metadata, never arbitrary node names or error prose."""
    result = {}
    for key in ("task_id", "job_id", "node_id", "reco_id", "action_id", "pid", "tid", "line"):
        value = fields.get(key)
        if type(value) is int and 0 <= value < 2**63:
            result[key] = value
        elif isinstance(value, str) and re.fullmatch(r"\d{1,18}", value):
            result[key] = int(value)
    for key in ("entry", "node"):
        if isinstance(fields.get(key), str) and fields[key] in NATIVE_ENTRIES:
            result[key] = fields[key]
    source = fields.get("file", fields.get("source_file"))
    if isinstance(source, str):
        basename = source.replace("\\", "/").rsplit("/", 1)[-1]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}\.(?:cpp|cc|c|hpp|h|go|yak)", basename):
            result["source_file"] = basename
    function = fields.get("function")
    if isinstance(function, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:.*<>]{0,95}(?:\(\))?", function):
        result["function"] = function
    category = fields.get("error_category")
    if isinstance(category, str) and category in ERROR_CATEGORIES.values():
        result["error_category"] = category
    return result


def _native_context(tail: str) -> dict:
    fields = {}
    # Consume only a contiguous header/metadata prefix. Never search OCR or JSON bodies.
    for _ in range(24):
        tail = tail.lstrip(" ,\t")
        bracket = re.match(r"\[([^\]\r\n]{1,256})\]", tail)
        item = bracket[1] if bracket else None
        if item is not None:
            tail = tail[bracket.end():]
            if match := re.fullmatch(r"([PT])x(\d{1,18})", item):
                fields[{"P": "pid", "T": "tid"}[match[1]]] = int(match[2])
                continue
            if match := re.fullmatch(r"([PTL])(\d{1,18})", item):
                fields[{"P": "pid", "T": "tid", "L": "line"}[match[1]]] = match[2]
                continue
            if match := re.fullmatch(r"(.+\.(?:cpp|cc|c|hpp|h|go|yak))(?::(\d{1,9}))?", item):
                fields["file"] = match[1]
                if match[2]:
                    fields["line"] = match[2]
                continue
            if "file" in fields and "function" not in fields and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:.*<>]{0,95}(?:\(\))?", item):
                fields["function"] = item
                continue
            if re.fullmatch(r"(?:handle|this|resource|controller)\s*[:=]\s*0x[0-9a-fA-F]{1,16}", item):
                continue
        else:
            # Yak source headers commonly use file.go:line followed by whitespace.
            if match := re.match(r"([A-Za-z][A-Za-z0-9_]*\.go):(\d{1,9})(?=\s|$)", tail):
                fields.update(file=match[1], line=match[2])
                tail = tail[match.end():]
                continue
            match = re.match(r'(task_id|job_id|node_id|reco_id|action_id|entry|node|function)\s*[:=]\s*("[^"\r\n]{1,128}"|[A-Za-z0-9_:<>.]+)(?=\s|,|$)', tail)
            if match:
                fields[match[1]] = match[2].strip('"')
                tail = tail[match.end():]
                continue
        if item is not None and (match := re.fullmatch(r'(task_id|job_id|node_id|reco_id|action_id|entry|node|function)\s*[:=]\s*"?([A-Za-z0-9_:<>.]+)"?', item)):
            fields[match[1]] = match[2]
            continue
        error_text = item if item is not None else tail
        for prefix, category in ERROR_CATEGORIES.items():
            if re.match(re.escape(prefix) + r"(?=\s|:|\.|$)", error_text, re.I):
                fields["error_category"] = category
                break
        break
    return _correlation(fields)


_UPDATER_PHASES = {"idle", "checking", "available", "downloading", "ready", "installing", "error"}
_UPDATER_RESULTS = {"installed", "error", "reboot_required", "cancelled", "installing"}
_SAFE_VERSION = re.compile(r"v?[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")


def _updater_observation() -> dict | None:
    """Passive snapshot of the existing singleton; never initializes the updater."""
    module = sys.modules.get("backend.app.updater")
    instance = getattr(module, "_instance", None) if module is not None else None
    if instance is None:
        return None
    try:
        state = instance.snapshot()
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    safe = {}
    if type(state.get("supported")) is bool:
        safe["supported"] = state["supported"]
    phase = state.get("phase")
    if phase in _UPDATER_PHASES:
        safe["phase"] = phase
        safe["handoff"] = phase == "installing"
    safe["has_error"] = bool(state.get("error"))
    candidate = state.get("candidate")
    if isinstance(candidate, dict):
        run_id = candidate.get("run_id")
        if type(run_id) is int and 0 <= run_id < 2**63:
            safe["candidate_run_id"] = run_id
    last = state.get("last_result")
    if isinstance(last, dict):
        if last.get("status") in _UPDATER_RESULTS:
            safe["last_result_status"] = last["status"]
        version = last.get("version")
        if isinstance(version, str) and _SAFE_VERSION.fullmatch(version):
            safe["last_result_version"] = version
    return safe


def runtime_observations() -> dict:
    """Integration hook: cached, noninitializing, nonblocking observations only."""
    names = {"api_loaded": "backend.app.api.main", "gui_loaded": "backend.app.shared.backend.maafw_runner",
             "sync_loaded": "backend.app.shared.backend.sync_coordinator", "outbox_loaded": "backend.app.shared.backend.outbox_service"}
    result = {label: name in sys.modules for label, name in names.items()}
    native = sys.modules.get("backend.app.shared.utils.native_logs")
    if native is not None:
        result["native_max_bytes"] = getattr(native, "NATIVE_MAX_BYTES", None)
        age = getattr(native, "LOG_MAX_AGE_S", None)
        if type(age) in (int, float):
            result["native_max_age_days"] = age / 86400
    runner = sys.modules.get(names["gui_loaded"])
    if runner is not None:
        state = runner.get_native_log_status()
        result.update(
            gui_initialized=state["initialized"], native_active_session=state["active_session"],
            native_retained_bytes=state["bytes"], native_observed_at=state["observed_at"],
            native_outstanding_jobs=state["outstanding_jobs"],
            native_post_uncertain=state["post_uncertain"],
            native_switch_uncertain="native_log_switch_uncertain" in state["warnings"],
            native_policy_unavailable="native_log_policy_unavailable" in state["warnings"],
            native_retention_budget_exceeded="native_retention_budget_exceeded" in state["warnings"],
        )
    workers = {}
    for key, module_name, getter in (
        ("sync", names["sync_loaded"], "observe_sync_service"),
        ("outbox", names["outbox_loaded"], "observe_outbox_verifier"),
    ):
        module = sys.modules.get(module_name)
        state = getattr(module, getter)() if module is not None else None
        result[key + "_initialized"] = state is not None
        workers[key] = state
    queues = sys.modules.get("backend.app.task_queue")
    if queues is not None:
        for name in ("maafw", "translation"):
            workers[name] = queues.TaskQueue.observations(name)
    result["workers"] = workers
    result["updater"] = _updater_observation()
    return result


def _runtime_summary(deadline: float) -> dict:
    result = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
              "platform": sys.platform, "account_context_accessed": False, "network_or_gui_probes": False,
              "build": {"status": "unavailable"}, "observations": {}}
    if time.monotonic() >= deadline:
        result["build"]["status"] = "omitted_deadline"
        return result
    try:
        values = runtime_observations()
        for key in ("api_loaded", "gui_loaded", "sync_loaded", "outbox_loaded", "gui_initialized",
                     "sync_initialized", "outbox_initialized", "shutdown_requested", "native_switch_uncertain",
                     "native_retention_budget_exceeded", "native_policy_unavailable", "native_post_uncertain"):
            if type(values.get(key)) is bool:
                result["observations"][key] = values[key]
        for key in ("native_max_bytes", "native_retained_bytes", "native_max_age_days",
                    "native_observed_at", "native_outstanding_jobs"):
            if type(values.get(key)) in (int, float) and 0 <= values[key] < 2**63:
                result["observations"][key] = values[key]
        active = values.get("native_active_session")
        if isinstance(active, str) and re.fullmatch(r"session-[0-9a-f]{32}", active):
            result["observations"]["native_active_session"] = active
        result["workers"] = {}
        for name in ("sync", "outbox", "maafw", "translation"):
            state = values.get("workers", {}).get(name)
            if not isinstance(state, dict):
                result["workers"][name] = {"initialized": False}
                continue
            safe = {}
            for key in ("initialized", "started", "alive", "stopping"):
                if type(state.get(key)) is bool:
                    safe[key] = state[key]
            for key in ("started_at", "heartbeat_at", "last_progress_at", "phase_started_at",
                        "phase_age_s", "observed_at", "pending", "active", "pending_observed_at",
                        "completed_iterations", "current_started", "last_completed"):
                value = state.get(key)
                if value is None or type(value) in (int, float) and 0 <= value < 2**63:
                    safe[key] = value
            phase = state.get("phase")
            if phase in {"not_started", "starting", "checking_source", "waiting", "stopped",
                         "verifying", "reconciling", "stopping"}:
                safe["phase"] = phase
            # Account context and free-form errors must never enter the bundle.
            safe["has_error"] = bool(state.get("last_error"))
            result["workers"][name] = safe
        updater = values.get("updater")
        result["updater"] = {"initialized": isinstance(updater, dict)}
        if isinstance(updater, dict):
            for key in ("supported", "handoff", "has_error"):
                if type(updater.get(key)) is bool:
                    result["updater"][key] = updater[key]
            if updater.get("phase") in _UPDATER_PHASES:
                result["updater"]["phase"] = updater["phase"]
            run_id = updater.get("candidate_run_id")
            if type(run_id) is int and 0 <= run_id < 2**63:
                result["updater"]["candidate_run_id"] = run_id
            if updater.get("last_result_status") in _UPDATER_RESULTS:
                result["updater"]["last_result_status"] = updater["last_result_status"]
            version = updater.get("last_result_version")
            if isinstance(version, str) and _SAFE_VERSION.fullmatch(version):
                result["updater"]["last_result_version"] = version
    except Exception:
        result["observations_status"] = "unavailable"
    path = resolve_repo_root() / "build-info.json"
    try:
        if time.monotonic() >= deadline:
            result["build"]["status"] = "omitted_deadline"
            return result
        info = _safe_stat(path)
        if info.st_size > 16384:
            return result
        with _open_checked(path, info) as stream:
            build = json.loads(stream.read(16384).decode("utf-8-sig"))
        if not isinstance(build, dict):
            return result
        safe = {}
        for key in ("run_id", "run_number", "run_attempt"):
            if type(build.get(key)) is int and 0 <= build[key] < 2**63:
                safe[key] = build[key]
        for key, pattern in (("version", r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]{1,64})?"), ("sha", r"[0-9a-f]{40}")):
            if isinstance(build.get(key), str) and re.fullmatch(pattern, build[key]):
                safe[key] = build[key]
        result["build"] = {"status": "available" if safe else "unavailable", **safe}
    except (OSError, ValueError, RecursionError):
        pass
    return result


def _sources() -> tuple[list[Source], Path | None]:
    backend, repo = resolve_backend_root(), resolve_repo_root()
    sources = []
    for name in ("api", "agent"):
        sources.append(Source(name, backend / "data/logs",
                              rf"{name}(?:[._-][0-9A-Za-z_.-]{{1,80}})?\.(?:log|jsonl)(?:\.\d{{1,3}})?",
                              (f"{name}.log", f"{name}.jsonl"), "application"))
    sources.extend([
        Source("yak", backend / "data/logs", r"yak_mitm(?:\.[0-9_.-]+)?\.log(?:\.\d{1,3})?", ("yak_mitm.log",), "yak"),
        Source("cli", backend / "data/logs", r"maafw_cli\.log(?:\.\d{1,3})?", ("maafw_cli.log",), "native"),
        Source("cli-legacy", backend / "debug", r"maafw\.log(?:\.\d{1,3})?", ("maafw.log",), "native"),
        Source("startup", backend / "data/logs", r"startup_failure\.log(?:\.\d{1,3})?", ("startup_failure.log",), "startup"),
    ])
    for label, directory in (("backend", backend / "debug"), ("backend-nested", backend / "debug/debug"),
                             ("assets", backend / "assets/debug"), ("deps", backend / "deps/bin/debug"),
                             ("deps-nested", backend / "deps/bin/debug/debug")):
        for prefix in ("maa", "maafw"):
            if label == "backend" and prefix == "maafw":
                pattern = r"maafw[._-][0-9A-Za-z_.-]{1,80}\.log(?:\.\d{1,3})?"
                current = ()
            else:
                pattern = rf"{prefix}(?:[._-][0-9A-Za-z_.-]{{1,80}})?\.log(?:\.\d{{1,3}})?"
                current = (f"{prefix}.log",)
            sources.append(Source(f"native-{label}-{prefix}", directory, pattern, current, "native"))
    # Match the updater's fixed per-install identity without resolving away reparse points.
    local = os.environ.get("LOCALAPPDATA", "")
    stage = None
    if local and Path(local).is_absolute():
        identity = hashlib.sha256(str(repo.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
        stage = Path(local) / "MaaAlibabaSupplierUpdater" / identity
        sources.append(Source("update-result", stage, r"last-result\.json", ("last-result.json",), "result"))
    for name in ("backend", "frontend"):
        sources.append(Source(f"dev-{name}", repo / "debug", rf"dev-preview-{name}\.log(?:\.err)?",
                              (f"dev-preview-{name}.log", f"dev-preview-{name}.log.err"), "application"))
    return sources, stage


def _safe_stat(path: Path, *, directory: bool = False):
    """Check every ancestor, including configured roots, without resolving links."""
    for component in reversed((path, *path.parents)):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("Unsafe diagnostic path")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("Not a directory")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError("Not a private regular file")
    return info


def _open_checked(path: Path, expected):
    """Validate the opened object before reading, including parent replacement races."""
    _safe_stat(path)
    descriptor = None
    try:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            descriptor = os.open(path, os.O_RDONLY | os.O_BINARY)
            get_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
            get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            get_path.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            length = get_path(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
            actual = buffer.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            if not 0 < length < len(buffer) or os.path.normcase(actual) != os.path.normcase(str(path.absolute())):
                raise OSError("Diagnostic path changed")
        else:
            parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in path.parts[1:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    os.close(parent)
                    parent = child
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            finally:
                os.close(parent)
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)):
            raise OSError("Diagnostic file rotated")
        _safe_stat(path)
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        return stream
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _metadata(line: str, kind: str) -> str | None:
    if kind == "application" or kind in ("native", "yak") and line.lstrip().startswith("{"):
        from backend.app.shared.utils.logging import sanitize_diagnostic_record

        safe = sanitize_diagnostic_record(line, source=kind)
        if safe is None:
            return None
        record = json.loads(safe)
        # Free-form legacy prose cannot be made safe by credential regexes alone.
        record.pop("message", None)
        if kind in ("native", "yak"):
            original = json.loads(line)
            fields = original.get("context")
            for key in ("task_id", "job_id", "node_id", "reco_id", "action_id", "pid", "tid", "line",
                        "entry", "node", "file", "source_file", "function", "error_category"):
                record["context"].pop(key, None)
            record["context"].update(_correlation({**original, **(fields if isinstance(fields, dict) else {})}))
        return json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    record = None
    if kind == "startup" and line.startswith("Backend startup failed:"):
        record = {"event": "startup_failed"}
    elif kind == "result":
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            return None
        if isinstance(value, dict) and value.get("status") in ("installed", "reboot_required", "error",
                                                               "cancelled", "installing"):
            record = {"event": "update_result", "status": value["status"]}
            version = value.get("version")
            if isinstance(version, str) and re.fullmatch(r"v?[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", version):
                record["version"] = version
    elif match := _NATIVE.match(line):
        record = {"time": match[1], "level": match[2].upper(), "event": "native_record"}
        record["context"] = _native_context(line[match.end():])
    elif kind == "yak":
        if match := _YAK.match(line):
            record = {"time": match[2], "level": match[1].upper(), "event": "yak_record"}
            record["context"] = _native_context(line[match.end():])
        else:
            for prefix, event in (("[!] Forwarding to Python failed (transport error)", "forward_transport_error"),
                                  ("[!] Forwarding to Python failed: HTTP ", "forward_http_error"),
                                  ("[!] GetHTTPPacketBody failed:", "packet_parse_error"),
                                  ("[!] payload error:", "payload_error"),
                                  ("[*] Yak MITM ", "proxy_started")):
                if line.startswith(prefix):
                    record = {"event": event}
                    if event == "forward_http_error" and re.fullmatch(r"[1-5]\d\d\s*", line[len(prefix):]):
                        record["status"] = int(line[len(prefix):])
                    break
    elif kind == "installer" and (match := _INSTALLER.match(line)):
        events = {"Installation process starting.": "installation_started",
                  "Installation process succeeded.": "installation_succeeded",
                  "Installation process failed.": "installation_failed",
                  "Log closed.": "log_closed"}
        record = {"time": match[1], "event": events.get(match[2].strip(), "installer_record")}
    return json.dumps(record, ensure_ascii=True, separators=(",", ":")) if record else None


_MAX_LEGACY_ORIGINS = 256


def _legacy_origin(safe: str) -> tuple | None:
    """Dedupe key for pre-schema log lines whose text is never exported."""
    record = json.loads(safe)
    if record.get("event") != "legacy.message":
        return None
    return record.get("level"), record.get("logger"), record.get("function"), record.get("line")


def _read_recent(path, info, kind, budget, deadline=None):
    expired = lambda: deadline is not None and time.monotonic() >= deadline
    details = {"read_bytes": 0, "tail_truncated": False, "omitted_records": 0,
               "omission_reasons": {}, "output_truncated": False, "deadline_exceeded": False,
               "discarded_prefix_bytes": 0, "snapshot_changed": False, "unprocessed_read_bytes": 0}
    if expired():
        details["deadline_exceeded"] = True
        return bytearray(), details
    with _open_checked(path, info) as stream:
        end = min(info.st_size, os.fstat(stream.fileno()).st_size)
        prefix = stream.read(min(3, end, budget))
        details["read_bytes"] = len(prefix)
        encoding = "utf-16-le" if prefix.startswith(b"\xff\xfe") else "utf-16-be" if prefix.startswith(b"\xfe\xff") else "utf-8"
        details["encoding"] = encoding
        start = max(0, end - max(0, budget - len(prefix))) if end > budget else 0
        if start and encoding.startswith("utf-16"):
            start += start % 2
        details["tail_truncated"] = bool(start)
        details["snapshot_bytes"] = end
        details["snapshot_changed"] = end != info.st_size
        if start == 0:
            raw = bytearray(prefix)
            start_read = len(prefix)
        else:
            raw = bytearray()
            start_read = start
        stream.seek(start_read)
        while stream.tell() < end and details["read_bytes"] < budget:
            if expired():
                details["deadline_exceeded"] = True
                break
            chunk = stream.read(min(64 * 1024, end - stream.tell(), budget - details["read_bytes"]))
            if not chunk:
                break
            raw.extend(chunk)
            details["read_bytes"] += len(chunk)
        after = os.fstat(stream.fileno())
        details["snapshot_changed"] |= after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns
        details["snapshot_read_incomplete"] = stream.tell() < end
    delimiter = b"\n\x00" if encoding == "utf-16-le" else b"\x00\n" if encoding == "utf-16-be" else b"\n"

    def lines():
        offset = 0
        while offset < len(raw):
            position = -1 if kind == "result" else raw.find(delimiter, offset)
            while position >= 0 and encoding.startswith("utf-16") and position % 2:
                if expired():
                    break
                position = raw.find(delimiter, position + 1)
            end_line = len(raw) if position < 0 else position + len(delimiter)
            yield raw[offset:end_line]
            offset = end_line

    result = bytearray()
    processed = 0
    legacy_origins = set()
    for raw_line in lines():
        if start and processed == 0 and kind != "result":
            processed += len(raw_line)
            details["discarded_prefix_bytes"] = len(raw_line)
            continue
        if expired():
            details["deadline_exceeded"] = True
            break
        size = len(raw_line)
        processed += size
        reason = None
        try:
            line = raw_line.decode(encoding).lstrip("\ufeff")
        except UnicodeError:
            line = ""
            reason = "invalid_encoding"
        if size > MAX_LINE_BYTES:
            reason = "oversized"
        elif reason:
            pass
        elif kind != "result" and not line.endswith("\n"):
            reason = "incomplete_last_record"
        elif kind == "result" and (start or details["snapshot_read_incomplete"]):
            reason = "incomplete_result"
        else:
            try:
                safe = _metadata(line, kind)
            except (ValueError, TypeError, RecursionError):
                safe = None
            if safe is None:
                reason = "unrecognized"
            elif kind == "application":
                origin = _legacy_origin(safe)
                if origin is not None and origin in legacy_origins:
                    reason = "duplicate"
                elif origin is not None and len(legacy_origins) < _MAX_LEGACY_ORIGINS:
                    legacy_origins.add(origin)
        if reason:
            details["omitted_records"] += 1
            details["omission_reasons"][reason] = details["omission_reasons"].get(reason, 0) + 1
            continue
        encoded = safe.encode("utf-8") + b"\n"
        if len(result) + len(encoded) > budget:
            details["output_truncated"] = True
            processed -= size
            break
        result.extend(encoded)
    details["unprocessed_read_bytes"] = max(0, len(raw) - processed)
    details["omitted_records_exact"] = not (details["deadline_exceeded"] or details["output_truncated"])
    return result, details


def _skip_reason(exc: OSError) -> str:
    """Categorize a rejected candidate without exposing its path."""
    text = str(exc)
    if "Unsafe" in text:
        return "unsafe_path"
    if "Not a" in text:
        return "nonregular"
    if "rotated" in text or "changed" in text:
        return "rotated"
    return "unavailable"


def build_bundle() -> bytes:
    deadline = time.monotonic() + COLLECTION_SECONDS
    expired = lambda: time.monotonic() >= deadline
    runtime = _runtime_summary(deadline)
    sources, stage = _sources()
    scans = {}
    scan_remaining = MAX_SCAN_ENTRIES
    scan_limited = False
    skipped_reasons = Counter()
    installer_omitted = 0

    def entries(directory):
        nonlocal scan_remaining, scan_limited
        if directory in scans:
            return scans[directory]
        names = []
        if expired():
            return names
        try:
            _safe_stat(directory, directory=True)
            limit = min(MAX_DIRECTORY_ENTRIES, scan_remaining)
            with os.scandir(directory) as iterator:
                for item in itertools.islice(iterator, limit):
                    if expired():
                        break
                    names.append(item.name)
            scan_remaining -= len(names)
            scan_limited |= len(names) == limit
        except OSError:
            pass
        scans[directory] = names
        return names

    if stage is not None:
        candidates = []
        for name in entries(stage):
            if expired():
                break
            if re.fullmatch(r"[0-9a-f]{32}", name):
                try:
                    info = _safe_stat(stage / name, directory=True)
                    candidates.append((info.st_mtime_ns, stage / name))
                except OSError as exc:
                    skipped_reasons[_skip_reason(exc)] += 1
        # Updater extraction puts installer.log next to the installer in the stage.
        installer_omitted = max(0, len(candidates) - 4)
        for _, directory in sorted(candidates, reverse=True)[:4]:
            sources.append(Source("installer", directory, r"installer\.log", ("installer.log",), "installer"))

    native_root = resolve_backend_root() / "data/logs/native"
    sessions = []
    active = runtime["observations"].get("native_active_session")
    native_names = dict.fromkeys(([active] if active else []) + entries(native_root))
    for name in native_names:
        if expired():
            break
        if re.fullmatch(r"session-[0-9a-f]{32}", name):
            session = native_root / name
            try:
                session_info = _safe_stat(session, directory=True)
                owner = _safe_stat(session / ".owner")
                with _open_checked(session / ".owner", owner) as marker:
                    if marker.read(64) != b"maa-native-session-v1\n":
                        continue
                try:
                    closed = _safe_stat(session / ".closed")
                except FileNotFoundError:
                    closed = None
                state = "active" if name == active else "closed" if closed else "open_or_unclosed"
                sessions.append((state != "closed", name == active, session_info.st_mtime_ns, session, state))
            except OSError as exc:
                skipped_reasons[_skip_reason(exc)] += 1
                continue
    chosen_sessions = sorted(sessions, reverse=True)[:4]
    for _, _, _, session, state in chosen_sessions:
        for directory in (session, session / "debug"):
            for prefix in ("maa", "maafw"):
                sources.append(Source(f"native-session-{prefix}", directory,
                                      rf"{prefix}(?:[._-][0-9A-Za-z_.-]{{1,80}})?\.log(?:\.\d{{1,3}})?",
                                      (f"{prefix}.log",), "native", state))

    by_source = {}
    for source in sources:
        by_source.setdefault(source.name, [])
        if expired():
            continue
        found = {}
        for name in (*source.current, *entries(source.directory)):
            if expired():
                break
            if re.fullmatch(source.pattern, name) is None:
                continue
            path = source.directory / name
            try:
                found[path] = _safe_stat(path)
            except OSError as exc:
                skipped_reasons[_skip_reason(exc)] += 1
                continue
        by_source.setdefault(source.name, []).extend((source, path, info) for path, info in found.items())
    groups = [sorted(group, key=lambda item: (item[0].session_state == "active",
                     item[0].session_state == "open_or_unclosed", item[2].st_mtime_ns), reverse=True)
              for group in by_source.values()]
    source_status = [{"source": name, "candidates": len(group)} for name, group in by_source.items()]
    # One recent file per source precedes every source's rotations and optional dev output.
    primary = [group for group in groups if group and not group[0][0].name.startswith("dev-")]
    optional = [group for group in groups if group and group[0][0].name.startswith("dev-")]
    selected = [group[0] for group in primary]
    selected += sorted((item for group in primary for item in group[1:]), key=lambda item: item[2].st_mtime_ns, reverse=True)
    selected += [item for group in optional for item in group]
    file_limit_omitted = max(0, len(selected) - (MAX_FILES - 2))
    selected = selected[:MAX_FILES - 2]
    remaining = MAX_TOTAL_BYTES - 64 * 1024
    manifest = {"schema_version": 1, "policy": "recent-metadata-only", "sources": source_status,
                "scan_limited": scan_limited, "discovery_incomplete": expired() or scan_limited,
                "omitted_files_file_limit": file_limit_omitted,
                "unavailable_or_unsafe_candidates": sum(skipped_reasons.values()),
                "unavailable_or_unsafe_categories": dict(sorted(skipped_reasons.items())),
                "omitted_installer_stages": installer_omitted,
                "omitted_native_sessions": max(0, len(sessions) - len(chosen_sessions)),
                "files": [], "limits": {"collection_seconds": COLLECTION_SECONDS,
                    "files": MAX_FILES, "per_file_bytes": MAX_FILE_BYTES,
                    "total_bytes": MAX_TOTAL_BYTES, "archive_bytes": MAX_ARCHIVE_BYTES}}
    with tempfile.TemporaryFile() as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, (source, path, info) in enumerate(selected):
                budget = min(MAX_FILE_BYTES, remaining // (len(selected) - index))
                entry = {"source": source.name, "file": f"logs/{index + 1:02d}-{source.name}.jsonl"}
                if source.session_state:
                    entry["session_state"] = source.session_state
                    entry["live_snapshot"] = source.session_state != "closed"
                if expired():
                    entry["status"] = "omitted_deadline"
                    manifest["files"].append(entry)
                    continue
                try:
                    data, details = _read_recent(path, info, source.kind, budget, deadline)
                except OSError:
                    remaining -= budget  # A failed read may already have consumed its whole allowance.
                    entry["status"] = "unavailable_or_rotated"
                else:
                    remaining -= max(details["read_bytes"], len(data))
                    if data:
                        archive.writestr(entry["file"], data)
                    entry.update(details, status="included", exported_bytes=len(data))
                    del data
                manifest["files"].append(entry)
            manifest["deadline_exceeded"] = expired() or any(item.get("deadline_exceeded") for item in manifest["files"])
            runtime["native_retention"] = {
                "status": "bounded_observation", "inventory_complete": False,
                "owned_sessions_observed": len(sessions), "sessions_selected": len(chosen_sessions),
                "closed_sessions_observed": sum(item[4] == "closed" for item in sessions),
                "open_or_unclosed_sessions_observed": sum(item[4] != "closed" for item in sessions),
                "active_session_known": bool(active), "session_limit": 4,
                "log_bytes_observed": sum(info.st_size for group in groups for source, _, info in group
                                          if source.session_state is not None),
                "scan_limited": scan_limited, "deadline_exceeded": manifest["deadline_exceeded"],
                "rotated_or_pruned": False,
            }
            for key in ("max_bytes", "retained_bytes", "max_age_days", "observed_at", "outstanding_jobs",
                        "switch_uncertain", "retention_budget_exceeded", "policy_unavailable", "post_uncertain"):
                if "native_" + key in runtime["observations"]:
                    runtime["native_retention"][key] = runtime["observations"]["native_" + key]
            archive.writestr("runtime.json", json.dumps(runtime, ensure_ascii=True))
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True))
        if output.tell() > MAX_ARCHIVE_BYTES:
            raise ValueError("Diagnostic archive limit exceeded")
        output.seek(0)
        return output.read(MAX_ARCHIVE_BYTES)


class _Lease:
    """A cancelled request cannot release admission while its worker still runs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.worker_done = False
        self.closed = False

    def finish(self, *, worker=False):
        with self.lock:
            if worker:
                self.worker_done = True
            else:
                self.closed = True
            if self.worker_done and self.closed:
                _ADMISSION.release()


class DiagnosticResponse(Response):
    """Retain admission through the existing BaseHTTPMiddleware send boundary."""

    async def __call__(self, scope, receive, send):
        if not _ADMISSION.acquire(blocking=False):
            await JSONResponse(err("A diagnostic download is already active."), status_code=429,
                               headers={"Retry-After": "2"})(scope, receive, send)
            return
        lease = _Lease()
        # Scheduling is synchronous, so cancellation cannot strand an unstarted lease.
        import asyncio

        try:
            future = asyncio.get_running_loop().run_in_executor(None, build_bundle)
        except BaseException:
            _ADMISSION.release()
            raise

        def worker_finished(done):
            # Consume late errors without logging exception locals or paths.
            if not done.cancelled():
                done.exception()
            lease.finish(worker=True)

        future.add_done_callback(worker_finished)
        try:
            try:
                body = await asyncio.shield(future)
            except Exception:
                response = JSONResponse(err("Diagnostic bundle is unavailable."), status_code=503)
            else:
                response = Response(body, media_type="application/zip", headers={
                    "Content-Disposition": 'attachment; filename="diagnostics-recent.zip"',
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
            await response(scope, receive, send)
            # The application's BaseHTTPMiddleware reports disconnect after its outer
            # response is fully sent. Its task group cancels us on send failure. Merely
            # finishing Response.__call__ here would release admission too early.
            while (await receive())["type"] != "http.disconnect":
                pass
        finally:
            lease.finish()
