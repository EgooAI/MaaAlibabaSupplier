"""Yak/Yakit MITM receiver lifecycle and CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from loguru import logger
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Sequence
from urllib.parse import parse_qs, urlparse

from app.shared.mitm.parsers import (
    parse_contact_extinfo_get,
    parse_fetch_card,
    parse_generic_card,
    parse_get_user_info_by_params,
    parse_im_id_get,
    parse_inquiry_card,
    parse_query_customer_info,
)
from app.shared.mitm.pool import SelfInfo, UserInfo, get_generic_card_pool, get_inquiry_card_pool, get_product_card_pool, get_self_info_pool, get_user_info_pool
from app.shared.crm import sync_self_info, sync_user_info
from app.shared.utils.logging import configure_logging

_COOKIE_ALI_ID_RE = re.compile(r"xman_i=.*?aid=(\d+)")

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


class TrafficRouter:
    def __init__(self, url_filters: list[str] | None = None) -> None:
        self.url_filters = url_filters or []
        self._self_ali_id = ""
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

        self._update_self_ali_id(event.request_headers)
        route = self._match_route(event)
        if route is None:
            return
        keyword, handler = route

        if not event.response_body:
            logger.debug("MITM event matched {} but response_body is empty: {}", keyword, event.url)
            return

        logger.info("[YAK→PY] {} {}", event.method, event.url)
        handler(event)

    def _match_route(self, event: _TrafficEvent) -> tuple[str, Callable[[_TrafficEvent], None]] | None:
        for keyword, handler in self._routes:
            if keyword in event.route_target:
                return keyword, handler
        return None

    @staticmethod
    def _header_get(headers: Any, name: str) -> str:
        if not isinstance(headers, dict):
            return ""
        expected = name.lower()
        for key, value in headers.items():
            if str(key).lower() != expected:
                continue
            if isinstance(value, list):
                return str(value[0]) if value else ""
            return str(value or "")
        return ""

    def _update_self_ali_id(self, headers: Any) -> None:
        if self._self_ali_id:
            return
        cookie = self._header_get(headers, "cookie")
        m = _COOKIE_ALI_ID_RE.search(cookie)
        if not m:
            return
        self._self_ali_id = m.group(1)
        logger.info("Self ali_id detected from Cookie: {}", self._self_ali_id)
        info = SelfInfo(ali_id=self._self_ali_id)
        get_self_info_pool().put(info)
        sync_self_info(info)

    def _handle_query_customer_info(self, event: _TrafficEvent) -> None:
        qs = parse_qs(urlparse(event.url).query)
        buyer_login_id = (qs.get("buyerLoginId") or [""])[0]
        info = parse_query_customer_info(event.response_body, url_buyer_login_id=buyer_login_id)
        if info:
            logger.info("  -> UserInfo (CRM): ali_id={} login_id={}", info.ali_id, info.login_id)
            get_user_info_pool().put(info)
            sync_user_info(info)

    @staticmethod
    def _put_users(users: list[UserInfo], source: str) -> None:
        pool = get_user_info_pool()
        for user in users:
            logger.info("  -> UserInfo ({}): ali_id={} login_id={}", source, user.ali_id, user.login_id)
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

        self_pool = get_self_info_pool()
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
            if self._self_ali_id and account.ali_id == self._self_ali_id:
                logger.info("  -> SelfInfo: ali_id={} login_id={}", account.ali_id, account.login_id)
                self_pool.put(account)
                sync_self_info(account)

    @staticmethod
    def _handle_fetch_card(event: _TrafficEvent) -> None:
        card = parse_fetch_card(event.response_body)
        if card:
            logger.info("  -> ProductCard: id={} title={}", card.card_id, card.title[:40])
            get_product_card_pool().put(card)
            return

        # Inquiry cards
        inquiry = parse_inquiry_card(event.response_body)
        if inquiry:
            logger.info("  -> InquiryCard: id={} products={}", inquiry.inquiry_id, len(inquiry.products))
            get_inquiry_card_pool().put(inquiry)
            return

        # Other non-product cards: store as generic
        generic = parse_generic_card(event.response_body, source_url=event.url)
        pool = get_generic_card_pool()
        for gc in generic:
            logger.info("  -> GenericCard: type={} id={}", gc.card_type, gc.card_id[:40])
            pool.put(gc)


class TrafficHandler(BaseHTTPRequestHandler):
    router: TrafficRouter = TrafficRouter()

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._respond(204)
            return

        raw = self.rfile.read(content_length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._respond(400)
            return

        if isinstance(data, dict):
            url = _normalize_url(data.get("url", ""), bool(data.get("is_https", True)))
            try:
                self.router.process(data)
            except Exception:
                logger.exception("Failed to process Yak MITM event")
        else:
            url = ""

        self._respond(204, url)

    def do_GET(self) -> None:
        self._respond(405, self.path)

    def _respond(self, code: int, url: str = "") -> None:
        self.send_response(code)
        self.end_headers()
        clean = url.split("?")[0] if url else "(no url)"
        logger.debug("{} [{}] {}", self.command, code, clean)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # suppressed — _respond handles logging


def run_receiver(
    host: str = "127.0.0.1",
    port: int = 8085,
    url_filters: list[str] | None = None,
) -> None:
    TrafficHandler.router = TrafficRouter(url_filters)
    server = ReusableThreadingHTTPServer((host, port), TrafficHandler)
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
) -> None:
    await asyncio.to_thread(run_receiver, host, port, url_filters)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MITM v4 receiver — accepts Yak/Yakit traffic events")
    p.add_argument("--host", default="127.0.0.1", help="Listen address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8085, help="Listen port (default: 8085)")
    p.add_argument("--url", action="append", default=None,
                   help="URL keyword filter; may be specified multiple times")
    return p.parse_args(argv)


def main() -> None:
    configure_logging()
    args = _parse_args()
    run_receiver(args.host, args.port, args.url or [])


if __name__ == "__main__":
    main()
