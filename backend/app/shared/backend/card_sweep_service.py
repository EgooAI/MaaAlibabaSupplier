"""Activate client conversations so the client requests card details for MITM.

Product cards only show title, price and image when the passive ``fetchcard``
capture has seen that product. The client issues those requests while a
conversation is open, so unresolved product cards are re-visited through the
existing contact-search pipeline on a fixed interval or on demand.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import Event, RLock, Thread

from loguru import logger

from backend.app.api.envelope import AppError
from backend.app.shared.backend import gui_session, maafw_runner
from backend.app.shared.backend.account_context import AccountContext, get_account_context
from backend.app.shared.cards import PRODUCT_CARD_TYPES, parse_card, product_ids
from backend.app.shared.crm.identities import session_key_prefix
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.mitm.pool import ProductCardPool, get_product_card_pool
from backend.app.shared.utils.log_context import log_event

SWEEP_INTERVAL_S = 3600.0
SWEEP_DWELL_S = 5.0
SWEEP_MAX_TARGETS = 20
_BACKOFF_BASE_S = 3600.0
_BACKOFF_MAX_S = 86400.0


@dataclass(frozen=True)
class SweepTarget:
    sid: int
    contact_ali_id: str
    product_ids: tuple[str, ...]


@dataclass(frozen=True)
class SweepOutcome:
    target: SweepTarget
    navigated: bool
    resolved: tuple[str, ...]

    @property
    def enriched(self) -> bool:
        return bool(self.resolved)


def select_targets(adapter: CRMAdapter, self_ali_id: str, pool: ProductCardPool) -> list[SweepTarget]:
    """Conversations holding product cards whose product id is not in the pool."""
    prefix = session_key_prefix(self_ali_id)
    targets: list[SweepTarget] = []
    for meta in adapter.sessions.list_session_meta():
        if not meta.sid or not str(meta.key or "").startswith(prefix):
            continue
        conversation = adapter.get_conversation_detail(self_ali_id, meta.sid)
        if conversation is None:
            continue
        missing: list[str] = []
        for message in conversation.messages:
            card = parse_card(message.content)
            if card is None or card.card_type not in PRODUCT_CARD_TYPES:
                continue
            for product_id in product_ids(card):
                if product_id not in missing and pool.find_by_product_id(product_id) is None:
                    missing.append(product_id)
        if missing:
            targets.append(SweepTarget(conversation.sid, conversation.contact_ali_id, tuple(missing)))
    return targets


def run_targets(
    targets: Sequence[SweepTarget],
    *,
    navigate: Callable[[str], tuple[bool, str]],
    resolved: Callable[[str], bool],
    sleep: Callable[[float], None] = time.sleep,
    dwell: float = SWEEP_DWELL_S,
) -> list[SweepOutcome]:
    outcomes: list[SweepOutcome] = []
    for target in targets:
        ok, _ = navigate(target.contact_ali_id)
        if not ok:
            outcomes.append(SweepOutcome(target, False, ()))
            continue
        sleep(dwell)
        outcomes.append(SweepOutcome(
            target, True, tuple(pid for pid in target.product_ids if resolved(pid)),
        ))
    return outcomes


def outbox_busy(context: AccountContext, *, store=None) -> bool:
    if store is None:
        from backend.app.shared.backend.outbox_service import get_outbox_service

        store = get_outbox_service().store
    return bool(store.list_pending(seller=context.self_ali_id, data_dir=context.data_dir))


class CardSweepService:
    def __init__(self, *, interval: float = SWEEP_INTERVAL_S, clock: Callable[[], float] = time.time) -> None:
        self.interval = interval
        self._clock = clock
        self._stop = Event()
        self._wake = Event()
        self._lifecycle_lock = RLock()
        self._thread: Thread | None = None
        self._backoff: dict[str, tuple[int, float]] = {}
        self._observation_lock = RLock()
        self._observation = {
            "started_at": None, "heartbeat_at": None, "last_progress_at": None, "last_sweep_at": None,
            "phase": "not_started", "phase_started_at": None,
            "completed_iterations": 0, "last_error": None,
            "targets": 0, "visited": 0, "enriched": 0, "failed": 0,
        }

    def _observe(self, phase, **changes):
        with self._observation_lock:
            now = self._clock()
            if phase != self._observation["phase"]:
                self._observation.update(phase=phase, phase_started_at=now)
            self._observation.update(heartbeat_at=now, **changes)

    def observation(self):
        with self._observation_lock:
            state = dict(self._observation)
        now = self._clock()
        return {
            **state, "started": state["started_at"] is not None,
            "alive": self._thread is not None and self._thread.is_alive(),
            "stopping": self._stop.is_set(), "observed_at": now,
            "backoff_contacts": len(self._backoff),
            "phase_age_s": max(0, now - state["phase_started_at"]) if state["phase_started_at"] is not None else None,
            "progress_unit": "sweep_iterations",
        }

    def start(self) -> CardSweepService:
        with self._lifecycle_lock:
            if self._thread is not None and not self._stop.is_set():
                return self
            self._stop.clear()
            self._observe("starting", started_at=self._clock(), last_error=None)
            self._thread = Thread(target=self._run, name="card-sweep", daemon=True)
            self._thread.start()
        return self

    def trigger(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            self._wake.set()
        thread.join(timeout)
        with self._lifecycle_lock:
            self._thread = None
        self._observe("stopped")

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.wait(self.interval)
                self._wake.clear()
                if self._stop.is_set():
                    break
                try:
                    self._sweep_once()
                except Exception:
                    logger.exception("Card sweep iteration failed")
                    self._observe("waiting", last_error="sweep_failed")
                with self._observation_lock:
                    self._observation["completed_iterations"] += 1
        finally:
            self._observe("stopped")

    def _sweep_once(self) -> None:
        context = get_account_context()
        if not context.self_ali_id or not context.data_dir:
            log_event("card.sweep_skipped", reason="no_account")
            self._observe("waiting")
            return
        if outbox_busy(context):
            log_event("card.sweep_skipped", reason="outbox_busy")
            self._observe("waiting")
            return
        adapter = CRMAdapter()
        try:
            self._observe("selecting")
            unresolved = select_targets(adapter, context.self_ali_id, get_product_card_pool())
        finally:
            adapter.engine.dispose()
        now = self._clock()
        eligible = [target for target in unresolved if self._eligible(target.contact_ali_id, now)]
        targets = eligible[:SWEEP_MAX_TARGETS]
        log_event("card.sweep_selected", targets=len(unresolved),
                  backoff=len(unresolved) - len(eligible), eligible=len(eligible))
        if not targets:
            self._observe("waiting")
            return
        try:
            token = gui_session.capture_gui_session(context.epoch)
        except AppError as exc:
            log_event("card.sweep_skipped", reason="gui_unavailable")
            self._observe("waiting", last_error=str(exc), targets=len(targets), visited=0, enriched=0, failed=0)
            return
        self._observe("sweeping", last_error=None, targets=len(targets), visited=0, enriched=0, failed=0)

        def navigate(contact_ali_id: str) -> tuple[bool, str]:
            result = gui_session.run_guarded(token, lambda: maafw_runner.goto_contact(contact_ali_id))
            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
                return result
            return False, "GUI guard returned no result"

        outcomes = run_targets(targets, navigate=navigate, resolved=self._resolved, sleep=self._sleep)
        for outcome in outcomes:
            self._record(outcome)
            log_event(
                "card.sweep_target", sid=outcome.target.sid, key_kind="ali_id",
                navigated=outcome.navigated,
                unresolved=len(outcome.target.product_ids), resolved=len(outcome.resolved),
            )
        enriched = sum(1 for outcome in outcomes if outcome.enriched)
        failed = sum(1 for outcome in outcomes if outcome.navigated and not outcome.enriched)
        self._observe("waiting", last_progress_at=self._clock(), last_sweep_at=self._clock(), last_error=None,
                      targets=len(targets), visited=len(outcomes), enriched=enriched, failed=failed)
        log_event("card.sweep_done", targets=len(targets), visited=len(outcomes),
                  enriched=enriched, failed=failed)

    @staticmethod
    def _resolved(product_id: str) -> bool:
        return get_product_card_pool().find_by_product_id(product_id) is not None

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _eligible(self, contact_ali_id: str, now: float) -> bool:
        return self._backoff.get(contact_ali_id, (0, 0.0))[1] <= now

    def _record(self, outcome: SweepOutcome) -> None:
        contact = outcome.target.contact_ali_id
        if outcome.enriched:
            self._backoff.pop(contact, None)
            return
        attempts = self._backoff.get(contact, (0, 0.0))[0] + 1
        delay = min(_BACKOFF_BASE_S * (2 ** (attempts - 1)), _BACKOFF_MAX_S)
        self._backoff[contact] = (attempts, self._clock() + delay)


_service: CardSweepService | None = None
_service_lock = RLock()


def observe_card_sweep():
    service = _service
    return service.observation() if service is not None else None


def get_card_sweep_service() -> CardSweepService:
    global _service
    with _service_lock:
        if _service is None:
            _service = CardSweepService()
        return _service


def start_card_sweep_service() -> CardSweepService:
    return get_card_sweep_service().start()


def stop_card_sweep_service(timeout: float = 5.0) -> None:
    global _service
    with _service_lock:
        if _service is not None:
            _service.stop(timeout)
            _service = None
