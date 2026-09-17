"""Keep synchronous account endpoints and their response lifetime on one epoch."""

from contextvars import ContextVar
from functools import wraps
from inspect import signature

from fastapi.routing import APIRoute

from backend.app.api.envelope import AppError
from backend.app.shared.backend.account_context import account_lock, changing_account, get_account_context


request_epoch: ContextVar[str | None] = ContextVar("request_account_epoch", default=None)
# A mutable marker carries the endpoint's epoch back across the threadpool's
# copied Context. Settings writes may intentionally change it while locked.
response_epoch: ContextVar[list[str] | None] = ContextVar("response_account_epoch", default=None)


def is_account_setting(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in (
        "/api/settings/alibaba-data-dir", "/api/settings/ali-id", "/api/settings/ali-keys",
    ))


def is_account_path(path: str) -> bool:
    return is_account_setting(path) or any(path == prefix or path.startswith(prefix + "/") for prefix in (
        "/api/conversations", "/api/messages", "/api/self-info",
        "/api/sync-state", "/api/cache/reset", "/api/status/node-test",
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

            @wraps(original)
            def endpoint(*args, **values):
                # FastAPI runs this complete call in one worker. RLock must not
                # be acquired/released by separate yield-dependency workers.
                with account_lock:
                    expected = request_epoch.get()
                    require_epoch(expected)
                    result = original(*args, **values)
                    require_epoch(expected)
                    return result

            endpoint.__signature__ = signature(original, eval_str=True)
            endpoint._account_scoped = True
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
