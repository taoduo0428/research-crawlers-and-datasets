from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import socket
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import certifi
import requests
import yaml
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - incomplete dependency installation
    curl_requests = None  # type: ignore[assignment]


LOGGER = logging.getLogger("drv_crawler")
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+")


_REQUEST_EXCEPTIONS: tuple[type[Exception], ...] = (requests.RequestException,)
if curl_requests is not None:
    _REQUEST_EXCEPTIONS += (curl_requests.RequestsError,)


class CrawlerError(RuntimeError):
    pass


class RobotsDeniedError(CrawlerError):
    pass


class BlockedResponseError(CrawlerError):
    def __init__(self, message: str, *, status_code: int, url: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class ResponseTooLargeError(CrawlerError):
    pass


class SessionRequiredError(CrawlerError):
    pass


@dataclass(slots=True)
class FetchedPage:
    requested_url: str
    final_url: str
    status_code: int
    content: bytes
    encoding: str

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")

    def soup(self) -> BeautifulSoup:
        # Parse decoded text so JavaScript strings such as `charset = 'gbk'`
        # cannot override the response encoding during BeautifulSoup detection.
        return BeautifulSoup(self.text, "html.parser")


class ProjectConfig:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with self.path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        self.data: dict[str, Any] = raw.get("crawler", raw)

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        if not isinstance(value, dict):
            raise CrawlerError(f"Config section crawler.{name} must be a mapping")
        return value

    def site(self, name: str) -> dict[str, Any]:
        sites = self.section("sites")
        value = sites.get(name)
        if not isinstance(value, dict):
            raise CrawlerError(f"Unknown site in config: {name}")
        return value

    @property
    def site_names(self) -> list[str]:
        return [
            name
            for name, value in self.section("sites").items()
            if isinstance(value, dict) and value.get("enabled", True)
        ]


def safe_url(url: str | None) -> str | None:
    if not url:
        return url
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def safe_error_message(message: str) -> str:
    return _URL_IN_TEXT_RE.sub(
        lambda match: safe_url(match.group(0)) or "",
        message,
    )


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", delete=False, dir=path.parent
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    for attempt in range(5):
        try:
            os.replace(temp_path, path)
            break
        except PermissionError:
            if attempt == 4:
                temp_path.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (2**attempt))


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="wb", delete=False, dir=path.parent) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    for attempt in range(5):
        try:
            os.replace(temp_path, path)
            break
        except PermissionError:
            if attempt == 4:
                temp_path.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (2**attempt))


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _ascii_system_temp_dir() -> Path:
    candidates = [Path(tempfile.gettempdir())]
    system_root = os.getenv("SystemRoot")
    if system_root:
        candidates.append(Path(system_root) / "Temp")
    for candidate in candidates:
        if not _is_ascii_path(candidate):
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        return candidate
    raise CrawlerError(
        "curl_cffi requires a writable ASCII system temporary directory for TLS CA data"
    )


def _curl_ca_bundle_path(project_path: Path) -> str:
    """Return a verified CA path that libcurl can open from Unicode projects."""
    source = Path(certifi.where()).resolve()
    project_root = project_path.resolve().parent
    if _is_ascii_path(project_root) and _is_ascii_path(source):
        return str(source)
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise CrawlerError(f"Could not read certifi CA bundle: {source}") from exc
    digest = hashlib.sha256(content).hexdigest()[:16]
    destination = _ascii_system_temp_dir() / f"drv-crawler-certifi-{digest}.pem"
    try:
        if not destination.exists() or destination.read_bytes() != content:
            atomic_write_bytes(destination, content)
    except OSError as exc:
        raise CrawlerError(
            "Could not prepare curl_cffi CA bundle in the system temporary directory"
        ) from exc
    return str(destination)


def _proxy_reachable(url: str, timeout: float) -> bool:
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_proxy(config: ProjectConfig) -> str | None:
    proxy = config.section("proxy")
    mode = os.getenv("CRAWLER_PROXY_MODE", str(proxy.get("mode", "auto"))).lower()
    configured = os.getenv(
        str(proxy.get("url_env", "CRAWLER_PROXY_URL")),
        str(proxy.get("default_url", "http://127.0.0.1:7897")),
    ).strip()
    if mode == "off" or not configured:
        return None
    reachable = _proxy_reachable(
        configured, float(proxy.get("probe_timeout_seconds", 1.5))
    )
    if reachable:
        LOGGER.info("Proxy enabled: %s", safe_url(configured))
        return configured
    if mode == "required":
        raise CrawlerError(f"Required proxy is unavailable: {safe_url(configured)}")
    LOGGER.warning("Proxy unavailable; auto mode falls back to direct traffic")
    return None


