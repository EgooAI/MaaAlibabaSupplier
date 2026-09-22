"""Durable GUI attempts with explicit recipient confirmation and local evidence.

An observed message is evidence in the local source, never a delivery receipt.
GUI tokens live only in this process; recovery never replays an attempt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Condition, Event, RLock, Thread
from uuid import UUID, uuid4
from types import MappingProxyType
from collections.abc import Mapping

from loguru import logger

from backend.app.api.envelope import AppError
from backend.app.shared.backend import gui_session, maafw_runner as runner
from backend.app.shared.backend.account_context import AccountContext, get_account_context
from backend.app.shared.backend.gui_evidence import ClientFrame, compare_frame
from backend.app.shared.crm.outbox_store import OutboxConflict, OutboxStore
from backend.app.shared.crm.outbox_state import (
    ACTIVE_COMPENSATABLE_STATUSES,
    NON_EXECUTING_STATUSES,
    is_compensatable,
    is_gui_risk,
)
from backend.app.shared.utils.settings import resolve_backend_root
from backend.app.task_queue import get_task_queue
from backend.app.shared.utils.log_context import bind_log_context, capture_log_context, failure_report_due, log_event


SCREENSHOT_TTL = 120.0
RECONCILE_INTERVAL = 2.0
_CLEAR_SCREENSHOT = {"screenshot_id": None, "screenshot_at": None, "screenshot_digest": None}


@dataclass(frozen=True)
class _Attempt:
    context: AccountContext
    token: gui_session.GuiSessionToken
    attempt: int
    log_context: Mapping


class ScreenshotExpiredError(AppError):
    """The stored confirmation screenshot no longer matches the task."""


class OutboxService:
    def __init__(self, store: OutboxStore | None = None, queue=None) -> None:
        self.store = store if store is not None else OutboxStore()
        self._queue = queue
        self._condition = Condition(RLock())
        self._attempts: dict[str, _Attempt] = {}
        self._active: dict[str, dict] = {}
        self._pending_terminal_writes: dict[str, dict] = {}
        self._stopping = Event()
        self._started = False
        self._thread: Thread | None = None
        self._reconcile_lock = RLock()
        self._screenshot_dir = resolve_backend_root() / "data" / "outbox"
        self._observation_lock = RLock()
        self._observation = {
            "started_at": None, "heartbeat_at": None, "last_progress_at": None,
            "phase": "not_started", "phase_started_at": None,
            "completed_iterations": 0, "last_error": None,
            "pending": None, "pending_observed_at": None, "context": None,
        }

    def _observe_verifier(self, phase, **changes):
        with self._observation_lock:
            now = time.time()
            if phase != self._observation["phase"]:
                self._observation.update(phase=phase, phase_started_at=now)
            self._observation.update(heartbeat_at=now, **changes)

    def verifier_observation(self):
        # Independent of GUI/persistence locks, including during stop().
        with self._observation_lock:
            state = dict(self._observation)
        now = time.time()
        return {
            **state, "started": state["started_at"] is not None,
            "alive": self._thread is not None and self._thread.is_alive(),
            "stopping": self._stopping.is_set(), "observed_at": now,
            "phase_age_s": max(0, now - state["phase_started_at"]) if state["phase_started_at"] is not None else None,
            "progress_unit": "verification_iterations",
        }

    @staticmethod
    def _scope(context: AccountContext) -> dict:
        return {"seller": context.self_ali_id, "data_dir": context.data_dir}

    def start(self) -> OutboxService:
        with self._condition:
            if self._stopping.is_set():
                raise AppError("Outbox service is stopping", status_code=503)
            if not self._started:
                self.store.initialize()
                self.store.recover()
                self._started = True
                self._observe_verifier("waiting", started_at=time.time())
                self._thread = Thread(target=self._verify_loop, name="outbox-verifier", daemon=True)
                self._thread.start()
        return self

    def _accepting(self) -> None:
        if self._stopping.is_set():
            raise AppError("Outbox service is stopping", status_code=503)

    def get(self, context: AccountContext, task_id: str) -> dict | None:
        return self.store.get(task_id, **self._scope(context))

    def list(self, context: AccountContext, conversation_id: int, *, limit: int = 100) -> list[dict]:
        return self.store.list(conversation_id, **self._scope(context), limit=limit)

    def _require(self, context: AccountContext, task_id: str) -> dict:
        record = self.get(context, task_id)
        if record is None:
            raise AppError("Outbox task not found", status_code=404)
        return record

    @staticmethod
    def _capture_token(context: AccountContext) -> gui_session.GuiSessionToken:
        token = gui_session.capture_gui_session(context.epoch)
        if (token.self_ali_id, token.data_dir, token.epoch) != (
            context.self_ali_id, context.data_dir, context.epoch,
        ):
            raise OutboxConflict("Account context changed")
        return token

    def submit(
        self, context: AccountContext, conversation_id: int, contact_ali_id: str,
        login_id: str, content: str, action: str, idempotency_key: str,
    ) -> dict:
        self.start()
        with self._condition:
            self._accepting()
            record, created = self.store.create(
                **self._scope(context), conversation_id=conversation_id, contact_ali_id=contact_ali_id,
                login_id=login_id, content=content, action=action, idempotency_key=idempotency_key,
                origin_request_id=capture_log_context().get("request_id"), origin_account_epoch=context.epoch,
            )
        if not created:
            return record
        # Persist first, including failures to acquire the initial GUI authorization.
        try:
            token = self._capture_token(context)
            with self._condition:
                self._accepting()
                binding = self._bind_attempt(context, token, record)
                self._attempts[record["id"]] = binding
                self._enqueue(record, binding, send=False)
        except Exception as exc:
            self._fail_if_current(context, record, str(exc))
        return self._require(context, record["id"])

    def confirm(
        self, context: AccountContext, task_id: str, *, screenshot_id: str,
        version: int, expected_epoch: str | None = None,
    ) -> dict:
        self._accepting()
        if expected_epoch is not None and expected_epoch != context.epoch:
            raise OutboxConflict("Account context changed")
        # Never wait for the GUI/account lock while holding the service lock.
        token = self._capture_token(context)
        with self._condition:
            self._accepting()
            record = self._require(context, task_id)
            binding = self._attempts.get(task_id)
            if (binding is None or binding.context != context or binding.token != token
                    or binding.attempt != record["attempt"]):
                raise OutboxConflict("Original GUI session expired; explicit retry is required")
            if (record["status"] != "awaiting_confirmation" or record["version"] != version
                    or record["screenshot_id"] != screenshot_id):
                raise OutboxConflict("Screenshot or task version changed")
            record = self._move(record, "queued_send")
            self._enqueue(record, binding, send=True)
            return self._require(context, task_id)

    def cancel(self, context: AccountContext, task_id: str, *, version: int | None = None) -> dict:
        with self._condition:
            self._accepting()
            record = self.store.cancel(task_id, version, **self._scope(context))
            self._attempts.pop(task_id, None)
            return record

    def retry(self, context: AccountContext, task_id: str, *, version: int | None = None) -> dict:
        self.start()
        token = self._capture_token(context)
        with self._condition:
            self._accepting()
            record = self.store.retry(task_id, version, **self._scope(context))
            binding = self._bind_attempt(context, token, record)
            self._attempts[task_id] = binding
            self._enqueue(record, binding, send=False)
            return self._require(context, task_id)

    def get_screenshot(self, context: AccountContext, task_id: str, screenshot_id: str) -> bytes:
        try:
            if UUID(screenshot_id).hex != screenshot_id:
                raise ValueError("Noncanonical screenshot ID")
        except (ValueError, TypeError, AttributeError):
            raise AppError("Screenshot not found", status_code=404) from None
        with self._condition:
            record = self._require(context, task_id)
            if (record["screenshot_id"] != screenshot_id or self._expired(record)
                    or record["status"] not in ("awaiting_confirmation", "queued_send")):
                raise ScreenshotExpiredError("Screenshot expired or replaced", status_code=404)
            try:
                return (self._screenshot_dir / f"{screenshot_id}.png").read_bytes()
            except OSError:
                raise AppError("Screenshot not found", status_code=404) from None

    @staticmethod
    def _expired(record: dict) -> bool:
        stamp = record["screenshot_at"]
        return stamp is None or not 0 <= time.time() - stamp < SCREENSHOT_TTL

    @staticmethod
    def _bind_attempt(context, token, record) -> _Attempt:
        fields = {
            **capture_log_context(), "outbox_id": record["id"], "attempt": record["attempt"],
            "account_epoch": context.epoch, "origin_request_id": record.get("origin_request_id"),
            "origin_account_epoch": record.get("origin_account_epoch"),
        }
        binding = _Attempt(context, token, record["attempt"], MappingProxyType(fields))
        with bind_log_context(**fields):
            log_event("outbox.attempt_created")
        return binding

    def _move(self, record: dict, status: str, **changes) -> dict:
        current = self.store.transition(
            record["id"], record["status"], record["version"], seller=record["seller"],
            data_dir=record["data_dir"], status=status, **changes,
        )
        if record["id"] in self._active:
            self._active[record["id"]] = current
        binding = self._attempts.get(record["id"])
        fields = dict(binding.log_context) if binding is not None else {
            "request_id": record.get("origin_request_id"),
            "account_epoch": record.get("origin_account_epoch"),
        }
        with bind_log_context(**fields):
            log_event("outbox.transition", outbox_id=record["id"], attempt=record["attempt"],
                      origin_request_id=record.get("origin_request_id"),
                      origin_account_epoch=record.get("origin_account_epoch"),
                      status=status, version=current["version"])
        return current

    def _update(self, record: dict, **changes) -> dict:
        current = self.store.update_fields(
            record["id"], record["status"], record["version"], seller=record["seller"],
            data_dir=record["data_dir"], **changes,
        )
        if record["id"] in self._active:
            self._active[record["id"]] = current
        return current

    def _fail_if_current(self, context: AccountContext, record: dict, reason: str) -> None:
        with self._condition:
            if record["id"] in self._active:
                return
            # Only finished callbacks (or jobs that never entered GUI) can be retried
            # here. Retain no payload or GUI authorization in this compensation map.
            pending = self._pending_terminal_writes.get(record["id"])
            if pending is None or (record["attempt"], record["version"]) >= (pending["attempt"], pending["version"]):
                self._pending_terminal_writes[record["id"]] = {
                    name: record[name] for name in ("id", "seller", "data_dir", "attempt", "version", "status")
                } | {"reason": reason}
            binding = self._attempts.get(record["id"])
            if binding is not None and binding.attempt == record["attempt"]:
                self._attempts.pop(record["id"], None)
            self._flush_terminal_writes()

    def _flush_terminal_writes(self) -> None:
        with self._condition:
            for task_id, pending in tuple(self._pending_terminal_writes.items()):
                if task_id in self._active:
                    continue
                try:
                    current = self.store.get(task_id, seller=pending["seller"], data_dir=pending["data_dir"])
                    if (current is not None and current["attempt"] == pending["attempt"]
                            and current["version"] == pending["version"]
                            and is_compensatable(current["status"])):
                        risky = is_gui_risk(current["status"])
                        self._move(
                            current, "unknown" if risky else "failed", reason=pending["reason"],
                            evidence={"status": pending["status"], "detail": pending["reason"]}, **_CLEAR_SCREENSHOT,
                        )
                except OutboxConflict:
                    # A newer version/attempt or a durable outcome supersedes this write.
                    pass
                except Exception:
                    logger.exception("Outbox terminal write deferred for {}", task_id)
                    continue
                self._pending_terminal_writes.pop(task_id, None)

    def _enqueue(self, record: dict, binding: _Attempt, *, send: bool) -> None:
        try:
            if self._queue is None:
                self._queue = get_task_queue()
            with bind_log_context(**binding.log_context):
                self._queue.enqueue(
                    lambda: self._execute(record, binding, send=send),
                    description=f"Outbox {'input/send' if send else 'navigation'} {record['id']}",
                )
        except Exception as exc:
            self._fail_if_current(binding.context, record, str(exc))

    def _save_frame(self, record: dict, frame: ClientFrame, *, reason: str | None = None) -> dict:
        screenshot_id = uuid4().hex
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        (self._screenshot_dir / f"{screenshot_id}.png").write_bytes(frame.png_bytes)
        return self._move(
            record, "awaiting_confirmation", screenshot_id=screenshot_id,
            screenshot_at=time.time(), screenshot_digest=frame.sha256, reason=reason,
        )

    def _execute(self, queued: dict, binding: _Attempt, *, send: bool) -> tuple[bool, str]:
        with bind_log_context(**binding.log_context):
            return self._execute_attempt(queued, binding, send=send)

    def _execute_attempt(self, queued: dict, binding: _Attempt, *, send: bool) -> tuple[bool, str]:
        context, task_id = binding.context, queued["id"]
        with self._condition:
            if self._stopping.is_set():
                self._fail_if_current(context, queued, "shutdown_before_execution")
                return False, "Outbox service is stopping"
            current = self.get(context, task_id)
            if (current is None or current["version"] != queued["version"]
                    or self._attempts.get(task_id) != binding):
                return False, "Outbox task superseded"
            self._active[task_id] = current
        failure = "gui_execution_interrupted"
        try:
            baseline = None
            if send and queued["action"] == "send":
                from backend.app.shared.backend.source_messages import read_source_messages

                snapshot = read_source_messages(context, queued["contact_ali_id"])
                if (not snapshot.get("valid") or not snapshot.get("origin")
                        or not isinstance(snapshot.get("messages"), list)):
                    raise RuntimeError("Source baseline unavailable; no text was entered")
                baseline = {
                    "origin": snapshot["origin"], "ids": sorted(message["id"] for message in snapshot["messages"]),
                    "checked_at": snapshot["checked_at"], "send_started_at": None,
                }

            def act() -> tuple[bool, str]:
                with self._condition:
                    self._accepting()
                    record = self._require(context, task_id)
                    if record["version"] != queued["version"]:
                        return False, "Outbox task superseded"
                    if not send:
                        record = self._move(record, "navigating")
                if not send:
                    ok, reason = runner.goto_contact(record["login_id"])
                    if not ok:
                        raise RuntimeError(reason)
                    frame = runner.capture_client_frame()
                    with self._condition:
                        self._accepting()
                        self._save_frame(record, frame)
                    return True, "Recipient screenshot requires confirmation"

                # Baseline IO precedes this final frame. No GUI input may intervene.
                frame = runner.capture_client_frame()
                with self._condition:
                    self._accepting()
                    if self._expired(record) or compare_frame(record["screenshot_digest"], frame).needs_reconfirm:
                        self._save_frame(record, frame, reason="screenshot_expired_or_changed")
                        return True, "Fresh screenshot requires confirmation"
                    if baseline is not None:
                        baseline["send_started_at"] = time.time()
                        baseline["sent_at"] = baseline["send_started_at"]
                    record = self._move(record, "running", baseline=baseline)
                ok, reason = runner.chat_input(record["content"])
                if not ok:
                    raise RuntimeError(reason)
                with self._condition:
                    self._accepting()
                    if record["action"] == "test":
                        self._move(record, "filled", **_CLEAR_SCREENSHOT)
                        return True, "Text filled; no send click requested"
                    # Durable uncertainty barrier: a crash after this point cannot replay.
                    record = self._update(record, may_have_sent=True)
                    self._accepting()
                    # stop() must acquire this same lock before it can return. Only
                    # native submission belongs here; never hold it during job.wait().
                    send_job = runner.submit_send()
                ok, reason = runner.wait_send(send_job)
                if not ok:
                    raise RuntimeError(reason)
                with self._condition:
                    self._move(record, "verifying", **_CLEAR_SCREENSHOT)
                return True, "GUI send completed; awaiting local source evidence"

            result = gui_session.run_guarded(binding.token, act)
            if not isinstance(result, tuple) or not result[0]:
                failure = str(result[1]) if isinstance(result, tuple) else "GUI guard returned no result"
                return False, failure
            failure = None
            return result
        except Exception as exc:
            failure = str(exc)
            return False, failure
        finally:
            with self._condition:
                current = self._active.pop(task_id)
                try:
                    if failure is not None:
                        self._fail_if_current(context, current, failure)
                    elif current["status"] not in ("queued", "awaiting_confirmation", "queued_send"):
                        self._attempts.pop(task_id, None)
                finally:
                    self._condition.notify_all()

    def reconcile_once(self) -> list[dict]:
        """Read only the selected source; matching never acquires a GUI lock."""
        if self._stopping.is_set():
            return []
        with self._reconcile_lock:
            self._flush_terminal_writes()
            context = get_account_context()
            self._observe_verifier("scanning", context=context.to_dict(), pending=None, pending_observed_at=None)
            if not context.self_ali_id or not context.data_dir:
                return []
            pending = [record for record in self.store.list_pending(
                ("verifying", "unknown"), **self._scope(context),
            ) if record["action"] == "send" and record["may_have_sent"]]
            self._observe_verifier("reading_source" if pending else "scanning", pending=len(pending), pending_observed_at=time.time())
            if not pending:
                return []
            from backend.app.shared.backend.source_messages import read_source_messages
            from backend.app.shared.backend.send_verification import match_outbox

            observed = []
            for contact in {record["contact_ali_id"] for record in pending}:
                snapshot = read_source_messages(context, contact)
                with self._condition:
                    if self._stopping.is_set() or get_account_context() != context:
                        return observed
                    # Source reads can be slow: include sends admitted while reading,
                    # missing baselines, and already claimed evidence in ambiguity checks.
                    others = self.store.list_pending(
                        ("running", "verifying", "unknown", "observed", "failed"), **self._scope(context),
                    )
                    for record in others:
                        if (record["contact_ali_id"] != contact or record["status"] not in ("verifying", "unknown")
                                or record["action"] != "send" or not record["may_have_sent"]):
                            continue
                        proof = match_outbox(record, snapshot, others)
                        if proof["status"] == record["status"]:
                            continue
                        try:
                            if proof["status"] == "observed":
                                observed.append(self._move(
                                    record, "observed", matched_message_id=proof["matched_message_id"],
                                    evidence=proof["evidence"], reason=proof["reason"],
                                ))
                            elif proof["status"] == "unknown" and record["status"] == "verifying":
                                self._move(record, "unknown", evidence=proof["evidence"], reason=proof["reason"])
                        except OutboxConflict:
                            # Another reconciliation tick/task already claimed this evidence.
                            continue
                self._observe_verifier("reading_source", last_progress_at=time.time())
            return observed

    def _verify_loop(self) -> None:
        failures = 0
        try:
            while not self._stopping.wait(RECONCILE_INTERVAL):
                self._observe_verifier("reconciling")
                try:
                    self.reconcile_once()
                except Exception as exc:
                    failures += 1
                    self._observe_verifier("waiting", last_error=type(exc).__name__)
                    # A persistent local-source failure must not log every 2 seconds.
                    if failure_report_due(failures):
                        logger.exception("Outbox local-source reconciliation failed")
                else:
                    if failures:
                        log_event("outbox.reconcile_recovered", count=failures)
                    failures = 0
                    with self._observation_lock:
                        self._observation["completed_iterations"] += 1
                    self._observe_verifier("waiting", last_progress_at=time.time(), last_error=None)
        finally:
            self._observe_verifier("stopped")

    def stop(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        self._stopping.set()
        try:
            with self._condition:
                for task_id, binding in tuple(self._attempts.items()):
                    if task_id in self._active:
                        continue
                    try:
                        record = self.get(binding.context, task_id)
                        if record and record["status"] in NON_EXECUTING_STATUSES:
                            self._fail_if_current(binding.context, record, "shutdown_before_execution")
                    except Exception:
                        logger.exception("Outbox shutdown persistence failed for {}; startup will recover it", task_id)
                while self._active and time.monotonic() < deadline:
                    self._condition.wait(max(0, deadline - time.monotonic()))
                for record in tuple(self._active.values()):
                    try:
                        if record["status"] in ACTIVE_COMPENSATABLE_STATUSES:
                            risky = is_gui_risk(record["status"])
                            self._move(record, "unknown" if risky else "failed",
                                       reason="shutdown_execution_uncertain", **_CLEAR_SCREENSHOT)
                    except OutboxConflict:
                        pass
                    except Exception:
                        # Still executing: keep its snapshot until the callback exits.
                        logger.exception("Outbox shutdown write failed for {}", record["id"])
                self._flush_terminal_writes()
        finally:
            with self._condition:
                self._attempts.clear()
                self._condition.notify_all()
            if self._thread is not None:
                self._thread.join(max(0, deadline - time.monotonic()))
                if self._thread.is_alive():
                    logger.warning("Outbox verifier has not stopped within {} seconds", timeout)
            if self._active:
                logger.warning("Outbox GUI attempt still running after shutdown timeout")


_service: OutboxService | None = None
_service_lock = RLock()


def observe_outbox_verifier():
    service = _service
    return service.verifier_observation() if service is not None else None


def get_outbox_service() -> OutboxService:
    global _service
    with _service_lock:
        if _service is None:
            _service = OutboxService()
        return _service


def start_outbox_service() -> OutboxService:
    return get_outbox_service().start()


def stop_outbox_service(timeout: float = 5.0) -> None:
    global _service
    with _service_lock:
        if _service is not None:
            _service.stop(timeout)
            _service = None
