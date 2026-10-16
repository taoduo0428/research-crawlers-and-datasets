from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import socket
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Error as PlaywrightError, sync_playwright
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class CrawlerRuntimeError(RuntimeError):
    pass


class CappedRetry(Retry):
    """urllib3 Retry with a hard upper bound for server-directed sleeps."""

    max_retry_after_seconds: float = 30.0

    def new(self, **kw: Any) -> CappedRetry:
        retry = super().new(**kw)
        retry.max_retry_after_seconds = self.max_retry_after_seconds
        return retry

    def get_retry_after(self, response: Any) -> float | None:
        seconds = super().get_retry_after(response)
        if seconds is None:
            return None
        return min(float(seconds), self.max_retry_after_seconds)


@dataclass(frozen=True)
class ProxyDecision:
    mode: str
    configured_url: str | None
    effective_url: str | None
    port_reachable: bool
    fallback_direct: bool
    reason: str


def redact_proxy_url(value: str | None) -> str | None:
    if not value:
        return value
    parsed = urlparse(value)
    if not parsed.username and not parsed.password:
        return value
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    netloc = host + ((":" + str(parsed.port)) if parsed.port else "")
    return parsed._replace(netloc=netloc).geturl()


def public_proxy_decision(decision: ProxyDecision) -> dict[str, Any]:
    value = asdict(decision)
    value["configured_url"] = redact_proxy_url(decision.configured_url)
    value["effective_url"] = redact_proxy_url(decision.effective_url)
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