class RobotsCache:
    def __init__(
        self,
        session: Any,
        *,
        proxy_url: str | None,
        timeout: tuple[float, float],
        user_agent: str,
        enabled: bool,
    ) -> None:
        self.session = session
        self.proxy_url = proxy_url
        self.timeout = timeout
        self.user_agent = user_agent
        self.enabled = enabled
        self._cache: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._cache:
            robots_url = f"{root}/robots.txt"
            try:
                response = self.session.get(
                    robots_url,
                    timeout=self.timeout,
                    proxies=_requests_proxies(self.proxy_url),
                    allow_redirects=True,
                )
                if response.status_code == 200:
                    parser = RobotFileParser(robots_url)
                    parser.parse(response.text.splitlines())
                    self._cache[root] = parser
                else:
                    LOGGER.warning(
                        "robots.txt unavailable (%s): %s",
                        response.status_code,
                        safe_url(robots_url),
                    )
                    self._cache[root] = None
            except _REQUEST_EXCEPTIONS as exc:
                LOGGER.warning("robots.txt check failed for %s: %s", root, exc)
                self._cache[root] = None
        parser = self._cache[root]
        return True if parser is None else parser.can_fetch(self.user_agent, url)


def _requests_proxies(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def _retry_after_seconds(response: Any, maximum: float) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(maximum, max(0.0, float(raw)))
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(raw).timestamp() - time.time())
            return min(maximum, max(0.0, seconds))
        except (TypeError, ValueError, OverflowError):
            return None


