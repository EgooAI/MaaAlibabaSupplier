"""Fetches a chat image from the platform file service for the image endpoint.

The stored URL (``clouddisk.alibaba.com/file/redirectFileUrl.htm``) answers with a
302 to a short-lived signed CDN URL, so every fetch walks the redirect chain
again. Observed failure modes: a missing or wrong signature answers 200 with an
empty body, and a removed file redirects to a generic "expired" placeholder image.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

ALLOWED_HOSTS = frozenset({"clouddisk.alibaba.com", "s.alicdn.com"})
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
MAX_BYTES = 20 * 1024 * 1024
MAX_HOPS = 3
TIMEOUT_SECONDS = 15
EXPIRED_PLACEHOLDER = "/expiredfile.png"

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_slots = threading.BoundedSemaphore(4)


class ImageUnavailable(Exception):
    """The platform no longer serves this file."""


class ImageFetchError(Exception):
    """The platform could not be reached or answered something unusable."""


@dataclass(frozen=True)
class FetchedImage:
    content_type: str
    body: bytes


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def open_url(url: str) -> Response:
    """One HTTP GET without following redirects and without any cookies."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return Response(response.status, _lower(response.headers), response.read(MAX_BYTES + 1))
    except urllib.error.HTTPError as exc:
        with exc:
            return Response(exc.code, _lower(exc.headers), b"")
    except (urllib.error.URLError, OSError) as exc:
        raise ImageFetchError(type(exc).__name__) from None


def fetch_image(url: str, *, opener: Callable[[str], Response] = open_url) -> FetchedImage:
    with _slots:
        return _walk(url, opener)


def _walk(url: str, opener: Callable[[str], Response]) -> FetchedImage:
    for _ in range(MAX_HOPS):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ImageFetchError("host_not_allowed")
        response = opener(url)
        if response.status in _REDIRECTS:
            target = urljoin(url, response.headers.get("location", ""))
            if urlsplit(target).path.lower().endswith(EXPIRED_PLACEHOLDER):
                raise ImageUnavailable("expired")
            url = target
            continue
        return _image(response)
    raise ImageFetchError("too_many_redirects")


def _image(response: Response) -> FetchedImage:
    if response.status != 200:
        raise ImageFetchError(f"status_{response.status}")
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    # A rejected signature answers 200 with an empty body and no content type.
    if not response.body:
        raise ImageUnavailable("empty")
    if content_type not in IMAGE_TYPES:
        raise ImageFetchError("not_an_image")
    if len(response.body) > MAX_BYTES:
        raise ImageFetchError("too_large")
    return FetchedImage(content_type, response.body)


def _lower(headers) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {key.lower(): value for key, value in headers.items()}


__all__ = ["FetchedImage", "ImageFetchError", "ImageUnavailable", "fetch_image"]
