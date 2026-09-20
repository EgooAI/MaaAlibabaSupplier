"""Bounded, sanitized child output; disk failures must not stop pipe draining."""

from __future__ import annotations

import math
import re
import stat
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO, Iterator

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_ARCHIVES = 5
LOG_MAX_AGE_S = 7 * 24 * 60 * 60
MAX_LINE_BYTES = 16 * 1024


def is_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def check_plain_path(path: Path) -> None:
    """Reject symlinks and Windows junctions, including existing ancestors."""
    for part in (path, *path.parents):
        try:
            if is_reparse(part):
                raise ValueError("Diagnostic path contains a reparse point")
        except FileNotFoundError:
            continue


def bounded_lines(
    stream: BinaryIO, *, max_line_bytes: int = MAX_LINE_BYTES,
    max_read_bytes: int | None = None,
) -> Iterator[str | None]:
    """Drop an oversized line in full, including its tail; never split secrets.

    None indicates a dropped record. Memory is bounded even without newlines.
    A read budget is used for native exports, never for live child pipes.
    """
    pending = bytearray()
    dropping = False
    remaining = max_read_bytes
    while remaining is None or remaining > 0:
        chunk = stream.read(8192 if remaining is None else min(8192, remaining))
        if not chunk:
            if pending and not dropping:
                yield pending.decode("utf-8", errors="replace")
            return
        if remaining is not None:
            remaining -= len(chunk)
        parts = chunk.split(b"\n")
        for index, part in enumerate(parts):
            if not dropping:
                if len(pending) + len(part) > max_line_bytes:
                    pending.clear()
                    dropping = True
                    yield None
                else:
                    pending.extend(part)
            if index < len(parts) - 1:
                if not dropping:
                    yield pending.decode("utf-8", errors="replace").rstrip("\r")
                pending.clear()
                dropping = False
    # A budget boundary is not a complete line and cannot be safely sanitized.


