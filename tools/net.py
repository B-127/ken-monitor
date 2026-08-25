"""Hardened HTTP fetching.

Every request this project makes goes through `fetch`. The rules:

  * Only hosts in schema.ALLOWED_HOSTS are ever contacted. A URL built from
    feed content can therefore never make the collector reach an internal
    address, a cloud metadata endpoint, or an attacker's server.
  * Redirects are not followed automatically. Each hop is re-validated against
    the allowlist and the hop count is capped.
  * Responses are read in bounded chunks and abandoned past the size cap, so a
    hostile or broken endpoint cannot exhaust memory.
  * Requests are rate-limited, retried with backoff, and time-limited.

Nothing here parses HTML or fetches article bodies. The project reads
headlines and links from structured feeds only.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlsplit

import requests

from . import schema


class FetchError(Exception):
    pass


_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = schema.HTTP_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _resolves_to_public_ip(host: str) -> bool:
    """Reject hosts that resolve to private, loopback, link-local or reserved
    space. The allowlist already covers us; this is defence in depth against
    DNS rebinding of an allowlisted name."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve host '{host}': {exc}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast):
            return False
    return True


def validate_url(url: str) -> str:
    """Return the URL if it is safe to request, else raise."""
    if len(url) > schema.MAX_URL_CHARS:
        raise FetchError("URL exceeds length cap.")
    parts = urlsplit(url)
    if parts.scheme not in schema.ALLOWED_URL_SCHEMES:
        raise FetchError(f"scheme '{parts.scheme}' not permitted.")
    host = (parts.hostname or "").lower()
    if host not in schema.ALLOWED_HOSTS:
        raise FetchError(f"host '{host}' is not on the allowlist.")
    if not _resolves_to_public_ip(host):
        raise FetchError(f"host '{host}' resolves to a non-public address.")
    return url


def is_safe_public_url(url: str) -> bool:
    """Scheme/length check for URLs we *store* (article links).

    These are not fetched, so no allowlist applies — but they are rendered as
    hrefs in the dashboard, and a javascript: or data: URL arriving inside a
    feed must never reach the DOM.
    """
    if not url or len(url) > schema.MAX_URL_CHARS:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in schema.ALLOWED_URL_SCHEMES and bool(parts.netloc)


def _read_capped(response: requests.Response) -> bytes:
    buf = bytearray()
    for chunk in response.iter_content(chunk_size=65_536):
        buf.extend(chunk)
        if len(buf) > schema.HTTP_MAX_BYTES:
            raise FetchError("response exceeded size cap.")
    return bytes(buf)


def fetch(url: str, *, accept: str = "*/*") -> bytes:
    """GET an allowlisted URL and return the body, or raise FetchError."""
    validate_url(url)
    headers = {"User-Agent": schema.USER_AGENT, "Accept": accept}
    last_exc: Exception | None = None

    for attempt in range(1, schema.HTTP_RETRIES + 1):
        current = url
        try:
            for _ in range(schema.HTTP_MAX_REDIRECTS + 1):
                _throttle()
                resp = requests.get(
                    current,
                    headers=headers,
                    timeout=schema.HTTP_TIMEOUT_SECONDS,
                    allow_redirects=False,
                    stream=True,
                )
                if resp.is_redirect or resp.is_permanent_redirect:
                    location = resp.headers.get("Location", "")
                    resp.close()
                    if not location:
                        raise FetchError("redirect without a Location header.")
                    current = requests.compat.urljoin(current, location)
                    validate_url(current)     # re-check every hop
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code} from {current}")
                try:
                    return _read_capped(resp)
                finally:
                    resp.close()
            raise FetchError("too many redirects.")
        except (requests.RequestException, FetchError) as exc:
            last_exc = exc
            if attempt < schema.HTTP_RETRIES:
                time.sleep(schema.HTTP_BACKOFF_SECONDS * attempt)

    raise FetchError(f"failed after {schema.HTTP_RETRIES} attempts: {last_exc}")
