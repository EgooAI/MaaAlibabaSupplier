import io
import json
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.mitm import proxy


TOKEN = "mitm_" + "x" * 43


class RequestStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.body_reads = []

    def read(self, size=-1):
        self.body_reads.append(size)
        return super().read(size)


def request(*, headers=(), path="/internal/traffic", method="POST", body=b"{}", server=None):
    stream = RequestStream(
        f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n".encode()
        + b"".join(f"{key}: {value}\r\n".encode("utf-8") for key, value in headers)
        + b"\r\n" + body
    )
    output = bytearray()
    connection = SimpleNamespace(makefile=lambda *args: stream, sendall=output.extend)
    if server is None:
        server = SimpleNamespace(internal_token=TOKEN.encode(), router=Mock())
    proxy.TrafficHandler(connection, ("127.0.0.1", 12345), server)
    return int(output.split(b" ", 2)[1]), stream.body_reads, server.router


@pytest.mark.parametrize("headers", [
    [], [("Authorization", "Bearer wrong")], [("Authorization", "Bearer maa_" + "x" * 43)],
    [("Authorization", "Basic " + TOKEN)], [("Authorization", "Bearer")],
    [("Authorization", "Bearer " + TOKEN), ("Authorization", "Bearer " + TOKEN)],
    [("Authorization", "Bearer " + TOKEN + " extra")], [("Authorization", "Bearer \u00e9")],
])
def test_rejects_auth_before_reading_or_parsing_body(headers):
    status, reads, router = request(headers=headers + [("Content-Length", "invalid")], body=b"not json")
    assert status == 401
    assert reads == []
    router.process.assert_not_called()


@pytest.mark.parametrize("path", ["/", "/internal/traffic/", "/internal/traffic?x=1", "//internal/traffic",
                                 "/internal/%74raffic", "http://localhost/internal/traffic"])
def test_rejects_every_other_request_target_without_reading(path):
    status, reads, router = request(path=path, headers=[("Authorization", "Bearer " + TOKEN)])
    assert status == 404
    assert reads == []
    router.process.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "PUT", "OPTIONS", "DELETE", "HEAD"])
def test_only_post_can_ingest(method):
    status, reads, router = request(method=method, headers=[("Authorization", "Bearer " + TOKEN)])
    assert status in (405, 501)
    assert reads == []
    router.process.assert_not_called()


def test_authenticated_event_uses_constant_time_comparison_and_routes(monkeypatch):
    compare = Mock(wraps=proxy.secrets.compare_digest)
    monkeypatch.setattr(proxy.secrets, "compare_digest", compare)
    payload = {"url": "https://example.test/queryCustomerInfo", "response_body": "{}"}
    body = json.dumps(payload).encode()
    status, reads, router = request(body=body, headers=[
        ("Authorization", "bEaReR " + TOKEN), ("Content-Length", str(len(body))),
    ])
    assert status == 204
    assert reads == [len(body)]
    compare.assert_called_once_with(TOKEN.encode(), TOKEN.encode())
    router.process.assert_called_once_with(payload)


@pytest.mark.parametrize("headers,expected", [
    ([("Content-Length", "-1")], 400),
    ([("Content-Length", str(10 * 1024 * 1024 + 1))], 413),
    ([("Transfer-Encoding", "chunked")], 400),
    ([("Content-Length", "2"), ("Content-Length", "2")], 400),
])
def test_rejects_invalid_framing_without_reading(headers, expected):
    status, reads, router = request(headers=[("Authorization", "Bearer " + TOKEN)] + headers)
    assert status == expected
    assert reads == []
    router.process.assert_not_called()


@pytest.mark.parametrize("body", [b"not json", b'"\xff"'])
def test_invalid_authenticated_body_returns_bad_request(body):
    status, _, router = request(body=body, headers=[
        ("Authorization", "Bearer " + TOKEN), ("Content-Length", str(len(body))),
    ])
    assert status == 400
    router.process.assert_not_called()