class RotatingDiagnosticFile:
    """Single-writer size/age limits; .age records each generation's first write."""

    def __init__(
        self, path: Path, *, max_bytes: int = LOG_MAX_BYTES,
        archives: int = LOG_ARCHIVES, max_age_s: float = LOG_MAX_AGE_S,
    ) -> None:
        if max_bytes < 1 or archives < 1 or max_age_s <= 0:
            raise ValueError("Invalid diagnostic retention limits")
        self.path = path
        self.max_bytes = max_bytes
        self.archives = archives
        self.max_age_s = max_age_s
        self._last_prune = float("-inf")
        self._next_expiry = float("inf")
        self._opened_at: float | None = None
        self._rotate_at: float | None = None
        check_plain_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

    def _archive(self, number: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{number}")

    def _age_path(self, path: Path) -> Path:
        return path.with_name(path.name + ".age")

    def _read_age(self, path: Path) -> float:
        age_path = self._age_path(path)
        check_plain_path(age_path)
        try:
            with age_path.open("r", encoding="ascii") as metadata:
                epoch = float(metadata.read(64))
            return epoch if math.isfinite(epoch) and epoch >= 0 else 0.0
        except FileNotFoundError:
            info = path.stat()
            # Legacy append logs have no first-write metadata. Birth time is a
            # conservative lower bound; ctime is NOT creation time on Unix.
            born = getattr(info, "st_birthtime", info.st_ctime if sys.platform == "win32" else 0.0)
            return min(born, info.st_mtime)
        except (ValueError, UnicodeError):
            return 0.0

    def _write_age(self, path: Path, epoch: float) -> None:
        age_path = self._age_path(path)
        check_plain_path(age_path)
        age_path.write_text(repr(epoch), encoding="ascii")

    def write(self, text: str) -> None:
        payload = (text.rstrip("\r\n") + "\n").encode("utf-8", errors="replace")
        if len(payload) > self.max_bytes:
            return
        now = time.time()
        monotonic = time.monotonic()
        check_plain_path(self.path)
        if monotonic - self._last_prune >= min(60, self.max_age_s) or now >= self._next_expiry:
            self._next_expiry = float("inf")
            for path in (self.path, *(self._archive(i) for i in range(1, self.archives + 1))):
                check_plain_path(path)
                age_path = self._age_path(path)
                check_plain_path(age_path)
                try:
                    info = path.stat()
                    epoch = self._read_age(path)
                    if epoch <= now - self.max_age_s or epoch > now or info.st_size > self.max_bytes:
                        path.unlink()
                        age_path.unlink(missing_ok=True)
                    else:
                        self._next_expiry = min(self._next_expiry, epoch + self.max_age_s)
                except FileNotFoundError:
                    age_path.unlink(missing_ok=True)
            self._last_prune = monotonic
        exists = self.path.exists()
        if self._opened_at is None or not exists:
            self._opened_at = self._read_age(self.path) if exists else now
            self._rotate_at = monotonic + max(0, min(86400, self.max_age_s) - (now - self._opened_at))
            # Persist before appending, so failures cannot give old data a new age.
            self._write_age(self.path, self._opened_at)
        if exists and (
            self.path.stat().st_size + len(payload) > self.max_bytes
            or now - self._opened_at >= min(86400, self.max_age_s)
            or monotonic >= self._rotate_at
        ):
            for number in range(self.archives, 0, -1):
                target = self._archive(number)
                source = self.path if number == 1 else self._archive(number - 1)
                check_plain_path(source)
                check_plain_path(target)
                check_plain_path(self._age_path(source))
                check_plain_path(self._age_path(target))
                if number == self.archives:
                    target.unlink(missing_ok=True)
                    self._age_path(target).unlink(missing_ok=True)
                if source.exists():
                    epoch = self._read_age(source)
                    source.replace(target)
                    self._write_age(target, epoch)
                    self._age_path(source).unlink(missing_ok=True)
            self._opened_at = now
            self._rotate_at = monotonic + min(86400, self.max_age_s)
            self._write_age(self.path, now)
        self._next_expiry = min(self._next_expiry, self._opened_at + self.max_age_s)
        with self.path.open("ab") as output:
            output.write(payload)


class ProcessLogDrain:
    def __init__(self, stream: BinaryIO, path: Path, *, source: str) -> None:
        self.stream = stream
        self.path = path
        self.source = source
        self.dropped_records = 0
        self.error_type: str | None = None
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"{source}-logs")

    def start(self) -> ProcessLogDrain:
        self.thread.start()
        return self

    def _run(self) -> None:
        writer = None
        try:
            try:
                writer = RotatingDiagnosticFile(self.path)
            except Exception as exc:
                self.error_type = type(exc).__name__
            for line in bounded_lines(self.stream):
                if line is None or writer is None:
                    self.dropped_records += 1
                    continue
                try:
                    from backend.app.shared.utils.logging import sanitize_diagnostic_record

                    safe = sanitize_diagnostic_record(line, source=self.source)
                    if safe is not None:
                        writer.write(safe)
                    else:
                        self.dropped_records += 1
                except Exception as exc:
                    # Never write raw output as a fallback, including on disk full.
                    self.error_type = type(exc).__name__
                    self.dropped_records += 1
        except Exception as exc:
            self.error_type = type(exc).__name__
        finally:
            try:
                self.stream.close()
            except Exception as exc:
                self.error_type = type(exc).__name__
            finally:
                self.finished.set()

    def join(self, timeout: float = 1.0) -> bool:
        self.thread.join(timeout)
        return self.finished.is_set()

    def status(self) -> dict:
        return {
            "source": self.source, "finished": self.finished.is_set(),
            "dropped_records": self.dropped_records, "error_type": self.error_type,
        }


def attach_process_log(process, path: Path, *, source: str) -> ProcessLogDrain:
    """Requires Popen(stdout=PIPE, stderr=STDOUT, bufsize=0)."""
    drain = ProcessLogDrain(process.stdout, path, source=source)
    process.diagnostic_log = drain
    try:
        return drain.start()
    except BaseException:
        # A child with an undrained PIPE would eventually deadlock.
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
        process.stdout.close()
        raise


def finish_process_log(process, timeout: float = 1.0) -> bool:
    drain = getattr(process, "diagnostic_log", None)
    try:
        return drain.join(timeout) if drain is not None else True
    except Exception:
        return False


def report_startup_failure(exc: BaseException) -> None:
    """Works before configured logging; never formats values, locals or source."""
    identifier = lambda value: re.sub(r"[^a-zA-Z0-9_.<>]", "_", value)[:100]
    lines = [f"Backend startup failed: {identifier(type(exc).__name__)}"]
    frame = exc.__traceback__
    while frame is not None and len(lines) <= 12:
        code = frame.tb_frame.f_code
        lines.append(f"  {identifier(Path(code.co_filename).name)}:{frame.tb_lineno}:{identifier(code.co_name)}")
        frame = frame.tb_next
    safe = "\n".join(lines)
    try:
        if sys.stderr is not None:
            sys.stderr.write(safe + "\n")
            sys.stderr.flush()
    except Exception:
        pass
    try:
        from backend.app.shared.utils.settings import resolve_backend_root

        RotatingDiagnosticFile(resolve_backend_root() / "data" / "logs" / "startup_failure.log").write(safe)
    except Exception:
        pass
