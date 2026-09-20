"""Retain raw Maa logs locally; export only source-sanitized closed sessions.

The runner must serialize every call to prepare_session with native operations,
including controller/resource jobs and the full submit_send/wait_send interval.
No native library is imported here. A successful set_log_dir callback is the only
evidence that the previous writer has released its session. A process-held lease
also allows retention to reclaim sessions after their owner exits. Missing or
unverifiable leases are never evidence of closure.
"""

from __future__ import annotations

import os
import re
import stat
import threading
import time
from pathlib import Path
from typing import Callable
from uuid import uuid4

from backend.app.shared.utils.process_logs import (
    LOG_MAX_AGE_S, LOG_MAX_BYTES, bounded_lines, check_plain_path, is_reparse,
)

NATIVE_MAX_BYTES = 256 * 1024 * 1024
NATIVE_MAX_SESSIONS = 128
_OWNER = b"maa-native-session-v1\n"
_SESSION = re.compile(r"session-[0-9a-f]{32}\Z")


def _try_lock_lease(descriptor: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


class NativeLogSessions:
    def __init__(
        self, root: Path | None = None, *, max_bytes: int = NATIVE_MAX_BYTES,
        max_age_s: float = LOG_MAX_AGE_S,
    ) -> None:
        from backend.app.shared.utils.settings import resolve_backend_root

        self.root = root if root is not None else resolve_backend_root() / "data" / "logs" / "native"
        self.max_bytes = max_bytes
        self.max_age_s = max_age_s
        self.active: Path | None = None
        self._opened_at = 0.0
        self._switch_uncertain = False
        # Raw descriptors deliberately outlive Python object collection. Only a
        # confirmed writer switch/native shutdown or OS process exit releases them.
        self._leases: dict[Path, int] = {}
        self._candidate: Path | None = None
        self._lock = threading.RLock()

    def _close_session(self, session: Path) -> None:
        descriptor = self._leases.pop(session)
        try:
            check_plain_path(session / ".closed")
            (session / ".closed").touch(exist_ok=True)
        finally:
            os.close(descriptor)

    def release_after_shutdown(self, *, native_stopped: bool) -> None:
        """Only after native objects/writers are destroyed; never at operation end."""
        if not native_stopped:
            raise RuntimeError("Native writer shutdown must be confirmed")
        with self._lock:
            try:
                for session in list(self._leases):
                    self._close_session(session)
            finally:
                self.active = None
                self._candidate = None

    def _recover_exited_session(self, session: Path, last_write: float) -> bool:
        if session in self._leases:
            return False
        lease = session / ".lease"
        check_plain_path(lease)
        try:
            descriptor = os.open(lease, os.O_RDWR | getattr(os, "O_BINARY", 0))
        except FileNotFoundError:
            # Interrupted deletion can remove the lease after closing the writer.
            # A missing lease alone never authorizes removing an unclosed tree.
            closed = session / ".closed"
            check_plain_path(closed)
            return closed.is_file()
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 1:
                return False
            if not _try_lock_lease(descriptor):
                return False
            # No live owner can release its lease before switching away. Session
            # names are never reused, so PID reuse cannot authorize this cleanup.
            check_plain_path(session / ".closed")
            closed = session / ".closed"
            if not closed.exists():
                closed.touch(exist_ok=False)
                os.utime(closed, (last_write, last_write))
            return True
        finally:
            os.close(descriptor)

    def _tree(self, session: Path) -> tuple[list[Path], list[Path]]:
        check_plain_path(session)
        files, directories = [], []
        pending = [session]
        while pending:
            directory = pending.pop()
            check_plain_path(directory)
            directories.append(directory)
            for child in directory.iterdir():
                if is_reparse(child):
                    raise ValueError("Native session contains a reparse point")
                if child.is_dir():
                    pending.append(child)
                elif child.is_file():
                    files.append(child)
                else:
                    raise ValueError("Native session contains a special file")
        return files, directories

    def _owned(self, session: Path) -> bool:
        if not _SESSION.fullmatch(session.name):
            return False
        check_plain_path(session / ".owner")
        try:
            with (session / ".owner").open("rb") as marker:
                return marker.read(len(_OWNER) + 1) == _OWNER
        except FileNotFoundError:
            return False

    def _inventory(self, *, recover_exited: bool = False) -> tuple[list[dict], bool]:
        check_plain_path(self.root)
        if not self.root.exists():
            return [], False
        sessions = []
        skipped = False
        for session in self.root.iterdir():
            try:
                if not self._owned(session):
                    continue
                files, _ = self._tree(session)
                lease_free = session not in self._leases
                if recover_exited:
                    try:
                        lease_free = self._recover_exited_session(
                            session, max((path.stat().st_mtime for path in files), default=time.time()),
                        )
                    except OSError:
                        lease_free = False
                closed = session / ".closed"
                is_closed = lease_free and closed.is_file()
                sessions.append({
                    "id": session.name,
                    "bytes": sum(path.stat().st_size for path in files),
                    "closed": is_closed,
                    "closed_at": closed.stat().st_mtime if is_closed else None,
                })
            except (OSError, ValueError):
                skipped = True
        return sessions, skipped

    def prepare_session(
        self, set_log_dir: Callable[[Path], bool], *, quiescent: bool,
        force: bool = False,
    ) -> Path:
        """Call under the runner lock before native work, never from a timer.

        set_log_dir must return True only after native writers switched. Call
        after Toolkit.init_option with force=True (it can overwrite the global
        log option), before creating native controllers/resources. Later calls rotate at a
        quiescent boundary after 10 MiB or one day. Failed switches preserve both
        directories and raise. Callers must expose the policy failure and cannot
        assume which directory native logging targets after an uncertain switch.
        """
        if not quiescent:
            raise RuntimeError("Native log switch requires completed native operations")
        with self._lock:
            if self.active is not None and not force and not self._switch_uncertain:
                files, _ = self._tree(self.active)
                size = sum(path.stat().st_size for path in files)
                if size < LOG_MAX_BYTES and time.time() - self._opened_at < 86400:
                    self.prune()
                    return self.active
            check_plain_path(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            session = self._candidate
            if session is None:
                session = self.root / f"session-{uuid4().hex}"
                session.mkdir()
                descriptor = os.open(session / ".lease", os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
                try:
                    os.write(descriptor, b"1")
                    if not _try_lock_lease(descriptor):
                        raise OSError("Native session lease unavailable")
                    (session / ".owner").write_bytes(_OWNER)
                except BaseException:
                    os.close(descriptor)
                    raise
                self._leases[session] = descriptor
                self._candidate = session
            self._switch_uncertain = True
            if not set_log_dir(session):
                raise RuntimeError("Native log directory switch failed")
            self.active = session
            self._candidate = None
            self._opened_at = time.time()
            self._switch_uncertain = False
            for previous in list(self._leases):
                if previous != session:
                    self._close_session(previous)
            self.prune()
            return session

    def prune(self) -> dict:
        """Delete only owned, explicitly closed trees; never traverse reparses."""
        with self._lock:
            sessions, skipped = self._inventory(recover_exited=True)
            total = sum(item["bytes"] for item in sessions)
            count = len(sessions)
            cutoff = time.time() - self.max_age_s
            deleted = []
            for item in sorted(
                (item for item in sessions if item["closed"]), key=lambda item: item["closed_at"],
            ):
                if total <= self.max_bytes and count <= NATIVE_MAX_SESSIONS and item["closed_at"] >= cutoff:
                    continue
                session = self.root / item["id"]
                try:
                    if not self._owned(session) or session in self._leases:
                        continue
                    files, directories = self._tree(session)
                    # Preserve ownership/closure evidence until all payloads are gone.
                    files.sort(key=lambda path: path.name in {".owner", ".closed", ".lease"})
                    for path in files:
                        check_plain_path(path)
                        path.unlink()
                    for path in reversed(directories):
                        check_plain_path(path)
                        path.rmdir()
                    total -= item["bytes"]
                    count -= 1
                    deleted.append(item["id"])
                except (OSError, ValueError):
                    skipped = True
            return {"deleted": deleted, "bytes": total, "skipped_unsafe_or_unreadable": skipped}

    def status(self) -> dict:
        with self._lock:
            sessions, skipped = self._inventory()
            total = sum(item["bytes"] for item in sessions)
            warnings = []
            if self._switch_uncertain:
                warnings.append("native_log_switch_uncertain")
            if self.active is not None:
                warnings.append("native_active_writer_unbounded")
            if any(not item["closed"] and item["id"] != (self.active.name if self.active else None) for item in sessions):
                warnings.append("native_unclosed_sessions_retained")
            if total > self.max_bytes:
                warnings.append("native_retention_budget_exceeded")
            if len(sessions) > NATIVE_MAX_SESSIONS:
                warnings.append("native_session_count_exceeded")
            if skipped:
                warnings.append("native_unsafe_or_unreadable_sessions_skipped")
            return {
                "active_session": self.active.name if self.active else None,
                "bytes": total, "max_bytes": self.max_bytes,
                "max_age_days": self.max_age_s / 86400,
                "warnings": warnings, "sessions": sessions,
            }

    def export_sanitized(
        self, session_id: str, *, max_bytes: int = 1024 * 1024,
        max_read_bytes: int = 16 * 1024 * 1024,
    ) -> str:
        """Closed-session text only. Never return raw files, images or paths.

        The API must authenticate/authorize this controlled export. Output and
        input scan budgets apply across all files; oversized/partial lines and
        records rejected by the shared native sanitizer are omitted.
        """
        if not _SESSION.fullmatch(session_id):
            raise ValueError("Invalid native session identifier")
        if max_bytes < 1 or max_read_bytes < 1:
            raise ValueError("Invalid native export budget")
        max_bytes = min(max_bytes, 1024 * 1024)
        max_read_bytes = min(max_read_bytes, 16 * 1024 * 1024)
        with self._lock:
            session = self.root / session_id
            if session == self.active or not self._owned(session):
                raise ValueError("Only owned closed native sessions can be exported")
            files, _ = self._tree(session)
            if session / ".closed" not in files:
                raise ValueError("Only closed native sessions can be exported")
            from backend.app.shared.utils.logging import sanitize_diagnostic_record

            result = bytearray()
            remaining = max_read_bytes
            for path in sorted(files):
                if path.suffix.lower() != ".log" or remaining <= 0:
                    continue
                check_plain_path(path)
                with path.open("rb") as stream:
                    for line in bounded_lines(stream, max_read_bytes=remaining):
                        if line is None:
                            continue
                        safe = sanitize_diagnostic_record(line, source="native")
                        if safe is None:
                            continue
                        record = (safe.rstrip("\r\n") + "\n").encode("utf-8")
                        if len(result) + len(record) > max_bytes:
                            return result.decode("utf-8")
                        result.extend(record)
                    remaining -= stream.tell()
            return result.decode("utf-8")