class ResilientHttpClient:
    """Low-concurrency sticky HTTP client with bounded retries and robots checks."""

    def __init__(
        self, config: ProjectConfig, *, site_name: str | None = None
    ) -> None:
        self.config = config
        self.site_name = site_name
        site_config = config.site(site_name) if site_name else {}
        self.transport = str(
            site_config.get("http_transport", "requests")
        ).strip().lower()
        if self.transport not in {"requests", "curl_cffi"}:
            raise CrawlerError(
                f"Unsupported HTTP transport for {site_name or 'default'}: "
                f"{self.transport}"
            )
        timeout_config = config.section("timeout")
        self.timeout = (
            float(timeout_config.get("connect_timeout_seconds", 8)),
            float(timeout_config.get("read_timeout_seconds", 20)),
        )
        limits = config.section("limits")
        self.max_response_bytes = int(limits.get("max_response_bytes", 5_000_000))
        retry = config.section("retry")
        self.retry_times = int(retry.get("retry_times", 2))
        self.retry_codes = {int(code) for code in retry.get("retry_http_codes", [])}
        self.max_retry_after = float(retry.get("max_retry_after_seconds", 30))
        self.block_codes = {
            int(code)
            for code in config.section("anti_bot").get(
                "block_status_codes", [401, 403, 407, 418, 429]
            )
        }
        throttle = config.section("throttling")
        self.delay = float(
            site_config.get(
                "download_delay",
                throttle.get("download_delay", 1.5),
            )
        )
        if self.delay < 0:
            raise CrawlerError("download_delay must not be negative")
        self.randomize_delay = bool(
            site_config.get(
                "randomize_download_delay",
                throttle.get("randomize_download_delay", True),
            )
        )
        self.proxy_url = resolve_proxy(config)
        headers = config.section("headers")
        self.user_agent = os.getenv(
            str(headers.get("user_agent_env", "CRAWLER_USER_AGENT")),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        )
        if self.transport == "curl_cffi":
            if curl_requests is None:
                raise CrawlerError(
                    "curl_cffi transport is configured but the dependency is not installed"
                )
            impersonate = str(
                site_config.get("curl_impersonate", "chrome")
            ).strip()
            if not impersonate:
                raise CrawlerError("curl_impersonate must not be empty")
            self.session = curl_requests.Session(
                impersonate=impersonate,
                verify=_curl_ca_bundle_path(config.path),
            )
            LOGGER.info(
                "HTTP transport for %s: curl_cffi impersonate=%s",
                site_name,
                impersonate,
            )
        else:
            self.session = requests.Session()
        stable_headers = {
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": str(
                headers.get("accept_language", "zh-CN,zh;q=0.9,en;q=0.8")
            ),
        }
        if self.transport == "requests":
            stable_headers["User-Agent"] = self.user_agent
            stable_headers["Connection"] = "keep-alive"
        self.session.headers.update(stable_headers)
        self.robots = RobotsCache(
            self.session,
            proxy_url=self.proxy_url,
            timeout=self.timeout,
            user_agent=self.user_agent,
            enabled=bool(config.data.get("obey_robots_txt", True)),
        )
        self._last_request_at: dict[str, float] = {}

    def close(self) -> None:
        self.session.close()

    def load_storage_state(
        self, path: str | Path, *, allowed_domains: Iterable[str]
    ) -> int:
        """Import domain-scoped browser cookies without logging their values."""
        state_path = Path(path)
        if not state_path.exists():
            return 0
        allowed = {domain.lower().lstrip(".") for domain in allowed_domains}
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CrawlerError(f"Invalid storage-state file: {state_path}") from exc
        imported = 0
        for item in payload.get("cookies", []):
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain", "")).lower().lstrip(".")
            if not domain or not any(
                domain == allowed_domain or domain.endswith(f".{allowed_domain}")
                for allowed_domain in allowed
            ):
                continue
            name = item.get("name")
            value = item.get("value")
            if not name or value is None:
                continue
            expires = item.get("expires")
            if isinstance(expires, (int, float)) and expires > 0 and expires <= time.time():
                continue
            self.session.cookies.set(
                str(name),
                str(value),
                domain=str(item.get("domain") or domain),
                path=str(item.get("path") or "/"),
                secure=bool(item.get("secure", False)),
            )
            imported += 1
        LOGGER.info("Imported %s domain-scoped browser cookies", imported)
        return imported

    def __enter__(self) -> ResilientHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ensure_allowed(self, url: str) -> None:
        if not self.robots.allowed(url):
            raise RobotsDeniedError(f"robots.txt disallows URL: {safe_url(url)}")

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        previous = self._last_request_at.get(host)
        if previous is not None:
            minimum = self.delay
            if self.randomize_delay:
                minimum *= random.uniform(0.8, 1.25)
            remaining = minimum - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at[host] = time.monotonic()

    def get(
        self,
        url: str,
        *,
        referer: str | None = None,
        headers: Mapping[str, str] | None = None,
        obey_robots: bool = True,
    ) -> FetchedPage:
        if obey_robots:
            self.ensure_allowed(url)
        request_headers = dict(headers or {})
        if referer:
            request_headers.setdefault("Referer", referer)
        attempts = self.retry_times + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            self._throttle(url)
            try:
                response = self.session.get(
                    url,
                    headers=request_headers or None,
                    timeout=self.timeout,
                    proxies=_requests_proxies(self.proxy_url),
                    allow_redirects=True,
                    stream=True,
                )
            except _REQUEST_EXCEPTIONS as exc:
                last_error = exc
                if attempt >= self.retry_times:
                    break
                time.sleep(min(8.0, (2**attempt) + random.random()))
                continue

            if response.status_code in self.block_codes and response.status_code != 429:
                response.close()
                raise BlockedResponseError(
                    f"Blocked response {response.status_code} for {safe_url(url)}",
                    status_code=response.status_code,
                    url=url,
                )
            if response.status_code in self.retry_codes and attempt < self.retry_times:
                wait = _retry_after_seconds(response, self.max_retry_after)
                if response.status_code == 429 and wait is None:
                    response.close()
                    raise BlockedResponseError(
                        f"HTTP 429 without Retry-After; stop domain run: {safe_url(url)}",
                        status_code=429,
                        url=url,
                    )
                response.close()
                if wait is None:
                    wait = min(8.0, (2**attempt) + random.random())
                time.sleep(wait)
                continue
            if response.status_code >= 400:
                status = response.status_code
                response.close()
                raise BlockedResponseError(
                    f"HTTP {status} for {safe_url(url)}", status_code=status, url=url
                )

            declared_length = int(response.headers.get("Content-Length", "0") or 0)
            if declared_length > self.max_response_bytes:
                response.close()
                raise ResponseTooLargeError(
                    f"Response exceeds {self.max_response_bytes} bytes: {safe_url(url)}"
                )
            chunks: list[bytes] = []
            total = 0
            content_chunks = (
                response.iter_content()
                if self.transport == "curl_cffi"
                else response.iter_content(chunk_size=65_536)
            )
            try:
                for chunk in content_chunks:
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        response.close()
                        raise ResponseTooLargeError(
                            f"Response exceeds {self.max_response_bytes} bytes: {safe_url(url)}"
                        )
                    chunks.append(chunk)
            except _REQUEST_EXCEPTIONS as exc:
                response.close()
                last_error = exc
                if attempt >= self.retry_times:
                    break
                time.sleep(min(8.0, (2**attempt) + random.random()))
                continue
            content = b"".join(chunks)
            page = FetchedPage(
                requested_url=url,
                final_url=response.url,
                status_code=response.status_code,
                content=content,
                encoding=response.encoding or "utf-8",
            )
            response.close()
            return page
        raise CrawlerError(f"Request failed after {attempts} attempts: {last_error}")
