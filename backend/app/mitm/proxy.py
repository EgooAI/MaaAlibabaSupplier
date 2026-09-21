"""Yak/Yakit MITM receiver lifecycle and CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import re
import secrets
import socket

from loguru import logger
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Sequence
from urllib.parse import parse_qs, urlparse

from backend.app.shared.mitm.parsers import (
    parse_contact_extinfo_get,
    parse_fetch_card,
    parse_generic_card,
    parse_get_user_info_by_params,
    parse_im_id_get,
    parse_inquiry_card,
    parse_query_customer_info,
)
from backend.app.shared.mitm.pool import UserInfo, get_generic_card_pool, get_product_card_pool, get_user_info_pool
from backend.app.shared.crm import sync_self_info, sync_user_info
from backend.app.shared.utils.app_config import get_configured_self_ali_id
from backend.app.shared.utils.logging import configure_logging
from backend.app.shared.utils.env import get_env_int, get_env_str
from backend.app.shared.utils.settings import MITM_RECEIVER_HOST_DEFAULT, MITM_RECEIVER_PORT_DEFAULT


def validate_receiver_config(host: str, port: int, internal_token: str | None) -> str:
    if not internal_token:
        raise ValueError("MAA_MITM_INTERNAL_TOKEN is required; set the same private token for the receiver and Yak")
    if re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", internal_token) is None or internal_token.startswith("maa_"):
        raise ValueError("MAA_MITM_INTERNAL_TOKEN must be an ASCII bearer token distinct from browser session tokens (maa_)")
    host = host.strip()
    if host.lower() == "localhost":
        host = "127.0.0.1"
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("MITM_RECEIVER_HOST must be a loopback IP address or localhost") from None
    if not address.is_loopback or "%" in host:
        raise ValueError("MITM_RECEIVER_HOST must be a loopback IP address or localhost")
    if not 1 <= port <= 65535:
        raise ValueError("MITM_RECEIVER_PORT must be between 1 and 65535")
    return str(address)


@dataclass(frozen=True)
class _TrafficEvent:
    url: str
    route_target: str
    method: str
    request_headers: Any
    response_body: bytes

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> _TrafficEvent | None:
        url = _normalize_url(data.get("url", ""), bool(data.get("is_https", True)))
        if not url:
            return None
        parsed = urlparse(url)
        route_target = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
        return cls(
            url=url,
            route_target=route_target,
            method=str(data.get("method", "?")),
            request_headers=data.get("request_headers", {}),
            response_body=_body_bytes(data.get("response_body", b"")),
        )


def _normalize_url(url: Any, is_https: bool) -> str:
    value = str(url or "")
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    scheme = "https" if is_https else "http"
    return f"{scheme}://{value}"


def _body_bytes(body: Any) -> bytes:
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return b""


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    internal_token: bytes
    router: TrafficRouter


class TrafficRouter:
    def __init__(self, url_filters: list[str] | None = None) -> None:
        self.url_filters = url_filters or []
        self._routes: list[tuple[str, Callable[[_TrafficEvent], None]]] = [
            ("queryCustomerInfo", self._handle_query_customer_info),
            ("getuserinfobyparams", self._handle_get_user_info_by_params),
            ("icbu.im.id.get", self._handle_im_id_get),
            ("contact.extinfo.get", self._handle_contact_extinfo_get),
            ("fetchcard", self._handle_fetch_card),
        ]

    def process(self, data: dict[str, Any]) -> None:
        event = _TrafficEvent.from_payload(data)
        if event is None:
            return
        if self.url_filters and not any(kw in event.url for kw in self.url_filters):
            return

        route = self._match_route(event)
        if route is None:
            return
        keyword, handler = route

        if not event.response_body:
            logger.debug("MITM event matched {} but response_body is empty", keyword)
            return

        logger.info("MITM event matched {}", keyword)
        handler(event)

    def _match_route(self, event: _TrafficEvent) -> tuple[str, Callable[[_TrafficEvent], None]] | None:
        for keyword, handler in self._routes:
            if keyword in event.route_target:
                return keyword, handler
        return None

    def _handle_query_customer_info(self, event: _TrafficEvent) -> None:
        qs = parse_qs(urlparse(event.url).query)
        buyer_login_id = (qs.get("buyerLoginId") or [""])[0]
        info = parse_query_customer_info(event.response_body, url_buyer_login_id=buyer_login_id)
        if info:
            logger.info("UserInfo parsed (CRM)")
            get_user_info_pool().put(info)
            sync_user_info(info)

    @staticmethod
    def _put_users(users: list[UserInfo], source: str) -> None:
        pool = get_user_info_pool()
        for user in users:
            logger.info("UserInfo parsed ({})", source)
            pool.put(user)
            sync_user_info(user)

    def _handle_get_user_info_by_params(self, event: _TrafficEvent) -> None:
        self._put_users(parse_get_user_info_by_params(event.response_body), "batch")

    def _handle_im_id_get(self, event: _TrafficEvent) -> None:
        self._put_users(parse_im_id_get(event.response_body), "ID")

    def _handle_contact_extinfo_get(self, event: _TrafficEvent) -> None:
        accounts = parse_contact_extinfo_get(event.response_body)
        if not accounts:
            return

        selected_ali_id = get_configured_self_ali_id()
        user_pool = get_user_info_pool()
        for account in accounts:
            user_info = UserInfo(
                ali_id=account.ali_id,
                login_id=account.login_id,
                encrypt_account_id=account.encrypt_account_id,
                first_name=account.first_name,
                last_name=account.last_name,
                country_code=account.country,
                company_name=account.company_name,
            )
            user_pool.put(user_info)
            sync_user_info(user_info)
            if selected_ali_id and account.ali_id == selected_ali_id:
                logger.info("SelfInfo parsed for selected account")
                sync_self_info(account)

    @staticmethod
    def _handle_fetch_card(event: _TrafficEvent) -> None:
        card = parse_fetch_card(event.response_body)
        if card:
            logger.info("ProductCard parsed")
            get_product_card_pool().put(card)
            return

        # Inquiry cards have no reader in the product; keep them out of the
        # generic pool so they are not rendered as unrelated cards.
        inquiry = parse_inquiry_card(event.response_body)
        if inquiry:
            logger.info("InquiryCard parsed ({} products)", len(inquiry.products))
            return

        # Other non-product cards: store as generic
        generic = parse_generic_card(event.response_body, source_url=event.url)
        pool = get_generic_card_pool()
        for gc in generic:
            logger.info("GenericCard parsed")
            pool.put(gc)


class TrafficHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        values = self.headers.get_all("Authorization", [])
        parts = values[0].split(" ") if len(values) == 1 else []
        if (len(parts) != 2 or parts[0].lower() != "bearer"
                or not secrets.compare_digest(parts[1].encode("utf-8"), self.server.internal_token)):
            self._respond(401)
            return
        # BaseHTTPRequestHandler normalizes a leading //; check the original target.
        if self.requestline.split()[1] != "/internal/traffic":
            self._respond(404)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) > 1:
            self._respond(400)
            return
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._respond(400)
            return
        if content_length < 0:
            self._respond(400)
            return
        if content_length == 0:
            self._respond(204)
            return
        if content_length > 10 * 1024 * 1024:
            self._respond(413)
            return

        raw = self.rfile.read(content_length)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._respond(400)
            return

        if isinstance(data, dict):
            url = _normalize_url(data.get("url", ""), bool(data.get("is_https", True)))
            try:
                self.server.router.process(data)
            except Exception:
                logger.exception("Failed to process Yak MITM event")
                self._respond(500, url)
                return
        else:
            url = ""

        self._respond(204, url)

    def do_GET(self) -> None:
        self._respond(405, self.path)

    def _respond(self, code: int, url: str = "") -> None:
        self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        if code == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        logger.debug("MITM response status={}", code)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # suppressed — _respond handles logging


def create_receiver(
    host: str,
    port: int,
    url_filters: list[str] | None = None,
    *,
    internal_token: str,
) -> ReusableThreadingHTTPServer:
    host = validate_receiver_config(host, port, internal_token)

    class ReceiverServer(ReusableThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    # Bind synchronously so startup cannot continue after a receiver bind failure.
    server = ReceiverServer((host, port), TrafficHandler)
    server.internal_token = internal_token.encode("ascii")
    server.router = TrafficRouter(url_filters)
    return server


def run_receiver(
    host: str = MITM_RECEIVER_HOST_DEFAULT,
    port: int = MITM_RECEIVER_PORT_DEFAULT,
    url_filters: list[str] | None = None,
    *,
    internal_token: str | None = None,
) -> None:
    if internal_token is None:
        internal_token = os.environ.get("MAA_MITM_INTERNAL_TOKEN", "")
    server = create_receiver(host, port, url_filters, internal_token=internal_token)
    logger.info("MITM v4 receiver listening on {}:{}", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


async def start_proxy(
    host: str = "127.0.0.1",
    port: int = 8085,
    url_filters: list[str] | None = None,
    *,
    internal_token: str | None = None,
) -> None:
    await asyncio.to_thread(run_receiver, host, port, url_filters, internal_token=internal_token)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MITM v4 receiver — accepts Yak/Yakit traffic events")
    p.add_argument("--host", default=get_env_str("MITM_RECEIVER_HOST", MITM_RECEIVER_HOST_DEFAULT),
                   help="Loopback listen address (default: MITM_RECEIVER_HOST or 127.0.0.1)")
    p.add_argument("--port", type=int, default=get_env_int("MITM_RECEIVER_PORT", MITM_RECEIVER_PORT_DEFAULT),
                   help="Listen port (default: MITM_RECEIVER_PORT or 8085)")
    p.add_argument("--url", action="append", default=None,
                   help="URL keyword filter; may be specified multiple times")
    return p.parse_args(argv)


def main() -> None:
    args = _parse_args()
    # Standalone operation requires an explicit process environment, never dotenv.
    internal_token = os.environ.get("MAA_MITM_INTERNAL_TOKEN", "")
    host = validate_receiver_config(args.host, args.port, internal_token)
    configure_logging()
    run_receiver(host, args.port, args.url or [], internal_token=internal_token)


if __name__ == "__main__":
    main()
