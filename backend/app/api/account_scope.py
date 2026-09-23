"""Keep synchronous account endpoints and their response lifetime on one epoch."""

from contextvars import ContextVar
from functools import wraps
from inspect import signature

from fastapi.routing import APIRoute

from backend.app.api.envelope import AppError
from backend.app.shared.backend.account_context import AccountContext, account_lock, changing_account, get_account_context


request_epoch: ContextVar[str | None] = ContextVar("request_account_epoch", default=None)
# A mutable marker carries the endpoint's epoch back across the threadpool's
# copied Context. Settings writes may intentionally change it while locked.
response_epoch: ContextVar[list[str] | None] = ContextVar("response_account_epoch", default=None)
outbox_context: ContextVar[AccountContext] = ContextVar("outbox_account_context")


def is_account_setting(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in (
        "/api/settings/alibaba-data-dir", "/api/settings/ali-id", "/api/settings/ali-keys",
    ))


def is_account_path(path: str) -> bool:
    return is_account_setting(path) or any(path == prefix or path.startswith(prefix + "/") for prefix in (
        "/api/conversations", "/api/inbox", "/api/messages", "/api/outbox", "/api/self-info",
        "/api/sync-state", "/api/cache/reset", "/api/status/node-test", "/api/cards",
    ))


def require_epoch(expected: str | None) -> str:
    current = get_account_context().epoch
    if not expected or expected != current:
        raise AppError("账号已切换或请求缺少账号版本，请刷新后重试。", status_code=409)
    return current


class AccountRoute(APIRoute):
    def __init__(self, path, endpoint, **kwargs):
        if is_account_path(path) and not getattr(endpoint, "_account_scoped", False):
            original = endpoint
            reject_busy = ("PUT" in (kwargs.get("methods") or ()) and path == "/api/inbox/settings") or ("POST" in (kwargs.get("methods") or ()) and path in {
                "/api/conversations/{conversation_id}/messages",
                "/api/outbox/{task_id}/confirm",
                "/api/outbox/{task_id}/retry",
            })

            @wraps(original)
            def endpoint(*args, **values):
                # FastAPI runs this complete call in one worker. RLock must not
                # be acquired/released by separate yield-dependency workers.
                expected = request_epoch.get()
                if reject_busy:
                    require_epoch(expected)
                # Reject before calling the service so timed-out writes cannot
                # resume and enqueue after a long GUI operation releases its lock.
                with changing_account() if reject_busy else account_lock:
                    require_epoch(expected)
                    result = original(*args, **values)
                    require_epoch(expected)
                    return result

            endpoint.__signature__ = signature(original, eval_str=True)
            endpoint._account_scoped = True
        super().__init__(path, endpoint, **kwargs)


class OutboxObservationRoute(APIRoute):
    """Scoped store reads and cancellation must not wait for a GUI operation."""

    def __init__(self, path, endpoint, **kwargs):
        if not getattr(endpoint, "_outbox_scoped", False):
            original = endpoint

            @wraps(original)
            def endpoint(*args, **values):
                expected = request_epoch.get()
                context = get_account_context()
                if not expected or context.epoch != expected:
                    raise AppError("账号已切换，请刷新后重试。", status_code=409)
                if not context.self_ali_id or not context.data_dir:
                    raise AppError("请先在设置页选择数据目录和卖家账号。", status_code=503)
                token = outbox_context.set(context)
                try:
                    result = original(*args, **values)
                    require_epoch(expected)
                    return result
                finally:
                    outbox_context.reset(token)

            endpoint.__signature__ = signature(original, eval_str=True)
            endpoint._outbox_scoped = True
        super().__init__(path, endpoint, **kwargs)


class SettingsRoute(APIRoute):
    def __init__(self, path, endpoint, **kwargs):
        if set(kwargs.get("methods") or ()) - {"GET", "HEAD", "OPTIONS"} and not getattr(endpoint, "_settings_scoped", False):
            original = endpoint

            @wraps(original)
            def endpoint(*args, **values):
                # Enter before directory scans, persistence or context reads;
                # a running GUI/AI operation must cause an immediate conflict.
                with changing_account():
                    if is_account_setting(path):
                        require_epoch(request_epoch.get())
                    result = original(*args, **values)
                    marker = response_epoch.get()
                    if is_account_setting(path) and marker is not None:
                        marker[0] = get_account_context().epoch
                    return result

            endpoint.__signature__ = signature(original, eval_str=True)
            endpoint._settings_scoped = True
        super().__init__(path, endpoint, **kwargs)
