"""Shared log transport and the diagnostic export trust boundary."""

from __future__ import annotations

import json
import logging
import re
import stat
import sys
import threading
import time
from pathlib import Path

from loguru import logger

from backend.app.shared.utils.settings import resolve_backend_root

_CONFIGURED = False
_CONFIGURE_LOCK = threading.Lock()
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_ARCHIVES = 5
LOG_MAX_AGE_S = 7 * 24 * 60 * 60
LOG_ARCHIVE_BYTES = LOG_MAX_BYTES * LOG_ARCHIVES
_LABEL = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_ANSI_SGR = re.compile(r"\x1b\[[0-9;:]{0,64}m")
_NATIVE_IDS = frozenset({"task_id", "job_id", "node_id", "reco_id", "action_id", "pid", "tid", "line"})
NATIVE_ENTRIES = frozenset({
    "ChatInput", "ChatInput_GoToInput", "ChatInput_SelectAll", "ChatInput_SelectAll_A",
    "ChatInput_SelectAll_ReleaseCtrl", "ChatInput_ClearText", "ChatInput_InputText",
    "ChatInput_SendOnly", "ChatInput_SendMessage", "ContactSearch", "ContactSearch_GoToSearch",
    "ContactSearch_ClickSearchButton", "ContactSearch_ClearSearchBox", "ContactSearch_InputText",
    "ContactSearch_PressEnter", "Diagnostics_ChatInput", "Diagnostics_ChatInput_Recognize",
    "Diagnostics_ContactSearch", "Diagnostics_ContactSearch_Recognize",
})
ERROR_CATEGORIES = {
    "timeout": "timeout", "timed out": "timeout", "connection refused": "connection_refused",
    "connection reset": "connection_reset", "permission denied": "permission_denied",
    "recognition failed": "recognition_failed", "action failed": "action_failed",
    "resource load failed": "resource_load_failed", "invalid argument": "invalid_argument",
    "task failed": "task_failed", "job failed": "job_failed", "node failed": "node_failed",
}
_FIELDS = frozenset({
    "run_id", "request_id", "origin_request_id", "account_epoch", "origin_account_epoch",
    "queue_task_id", "queue", "outbox_id", "attempt", "version", "phase", "status",
    "native_job_id", "native_runtime_id", "native_log_session_id", "sync_id", "source_revision", "revision",
    "inserted", "updated", "unchanged", "count", "duration_ms", "method", "route",
    "complete", "disconnected", "send_failed", "entry", "exception_type", "signal", "component",
})
_RUNTIME_MESSAGES = (
    "Queued task failed", "request failed", "translation query failed", "translation chunk failed",
    "failed to load translation context", "Translation agent omitted an item",
    "Unhandled account API error", "Unhandled API error", "node diagnostic failed",
    "Outbox operation failed", "Outbox terminal write deferred", "Outbox local-source reconciliation failed",
    "Outbox shutdown persistence failed", "Outbox shutdown write failed",
    "Outbox verifier has not stopped", "Outbox GUI attempt still running after shutdown timeout",
    "IM sync service tick failed", "IM source refresh failed", "IM database source snapshot refreshed",
    "CRM sync state restore failed", "CRM mapping redirect", "Failed to sync data into CRM SDK",
    "Failed to load SelfInfo from CRM SDK", "Ignoring corrupt app config", "Failed to write app config",
    "Stored account key is corrupt, ignoring", "Encrypted DB not found", "DB decryption failed",
    "Rebuilt IM cache validation failed; discarded", "IM source copy failed; retaining previous cache",
    "Decrypting IM database...", "Failed to open cached IM database",
    "Legacy key file has invalid size, skipping", "Migrated legacy AES key file into database",
    "Retrieving AES key from process memory...", "AES key retrieved successfully",
    "Encrypted DB size is not a multiple of 16 bytes", "Skipping corrupt pool record",
    "Skipping corrupt user pool record", "JSON parse failed", "JSONP unwrap: no JSON found",
    "JSONP unwrap: JSON parse failed", "queryCustomerInfo parsed", "queryCustomerInfo: response did not report success",
    "queryCustomerInfo: buyerInfo is empty", "queryCustomerInfo: no login_id found",
    "queryCustomerInfo: JSONP unwrap returned None", "fetchcard: fbCardList empty", "fetchcard: card[0].data empty",
    "UserInfo parsed", "SelfInfo parsed for selected account", "ProductCard parsed", "InquiryCard parsed",
    "GenericCard parsed", "MITM event matched", "MITM response status", "MITM receiver thread started",
    "MITM v4 receiver listening", "Failed to process Yak MITM event", "Yak MITM proxy terminated",
    "Yak MITM script not found; proxy was not created", "Yak executable not found; proxy was not created",
    "Failed to create Yak MITM proxy process", "Yak MITM proxy exited during startup",
    "Yak MITM proxy process created", "MaaPiCli process created", "MaaPiCli process exited",
    "MaaPiCli exit observation failed", "MaaPiCli exit observation unavailable",
    "MaaPiCli termination not confirmed", "MaaPiCli output drain is still pending",
    "MaaFW runner init failed", "MaaFW node execution failed", "MaaFW send wait failed",
    "MaaFW send submission failed", "Failed to start MaaFW", "Outbox shutdown failed",
    "GUI task queue shutdown failed", "IM sync shutdown failed", "MaaFW shutdown failed",
    "Yak MITM shutdown failed", "MITM receiver shutdown failed", "Task queue",
    "Shutdown requested via API; stopping uvicorn server", "Failed to shut down Maa AgentServer",
    "[agent] stopping Maa AgentServer...", "[agent] interrupted; shutting down...",
    "[agent] AgentServer.join() still blocking after shutdown; forcing exit",
    "Started server process", "Finished server process", "Waiting for application startup.",
    "Application startup complete.", "Application startup failed. Exiting.", "Shutting down",
    "Waiting for application shutdown.", "Application shutdown complete.", "HTTP Request:",
)
_SANITIZATION_FAILURE = '{"schema":1,"level":"ERROR","event":"logging.sanitization_failed","message":"logging.sanitization_failed","context":{}}'


