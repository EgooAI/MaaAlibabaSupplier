import json

import pytest

from backend.app.api.envelope import AppError
from backend.app.shared.backend import card_sweep_service as service_module
from backend.app.shared.backend.account_context import AccountContext
from backend.app.shared.backend.card_sweep_service import (
    SWEEP_INTERVAL_S,
    CardSweepService,
    SweepOutcome,
    SweepTarget,
    outbox_busy,
    run_targets,
    select_targets,
)
from backend.app.shared.backend.im_chat_db import ContactConv, MessageRow
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.mitm.pool import ProductCard, SelfInfo, get_product_card_pool

SELF = "10001"
CREATED = 1756720000


def _row(mid: str, sender: str, content: bytes, *, contact: str) -> MessageRow:
    return MessageRow(
        table_name="msg_table", cid=f"{SELF}-{contact}", mid=mid, sender_id=sender,
        created_at=CREATED, user_content_type=10010, content_label="", content=content,
    )


def _conv(contact: str, messages: list[MessageRow]) -> ContactConv:
    return ContactConv(
        contact_ali_id=contact, messages=messages,
        last_created_at=CREATED, last_content_label="",
    )


def _product_card(product_id: str) -> bytes:
    return json.dumps({"cardType": 3, "params": {"id": product_id}}).encode()


def _file_card() -> bytes:
    return json.dumps({"cardType": 12, "params": {"id": "9", "name": "catalog.pdf"}}).encode()


def _adapter(tmp_path) -> CRMAdapter:
    adapter = CRMAdapter(database_path=tmp_path / "crm.sqlite")
    adapter.sync_conversations([
        _conv("20002", [
            _row("m1", "20002@icbu", _product_card("111"), contact="20002"),
            _row("m2", "20002@icbu", _file_card(), contact="20002"),
        ]),
        _conv("30003", [_row("m3", "30003@icbu", _product_card("222"), contact="30003")]),
    ], SelfInfo(ali_id=SELF, login_id="seller"))
    return adapter


def test_select_targets_only_unresolved_product_cards(tmp_path):
    adapter = _adapter(tmp_path)
    try:
        pool = get_product_card_pool()
        pool.put(ProductCard(card_id="222", product_id="222"))
        targets = select_targets(adapter, SELF, pool)
        assert [(target.contact_ali_id, target.product_ids) for target in targets] == [("20002", ("111",))]
    finally:
        adapter.engine.dispose()


def test_select_targets_ignores_other_sellers(tmp_path):
    adapter = _adapter(tmp_path)
    try:
        assert select_targets(adapter, "99999", get_product_card_pool()) == []
    finally:
        adapter.engine.dispose()


def test_run_targets_records_navigation_and_resolution():
    targets = [SweepTarget(1, "20002", ("111", "222")), SweepTarget(2, "30003", ("333",))]
    calls: list[str] = []
    sleeps: list[float] = []
    fetched = {"111"}

    def navigate(contact: str) -> tuple[bool, str]:
        calls.append(contact)
        return (False, "search failed") if contact == "30003" else (True, "")

    outcomes = run_targets(
        targets, navigate=navigate, resolved=lambda pid: pid in fetched,
        sleep=sleeps.append, dwell=4.0,
    )

    assert calls == ["20002", "30003"]
    assert sleeps == [4.0]
    assert [(item.target.sid, item.navigated, item.resolved, item.enriched) for item in outcomes] == [
        (1, True, ("111",), True),
        (2, False, (), False),
    ]


def test_outbox_busy_reads_pending_scope():
    context = AccountContext(self_ali_id="10001", data_dir="D:/data", epoch="e")

    class _Store:
        def __init__(self, rows):
            self.rows = rows

        def list_pending(self, *, seller, data_dir):
            assert (seller, data_dir) == ("10001", "D:/data")
            return self.rows

    assert outbox_busy(context, store=_Store([])) is False
    assert outbox_busy(context, store=_Store([{"id": "t1"}])) is True


def test_failed_targets_back_off_before_retry():
    now = [1000.0]
    service = CardSweepService(clock=lambda: now[0])
    target = SweepTarget(1, "20002", ("111",))

    assert service._eligible("20002", now[0])
    service._record(SweepOutcome(target, True, ()))
    assert not service._eligible("20002", now[0] + SWEEP_INTERVAL_S - 1)
    assert service._eligible("20002", now[0] + SWEEP_INTERVAL_S)
    service._record(SweepOutcome(target, True, ()))
    assert not service._eligible("20002", now[0] + 2 * SWEEP_INTERVAL_S - 1)

    service._record(SweepOutcome(target, True, ("111",)))
    assert service._eligible("20002", now[0])


def test_sweep_once_skips_while_outbox_is_busy(monkeypatch):
    service = CardSweepService()
    monkeypatch.setattr(service_module, "get_account_context", lambda: AccountContext("10001", "D:/data", "e"))
    monkeypatch.setattr(service_module, "outbox_busy", lambda context: True)
    monkeypatch.setattr(service_module, "select_targets", lambda *args: pytest.fail("selection must not run"))

    service._sweep_once()

    state = service.observation()
    assert state["phase"] == "waiting" and state["targets"] == 0


def test_sweep_once_waits_when_the_client_is_not_connected(monkeypatch):
    service = CardSweepService()
    monkeypatch.setattr(service_module, "get_account_context", lambda: AccountContext("10001", "D:/data", "e"))
    monkeypatch.setattr(service_module, "outbox_busy", lambda context: False)
    monkeypatch.setattr(service_module, "get_product_card_pool", lambda: object())
    monkeypatch.setattr(
        service_module, "select_targets",
        lambda *args: [SweepTarget(1, "20002", ("111",))],
    )

    def _offline(*args, **kwargs):
        raise AppError("client offline", status_code=503)

    monkeypatch.setattr(service_module.gui_session, "capture_gui_session", _offline)

    service._sweep_once()

    state = service.observation()
    assert state["targets"] == 1 and state["visited"] == 0
    assert state["last_error"] == "client offline"
