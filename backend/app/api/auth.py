"""Opaque, persistent API sessions. Importing this module performs no I/O."""

from __future__ import annotations

import hashlib
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger
from starlette.concurrency import run_in_threadpool
from starlette._utils import get_route_path
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.api.envelope import err
from backend.app.shared.utils.log_context import bind_log_context, log_event
from backend.app.shared.utils.settings import FRONTEND_DEV_ORIGINS, resolve_backend_root

# Successful read requests below this threshold are described by aggregate
# counters instead of one JSON record each; slower reads stay individually
# visible so latency regressions remain diagnosable.
SLOW_REQUEST_MS = 1000.0
_OBSERVATION_LOCK = threading.Lock()
_OBSERVATION = {"requests": 0, "reads": 0, "writes": 0, "errors": 0, "slow": 0, "observed_at": None}


def http_observations() -> dict:
    """Process-lifetime request counters; nonblocking, no I/O, no request data."""
    with _OBSERVATION_LOCK:
        return dict(_OBSERVATION)


def _record_request(method: str | None, status: int, duration_ms: float) -> None:
    with _OBSERVATION_LOCK:
        _OBSERVATION["requests"] += 1
        _OBSERVATION["reads" if method in {"GET", "HEAD", "OPTIONS"} else "writes"] += 1
        if status >= 400:
            _OBSERVATION["errors"] += 1
        if duration_ms >= SLOW_REQUEST_MS:
            _OBSERVATION["slow"] += 1
        _OBSERVATION["observed_at"] = time.time()


@dataclass(frozen=True)
class AuthConfig:
    secret_digest: bytes = field(repr=False)
    database_path: Path

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.secret_digest).hexdigest()


def validate_auth_config() -> AuthConfig:
    """Validate before starting any services; never include config values in errors."""
    value = os.environ.get("MAA_AUTH_SECRET_SHA256", "")
    if re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("MAA_AUTH_SECRET_SHA256 must contain exactly 64 hexadecimal characters")
    override = os.environ.get("MAA_AUTH_DB_PATH")
    if override is not None and not override.strip():
        raise ValueError("MAA_AUTH_DB_PATH must be a nonempty file path")
    path = Path(override).expanduser() if override is not None else resolve_backend_root() / "data" / "auth.sqlite"
    return AuthConfig(bytes.fromhex(value), path.resolve())