def safe_fields(fields: dict) -> dict:
    """Keep only bounded scalar telemetry; never stringify application objects."""
    result = {}
    for key, value in fields.items():
        if key in _NATIVE_IDS:
            if type(value) is str and re.fullmatch(r"\d{1,18}", value):
                value = int(value)
            if type(value) is int and 0 <= value < 2**63:
                result[key] = value
            continue
        if key == "exit_code":
            if type(value) is int and -(2**31) <= value < 2**32:
                result[key] = value
            continue
        if key == "port":
            if type(value) is int and 0 <= value <= 65535:
                result[key] = value
            continue
        if key == "tcp_reachable":
            if type(value) is bool:
                result[key] = value
            continue
        if key == "source_file":
            if type(value) is str:
                basename = value.replace("\\", "/").rsplit("/", 1)[-1]
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}\.(?:cpp|cc|c|hpp|h|go|yak|py)", basename):
                    result[key] = basename
            continue
        if key == "error_category":
            if type(value) is str and value in ERROR_CATEGORIES.values():
                result[key] = value
            continue
        if key == "node":
            if type(value) is str and value in NATIVE_ENTRIES:
                result[key] = value
            continue
        if key not in _FIELDS:
            continue
        if value is None or type(value) in (bool, int):
            result[key] = value
        elif type(value) is float and -1e15 < value < 1e15:
            result[key] = round(value, 3)
        elif isinstance(value, str):
            if value.startswith(("maa_", "sk-")):
                continue
            if key == "route":
                if len(value) <= 256 and re.fullmatch(r"/[A-Za-z0-9_/{}/:.-]*|unmatched", value):
                    result[key] = value
            elif _LABEL.fullmatch(value):
                result[key] = value
    return result