class CrawlerRuntime:
    def __init__(self, config_path: str | Path = "crawler.config.yaml") -> None:
        self.config_path = Path(config_path).resolve()
        if not self.config_path.is_file():
            raise CrawlerRuntimeError(f"config file not found: {self.config_path}")
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        crawler = raw.get("crawler", raw)
        if not isinstance(crawler, dict):
            raise CrawlerRuntimeError("crawler config root must be a mapping")
        self.config: dict[str, Any] = crawler
        self.log = logging.getLogger("crawler.runtime")
        self._proxy_cache: ProxyDecision | None = None
        self._user_agent_cache: str | None = None

    def section(self, name: str) -> dict[str, Any]:
        value = self.config.get(name, {})
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise CrawlerRuntimeError(f"crawler.{name} must be a mapping")
        return value

    @property
    def timeout_seconds(self) -> int:
        value = int(self.section("timeout").get("download_timeout_seconds", 30))
        return max(3, min(value, 120))

    @property
    def storage_state_path(self) -> Path:
        configured = self.section("cookies").get(
            "storage_state", ".crawler-state/storage-state.json"
        )
        return (self.config_path.parent / str(configured)).resolve()

    @property
    def storage_meta_path(self) -> Path:
        return self.storage_state_path.with_name(self.storage_state_path.stem + ".meta.json")

    def proxy_decision(self, refresh: bool = False) -> ProxyDecision:
        if self._proxy_cache is not None and not refresh:
            return self._proxy_cache
        cfg = self.section("proxy")
        configured_mode = cfg.get("mode", "auto")
        # YAML 1.1 parses an unquoted `off` as boolean false.
        if configured_mode is False:
            configured_mode = "off"
        mode = os.getenv(
            "CRAWLER_PROXY_MODE", str(configured_mode)
        ).strip().lower()
        if mode not in {"auto", "required", "off"}:
            raise CrawlerRuntimeError(f"invalid proxy mode: {mode}")
        env_name = str(cfg.get("url_env", "CRAWLER_PROXY_URL"))
        configured_url = os.getenv(env_name) or cfg.get("default_url")
        configured_url = str(configured_url).strip() if configured_url else None
        if mode == "off":
            decision = ProxyDecision(mode, configured_url, None, False, False, "proxy disabled")
            self._proxy_cache = decision
            return decision
        if not configured_url:
            if mode == "required":
                raise CrawlerRuntimeError("proxy is required but no proxy URL is configured")
            decision = ProxyDecision(mode, None, None, False, True, "proxy URL missing")
            self._proxy_cache = decision
            return decision
        parsed = urlparse(configured_url)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
            raise CrawlerRuntimeError("proxy URL must use http, https, or socks5 and include a host")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        probe_timeout = max(0.2, min(float(cfg.get("probe_timeout_seconds", 1.5)), 5.0))
        reachable = False
        error: Exception | None = None
        try:
            with socket.create_connection((parsed.hostname, port), timeout=probe_timeout):
                reachable = True
        except OSError as exc:
            error = exc
        if reachable:
            decision = ProxyDecision(mode, configured_url, configured_url, True, False, "proxy port reachable")
        elif mode == "required":
            raise CrawlerRuntimeError(f"required proxy is unavailable: {configured_url}: {error}")
        else:
            decision = ProxyDecision(mode, configured_url, None, False, True, f"direct fallback: {error}")
        self.log.info("proxy decision: %s", public_proxy_decision(decision))
        self._proxy_cache = decision
        return decision

    def _load_json(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CrawlerRuntimeError(f"invalid state file {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise CrawlerRuntimeError(f"state file root must be an object: {path}")
        return value

    def _launch_options(self) -> dict[str, Any]:
        decision = self.proxy_decision()
        options: dict[str, Any] = {"headless": True}
        if decision.effective_url:
            options["proxy"] = {"server": decision.effective_url}
        return options

    @staticmethod
    def _normalized_user_agent(browser: Browser) -> str:
        context = browser.new_context()
        try:
            page = context.new_page()
            return page.evaluate("navigator.userAgent").replace("HeadlessChrome/", "Chrome/")
        finally:
            context.close()

    def browser_user_agent(self) -> str:
        if self._user_agent_cache:
            return self._user_agent_cache
        headers_cfg = self.section("headers")
        env_name = str(headers_cfg.get("user_agent_env", "CRAWLER_USER_AGENT"))
        configured = os.getenv(env_name)
        if configured:
            self._user_agent_cache = configured.strip()
            return self._user_agent_cache
        meta = self._load_json(self.storage_meta_path)
        if meta and isinstance(meta.get("user_agent"), str) and meta["user_agent"].strip():
            self._user_agent_cache = meta["user_agent"].strip()
            return self._user_agent_cache
        if not bool(headers_cfg.get("copy_user_agent_from_playwright", True)):
            raise CrawlerRuntimeError(
                "no user agent configured; set CRAWLER_USER_AGENT or enable copy_user_agent_from_playwright"
            )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**self._launch_options())
            try:
                self._user_agent_cache = self._normalized_user_agent(browser)
            finally:
                browser.close()
        atomic_write_json(
            self.storage_meta_path,
            {"version": 1, "user_agent": self._user_agent_cache, "saved_at": time.time()},
        )
        return self._user_agent_cache

    def _copy_cookies_to_session(self, session: requests.Session) -> int:
        state = self._load_json(self.storage_state_path)
        if not state:
            return 0
        copied = 0
        now = time.time()
        for cookie in state.get("cookies", []):
            if not isinstance(cookie, dict) or not cookie.get("name"):
                continue
            raw_expires = cookie.get("expires")
            expires = int(raw_expires) if isinstance(raw_expires, (int, float)) and raw_expires > 0 else None
            if expires is not None and expires <= now:
                continue
            domain = str(cookie.get("domain", "")).lstrip(".") or None
            kwargs: dict[str, Any] = {
                "path": cookie.get("path") or "/",
                "secure": bool(cookie.get("secure", False)),
                "expires": expires,
                "rest": {
                    "HttpOnly": bool(cookie.get("httpOnly", False)),
                    "SameSite": cookie.get("sameSite"),
                },
            }
            if domain:
                kwargs["domain"] = domain
            session.cookies.set(cookie["name"], cookie.get("value", ""), **kwargs)
            copied += 1
        return copied

    def build_session(self) -> requests.Session:
        retry_cfg = self.section("retry")
        retries = max(0, min(int(retry_cfg.get("retry_times", 3)), 8))
        retry_codes = tuple(
            int(code)
            for code in retry_cfg.get("retry_http_codes", [408, 429, 500, 502, 503, 504])
            if int(code) not in {401, 403, 407, 418}
        )
        retry = CappedRetry(
            total=retries,
            connect=min(retries, 3),
            read=min(retries, 3),
            status=retries,
            backoff_factor=0.25,
            status_forcelist=retry_codes,
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        retry.max_retry_after_seconds = max(
            0.0,
            min(float(retry_cfg.get("max_retry_after_seconds", 30)), 300.0),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        session = requests.Session()
        session.trust_env = False
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        headers_cfg = self.section("headers")
        session.headers.update(
            {
                "User-Agent": self.browser_user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": str(
                    headers_cfg.get("accept_language", "zh-CN,zh;q=0.9,en;q=0.8")
                ),
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )
        decision = self.proxy_decision()
        if decision.effective_url:
            session.proxies.update(
                {"http": decision.effective_url, "https": decision.effective_url}
            )
        self._copy_cookies_to_session(session)
        return session

    @staticmethod
    def _bounded_wait_seconds(value: float | int | None, maximum: float = 120.0) -> float:
        try:
            numeric = float(value or 0)
        except (TypeError, ValueError) as exc:
            raise CrawlerRuntimeError("wait seconds must be numeric") from exc
        if numeric < 0:
            raise CrawlerRuntimeError("wait seconds must be non-negative")
        return min(numeric, maximum)

    @staticmethod
    def _safe_url(value: str) -> str:
        parsed = urlparse(value)
        return parsed._replace(query="", fragment="").geturl()

    @staticmethod
    def _json_summary(value: Any) -> dict[str, Any]:
        summary: dict[str, Any] = {"type": type(value).__name__}
        if isinstance(value, dict):
            summary["keys"] = list(value.keys())[:30]
            lists: dict[str, int] = {}
            for key, child in value.items():
                if isinstance(child, list):
                    lists[str(key)] = len(child)
                elif isinstance(child, dict):
                    for nested_key, nested_child in child.items():
                        if isinstance(nested_child, list):
                            lists[f"{key}.{nested_key}"] = len(nested_child)
            if lists:
                summary["lists"] = lists
        elif isinstance(value, list):
            summary["length"] = len(value)
        return summary

    def _document_signals(
        self,
        *,
        html: str,
        final_url: str,
        status_code: int | None,
    ) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        visible_text = soup.get_text(" ", strip=True)
        visible_lower = visible_text[:6000].lower()
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        title_lower = (title or "").strip().lower()
        parsed_path = urlparse(final_url).path.lower().rstrip("/")
        path_leaf = parsed_path.rsplit("/", 1)[-1]
        path_leaf_core = path_leaf.split(".", 1)[0]
        has_article_content = soup.select_one("article, main") is not None
        short_gate_document = len(visible_text) < 1200 and not has_article_content

        password_input = soup.select_one('input[type="password"]') is not None
        login_form_marker = False
        for form in soup.find_all("form"):
            attributes = " ".join(
                str(value)
                for key, value in form.attrs.items()
                if key in {"action", "id", "class", "name"}
            ).lower()
            if any(
                token in attributes
                for token in ("login", "signin", "sign-in", "passport", "auth")
            ):
                login_form_marker = True
                break
        login_path = path_leaf_core in {
            "login", "signin", "sign-in", "passport", "auth"
        }
        login_title = title_lower in {
            "login", "log in", "sign in", "user login",
            "登录", "用户登录",
        }
        login_cta = any(
            token in visible_lower
            for token in ("sign in", "log in", "登录", "请登录")
        )
        login_wall = bool(
            status_code == 401
            or password_input
            or (login_form_marker and login_cta)
            or ((login_path or login_title) and short_gate_document and login_cta)
        )

        captcha_structured = False
        for tag in soup.find_all(
            ["iframe", "input", "textarea", "form", "div", "section"]
        ):
            attributes = " ".join(str(value) for value in tag.attrs.values()).lower()
            if tag.has_attr("data-sitekey") or any(
                token in attributes
                for token in (
                    "captcha", "recaptcha", "hcaptcha", "turnstile", "geetest", "cf-chl"
                )
            ):
                captcha_structured = True
                break
        captcha_path = (
            path_leaf_core in {"captcha", "verify", "verification", "challenge"}
            or "/cdn-cgi/challenge" in parsed_path
        )
        captcha_title = any(
            token in title_lower
            for token in (
                "captcha", "verify you are human", "security check", "just a moment",
                "安全验证", "人机验证",
            )
        )
        captcha_text = any(
            token in visible_lower
            for token in (
                "verify you are human", "confirm you are human",
                "security verification", "human verification", "captcha",
                "验证码", "安全验证",
                "访问验证", "人机验证",
            )
        )
        block_codes = {
            int(code)
            for code in self.section("anti_bot").get(
                "block_status_codes", [401, 403, 407, 418, 429]
            )
        }
        captcha_detected = bool(
            captcha_structured
            or (
                captcha_text
                and short_gate_document
                and (captcha_path or captcha_title or status_code in block_codes)
            )
        )
        rate_limited = status_code == 429
        proxy_auth_required = status_code == 407
        script_count = len(soup.find_all("script"))
        app_root = soup.select_one("#root, #app, #__next, [data-reactroot]") is not None
        javascript_shell = bool(
            len(visible_text) < 240
            and script_count >= 1
            and (len(html) >= 400 or app_root)
        )
        pagination = bool(
            soup.select_one("a[rel~=next], link[rel~=next]")
            or re.search(
                r"(?:page=|page/|next|下一页|下页|加载更多)",
                html,
                re.I,
            )
        )
        api_candidates: list[str] = []
        for tag in soup.find_all(["a", "form", "script"]):
            for attribute in ("href", "action", "src"):
                raw = tag.get(attribute)
                if not raw:
                    continue
                candidate = urljoin(final_url, str(raw))
                if any(token in candidate.lower() for token in ("/api/", "graphql", "ajax")):
                    safe = self._safe_url(candidate)
                    if safe not in api_candidates:
                        api_candidates.append(safe)

        evidence: list[str] = []
        if status_code is not None:
            evidence.append(f"http:{status_code}")
        if password_input:
            evidence.append("login:password_input")
        elif login_form_marker:
            evidence.append("login:form_marker")
        elif login_wall:
            evidence.append("login:path_title_cta")
        if captcha_structured:
            evidence.append("captcha:structured_element")
        elif captcha_detected:
            evidence.append("captcha:short_gate_document")
        if javascript_shell:
            evidence.append("javascript:shell")
        if pagination:
            evidence.append("pagination:hint")
        if api_candidates:
            evidence.append("api:candidate")

        if proxy_auth_required:
            classification, confidence = "proxy_auth_required", 1.0
        elif rate_limited:
            classification, confidence = "rate_limited", 1.0
        elif captcha_detected:
            classification = "captcha_required"
            confidence = 0.99 if captcha_structured else 0.92
        elif login_wall:
            classification = "login_required"
            confidence = 1.0 if status_code == 401 else (0.97 if password_input else 0.9)
        elif status_code in block_codes:
            classification, confidence = "blocked", 0.99
        elif status_code is not None and status_code >= 400:
            classification, confidence = "http_error", 0.99
        elif javascript_shell:
            classification, confidence = "javascript_required", 0.9
        elif visible_text:
            classification, confidence = "content", 0.85
            evidence.append("content:visible_text")
        else:
            classification, confidence = "unknown", 0.35
            evidence.append("content:no_visible_text")
        blocking = classification in {
            "login_required", "captcha_required", "rate_limited",
            "proxy_auth_required", "blocked",
        }

        return {
            "title": title,
            "content_length": len(html.encode("utf-8")),
            "visible_text_length": len(visible_text),
            "javascript_shell_detected": javascript_shell,
            "login_wall_detected": login_wall,
            "captcha_detected": captcha_detected,
            "rate_limited": rate_limited,
            "proxy_auth_required": proxy_auth_required,
            "blocking_detected": blocking,
            "pagination_detected": pagination,
            "api_detected": bool(api_candidates),
            "api_candidates": api_candidates[:20],
            "classification": classification,
            "confidence": confidence,
            "signals": evidence,
        }

    @staticmethod
    def _merge_captured_status_signals(
        observation: dict[str, Any], captured: list[dict[str, Any]]
    ) -> None:
        statuses = sorted(
            {
                int(item["status_code"])
                for item in captured
                if isinstance(item.get("status_code"), int)
            }
        )
        failures = [
            {
                key: item[key]
                for key in ("url", "status_code", "retry_after")
                if item.get(key) is not None
            }
            for item in captured
            if isinstance(item.get("status_code"), int) and item["status_code"] >= 400
        ]
        observation["captured_status_codes"] = statuses
        observation["captured_failures"] = failures
        if not failures:
            return

        failure_statuses = {item["status_code"] for item in failures}
        if 407 in failure_statuses:
            classification, confidence = "proxy_auth_required", 0.98
        elif 429 in failure_statuses:
            classification, confidence = "rate_limited", 0.98
        elif 401 in failure_statuses:
            classification, confidence = "login_required", 0.95
        elif failure_statuses.intersection({403, 418}):
            classification, confidence = "blocked", 0.92
        else:
            classification, confidence = "http_error", 0.9
        observation["classification"] = classification
        observation["confidence"] = confidence
        observation["signals"].extend(
            f"xhr:{status}" for status in sorted(failure_statuses)
        )
        observation["rate_limited"] = classification == "rate_limited"
        observation["proxy_auth_required"] = classification == "proxy_auth_required"
        observation["login_wall_detected"] = classification == "login_required"
        observation["blocking_detected"] = classification in {
            "login_required", "rate_limited", "proxy_auth_required", "blocked",
        }

    def _static_observe(self, url: str) -> dict[str, Any]:
        session = self.build_session()
        try:
            response = session.get(url, timeout=(3, self.timeout_seconds))
            return {
                "adapter": "static",
                "status_code": response.status_code,
                "final_url": self._safe_url(response.url),
                "retry_after": response.headers.get("Retry-After"),
                **self._document_signals(
                    html=response.text,
                    final_url=response.url,
                    status_code=response.status_code,
                ),
                "proxy": public_proxy_decision(self.proxy_decision()),
            }
        finally:
            session.close()

    def _start_cdp_capture(
        self,
        context: Any,
        page: Any,
        *,
        capture_pattern: str | None,
        capture_when_pattern_missing: bool,
        max_responses: int,
        max_body_bytes: int,
    ) -> tuple[Any, list[dict[str, Any]], set[str], set[str]]:
        captured: list[dict[str, Any]] = []
        finished_ids: set[str] = set()
        failed_ids: set[str] = set()
        cdp = context.new_cdp_session(page)
        cdp.send(
            "Network.enable",
            {
                "maxTotalBufferSize": min(
                    max_body_bytes * max_responses, 20 * 1024 * 1024
                ),
                "maxResourceBufferSize": max_body_bytes,
            },
        )

        def on_response(event: dict[str, Any]) -> None:
            if len(captured) >= max_responses:
                return
            resource_type = str(event.get("type", "")).lower()
            if resource_type not in {"xhr", "fetch"}:
                return
            response = event.get("response") or {}
            raw_url = str(response.get("url") or "")
            if not capture_when_pattern_missing and not capture_pattern:
                return
            if capture_pattern and capture_pattern not in raw_url:
                return
            headers = {
                str(key).lower(): str(value)
                for key, value in (response.get("headers") or {}).items()
            }
            item: dict[str, Any] = {
                "request_id": str(event.get("requestId") or ""),
                "url": self._safe_url(raw_url),
                "status_code": int(response.get("status") or 0),
                "resource_type": resource_type,
                "content_type": headers.get("content-type", "").lower(),
            }
            if headers.get("retry-after") is not None:
                item["retry_after"] = headers["retry-after"]
            captured.append(item)

        cdp.on("Network.responseReceived", on_response)
        cdp.on(
            "Network.loadingFinished",
            lambda event: finished_ids.add(str(event.get("requestId") or "")),
        )
        cdp.on(
            "Network.loadingFailed",
            lambda event: failed_ids.add(str(event.get("requestId") or "")),
        )
        return cdp, captured, finished_ids, failed_ids

    def _await_cdp_capture(
        self,
        page: Any,
        captured: list[dict[str, Any]],
        finished_ids: set[str],
        failed_ids: set[str],
    ) -> None:
        configured = self.section("playwright").get(
            "capture_finish_wait_seconds", 1.0
        )
        maximum_wait = self._bounded_wait_seconds(configured, 3.0)
        deadline = time.monotonic() + maximum_wait
        while time.monotonic() < deadline:
            pending_ids = {
                item["request_id"]
                for item in captured
                if item["request_id"] not in finished_ids
                and item["request_id"] not in failed_ids
            }
            if not pending_ids:
                return
            page.wait_for_timeout(50)

    def _read_cdp_capture(
        self,
        cdp: Any,
        captured: list[dict[str, Any]],
        finished_ids: set[str],
        failed_ids: set[str],
        *,
        max_body_bytes: int,
        summarize_json: bool,
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for captured_item in captured:
            item = {
                key: value
                for key, value in captured_item.items()
                if key not in {"request_id", "content_type"}
            }
            request_id = captured_item["request_id"]
            if request_id in failed_ids:
                item["parse_error"] = "NetworkLoadingFailed"
                output.append(item)
                continue
            if request_id not in finished_ids:
                item["parse_error"] = "ResponseIncomplete"
                output.append(item)
                continue
            if "json" not in captured_item["content_type"]:
                item["parse_error"] = "NonJsonContent"
                output.append(item)
                continue
            try:
                result = cdp.send("Network.getResponseBody", {"requestId": request_id})
                raw_body = result.get("body", "")
                if result.get("base64Encoded"):
                    body = base64.b64decode(raw_body, validate=True)
                else:
                    body = str(raw_body).encode("utf-8")
                if len(body) > max_body_bytes:
                    raise CrawlerRuntimeError("captured response exceeded body-size limit")
                parsed = json.loads(body.decode("utf-8"))
                if summarize_json:
                    item["json"] = self._json_summary(parsed)
                else:
                    item["body"] = parsed
            except Exception as exc:
                item["parse_error"] = type(exc).__name__
            output.append(item)
        return output

    def _dynamic_observe(
        self,
        url: str,
        *,
        wait_selector: str | None,
        wait_seconds: float,
        capture_pattern: str | None,
    ) -> dict[str, Any]:
        max_responses = max(
            1, min(int(self.section("playwright").get("max_captured_responses", 50)), 200)
        )
        max_body_bytes = max(
            1024,
            min(
                int(self.section("playwright").get("max_capture_body_bytes", 1048576)),
                10485760,
            ),
        )
        bounded_wait = self._bounded_wait_seconds(wait_seconds, 30.0)
        self.browser_user_agent()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**self._launch_options())
            try:
                context = browser.new_context(**self._context_options())
                page = context.new_page()
                cdp, pending, finished_ids, failed_ids = self._start_cdp_capture(
                    context,
                    page,
                    capture_pattern=capture_pattern,
                    capture_when_pattern_missing=True,
                    max_responses=max_responses,
                    max_body_bytes=max_body_bytes,
                )
                navigation = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_seconds * 1000,
                )
                if wait_selector:
                    page.wait_for_selector(
                        wait_selector, timeout=self.timeout_seconds * 1000
                    )
                if bounded_wait:
                    page.wait_for_timeout(bounded_wait * 1000)
                self._await_cdp_capture(page, pending, finished_ids, failed_ids)
                captured = self._read_cdp_capture(
                    cdp,
                    pending,
                    finished_ids,
                    failed_ids,
                    max_body_bytes=max_body_bytes,
                    summarize_json=True,
                )
                html = page.content()
                signals = self._document_signals(
                    html=html,
                    final_url=page.url,
                    status_code=navigation.status if navigation else None,
                )
                if captured:
                    signals["api_detected"] = True
                    signals["api_candidates"] = [item["url"] for item in captured[:20]]
                    if "api:candidate" not in signals["signals"]:
                        signals["signals"].append("api:candidate")
                self._merge_captured_status_signals(signals, captured)
                return {
                    "adapter": "playwright",
                    "status_code": navigation.status if navigation else None,
                    "final_url": self._safe_url(page.url),
                    **signals,
                    "captured_responses": captured,
                    "proxy": public_proxy_decision(self.proxy_decision()),
                }
            finally:
                browser.close()

    def scout(
        self,
        url: str,
        *,
        dynamic: str = "auto",
        wait_selector: str | None = None,
        wait_seconds: float | None = None,
        capture_pattern: str | None = None,
    ) -> dict[str, Any]:
        mode = dynamic.lower()
        if mode not in {"auto", "on", "off"}:
            raise CrawlerRuntimeError("dynamic mode must be auto, on, or off")
        static = self._static_observe(url)
        should_render = mode == "on" or (
            mode == "auto" and static["javascript_shell_detected"]
        )
        dynamic_result = None
        if should_render and mode != "off":
            configured_wait = self.section("playwright").get("scout_wait_seconds", 3)
            dynamic_result = self._dynamic_observe(
                url,
                wait_selector=wait_selector,
                wait_seconds=(configured_wait if wait_seconds is None else wait_seconds),
                capture_pattern=capture_pattern,
            )
        observed = dynamic_result or static
        action_by_classification = {
            "login_required": "bootstrap_or_refresh_browser_session",
            "captcha_required": "checkpoint_and_human_or_headed_session_handoff",
            "rate_limited": "honor_retry_after_and_reduce_domain_concurrency",
            "proxy_auth_required": "fix_proxy_authentication_and_retry_once",
            "blocked": "stop_scaling_and_checkpoint_block_evidence",
            "http_error": "inspect_http_error_before_bounded_retry",
            "javascript_required": "render_with_playwright",
            "unknown": "inspect_page_evidence_before_selecting_an_adapter",
        }
        classification = observed["classification"]
        actions: list[str] = []
        if classification in action_by_classification:
            actions.append(action_by_classification[classification])
        elif observed["api_detected"]:
            actions.append("validate_candidate_api_with_a_small_sample")
        else:
            actions.append("run_a_bounded_static_sample")
        if observed["pagination_detected"]:
            actions.append("inspect_pagination_before_scaling")
        return {
            "mode": "scout",
            "requested_url": self._safe_url(url),
            "static": static,
            "dynamic": dynamic_result,
            "classification": classification,
            "confidence": observed["confidence"],
            "signals": observed["signals"],
            "recommended_next_actions": actions,
            "proxy": public_proxy_decision(self.proxy_decision()),
        }

    def _context_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "user_agent": self.browser_user_agent(),
            "extra_http_headers": {
                "Accept-Language": str(
                    self.section("headers").get(
                        "accept_language", "zh-CN,zh;q=0.9,en;q=0.8"
                    )
                )
            },
        }
        if self.storage_state_path.is_file():
            options["storage_state"] = str(self.storage_state_path)
        return options

    def acquire_cookies(
        self,
        url: str,
        *,
        headed: bool = False,
        wait_seconds: float = 0,
        wait_selector: str | None = None,
    ) -> dict[str, Any]:
        bounded_wait = max(0.0, min(float(wait_seconds), 120.0))
        decision = self.proxy_decision()
        self.browser_user_agent()
        with sync_playwright() as playwright:
            launch_options = self._launch_options()
            launch_options["headless"] = not headed
            browser = playwright.chromium.launch(**launch_options)
            try:
                context = browser.new_context(**self._context_options())
                page = context.new_page()
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_seconds * 1000,
                )
                if wait_selector:
                    page.wait_for_selector(wait_selector, timeout=self.timeout_seconds * 1000)
                if bounded_wait:
                    page.wait_for_timeout(bounded_wait * 1000)
                state = context.storage_state()
                atomic_write_json(self.storage_state_path, state)
                atomic_write_json(
                    self.storage_meta_path,
                    {
                        "version": 1,
                        "user_agent": self.browser_user_agent(),
                        "saved_at": time.time(),
                        "proxy_url": redact_proxy_url(decision.effective_url),
                    },
                )
                return {
                    "url": page.url,
                    "status_code": response.status if response is not None else None,
                    "cookie_count": len(state.get("cookies", [])),
                    "storage_state": str(self.storage_state_path),
                    "proxy": public_proxy_decision(decision),
                }
            finally:
                browser.close()

    @staticmethod
    def _extract(html: str, selector: str | None, max_items: int = 1000) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        if selector:
            nodes = soup.select(selector)[:max_items]
            return [
                {
                    "text": node.get_text(" ", strip=True)[:10000],
                    "href": node.get("href"),
                    "data_id": node.get("data-id"),
                }
                for node in nodes
            ]
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        return [{"title": title, "text": soup.get_text(" ", strip=True)[:10000]}]

    def _static_fetch(
        self,
        url: str,
        selector: str | None,
        cookie_bootstrap_url: str | None,
    ) -> dict[str, Any]:
        session = self.build_session()
        try:
            response = session.get(url, timeout=(3, self.timeout_seconds))
            refresh_codes = {
                int(code) for code in self.section("cookies").get("refresh_on_status", [401, 403])
            }
            if response.status_code in refresh_codes and cookie_bootstrap_url:
                self.acquire_cookies(cookie_bootstrap_url)
                session.close()
                session = self.build_session()
                response = session.get(url, timeout=(3, self.timeout_seconds))
            result = {
                "mode": "static",
                "url": response.url,
                "status_code": response.status_code,
                "items": self._extract(response.text, selector),
                "proxy": public_proxy_decision(self.proxy_decision()),
            }
            if response.status_code >= 400:
                raise CrawlerRuntimeError(
                    f"static fetch failed with HTTP {response.status_code}: {response.url}"
                )
            return result
        finally:
            session.close()

    def _dynamic_fetch(
        self,
        url: str,
        selector: str | None,
        wait_selector: str | None,
        capture_pattern: str | None,
        wait_seconds: float = 0,
    ) -> dict[str, Any]:
        self.log.debug("dynamic fetch start: %s", self._safe_url(url))
        max_responses = max(
            1, min(int(self.section("playwright").get("max_captured_responses", 50)), 200)
        )
        max_body_bytes = max(
            1024,
            min(
                int(self.section("playwright").get("max_capture_body_bytes", 1048576)),
                10485760,
            ),
        )
        self.browser_user_agent()
        with sync_playwright() as playwright:
            self.log.debug("dynamic fetch Playwright started")
            browser = playwright.chromium.launch(**self._launch_options())
            self.log.debug("dynamic fetch browser launched")
            try:
                context = browser.new_context(**self._context_options())
                page = context.new_page()
                cdp, pending, finished_ids, failed_ids = self._start_cdp_capture(
                    context,
                    page,
                    capture_pattern=capture_pattern,
                    capture_when_pattern_missing=False,
                    max_responses=max_responses,
                    max_body_bytes=max_body_bytes,
                )
                navigation = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_seconds * 1000,
                )
                self.log.debug("dynamic fetch navigation completed")
                if wait_selector:
                    page.wait_for_selector(
                        wait_selector, timeout=self.timeout_seconds * 1000
                    )
                bounded_wait = self._bounded_wait_seconds(wait_seconds, 30.0)
                if bounded_wait:
                    page.wait_for_timeout(bounded_wait * 1000)
                self.log.debug(
                    "dynamic fetch wait completed; captured=%d", len(pending)
                )
                self._await_cdp_capture(page, pending, finished_ids, failed_ids)
                captured_json = self._read_cdp_capture(
                    cdp,
                    pending,
                    finished_ids,
                    failed_ids,
                    max_body_bytes=max_body_bytes,
                    summarize_json=False,
                )
                html = page.content()
                status_code = navigation.status if navigation is not None else None
                if status_code is not None and status_code >= 400:
                    raise CrawlerRuntimeError(
                        f"dynamic fetch failed with HTTP {status_code}: {page.url}"
                    )
                observation = self._document_signals(
                    html=html,
                    final_url=page.url,
                    status_code=status_code,
                )
                self._merge_captured_status_signals(observation, captured_json)
                self.log.debug("dynamic fetch observation completed")
                return {
                    "mode": "dynamic",
                    "url": page.url,
                    "status_code": status_code,
                    "items": self._extract(html, selector),
                    "captured_json": captured_json,
                    "captured_failures": observation["captured_failures"],
                    "classification": observation["classification"],
                    "confidence": observation["confidence"],
                    "signals": observation["signals"],
                    "proxy": public_proxy_decision(self.proxy_decision()),
                }
            finally:
                self.log.debug("dynamic fetch browser closing")
                browser.close()
                self.log.debug("dynamic fetch browser closed")

    def fetch(
        self,
        url: str,
        *,
        mode: str = "static",
        selector: str | None = None,
        wait_selector: str | None = None,
        capture_pattern: str | None = None,
        cookie_bootstrap_url: str | None = None,
        wait_seconds: float = 0,
    ) -> dict[str, Any]:
        normalized_mode = mode.lower()
        if normalized_mode == "static":
            return self._static_fetch(url, selector, cookie_bootstrap_url)
        if normalized_mode == "dynamic":
            return self._dynamic_fetch(
                url, selector, wait_selector, capture_pattern, wait_seconds
            )
        raise CrawlerRuntimeError(f"unsupported fetch mode: {mode}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Config-driven static/dynamic crawler runtime")
    parser.add_argument("--config", default="crawler.config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="resolve and probe the configured proxy")
    probe.add_argument("--pretty", action="store_true")

    cookies = subparsers.add_parser("cookies", help="acquire and persist browser cookies")
    cookies.add_argument("url")
    cookies.add_argument("--headed", action="store_true")
    cookies.add_argument("--wait-seconds", type=float, default=0)
    cookies.add_argument("--wait-selector")
    cookies.add_argument("--pretty", action="store_true")

    fetch = subparsers.add_parser("fetch", help="fetch one page")
    fetch.add_argument("url")
    fetch.add_argument("--mode", choices=("static", "dynamic"), default="static")
    fetch.add_argument("--selector")
    fetch.add_argument("--wait-selector")
    fetch.add_argument("--wait-seconds", type=float, default=0)
    fetch.add_argument("--capture-pattern")
    fetch.add_argument("--cookie-bootstrap-url")
    fetch.add_argument("--pretty", action="store_true")
    scout = subparsers.add_parser(
        "scout", help="classify a page and recommend a bounded next adapter"
    )
    scout.add_argument("url")
    scout.add_argument("--dynamic", choices=("auto", "on", "off"), default="auto")
    scout.add_argument("--wait-selector")
    scout.add_argument("--wait-seconds", type=float)
    scout.add_argument("--capture-pattern")
    scout.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        runtime = CrawlerRuntime(args.config)
        if args.command == "probe":
            result: dict[str, Any] = {
                "proxy": public_proxy_decision(runtime.proxy_decision(refresh=True))
            }
        elif args.command == "cookies":
            result = runtime.acquire_cookies(
                args.url,
                headed=args.headed,
                wait_seconds=args.wait_seconds,
                wait_selector=args.wait_selector,
            )
        elif args.command == "scout":
            result = runtime.scout(
                args.url,
                dynamic=args.dynamic,
                wait_selector=args.wait_selector,
                wait_seconds=args.wait_seconds,
                capture_pattern=args.capture_pattern,
            )
        else:
            result = runtime.fetch(
                args.url,
                mode=args.mode,
                selector=args.selector,
                wait_selector=args.wait_selector,
                capture_pattern=args.capture_pattern,
                cookie_bootstrap_url=args.cookie_bootstrap_url,
                wait_seconds=args.wait_seconds,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    except (
        CrawlerRuntimeError,
        PlaywrightError,
        requests.RequestException,
        yaml.YAMLError,
        OSError,
        ValueError,
    ) as exc:
        logging.getLogger("crawler.runtime").error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