@pytest.mark.parametrize("host,normalized", [
    ("127.0.0.1", "127.0.0.1"), ("127.0.0.2", "127.0.0.2"), ("localhost", "127.0.0.1"),
    ("::1", "::1"), ("[::1]", "::1"), ("0:0:0:0:0:0:0:1", "::1"),
])
def test_loopback_addresses(host, normalized):
    assert proxy.validate_receiver_config(host, 8085, TOKEN) == normalized


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2", "example.test", "127.1", "::1%eth0", ""])
def test_rejects_nonloopback_or_ambiguous_hosts(host):
    with pytest.raises(ValueError, match="MITM_RECEIVER_HOST"):
        proxy.validate_receiver_config(host, 8085, TOKEN)


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_rejects_invalid_ports(port):
    with pytest.raises(ValueError, match="MITM_RECEIVER_PORT"):
        proxy.validate_receiver_config("127.0.0.1", port, TOKEN)


@pytest.mark.parametrize("token", [None, "", " ", "contains space", "\u00e9", "secret\r\nheader", "maa_" + "x" * 43])
def test_missing_or_invalid_token_cannot_bind(monkeypatch, token):
    bind = Mock(side_effect=AssertionError("must validate before binding"))
    monkeypatch.setattr(proxy.ReusableThreadingHTTPServer, "__init__", bind)
    with pytest.raises(ValueError, match="MAA_MITM_INTERNAL_TOKEN") as error:
        proxy.create_receiver("127.0.0.1", 8085, internal_token=token)
    if token and token.strip():
        assert token not in str(error.value)
    bind.assert_not_called()


def test_servers_keep_credentials_and_routers_separate_and_support_ipv6(monkeypatch):
    bindings = []

    def bind(server, address, handler):
        bindings.append((address, server.address_family, handler))

    monkeypatch.setattr(proxy.ReusableThreadingHTTPServer, "__init__", bind)
    first = proxy.create_receiver("127.0.0.1", 9001, internal_token=TOKEN)
    second = proxy.create_receiver("[::1]", 9002, internal_token="other-token")
    first.router, second.router = Mock(), Mock()
    headers = [("Authorization", "Bearer " + TOKEN), ("Content-Length", "2")]
    assert request(headers=headers, server=first)[0] == 204
    assert request(headers=headers, server=second)[0] == 401
    first.router.process.assert_called_once_with({})
    second.router.process.assert_not_called()
    assert bindings == [(("127.0.0.1", 9001), socket.AF_INET, proxy.TrafficHandler),
                        (("::1", 9002), socket.AF_INET6, proxy.TrafficHandler)]


def test_standalone_receiver_requires_explicit_environment(monkeypatch):
    monkeypatch.delenv("MAA_MITM_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr(proxy, "_parse_args", lambda: SimpleNamespace(host="127.0.0.1", port=8085, url=[]))
    logging = Mock()
    monkeypatch.setattr(proxy, "configure_logging", logging)
    with pytest.raises(ValueError, match="MAA_MITM_INTERNAL_TOKEN is required"):
        proxy.main()
    logging.assert_not_called()


def test_standalone_receiver_uses_env_token_and_configured_endpoint(monkeypatch):
    monkeypatch.setenv("MAA_MITM_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("MITM_RECEIVER_HOST", "::1")
    monkeypatch.setenv("MITM_RECEIVER_PORT", "9005")
    args = proxy._parse_args([])
    monkeypatch.setattr(proxy, "_parse_args", lambda: args)
    monkeypatch.setattr(proxy, "configure_logging", lambda: None)
    run = Mock()
    monkeypatch.setattr(proxy, "run_receiver", run)
    proxy.main()
    run.assert_called_once_with("::1", 9005, [], internal_token=TOKEN)


def test_run_receiver_env_credential_and_shutdown(monkeypatch):
    monkeypatch.setenv("MAA_MITM_INTERNAL_TOKEN", TOKEN)
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    create = Mock(return_value=server)
    monkeypatch.setattr(proxy, "create_receiver", create)
    proxy.run_receiver()
    create.assert_called_once_with("127.0.0.1", 8085, None, internal_token=TOKEN)
    server.server_close.assert_called_once()