def _native_context(tail: str) -> dict:
    """Read only the bounded metadata prefix; never search free-form payloads."""
    fields = {}
    function_seen = False
    for _ in range(24):
        tail = tail.lstrip(" ,\t")
        bracket = re.match(r"\[([^\]\r\n]{1,256})\]", tail)
        item = bracket[1] if bracket else None
        if bracket:
            tail = tail[bracket.end():]
            if match := re.fullmatch(r"([PT])x([0-9A-Fa-f]{1,16})", item):
                fields[{"P": "pid", "T": "tid"}[match[1]]] = int(match[2], 16)
                continue
            if match := re.fullmatch(r"([PTL])(\d{1,18})", item):
                fields[{"P": "pid", "T": "tid", "L": "line"}[match[1]]] = match[2]
                continue
            if match := re.fullmatch(r"(.+\.(?:cpp|cc|c|hpp|h|go|yak))(?::(\d{1,9}))?", item):
                fields["source_file"] = match[1]
                if match[2]:
                    fields["line"] = int(match[2])
                continue
            if ("source_file" in fields and not function_seen and ("::" in item or item.endswith("()"))
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:.*<>]{0,95}(?:\(\))?", item)):
                function_seen = True
                continue
            if re.fullmatch(r"(?:handle|this|resource|controller)\s*[:=]\s*0x[0-9a-fA-F]{1,16}", item):
                continue
        candidate = item if item is not None else tail
        match = re.match(r'(task_id|job_id|node_id|reco_id|action_id)\s*[:=]\s*"?(\d{1,18})"?(?=\s|,|$)', candidate)
        if match and (item is None or match.end() == len(item)):
            fields[match[1]] = int(match[2])
            if item is None:
                tail = tail[match.end():]
            continue
        match = re.match(r'(entry|node)\s*[:=]\s*"?([A-Za-z0-9_]{1,80})"?(?=\s|,|$)', candidate)
        if (match and (item is None or match.end() == len(item)) and match[2] in NATIVE_ENTRIES):
            fields[match[1]] = match[2]
            if item is None:
                tail = tail[match.end():]
            continue
        for prefix, category in ERROR_CATEGORIES.items():
            if re.match(re.escape(prefix) + r"(?=\s|:|\.|$)", candidate, re.I):
                fields["error_category"] = category
                break
        break
    return safe_fields(fields)


def _runtime_message(value: str) -> str:
    # Loguru has already interpolated arguments here. Return only audited literal
    # operation names, never the original string or an unknown dynamic suffix.
    for message in _RUNTIME_MESSAGES:
        if value.startswith(message):
            return message
    return "Log message omitted"


def _safe_origin(fields: dict) -> dict:
    result = {}
    for name in ("logger", "module", "file", "function"):
        value = fields.get(name)
        if type(value) is str and re.fullmatch(r"[\w.<>-]{1,128}", value):
            result[name] = value
    if type(fields.get("line")) is int and 0 <= fields["line"] <= 10**9:
        result["line"] = fields["line"]
    return result


def _safe_exception(exception) -> dict | None:
    if not exception:
        return None
    chain = []
    seen = set()
    remaining_frames = 32
    value = exception.value
    kind = exception.type.__name__
    tb = exception.traceback
    relation = None
    while len(chain) < 8:
        frames = []
        while tb is not None and remaining_frames:
            code = tb.tb_frame.f_code
            frames.append(_safe_origin({"file": Path(code.co_filename).name,
                                        "function": code.co_name, "line": tb.tb_lineno}))
            remaining_frames -= 1
            tb = tb.tb_next
        item = {"type": kind if _LABEL.fullmatch(kind) else "Exception", "frames": frames}
        if relation:
            item["relation"] = relation
        if tb is not None:
            item["truncated"] = True
        chain.append(item)
        if value is None:
            break
        seen.add(id(value))
        cause = value.__cause__
        relation = "cause" if cause is not None else "context"
        value = cause if cause is not None else None if value.__suppress_context__ else value.__context__
        if value is None:
            break
        if id(value) in seen or len(chain) == 8:
            chain[-1]["truncated"] = True
            break
        kind, tb = type(value).__name__, value.__traceback__
    result = chain[0]
    if len(chain) > 1:
        result["chain"] = chain[1:]
    return result


