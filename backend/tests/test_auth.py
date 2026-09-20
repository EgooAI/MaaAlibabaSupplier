import asyncio
import hashlib
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from backend.app.api import auth, auth_cli, main
from backend.tests.auth_client import authenticate


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(main, "LOGIN_LIMITER", auth.LoginLimiter(lambda: now[0]))
    return now


@pytest.fixture
def client(clock):
    with TestClient(main.create_app()) as client:
        yield client


def login(client, digest=None):
    return client.post("/api/auth/login", json={
        "secret_sha256": digest if digest is not None else os.environ["MAA_AUTH_SECRET_SHA256"],
    })


@pytest.mark.parametrize("value", [None, "", "a" * 63, "a" * 65, "g" * 64, " " + "a" * 64, "a" * 64 + "\n"])
def test_config_fails_closed_without_echo(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MAA_AUTH_SECRET_SHA256")
    else:
        monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", value)
    for validate in (auth.validate_auth_config, main.create_app):
        with pytest.raises(ValueError, match="MAA_AUTH_SECRET_SHA256") as error:
            validate()
        if value:
            assert value not in str(error.value)
    assert not Path(os.environ["MAA_AUTH_DB_PATH"]).exists()


def test_config_path_default_and_no_import_time_database(monkeypatch):
    monkeypatch.delenv("MAA_AUTH_DB_PATH")
    config = auth.validate_auth_config()
    assert config.database_path == Path.cwd() / "backend" / "data" / "auth.sqlite"
    assert not config.database_path.exists()
    main.create_app()
    assert not config.database_path.exists()
    monkeypatch.setenv("MAA_AUTH_DB_PATH", " ")
    with pytest.raises(ValueError, match="MAA_AUTH_DB_PATH"):
        main.create_app()


def test_every_business_route_is_guarded_before_epoch_and_database(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Business context accessed before auth"))
    monkeypatch.setattr(main, "get_account_context", forbidden)
    app = main.create_app()
    # OpenAPI includes nested routers even when app.routes is not flattened.
    routes = app.openapi()["paths"]
    assert routes
    for path in routes:
        assert path == "/api" or path.startswith("/api/"), f"Route outside auth boundary: {path}"
    methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    paths = [(method.upper(), path) for path, operations in routes.items()
             for method in operations if method in methods and (method, path) != ("post", "/api/auth/login")]
    paths.extend((method, path) for method in ("GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS")
                 for path in ("/api", "/api/", "/api/unknown", "/api/auth/login/"))
    with TestClient(app, raise_server_exceptions=False) as client:
        for method, path in paths:
            response = client.request(method, path, content=b"{malformed")
            assert response.status_code == 401, (method, path, response.text)
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert response.headers["X-Request-ID"]
    forbidden.assert_not_called()


def test_rejections_do_not_read_body_or_initialize_database(clock):
    app = main.create_app()

    async def request(path, method, headers=()):
        messages = []

        async def receive():
            pytest.fail("Rejected request body was read")

        async def send(message):
            messages.append(message)

        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                   "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                   "query_string": b"", "headers": list(headers), "client": ("test", 1), "server": ("test", 80)}, receive, send)
        return messages[0]["status"]

    assert asyncio.run(request("/api/messages", "POST")) == 401
    for origin, status in ((b"http://localhost:3000", 200), (b"https://unsupported.example", 400)):
        assert asyncio.run(request("/api/messages", "OPTIONS", [
            (b"origin", origin), (b"access-control-request-method", b"POST"),
            (b"access-control-request-headers", b"authorization, content-type"),
        ])) == status
    assert main.LOGIN_LIMITER.retry_after() == 0
    assert asyncio.run(request("/api/auth/login", "POST")) == 429
    assert not Path(os.environ["MAA_AUTH_DB_PATH"]).exists()


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/messages/translations"])
@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000"])
def test_supported_preflight_is_public_without_business_work_or_login_budget(path, origin, clock, monkeypatch):
    app = main.create_app()
    forbidden = Mock(side_effect=AssertionError("Preflight entered auth or business work"))
    monkeypatch.setattr(main, "get_account_context", forbidden)
    monkeypatch.setattr(app.state.auth_store, "contains", forbidden)

    @app.options(path)
    async def business_options():
        forbidden()

    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type, x-account-epoch",
    }
    with closing(TestClient(app)) as client:
        response = client.options(path, headers=headers)
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == origin
        assert "POST" in response.headers["Access-Control-Allow-Methods"].split(", ")
        assert response.headers["Access-Control-Allow-Headers"] == headers["Access-Control-Request-Headers"]
        assert "access-control-allow-credentials" not in response.headers
        assert response.headers["X-Request-ID"]
        assert response.headers["Cache-Control"] == "no-store"
        forbidden.assert_not_called()
        assert not Path(os.environ["MAA_AUTH_DB_PATH"]).exists()
        assert login(client).status_code == 200
        assert client.options(path, headers=headers).status_code == 200
        assert login(client).status_code == 429
        forbidden.assert_not_called()


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/messages/translations"])
@pytest.mark.parametrize("headers,status", [
    ({}, 401),
    ({"Origin": "http://localhost:3000"}, 401),
    ({"Access-Control-Request-Method": "POST"}, 401),
    ({"Origin": "http://localhost:3000", "Access-Control-Request-Method": ""}, 401),
    ({"Origin": "https://unsupported.example", "Access-Control-Request-Method": "POST",
      "Access-Control-Request-Headers": "authorization, content-type"}, 400),
    ({"Origin": "http://localhost:3000", "Access-Control-Request-Method": "CONNECT",
      "Access-Control-Request-Headers": "authorization, content-type"}, 400),
])
def test_options_has_no_general_auth_bypass(path, headers, status, clock, monkeypatch):
    app = main.create_app()
    forbidden = Mock(side_effect=AssertionError("Rejected OPTIONS entered business work"))
    monkeypatch.setattr(main, "get_account_context", forbidden)
    monkeypatch.setattr(app.state.auth_store, "contains", forbidden)

    @app.options(path)
    async def business_options():
        forbidden()

    with closing(TestClient(app)) as client:
        response = client.options(path, headers=headers)
        assert response.status_code == status
        assert response.headers["X-Request-ID"]
        assert response.headers["Cache-Control"] == "no-store"
        if headers.get("Origin") == "https://unsupported.example":
            assert "access-control-allow-origin" not in response.headers
        forbidden.assert_not_called()
        assert not Path(os.environ["MAA_AUTH_DB_PATH"]).exists()
        assert login(client).status_code == 200


def test_root_path_cannot_bypass_auth(clock):
    with TestClient(main.create_app(), root_path="/backend") as client:
        assert client.get("/backend/api/auth/session").status_code == 401
        assert client.post("/backend/api/messages/translations", content=b"{").status_code == 401
        response = client.post("/backend/api/auth/login", json={"secret_sha256": os.environ["MAA_AUTH_SECRET_SHA256"]})
        assert response.status_code == 200
        token = response.json()["data"]["token"]
        assert client.get("/backend/api/auth/session", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert client.post("/backend/api/auth/login", content=b"{").status_code == 429


def test_login_contract_hash_only_storage_and_session(client):
    response = login(client)
    assert response.status_code == 200
    assert response.json()["code"] == 0
    token = response.json()["data"]["token"]
    assert re.fullmatch(r"maa_[A-Za-z0-9_-]{43}", token)
    assert response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in response.headers
    digest = os.environ["MAA_AUTH_SECRET_SHA256"]
    with closing(sqlite3.connect(os.environ["MAA_AUTH_DB_PATH"])) as db:
        assert db.execute("SELECT token_hash FROM auth_sessions").fetchall() == [(hashlib.sha256(token.encode()).hexdigest(),)]
        assert db.execute("SELECT fingerprint FROM auth_metadata").fetchone() == (hashlib.sha256(bytes.fromhex(digest)).hexdigest(),)
        dump = "\n".join(db.iterdump())
        assert token not in dump
        assert digest not in dump
    response = client.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert response.json() == {"code": 0, "msg": "ok", "data": {"authenticated": True}}


@pytest.mark.parametrize("body,status", [
    (b"{", 422), (b"null", 422), (b"{}", 422),
    (b'{"secret_sha256":123}', 422),
    (b'{"secret_sha256":"' + b"b" * 64 + b'"}', 401),
    (b'{"secret_sha256":"' + b"a1" * 32 + b'","extra":"sensitive"}', 422),
    (b'{"secret_sha256":"' + b"a1" * 32 + b'"}', 200),
])
def test_all_login_attempts_consume_same_budget(client, clock, body, status):
    response = client.post("/api/auth/login", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == status
    if status != 200:
        assert "sensitive" not in response.text
        assert "a1" * 32 not in response.text
    response = login(client)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "2"
    clock[0] += 1.25
    response = login(client)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    clock[0] += 0.75
    assert login(client).status_code == 200


def test_limiter_is_global_across_apps_and_cannot_be_spoofed(client, clock):
    assert login(client).status_code == 200
    with TestClient(main.create_app()) as second:
        response = second.post("/api/auth/login", json={"secret_sha256": "b" * 64}, headers={
            "X-Forwarded-For": "8.8.8.8", "Authorization": "Bearer another-token",
        })
        assert response.status_code == 429
    clock[0] += 2
    assert login(client).status_code == 200


def test_atomic_login_admission_under_concurrency(client):
    barrier = Barrier(12)

    def attempt(_):
        barrier.wait()
        return login(client).status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(attempt, range(12)))
    assert statuses.count(200) == 1
    assert statuses.count(429) == 11


def test_only_bearer_opaque_tokens_authorize(client):
    token = login(client).json()["data"]["token"]
    digest = os.environ["MAA_AUTH_SECRET_SHA256"]
    for header in (digest, f"Bearer {digest}", f"Basic {token}", f"Bearer {token} extra", f"Bearer  {token}"):
        assert client.get("/api/auth/session", headers={"Authorization": header}).status_code == 401
    assert client.get("/api/auth/session", headers=[("Authorization", f"Bearer {token}"), ("Authorization", f"Bearer {token}")]).status_code == 401
    assert client.get(f"/api/auth/session?token={token}").status_code == 401
    assert client.get("/api/auth/session", headers={"Cookie": f"token={token}; session={token}"}).status_code == 401
    assert client.post("/api/auth/session", json={"token": token}).status_code == 401
    assert client.get("/api/auth/session", headers={"Authorization": f"bearer {token}"}).status_code == 200


def test_persistence_logout_and_revoke_all(client, clock, monkeypatch, capsys):
    first = login(client).json()["data"]["token"]
    clock[0] += 2
    second = login(client).json()["data"]["token"]
    first_headers = {"Authorization": f"Bearer {first}"}
    second_headers = {"Authorization": f"Bearer {second}"}
    clock[0] += 1_000_000_000
    with TestClient(main.create_app()) as restarted:
        assert restarted.get("/api/auth/session", headers=first_headers).status_code == 200
        response = restarted.post("/api/auth/logout", headers=first_headers)
        assert response.json() == {"code": 0, "msg": "ok", "data": None}
        assert restarted.get("/api/auth/session", headers=first_headers).status_code == 401
        assert restarted.post("/api/auth/logout", headers=first_headers).status_code == 401
        assert client.get("/api/auth/session", headers=first_headers).status_code == 401
        assert client.get("/api/auth/session", headers=second_headers).status_code == 200
        monkeypatch.setattr("sys.argv", ["auth_cli", "revoke-all"])
        auth_cli.main()
        assert capsys.readouterr().out == "Revoked 1 session(s).\n"
        assert restarted.get("/api/auth/session", headers=second_headers).status_code == 401
    with TestClient(main.create_app()) as restarted:
        assert restarted.get("/api/auth/session", headers=first_headers).status_code == 401
        assert restarted.get("/api/auth/session", headers=second_headers).status_code == 401


def test_rotation_revokes_on_startup_and_cannot_resurrect(client, clock, monkeypatch):
    old = login(client).json()["data"]["token"]
    original = os.environ["MAA_AUTH_SECRET_SHA256"]
    monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", "b2" * 32)
    with TestClient(main.create_app()) as rotated:
        with closing(sqlite3.connect(os.environ["MAA_AUTH_DB_PATH"])) as db:
            assert db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
        assert rotated.get("/api/auth/session", headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert client.get("/api/auth/session", headers={"Authorization": f"Bearer {old}"}).status_code == 503
        clock[0] += 2
        assert login(client, original).status_code == 503
        clock[0] += 2
        new = login(rotated).json()["data"]["token"]
    monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", original)
    with TestClient(main.create_app()) as restored:
        for token in (old, new):
            assert restored.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_uppercase_digest_has_same_fingerprint(client, monkeypatch):
    token = login(client, os.environ["MAA_AUTH_SECRET_SHA256"].upper()).json()["data"]["token"]
    monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", os.environ["MAA_AUTH_SECRET_SHA256"].upper())
    with TestClient(main.create_app()) as restarted:
        assert restarted.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_failed_rotation_rolls_back_revocation(client, monkeypatch):
    token = login(client).json()["data"]["token"]
    with closing(sqlite3.connect(os.environ["MAA_AUTH_DB_PATH"])) as db, db:
        db.execute("CREATE TRIGGER fail_rotation BEFORE INSERT ON auth_metadata BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    monkeypatch.setenv("MAA_AUTH_SECRET_SHA256", "b2" * 32)
    with pytest.raises(sqlite3.IntegrityError):
        with TestClient(main.create_app()):
            pass
    assert client.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_database_failure_fails_startup_and_lazy_requests_closed(monkeypatch, tmp_path, clock):
    directory = tmp_path / "not-a-database"
    directory.mkdir()
    monkeypatch.setenv("MAA_AUTH_DB_PATH", str(directory))
    app = main.create_app()
    with pytest.raises(sqlite3.OperationalError):
        with TestClient(app):
            pass
    with closing(TestClient(app)) as client:
        response = login(client)
        assert response.status_code == 503
        assert str(directory) not in response.text
        assert client.get("/api/auth/session", headers={"Authorization": "Bearer maa_" + "a" * 43}).status_code == 503
        assert client.get("/api/settings/connection").status_code == 401


def test_headers_and_sanitized_errors(client, clock):
    origin = "http://localhost:3000"
    response = client.get("/api/status", headers={"Origin": origin})
    assert response.status_code == 401
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert "access-control-allow-credentials" not in response.headers
    assert "Retry-After" in response.headers["Access-Control-Expose-Headers"]
    authenticate(client)

    @client.app.get("/api/test-error")
    async def fail(request: Request):
        raise RuntimeError(request.headers["Authorization"])

    for path, status in (("/api/test-error", 500), ("/api/absent", 404)):
        response = client.get(path, headers={"Origin": origin})
        assert response.status_code == status
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Request-ID"]
        assert response.headers["Access-Control-Allow-Origin"] == origin
        assert client.headers["Authorization"] not in response.text
        assert response.json()["code"] == 1
    response = client.post("/api/auth/login", headers={"Origin": origin})
    assert response.status_code == 429
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == origin


@pytest.mark.parametrize("status,headers", [
    (401, {"WWW-Authenticate": 'Bearer realm="api"'}),
    (429, {"Retry-After": "7"}),
])
def test_http_exception_preserves_headers(client, status, headers):
    authenticate(client)

    @client.app.get("/api/header-error")
    async def fail():
        raise HTTPException(status_code=status, detail="Request rejected", headers=headers)

    response = client.get("/api/header-error", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == status
    assert response.json() == {"code": 1, "msg": "Request rejected", "data": None}
    for name, value in headers.items():
        assert response.headers[name] == value
    assert response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"


def test_frontend_is_public_and_docs_are_disabled(tmp_path, monkeypatch):
    output = tmp_path / "frontend" / "out"
    output.mkdir(parents=True)
    (output / "index.html").write_text("<html>Public shell</html>", encoding="utf-8")
    monkeypatch.setattr(main, "resolve_repo_root", lambda: tmp_path)
    with TestClient(main.create_app()) as client:
        assert client.get("/").text == "<html>Public shell</html>"
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        assert client.get("/api/unknown").status_code == 401
