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

from starlette.concurrency import run_in_threadpool
from starlette._utils import get_route_path
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.api.envelope import err
from backend.app.shared.utils.settings import FRONTEND_DEV_ORIGINS, resolve_backend_root


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
        path = get_route_path(scope) if scope["type"] in {"http", "websocket"} else ""
        protected = path == "/api" or path.startswith("/api/")
        if scope["type"] != "http":
            if scope["type"] == "websocket" and protected:
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def send_response(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                if protected:
                    headers["Cache-Control"] = "no-store"
            await send(message)

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
                        await reject(503, "Authentication unavailable")
                        return
                    if not valid:
                        await reject(401, "Authentication required", {"WWW-Authenticate": "Bearer"})
                        return
                    scope["state"]["auth_token_hash"] = token_hash
            await self.app(scope, receive, send_response)
        except Exception:
            if started:
                raise
            # Do not log exception locals: login bodies and bearer tokens are secrets.
            await reject(500, "Internal server error")