def _patch_record(record: dict) -> None:
    extra, message, exception = record.get("extra"), record.get("message"), record.get("exception")
    # Install a constant safe fallback BEFORE evaluating any record-owned value.
    record["exception"] = None
    record["message"] = "logging.sanitization_failed"
    record["extra"] = {"_safe_json": _SANITIZATION_FAILURE}
    try:
        from backend.app.shared.utils.log_context import capture_log_context

        extra = extra if type(extra) is dict else {}
        event = extra.get("event", "application.message")
        event = event if type(event) is str and _LABEL.fullmatch(event) else "application.message"
        source = extra.get("log_source", "application")
        context = extra.get("context")
        origin = extra.get("stdlib_origin")
        if type(origin) is not dict:
            origin = {"logger": record["name"], "module": record["module"], "file": record["file"].name,
                      "function": record["function"], "line": record["line"]}
        payload = {
            "schema": 1, "time": record["time"].isoformat(), "level": record["level"].name,
            **_safe_origin(origin),
            "source": source if type(source) is str and _LABEL.fullmatch(source) else "application", "event": event,
            "message": _runtime_message(message) if event == "application.message" else event,
            "context": safe_fields({**capture_log_context(), **(context if type(context) is dict else {})}),
        }
        safe_exception = _safe_exception(exception)
        if safe_exception:
            payload["exception"] = safe_exception
        serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        record["message"] = payload["message"]
        record["extra"] = {"_safe_json": serialized}
    except Exception:
        # Do not log this failure recursively or render its exception value.
        pass


def sanitize_diagnostic_record(line: str, source: str = "application") -> str | None:
    """Return one safe JSON line (no newline), or None for unknown/continuation data.

    Native and Yak output contributes recognized event metadata only. Malformed
    JSON and legacy traceback continuations are rejected. Application records
    accept our structured schema or a recognized single-line Loguru header.
    Free-form prose is omitted on export: redaction patterns cannot establish
    that an unlabeled string is not a message body or credential. Runtime files
    retain audited operation names; exports retain correlation and safe stacks.
    """
    if not isinstance(line, str) or len(line) > 65536:
        return None
    # SGR is emitted by the native CLI even through a pipe. Bound both sequence
    # length and count, and reject other terminal controls rather than guessing.
    line, sgr_count = _ANSI_SGR.subn("", line, count=33)
    if sgr_count > 32 or "\x1b" in line:
        return None
    source = source if isinstance(source, str) and _LABEL.fullmatch(source) else "application"
    try:
        data = json.loads(line)
    except (ValueError, TypeError, RecursionError):
        if source in {"native", "maafw_cli", "yak"}:
            native = re.match(
                r"^\[(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d{1,6})?)\]\s*"
                r"\[(TRC|DBG|INF|WRN|ERR|FTL|TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\]", line, re.I,
            )
            yak = re.match(r"^\[(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]\s+"
                           r"\[?(\d{4}-\d\d-\d\d|\d\d:\d\d:\d\d)", line, re.I) if source == "yak" else None
            if native:
                return json.dumps({"schema": 1, "source": source, "time": native[1],
                                   "level": native[2].upper(), "event": "native.record",
                                   "context": _native_context(line[native.end():])})
            if yak:
                return json.dumps({"schema": 1, "source": source, "time": yak[2],
                                   "level": yak[1].upper(), "event": "yak.record"})
            if source == "yak":
                for prefix, event in (("[!] Forwarding to Python failed (transport error)", "yak.forward_failed"),
                                      ("[!] Forwarding to Python failed: HTTP ", "yak.http_failed"),
                                      ("[!] GetHTTPPacketBody failed:", "yak.parse_failed"),
                                      ("[!] payload error:", "yak.payload_failed"),
                                      ("[*] Yak MITM ", "yak.started")):
                    if line.startswith(prefix):
                        return json.dumps({"schema": 1, "source": source, "event": event})
        match = re.fullmatch(
            r"\d{4}-\d\d-\d\d[ T][\d:.+ -]+\s*\|\s*(TRACE|DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)"
            r"\s*\|\s*([\w.]+):([\w<>]+):(\d+)\s*-\s*([^\r\n]*)[\r\n]*", line,
        )
        if match is None:
            return None
        data = {"schema": 1, "level": match[1], "event": "legacy.message", "message": match[5],
                "logger": match[2], "function": match[3], "line": int(match[4])}
    if not isinstance(data, dict) or data.get("schema") != 1:
        return None
    result = {"schema": 1, "source": source}
    for name in ("time", "level", "event"):
        value = data.get(name)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:+ -]{1,128}", value):
            result[name] = value
    message = data.get("message")
    if isinstance(message, str) and (message in {"completed", "started", "stopped"}
                                     or message == result.get("event")):
        result["message"] = message
    result.update(_safe_origin(data))
    result["context"] = safe_fields(data["context"]) if isinstance(data.get("context"), dict) else {}
    exception = data.get("exception")
    if isinstance(exception, dict):
        chain = exception.get("chain", [])
        sanitized = []
        remaining_frames = 32
        for index, item in enumerate([exception, *(chain[:7] if isinstance(chain, list) else [])]):
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if not isinstance(kind, str) or not _LABEL.fullmatch(kind):
                continue
            frames = []
            raw_frames = item.get("frames", [])
            for frame in raw_frames[:remaining_frames] if isinstance(raw_frames, list) else []:
                if not isinstance(frame, dict):
                    continue
                frames.append(_safe_origin({name: frame.get(name) for name in ("file", "function", "line")}))
                remaining_frames -= 1
            entry = {"type": kind, "frames": frames}
            if index and item.get("relation") in ("cause", "context"):
                entry["relation"] = item["relation"]
            if item.get("truncated") is True:
                entry["truncated"] = True
            sanitized.append(entry)
        if sanitized:
            result["exception"] = sanitized[0]
            if len(sanitized) > 1:
                result["exception"]["chain"] = sanitized[1:]
    return json.dumps(result, ensure_ascii=True, separators=(",", ":"))


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "uvicorn.access":
            return
        try:
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            origin = _safe_origin({"logger": record.name, "module": record.module,
                                   "file": Path(record.pathname).name, "function": record.funcName, "line": record.lineno})
            # Never call getMessage(): third-party arguments can contain secrets or
            # invoke arbitrary __str__. Preserve origin, severity and safe exceptions.
            message = _runtime_message(record.msg) if type(record.msg) is str else "Log message omitted"
            logger.bind(stdlib_origin=origin).opt(exception=record.exc_info).log(level, message)
        except Exception:
            pass