class LoginLimiter:
    """One attempt every two seconds, shared by all clients in this process."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._next_attempt = float("-inf")

    def retry_after(self) -> int:
        with self._lock:
            now = self._clock()
            if now < self._next_attempt:
                return max(1, math.ceil(self._next_attempt - now))
            self._next_attempt = now + 2.0
            return 0


LOGIN_LIMITER = LoginLimiter()
TOKEN_PATTERN = re.compile(r"maa_[A-Za-z0-9_-]{43}")


class SessionStore:
    def __init__(self, config: AuthConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.config.database_path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS auth_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), fingerprint TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS auth_sessions (token_hash TEXT PRIMARY KEY)")
                row = db.execute("SELECT fingerprint FROM auth_metadata WHERE id = 1").fetchone()
                if row is None or row[0] != self.config.fingerprint:
                    db.execute("DELETE FROM auth_sessions")
                    db.execute("INSERT OR REPLACE INTO auth_metadata (id, fingerprint) VALUES (1, ?)", (self.config.fingerprint,))
            self._initialized = True

    def _check_fingerprint(self, db: sqlite3.Connection) -> None:
        row = db.execute("SELECT fingerprint FROM auth_metadata WHERE id = 1").fetchone()
        if row is None or row[0] != self.config.fingerprint:
            # A stale app instance must never undo a rotation by another startup.
            raise RuntimeError("Authentication configuration changed; restart required")

    def issue(self) -> str:
        self.initialize()
        token = "maa_" + secrets.token_urlsafe(32)
        with closing(sqlite3.connect(self.config.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._check_fingerprint(db)
            db.execute("INSERT INTO auth_sessions (token_hash) VALUES (?)", (self.token_hash(token),))
        return token

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def contains(self, token_hash: str) -> bool:
        self.initialize()
        with closing(sqlite3.connect(self.config.database_path)) as db, db:
            db.execute("BEGIN")
            self._check_fingerprint(db)
            return db.execute("SELECT 1 FROM auth_sessions WHERE token_hash = ?", (token_hash,)).fetchone() is not None

    def revoke(self, token_hash: str | None = None) -> int:
        self.initialize()
        with closing(sqlite3.connect(self.config.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._check_fingerprint(db)
            if token_hash is None:
                return db.execute("DELETE FROM auth_sessions").rowcount
            return db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)).rowcount


class AuthMiddleware:
    """Authenticate before reading bodies or entering business middleware."""

    def __init__(self, app: ASGIApp, store: SessionStore, limiter: LoginLimiter) -> None:
        self.app = app
        self.store = store
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._handle(scope, receive, send)
            return
        request_id = uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = request_id
        start = time.monotonic()
        status = 500

        async def observe(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        with bind_log_context(request_id=request_id):
            try:
                await self._handle(scope, receive, observe)
            finally:
                route = getattr(scope.get("route"), "path", None)
                method = scope.get("method")
                duration_ms = (time.monotonic() - start) * 1000
                _record_request(method, status, duration_ms)
                # Keep one record for writes, failures and slow reads only;
                # successful fast reads live in http_observations().
                if method not in {"GET", "HEAD"} or status >= 400 or duration_ms >= SLOW_REQUEST_MS:
                    log_event("http.access", method=method, route=route or "unmatched",
                              status=status, duration_ms=duration_ms)

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = get_route_path(scope) if scope["type"] in {"http", "websocket"} else ""
        protected = path == "/api" or path.startswith("/api/")
        if scope["type"] != "http":
            if scope["type"] == "websocket" and protected:
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return

        request_id = scope["state"]["request_id"]
        started = False
        status = None
        complete = False
        disconnected = False
        send_failed = False
        app_returned = False

        async def receive_request() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def send_response(message: Message) -> None:
            nonlocal started, status, complete, send_failed
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                if protected:
                    headers["Cache-Control"] = "no-store"
            try:
                await send(message)
            except BaseException:
                send_failed = True
                raise
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True

        async def reject(status: int, message: str, headers: dict | None = None) -> None:
            response = JSONResponse(err(message), status_code=status, headers=headers)
            # Even rejections before the inner CORS middleware need CORS headers.
            cors = CORSMiddleware(response, allow_origins=FRONTEND_DEV_ORIGINS,
                                  allow_credentials=False, expose_headers=["X-Request-ID", "Retry-After"])
            request_headers = Headers(scope=scope)
            if "origin" in request_headers:
                await cors.simple_response(scope, receive, send_response, request_headers=request_headers)
            else:
                await response(scope, receive, send_response)

        try:
            if protected:
                request_headers = Headers(scope=scope)
                if (scope["method"] == "OPTIONS" and request_headers.get("origin")
                        and request_headers.get("access-control-request-method")):
                    # The inner CORS middleware validates and terminates preflights
                    # before epoch checks, body parsing, or business routing.
                    await self.app(scope, receive, send_response)
                    return
                if path == "/api/auth/login" and scope["method"] == "POST":
                    retry = self.limiter.retry_after()
                    if retry:
                        await reject(429, "Too many login attempts", {"Retry-After": str(retry)})
                        return
                else:
                    values = request_headers.getlist("authorization")
                    parts = values[0].split(" ") if len(values) == 1 else []
                    if len(parts) != 2 or parts[0].lower() != "bearer" or TOKEN_PATTERN.fullmatch(parts[1]) is None:
                        await reject(401, "Authentication required", {"WWW-Authenticate": "Bearer"})
                        return
                    token_hash = self.store.token_hash(parts[1])
                    try:
                        valid = await run_in_threadpool(self.store.contains, token_hash)
                    except Exception:
                        logger.exception("Authentication storage unavailable")
                        await reject(503, "Authentication unavailable")
                        return
                    if not valid:
                        await reject(401, "Authentication required", {"WWW-Authenticate": "Bearer"})
                        return
                    scope["state"]["auth_token_hash"] = token_hash
                if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                    from backend.app.updater import get_updater

                    if get_updater().writes_blocked():
                        await reject(409, "Application update handoff is active; new writes are unavailable.")
                        return
            await self.app(scope, receive_request, send_response)
            app_returned = True
        except Exception as exc:
            logger.opt(exception=exc).error("Unhandled authentication middleware error")
            if started:
                raise
            # Do not log exception locals: login bodies and bearer tokens are secrets.
            await reject(500, "Internal server error")
        finally:
            # http.access already carries writes, errors and slow reads; a
            # second record is useful only when completion itself was abnormal.
            if status is None or not complete or disconnected or send_failed:
                log_event("http.response", status=status, complete=complete,
                          disconnected=disconnected, send_failed=send_failed)
            pending = scope["state"].pop("update_handoff", None)
            if pending is not None:
                updater, operation_id = pending
                # This is outside BaseHTTPMiddleware's inner response stream.
                # Synchronous local transitions also run if this task is cancelled.
                if app_returned and complete and status is not None and 200 <= status < 300 and not send_failed and not disconnected:
                    updater.arm(operation_id)
                else:
                    updater.cancel_install(operation_id)
