from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import json
import logging
import os
from pathlib import Path
import random
import socket
import time
from typing import Any
from urllib.parse import urlparse

import requests

from .errors import AccessDeniedError, ConfigurationError, NetworkError


LOGGER = logging.getLogger(__name__)
DEFAULT_PROXY_URL = "http://127.0.0.1:7897"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class HttpPage:
    url: str
    final_url: str
    status_code: int
    text: str


def parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            cookies[name] = value
    return cookies


def load_cookie_file(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Cannot read Cookie/storage-state file: {path}") from exc

    if isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
        payload = payload["cookies"]
    if isinstance(payload, dict):
        return [{"name": str(name), "value": str(value)} for name, value in payload.items()]
    if isinstance(payload, list):
        output: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            output.append(item)
        return output
    raise ConfigurationError(f"Unsupported Cookie/storage-state structure: {path}")


def apply_cookies(
    session: requests.Session,
    *,
    cookie_file: Path | None = None,
    cookie_header: str | None = None,
) -> int:
    count = 0
    if cookie_header:
        for name, value in parse_cookie_header(cookie_header).items():
            session.cookies.set(name, value, domain=".liepin.com", path="/")
            count += 1
    if cookie_file:
        for item in load_cookie_file(cookie_file):
            expires = item.get("expires", item.get("expirationDate"))
            if isinstance(expires, (int, float)) and expires > 0 and expires <= time.time():
                continue
            name = str(item["name"])
            value = str(item.get("value", ""))
            domain = str(item.get("domain") or ".liepin.com")
            path = str(item.get("path") or "/")
            session.cookies.set(name, value, domain=domain, path=path)
            count += 1
    return count


def redact_proxy_url(proxy_url: str) -> str:
    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        return "<invalid-proxy-url>"
    port = f":{parsed.port}" if parsed.port else ""
    scheme = parsed.scheme or "http"
    return f"{scheme}://{parsed.hostname}{port}"


def _proxy_is_reachable(proxy_url: str, timeout_seconds: float = 0.4) -> bool:
    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def resolve_proxy(proxy_policy: str, proxy_url: str | None) -> str | None:
    if proxy_policy == "direct":
        return None
    candidate = proxy_url or os.getenv("CRAWLER_PROXY_URL") or DEFAULT_PROXY_URL
    if _proxy_is_reachable(candidate):
        return candidate
    if proxy_policy == "required":
        raise ConfigurationError(
            f"Required proxy is unavailable: {redact_proxy_url(candidate)}"
        )
    LOGGER.info(
        "Proxy unavailable; falling back to direct connection: %s",
        redact_proxy_url(candidate),
    )
    return None


def _retry_after_seconds(value: str | None, *, cap: float = 60.0) -> float | None:
    if not value:
        return None
    try:
        return min(max(float(value), 0.0), cap)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
            seconds = moment.timestamp() - time.time()
            return min(max(seconds, 0.0), cap)
        except (TypeError, ValueError, OverflowError):
            return None


class LiepinHttpClient:
    def __init__(
        self,
        *,
        proxy_policy: str = "auto",
        proxy_url: str | None = None,
        cookie_file: Path | None = None,
        cookie_header: str | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 20.0,
        retries: int = 2,
        backoff_base: float = 1.0,
        session: requests.Session | None = None,
    ) -> None:
        if proxy_policy not in {"auto", "direct", "required"}:
            raise ConfigurationError(f"Unsupported proxy policy: {proxy_policy}")
        if retries < 0 or retries > 5:
            raise ConfigurationError("retries must be between 0 and 5")
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.timeout = (connect_timeout, read_timeout)
        self.retries = retries
        self.backoff_base = backoff_base
        self.proxy_url = resolve_proxy(proxy_policy, proxy_url)
        if self.proxy_url:
            self.session.proxies.update(
                {"http": self.proxy_url, "https": self.proxy_url}
            )
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )
        cookie_count = apply_cookies(
            self.session,
            cookie_file=cookie_file,
            cookie_header=cookie_header,
        )
        if cookie_count:
            LOGGER.info("Loaded %d Liepin cookies without logging their values", cookie_count)

    def get(self, url: str, *, referer: str | None = None) -> HttpPage:
        retryable_statuses = {429, 500, 502, 503, 504}
        last_error: Exception | None = None
        headers = {"Referer": referer} if referer else None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, headers=headers, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                wait = self.backoff_base * (2**attempt) + random.uniform(0.0, 0.35)
                LOGGER.warning(
                    "Transient Liepin request failure (%d/%d); retrying in %.2fs",
                    attempt + 1,
                    self.retries + 1,
                    wait,
                )
                time.sleep(wait)
                continue

            if response.status_code in {401, 403, 407, 418}:
                raise AccessDeniedError(
                    f"Liepin rejected the session with HTTP {response.status_code}: {url}"
                )
            if response.status_code in retryable_statuses:
                if attempt >= self.retries:
                    raise NetworkError(
                        f"Liepin returned HTTP {response.status_code} after bounded retries: {url}"
                    )
                wait = _retry_after_seconds(response.headers.get("Retry-After"))
                if wait is None:
                    wait = self.backoff_base * (2**attempt) + random.uniform(0.0, 0.35)
                LOGGER.warning(
                    "Liepin returned HTTP %d; retrying in %.2fs",
                    response.status_code,
                    wait,
                )
                time.sleep(wait)
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise NetworkError(
                    f"Unexpected Liepin HTTP {response.status_code}: {url}"
                ) from exc

            text = response.content.decode("utf-8", errors="replace")
            return HttpPage(url, response.url, response.status_code, text)

        raise NetworkError(f"Liepin request failed after bounded retries: {url}") from last_error
