from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .core import ProjectConfig, ResilientHttpClient, safe_error_message, safe_url


LOGGER = logging.getLogger(__name__)


class EpwkBrowserRenderError(RuntimeError):
    pass


class EpwkBrowserLimitReached(EpwkBrowserRenderError):
    pass


class EpwkPublicationRenderer:
    """Bounded, reusable EPWK detail renderer backed by the crawler HTTP session."""

    HARD_MAX_DETAILS = 500
    HARD_NAVIGATION_TIMEOUT_MS = 15_000
    HARD_WAIT_TIMEOUT_MS = 10_000
    HARD_MAX_RESPONSE_BYTES = 6_000_000
    HARD_MAX_ROUTED_REQUESTS = 80
    MAX_DOCUMENT_REQUESTS = 3

    def __init__(
        self,
        config: ProjectConfig,
        client: ResilientHttpClient,
        site_config: dict[str, Any],
    ) -> None:
        if client.transport != "curl_cffi":
            raise EpwkBrowserRenderError(
                "EPWK browser fallback requires the curl_cffi transport"
            )
        self.config = config
        self.client = client
        self.site_config = site_config
        self.max_details = self._bounded_int(
            "publication_browser_max_details",
            default=50,
            minimum=0,
            maximum=self.HARD_MAX_DETAILS,
        )
        playwright_config = config.section("playwright")
        self.navigation_timeout_ms = min(
            self._positive_int(
                site_config.get(
                    "publication_browser_navigation_timeout_ms",
                    playwright_config.get("navigation_timeout_ms", 15_000),
                ),
                "publication_browser_navigation_timeout_ms",
            ),
            self.HARD_NAVIGATION_TIMEOUT_MS,
        )
        self.wait_timeout_ms = min(
            self._positive_int(
                site_config.get("publication_browser_wait_timeout_ms", 5_000),
                "publication_browser_wait_timeout_ms",
            ),
            self.HARD_WAIT_TIMEOUT_MS,
        )
        client_response_limit = min(
            int(client.max_response_bytes), self.HARD_MAX_RESPONSE_BYTES
        )
        self.max_response_bytes = self._bounded_int(
            "publication_browser_max_response_bytes",
            default=min(client_response_limit, 2_000_000),
            minimum=1,
            maximum=client_response_limit,
        )
        configured_request_limit = config.section("limits").get(
            "max_captured_responses", 40
        )
        self.max_routed_requests = min(
            self._positive_int(
                site_config.get(
                    "publication_browser_max_routed_requests",
                    configured_request_limit,
                ),
                "publication_browser_max_routed_requests",
            ),
            self.HARD_MAX_ROUTED_REQUESTS,
        )
        self.request_timeout_seconds = min(
            max(float(client.timeout[1]), 0.5), 8.0
        )
        self.impersonate = str(site_config.get("curl_impersonate", "chrome"))

        self.rendered_count = 0
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._deadline = 0.0
        self._routed_requests = 0
        self._document_requests = 0
        self._route_error: EpwkBrowserRenderError | None = None

    @staticmethod
    def _positive_int(value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise EpwkBrowserRenderError(f"{name} must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise EpwkBrowserRenderError(f"{name} must be an integer") from exc
        if parsed < 1:
            raise EpwkBrowserRenderError(f"{name} must be positive")
        return parsed

    def _bounded_int(
        self,
        name: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        value = self.site_config.get(name, default)
        if isinstance(value, bool):
            raise EpwkBrowserRenderError(f"{name} must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise EpwkBrowserRenderError(f"{name} must be an integer") from exc
        return min(max(parsed, minimum), maximum)

    def _ensure_started(self) -> None:
        if self._page is not None:
            return
        playwright_config = self.config.section("playwright")
        if not bool(playwright_config.get("enabled", True)):
            raise EpwkBrowserRenderError("Playwright is disabled")

        self._playwright = sync_playwright().start()
        launch_options: dict[str, Any] = {"headless": True}
        channel = str(playwright_config.get("browser_channel", "")).strip()
        if channel:
            launch_options["channel"] = channel
        if self.client.proxy_url:
            launch_options["proxy"] = {"server": self.client.proxy_url}
        self._browser = self._playwright.chromium.launch(**launch_options)

        context_options: dict[str, Any] = {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        storage_state = self.site_config.get("storage_state")
        if storage_state and Path(str(storage_state)).is_file():
            try:
                payload = json.loads(
                    Path(str(storage_state)).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise EpwkBrowserRenderError(
                    "EPWK browser storage state is invalid"
                ) from exc

            def is_epwk_host(value: Any) -> bool:
                host = str(value or "").lower().lstrip(".")
                return host == "epwk.com" or host.endswith(".epwk.com")

            context_options["storage_state"] = {
                "cookies": [
                    item
                    for item in payload.get("cookies", [])
                    if isinstance(item, dict)
                    and is_epwk_host(item.get("domain"))
                ],
                "origins": [
                    item
                    for item in payload.get("origins", [])
                    if isinstance(item, dict)
                    and is_epwk_host(
                        urlparse(str(item.get("origin") or "")).hostname
                    )
                ],
            }
        accept_language = self.config.section("headers").get("accept_language")
        if accept_language:
            context_options["extra_http_headers"] = {
                "Accept-Language": str(accept_language)
            }
        self._context = self._browser.new_context(**context_options)
        self._page = self._context.new_page()
        self._page.route("**/*", self._route_request)

    def _read_response_body(self, response: Any, url: str) -> bytes:
        declared = int(response.headers.get("Content-Length", "0") or 0)
        if declared > self.max_response_bytes:
            raise EpwkBrowserRenderError(
                f"EPWK routed response exceeds {self.max_response_bytes} bytes: "
                f"{safe_url(url)}"
            )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65_536):
            total += len(chunk)
            if total > self.max_response_bytes:
                raise EpwkBrowserRenderError(
                    f"EPWK routed response exceeds {self.max_response_bytes} bytes: "
                    f"{safe_url(url)}"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _route_request(self, route: Route) -> None:
        request = route.request
        host = (urlparse(request.url).hostname or "").lower()
        if host != "task.epwk.com":
            route.continue_()
            return
        if self._route_error is not None:
            route.abort("failed")
            return

        self._routed_requests += 1
        if request.resource_type == "document":
            self._document_requests += 1
        if self._routed_requests > self.max_routed_requests:
            self._route_error = EpwkBrowserRenderError(
                "EPWK routed request limit reached"
            )
            route.abort("failed")
            return
        if self._document_requests > self.MAX_DOCUMENT_REQUESTS:
            self._route_error = EpwkBrowserRenderError(
                "EPWK document redirect limit reached"
            )
            route.abort("failed")
            return

        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self._route_error = EpwkBrowserRenderError(
                "EPWK browser render deadline reached"
            )
            route.abort("timedout")
            return

        response = None
        try:
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower()
                not in {
                    "host",
                    "cookie",
                    "authorization",
                    "proxy-authorization",
                    "content-length",
                    "connection",
                    "accept-encoding",
                }
            }
            request_options: dict[str, Any] = {
                "method": request.method,
                "url": request.url,
                "headers": headers,
                "data": request.post_data_buffer,
                "impersonate": self.impersonate,
                "timeout": min(self.request_timeout_seconds, max(remaining, 0.5)),
                "allow_redirects": False,
                "stream": True,
            }
            if self.client.proxy_url:
                request_options["proxy"] = self.client.proxy_url
            response = self.client.session.request(**request_options)
            body = self._read_response_body(response, request.url)
            if response.status_code in self.client.block_codes or response.status_code >= 400:
                raise EpwkBrowserRenderError(
                    f"EPWK routed HTTP {response.status_code}: {safe_url(request.url)}"
                )
            response_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower()
                not in {
                    "content-encoding",
                    "content-length",
                    "transfer-encoding",
                    "connection",
                    "set-cookie",
                    "alt-svc",
                }
            }
            route.fulfill(
                status=response.status_code,
                headers=response_headers,
                body=body,
            )
        except Exception as exc:
            if isinstance(exc, EpwkBrowserRenderError):
                self._route_error = exc
            else:
                self._route_error = EpwkBrowserRenderError(
                    safe_error_message(str(exc))[:500]
                )
            try:
                route.abort("failed")
            except PlaywrightError:
                pass
        finally:
            if response is not None:
                response.close()

    def render(self, url: str, *, referer: str | None = None) -> str:
        if self.rendered_count >= self.max_details:
            raise EpwkBrowserLimitReached(
                f"EPWK browser detail limit reached: {self.max_details}"
            )
        self._ensure_started()
        assert self._page is not None

        self.rendered_count += 1
        self._deadline = time.monotonic() + (
            self.navigation_timeout_ms / 1000.0
        )
        self._routed_requests = 0
        self._document_requests = 0
        self._route_error = None
        if referer:
            self._page.set_extra_http_headers({"Referer": referer})
        else:
            self._page.set_extra_http_headers({})

        try:
            response = self._page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
        except PlaywrightTimeoutError:
            response = None
        except PlaywrightError as exc:
            if self._route_error is not None:
                raise self._route_error from exc
            raise EpwkBrowserRenderError(
                safe_error_message(str(exc))[:500]
            ) from exc
        if self._route_error is not None:
            raise self._route_error
        if response is not None and response.status >= 400:
            raise EpwkBrowserRenderError(
                f"EPWK browser HTTP {response.status}: {safe_url(url)}"
            )

        remaining_ms = int(max(0.0, self._deadline - time.monotonic()) * 1000)
        if remaining_ms > 0:
            try:
                self._page.wait_for_function(
                    r"""() => {
                        const selector = [
                            '.task-progress-item span.step_on font',
                            '.task-progress-item span.step_off font',
                            '.task-user-info-action > span:first-child > .dib_vm'
                        ].join(',');
                        return [...document.querySelectorAll(selector)].some(
                            node => /^20\d{2}[.-]\d{2}[.-]\d{2}$/.test(
                                (node.textContent || '').trim()
                            )
                        );
                    }""",
                    timeout=min(self.wait_timeout_ms, remaining_ms),
                )
            except PlaywrightTimeoutError:
                pass
        if self._route_error is not None:
            raise self._route_error

        html = self._page.content()
        if len(html.encode("utf-8")) > self.max_response_bytes:
            raise EpwkBrowserRenderError(
                f"EPWK rendered HTML exceeds {self.max_response_bytes} bytes: "
                f"{safe_url(url)}"
            )
        return html

    def close(self) -> None:
        resources = (
            (self._page, "page"),
            (self._context, "context"),
            (self._browser, "browser"),
            (self._playwright, "playwright"),
        )
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        for resource, name in resources:
            if resource is None:
                continue
            try:
                if name == "playwright":
                    resource.stop()
                else:
                    resource.close()
            except Exception as exc:
                LOGGER.warning(
                    "Could not close EPWK browser %s: %s",
                    name,
                    safe_error_message(str(exc))[:300],
                )
