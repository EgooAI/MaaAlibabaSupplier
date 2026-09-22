"""Account selection shared by requests, background work and GUI guards."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from threading import RLock
from uuid import uuid4

from backend.app.shared.utils.app_config import read_app_config
from backend.app.shared.utils.log_context import log_event


account_lock = RLock()
_context_lock = RLock()
_context = None


@dataclass(frozen=True)
class AccountContext:
    self_ali_id: str
    data_dir: str
    epoch: str

    def to_dict(self) -> dict:
        return asdict(self)


def _switched(previous: str | None) -> None:
    # Record only the opaque new epoch; the selected identity stays out of logs.
    if previous is not None:
        log_event("account.changed", account_epoch=_context.epoch)


def get_account_context() -> AccountContext:
    global _context
    with _context_lock:
        config = read_app_config()
        ali_id = str(config.get("self_ali_id") or "").strip()
        data_dir = str(config.get("alibaba_data_dir") or "").strip()
        if _context is None or (_context.self_ali_id, _context.data_dir) != (ali_id, data_dir):
            previous = _context.epoch if _context is not None else None
            _context = AccountContext(ali_id, data_dir, uuid4().hex)
            _switched(previous)
        return _context


def invalidate_account_context() -> AccountContext:
    global _context
    with _context_lock:
        previous = _context.epoch if _context is not None else None
        _context = None
        context = get_account_context()
        _switched(previous)
        return context


@contextmanager
def changing_account():
    """Reject changes while a GUI action or account snapshot is in use."""
    from backend.app.api.envelope import AppError

    if not account_lock.acquire(blocking=False):
        raise AppError("当前账号仍有操作正在执行，请稍后重试", status_code=409)
    try:
        yield
    finally:
        account_lock.release()