def _retain_logs(files) -> None:
    """Prune only owned, closed archives; each writer has a 60 MiB total budget."""
    cutoff = time.time() - LOG_MAX_AGE_S
    candidates = []
    for filename in files:
        path = Path(filename)
        if not re.fullmatch(r"(?:api|agent)\.[0-9_.-]+\.log", path.name):
            continue
        try:
            info = path.lstat()
            if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                candidates.append((info.st_mtime, path, info.st_size))
        except FileNotFoundError:
            continue
    retained, total = 0, 0
    for modified, path, size in sorted(candidates, key=lambda item: (item[0], item[1].name), reverse=True):
        if (modified < cutoff or retained >= LOG_ARCHIVES or size > LOG_MAX_BYTES
                or total + size > LOG_ARCHIVE_BYTES):
            path.unlink(missing_ok=True)
        else:
            retained += 1
            total += size


def _log_rotation():
    current_file, deadline = None, 0

    def rotate(message, file):
        nonlocal current_file, deadline
        now = time.time()
        if file is not current_file:
            current_file = file
            deadline = min(now, Path(file.name).stat().st_mtime) + 24 * 60 * 60
        # Daily rotation also ages out a continuously written, low-volume file.
        return now >= deadline or file.tell() + len(message.encode("utf-8")) > LOG_MAX_BYTES

    return rotate


def configure_logging(*, source: str = "application") -> None:
    global _CONFIGURED
    with _CONFIGURE_LOCK:
        if _CONFIGURED:
            return
        logger.remove()
        logger.configure(patcher=_patch_record, extra={"log_source": source})
        options = dict(format=lambda record: "{extra[_safe_json]}\n", backtrace=False, diagnose=False,
                       colorize=False, level="INFO")
        if sys.stderr is not None:
            logger.add(sys.stderr, **options)
        log_dir = resolve_backend_root() / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        # Separate writers avoid cross-process rotation races. At most 60 MiB per
        # writer (120 MiB application + agent), with seven-day archive retention.
        filename = "agent.log" if source == "agent" else "api.log"
        _retain_logs(log_dir.glob(f"{Path(filename).stem}.*.log"))
        logger.add(log_dir / filename, rotation=_log_rotation(), retention=_retain_logs, enqueue=True,
                   encoding="utf-8", **options)
        logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)
        _CONFIGURED = True
