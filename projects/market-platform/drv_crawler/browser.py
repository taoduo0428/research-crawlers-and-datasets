from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Response,
    sync_playwright,
)

from .boss_archive import BossArchiveCollector
from .core import (
    BlockedResponseError,
    CrawlerError,
    ProjectConfig,
    ResilientHttpClient,
    SessionRequiredError,
    resolve_proxy,
)
from .models import CrawlRecord, SiteResult, clean_text, dedupe_records, make_record_id
from .sites import BaseSiteAdapter, CheckpointCallback, add_price


_BOSS_AUTH_KEEP_OPEN_SCRIPT = r"""
Object.defineProperty(window, "close", {
    value: () => {},
    writable: false,
    configurable: false
});
"""

_BOSS_LOGIN_MARKERS = (
    "\u626b\u7801\u767b\u5f55",
    "\u624b\u673a\u53f7\u767b\u5f55",
    "\u9a8c\u8bc1\u7801\u767b\u5f55",
    "\u767b\u5f55/\u6ce8\u518c",
    "\u53d1\u9001\u9a8c\u8bc1\u7801",
    "\u62d6\u52a8\u6ed1\u5757",
    "\u5b89\u5168\u9a8c\u8bc1",
)

_EPWK_TASK_CARD_SELECTOR = ".content-lists .itemblock"


def _find_job_lists(value: Any, output: list[list[dict[str, Any]]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"jobList", "jobCards", "list"} and isinstance(child, list):
                if child and all(isinstance(item, dict) for item in child[:3]):
                    if any(
                        any(name in item for name in ("jobName", "salaryDesc", "jobTitle"))
                        for item in child[:3]
                    ):
                        output.append(child)
            _find_job_lists(child, output)
    elif isinstance(value, list):
        for child in value[:100]:
            _find_job_lists(child, output)


def _repair_mojibake(value: str) -> str:
    try:
        repaired = value.encode("gbk").decode("utf-8")
        return repaired if repaired else value
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _launch_browser(playwright: Any, config: ProjectConfig, *, headed: bool) -> Browser:
    browser_config = config.section("playwright")
    proxy_url = resolve_proxy(config)
    options: dict[str, Any] = {"headless": not headed}
    if proxy_url:
        options["proxy"] = {"server": proxy_url}
    channel = str(browser_config.get("browser_channel", "")).strip()
    if channel:
        options["channel"] = channel
    return playwright.chromium.launch(**options)


def _new_context(
    browser: Browser, config: ProjectConfig, *, storage_state: Path | None = None
) -> BrowserContext:
    headers = config.section("headers")
    options: dict[str, Any] = {
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "extra_http_headers": {
            "Accept-Language": str(
                headers.get("accept_language", "zh-CN,zh;q=0.9,en;q=0.8")
            )
        },
    }
    if storage_state and storage_state.exists():
        options["storage_state"] = str(storage_state)
    return browser.new_context(**options)


def _launch_persistent_edge_context(
    playwright: Any,
    config: ProjectConfig,
    site: dict[str, Any],
) -> BrowserContext:
    browser_config = config.section("playwright")
    profile_dir = Path(
        str(site.get("auth_profile_dir", ".crawler-state/boss-manual-edge-profile"))
    )
    profile_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {"headless": False}
    proxy_url = resolve_proxy(config)
    if proxy_url:
        options["proxy"] = {"server": proxy_url}
    channel = str(browser_config.get("browser_channel", "")).strip()
    if channel:
        options["channel"] = channel
    return playwright.chromium.launch_persistent_context(
        str(profile_dir.resolve()), **options
    )


def _launch_persistent_edge_context_with_retry(
    playwright: Any,
    config: ProjectConfig,
    site: dict[str, Any],
    *,
    wait_seconds: int = 10,
) -> BrowserContext:
    deadline = time.monotonic() + max(1, wait_seconds)
    last_error = ""
    while time.monotonic() < deadline:
        try:
            return _launch_persistent_edge_context(playwright, config, site)
        except PlaywrightError as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise SessionRequiredError(
        "could not reopen the persistent BOSS Edge profile after login; "
        f"last_error={last_error[:160]}"
    )


def _boss_login_ready(url: str, body: str) -> bool:
    parsed = urlparse(url)
    if not parsed.netloc.lower().endswith("zhipin.com"):
        return False
    if "/web/user" in parsed.path or "/web/passport/" in parsed.path:
        return False
    if parsed.path.endswith("verify.html") or "code=35" in parsed.query:
        return False
    return not any(token in body for token in _BOSS_LOGIN_MARKERS)


def _epwk_task_list_ready(url: str, task_card_count: int) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (host == "epwk.com" or host.endswith(".epwk.com")) and task_card_count > 0


def _open_auth_page(
    context: BrowserContext,
    *,
    site_name: str,
    auth_url: str,
    navigation_timeout_ms: int,
) -> Page:
    if site_name == "boss":
        # BOSS serves this route as a popup and calls window.close() when it is
        # opened as a standalone tab. Keep a real opener and suppress only that
        # close call so the user has time to complete the normal login flow.
        context.add_init_script(_BOSS_AUTH_KEEP_OPEN_SCRIPT)
        launcher = context.new_page()
        launcher.set_content("<title>BOSS login launcher</title>")
        with context.expect_page(timeout=navigation_timeout_ms) as popup_info:
            launcher.evaluate(
                "url => window.open(url, '_blank')",
                auth_url,
            )
        page = popup_info.value
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=navigation_timeout_ms,
        )
    else:
        page = context.new_page()
        page.goto(
            auth_url,
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms,
        )
    page.bring_to_front()
    return page


def _safe_page_url(page: Page) -> str:
    try:
        return page.url
    except PlaywrightError:
        return ""


def _wait_for_enter_or_timeout(wait_seconds: int) -> bool:
    deadline = time.monotonic() + max(1, wait_seconds)
    if os.name == "nt":
        import msvcrt

        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in {"\r", "\n"}:
                    return True
            time.sleep(0.2)
        return False
    try:
        import select

        while time.monotonic() < deadline:
            readable, _, _ = select.select([sys.stdin], [], [], 0.2)  # type: ignore[name-defined]
            if readable and sys.stdin.readline() is not None:  # type: ignore[name-defined]
                return True
        return False
    except Exception:
        input()
        return True


def _profile_domain_cookie_count(
    profile_dir: Path, domain: str, *, wait_seconds: int = 5
) -> int:
    cookie_db = profile_dir / "Default" / "Network" / "Cookies"
    deadline = time.monotonic() + max(1, wait_seconds)
    while time.monotonic() < deadline:
        if cookie_db.exists():
            try:
                connection = sqlite3.connect(
                    f"file:{cookie_db.resolve()}?mode=ro", uri=True, timeout=1
                )
                try:
                    row = connection.execute(
                        "SELECT COUNT(1) FROM cookies WHERE host_key LIKE ?",
                        (f"%{domain}",),
                    ).fetchone()
                    return int(row[0] if row else 0)
                finally:
                    connection.close()
            except sqlite3.Error:
                pass
        time.sleep(0.25)
    return 0


def _acquire_storage_state_from_external_edge(
    config: ProjectConfig,
    site_name: str,
    auth_url: str,
    state_path: Path,
    *,
    wait_seconds: int,
    keep_open: bool = False,
) -> Path:
    site = config.site(site_name)
    profile_dir = Path(str(site.get("auth_profile_dir", ".crawler-state/boss-manual-edge-profile")))
    profile_dir.mkdir(parents=True, exist_ok=True)
    entry_url = str(site.get("auth_entry_url") or site.get("start_url") or auth_url)
    start_url = str(site.get("start_url") or entry_url)
    with sync_playwright() as playwright:
        login_context: BrowserContext | None = None
        try:
            login_context = _launch_persistent_edge_context(playwright, config, site)
        except PlaywrightError as exc:
            raise SessionRequiredError(
                "could not open the persistent BOSS Edge profile; close any crawler Edge "
                "window using that profile, then rerun auth"
            ) from exc
        login_context.add_init_script(_BOSS_AUTH_KEEP_OPEN_SCRIPT)
        page = (
            login_context.pages[0]
            if login_context.pages
            else login_context.new_page()
        )
        try:
            try:
                page.goto(
                    entry_url,
                    wait_until="domcontentloaded",
                    timeout=int(
                        config.section("playwright").get(
                            "navigation_timeout_ms", 30_000
                        )
                    ),
                )
            except PlaywrightError:
                pass
            print(
                f"Opened {site_name} in a protected persistent Edge window. Complete "
                f"normal login/verification within {wait_seconds} seconds. Do not open "
                "DevTools. When the jobs page is visible, press Enter in this terminal."
            )
            if not _wait_for_enter_or_timeout(wait_seconds):
                raise SessionRequiredError(
                    f"{site_name} login confirmation timed out; profile was not accepted"
                )
        finally:
            try:
                login_context.close()
            except PlaywrightError:
                pass

        verify_context = _launch_persistent_edge_context_with_retry(
            playwright, config, site, wait_seconds=10
        )
        try:
            verify_page = verify_context.new_page()
            verify_page.goto(
                start_url,
                wait_until="domcontentloaded",
                timeout=int(
                    config.section("playwright").get(
                        "navigation_timeout_ms", 30_000
                    )
                ),
            )
            verify_page.wait_for_timeout(3_000)
            try:
                body = clean_text(
                    verify_page.locator("body").inner_text(timeout=3_000)
                ) or ""
            except PlaywrightError:
                body = ""
            if not _boss_login_ready(_safe_page_url(verify_page), body):
                current_path = urlparse(_safe_page_url(verify_page)).path or "unknown"
                raise SessionRequiredError(
                    "BOSS login was not verified on a fresh jobs page; "
                    f"current_path={current_path}"
                )

            cookie_count = _profile_domain_cookie_count(
                profile_dir, "zhipin.com", wait_seconds=5
            )
            if cookie_count <= 0:
                raise SessionRequiredError(
                    "BOSS jobs page opened but no BOSS cookies were persisted"
                )
            print(
                f"Verified the fresh BOSS jobs page and {cookie_count} persisted cookie "
                "rows; cookie values were not read or logged."
            )
            if keep_open:
                print(
                    "Login verified. Press Enter again when you want this auth Edge "
                    "window to close."
                )
                _wait_for_enter_or_timeout(wait_seconds)
            return profile_dir
        finally:
            try:
                verify_context.close()
            except PlaywrightError:
                pass


def acquire_storage_state(
    config: ProjectConfig,
    site_name: str,
    *,
    wait_seconds: int,
    keep_open: bool = False,
) -> Path:
    site = config.site(site_name)
    auth_url = str(site.get("auth_url") or site.get("start_url"))
    state_path = Path(str(site["storage_state"]))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    wait_seconds = max(10, min(wait_seconds, 600))
    if str(site.get("auth_mode", "playwright_popup")) == "external_edge_cdp":
        return _acquire_storage_state_from_external_edge(
            config,
            site_name,
            auth_url,
            state_path,
            wait_seconds=wait_seconds,
            keep_open=keep_open,
        )
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, config, headed=True)
        context = _new_context(
            browser, config, storage_state=state_path if state_path.exists() else None
        )
        navigation_timeout_ms = int(
            config.section("playwright").get("navigation_timeout_ms", 30_000)
        )
        temp_path: Path | None = None
        try:
            try:
                page = _open_auth_page(
                    context,
                    site_name=site_name,
                    auth_url=auth_url,
                    navigation_timeout_ms=navigation_timeout_ms,
                )
            except PlaywrightError as exc:
                raise SessionRequiredError(
                    f"{site_name} auth page navigation failed; storage state not changed"
                ) from exc
            print(
                f"Opened {site_name} in a headed browser. Complete normal login/verification "
                f"within {wait_seconds} seconds; CAPTCHA is not automated."
            )
            deadline = time.monotonic() + wait_seconds
            ready = False
            consecutive_ready = 0
            while time.monotonic() < deadline:
                page.wait_for_timeout(1_000)
                current_host = urlparse(page.url).netloc.lower()
                current_path = urlparse(page.url).path
                try:
                    body = clean_text(page.locator("body").inner_text(timeout=2_000)) or ""
                except PlaywrightError:
                    body = ""
                if site_name == "boss":
                    candidate = _boss_login_ready(page.url, body)
                elif site_name == "epwk":
                    try:
                        task_card_count = page.locator(
                            _EPWK_TASK_CARD_SELECTOR
                        ).count()
                    except PlaywrightError:
                        task_card_count = 0
                    candidate = _epwk_task_list_ready(page.url, task_card_count)
                else:
                    candidate = (
                        current_host.endswith("zbj.com")
                        and current_host != "verify.zbj.com"
                        and "拖动滑块" not in body
                        and "安全验证" not in body
                    )
                consecutive_ready = consecutive_ready + 1 if candidate else 0
                if consecutive_ready >= 3:
                    ready = True
                    break
            if not ready:
                raise SessionRequiredError(
                    f"{site_name} login/verification was not completed; storage state not changed"
                )
            with NamedTemporaryFile(
                suffix=".json", delete=False, dir=state_path.parent
            ) as temp:
                temp_path = Path(temp.name)
            context.storage_state(path=str(temp_path))
            os.replace(temp_path, state_path)
            temp_path = None
            if keep_open:
                print(
                    "Session state saved. Auth browser will stay open until you press "
                    "Enter in this terminal."
                )
                try:
                    input()
                except EOFError:
                    page.wait_for_timeout(30_000)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            context.close()
            browser.close()
    return state_path


class BossAdapter(BaseSiteAdapter):
    site_name = "boss"
    difficulty = 5
    adapter_name = "playwright_persisted_session_live_only"

    API_PATH_MARKERS = (
        "/wapi/zpgeek/pc/recommend/job/list.json",
        "/wapi/zpgeek/search/joblist.json",
    )

    def __init__(
        self,
        config: ProjectConfig,
        client: ResilientHttpClient,
        checkpoint: CheckpointCallback,
    ) -> None:
        super().__init__(config, client, checkpoint)
        self._captured_payloads: list[Any] = []
        self._captured_api_urls: list[str] = []
        self._capture_summaries: list[dict[str, Any]] = []
        self._capture_limit = int(
            config.section("limits").get("max_captured_responses", 40)
        )

    def _storage_state_for_run(self) -> Path | None:
        if bool(self.site_config.get("anonymous_only", True)):
            return None
        value = self.site_config.get("storage_state")
        if not value:
            return None
        path = Path(str(value))
        return path if path.exists() else None

    def _archive_fallback_enabled(self) -> bool:
        settings = self.site_config.get("archive_fallback", {})
        return isinstance(settings, dict) and bool(settings.get("enabled", False))

    def _append_candidate_records(
        self,
        result: SiteResult,
        candidates: list[CrawlRecord],
        *,
        numeric_price_only: bool,
        accepted_record_ids: set[str],
        seen_candidate_keys: set[tuple[str, str]],
    ) -> None:
        for record in dedupe_records(candidates):
            candidate_key = (record.record_id, record.price_raw_joined)
            if (
                record.record_id in accepted_record_ids
                or candidate_key in seen_candidate_keys
            ):
                continue
            seen_candidate_keys.add(candidate_key)
            if self.accept_record(
                result,
                record,
                numeric_price_only=numeric_price_only,
            ):
                accepted_record_ids.add(record.record_id)

    def _append_archive_fallback(
        self,
        result: SiteResult,
        *,
        limit: int,
        numeric_price_only: bool = False,
        accepted_record_ids: set[str] | None = None,
        seen_candidate_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        if not self._archive_fallback_enabled():
            return
        settings = self.site_config.get("archive_fallback", {})
        if not isinstance(settings, dict):
            return
        collection = BossArchiveCollector(self.client, settings).collect(limit=limit)
        if accepted_record_ids is None:
            accepted_record_ids = {record.record_id for record in result.records}
        if seen_candidate_keys is None:
            seen_candidate_keys = set()
        self._append_candidate_records(
            result,
            collection.records,
            numeric_price_only=numeric_price_only,
            accepted_record_ids=accepted_record_ids,
            seen_candidate_keys=seen_candidate_keys,
        )
        result.pages_fetched += collection.snapshots_fetched
        result.runtime_notes.append(
            "archive_fallback=Common Crawl public BOSS SEO snapshots; "
            "records are historical and not a live-status guarantee; "
            f"archive_index={collection.archive_index}; "
            f"snapshots={collection.snapshots_fetched}; "
            f"candidates={collection.candidates_considered}"
        )
        if collection.errors:
            result.runtime_notes.append(
                f"archive_skipped={len(collection.errors)} malformed or unavailable snapshots"
            )

    def _on_response(self, response: Response) -> None:
        path = urlparse(response.url).path
        if not any(marker in path for marker in self.API_PATH_MARKERS):
            return
        if len(self._capture_summaries) >= self._capture_limit:
            return
        if response.url not in self._captured_api_urls:
            self._captured_api_urls.append(response.url)
        summary: dict[str, Any] = {"path": path, "status": response.status}
        try:
            payload = response.json()
            summary["json"] = True
            if isinstance(payload, dict):
                summary["code"] = payload.get("code")
                message = payload.get("message") or payload.get("zpData", {}).get("message")
                if message:
                    summary["message"] = _repair_mojibake(
                        clean_text(str(message)) or ""
                    )[:160]
            self._captured_payloads.append(payload)
        except Exception as exc:
            summary["json"] = False
            summary["parse_error"] = type(exc).__name__
        self._capture_summaries.append(summary)

    def _replay_captured_api(self, context: BrowserContext, start_url: str) -> None:
        if self._captured_payloads or not self._captured_api_urls:
            return
        for url in self._captured_api_urls[:3]:
            try:
                response = context.request.get(
                    url,
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "Referer": start_url,
                    },
                    timeout=10_000,
                )
                payload = response.json()
                self._captured_payloads.append(payload)
                summary: dict[str, Any] = {
                    "path": urlparse(url).path,
                    "status": response.status,
                    "json": True,
                    "replayed": True,
                }
                if isinstance(payload, dict):
                    summary["code"] = payload.get("code")
                self._capture_summaries.append(summary)
                return
            except Exception as exc:
                self._capture_summaries.append(
                    {
                        "path": urlparse(url).path,
                        "replayed": True,
                        "replay_error": type(exc).__name__,
                    }
                )

    def _records_from_api(self, start_url: str) -> list[CrawlRecord]:
        job_lists: list[list[dict[str, Any]]] = []
        for payload in self._captured_payloads:
            _find_job_lists(payload, job_lists)
        records: list[CrawlRecord] = []
        for jobs in job_lists:
            for position, item in enumerate(jobs, start=1):
                title = clean_text(
                    str(item.get("jobName") or item.get("jobTitle") or "")
                )
                salary = clean_text(str(item.get("salaryDesc") or ""))
                if not title or not salary:
                    continue
                detail_candidate = item.get("jobUrl") or item.get("jobLink")
                detail_url = (
                    urljoin(start_url, str(detail_candidate)) if detail_candidate else None
                )
                record = CrawlRecord(
                    site_name=self.site_name,
                    content_category="full_time_job",
                    title_raw=title,
                    list_url=start_url,
                    detail_url=detail_url,
                    page_no=1,
                    list_position=position,
                    job_type_raw=clean_text(str(item.get("jobType") or "")),
                    publisher_name_raw=clean_text(
                        str(item.get("brandName") or item.get("companyName") or "")
                    ),
                    location_raw=clean_text(
                        " ".join(
                            str(item.get(key) or "")
                            for key in ("cityName", "areaDistrict", "businessDistrict")
                        )
                    ),
                    capture_method="playwright_api",
                    metadata={
                        "encrypt_job_id": item.get("encryptJobId"),
                        "security_id_present": bool(item.get("securityId")),
                        "experience_raw": item.get("jobExperience"),
                        "degree_raw": item.get("jobDegree"),
                        "labels": item.get("jobLabels") or [],
                    },
                    record_id=make_record_id(
                        self.site_name,
                        str(
                            item.get("encryptJobId")
                            or detail_url
                            or f"{title}|{item.get('brandName')}|{position}"
                        ),
                    ),
                )
                add_price(
                    record,
                    salary,
                    source="api_response",
                    url=start_url,
                    location="job list response salaryDesc",
                    kind="salary_range",
                )
                records.append(record)
        return dedupe_records(records)

    @staticmethod
    def _records_from_dom(page: Page, start_url: str) -> list[CrawlRecord]:
        items = page.evaluate(
            r"""
            () => {
              const text = (node) => node?.textContent?.trim() || null;
              const legacy = Array.from(document.querySelectorAll('.job-card-wrapper'))
                .slice(0, 100)
                .map((card, index) => {
                  const pick = (selector) => text(card.querySelector(selector));
                  const link = card.querySelector('a[href*="/job_detail/"]');
                  return {
                    position: index + 1,
                    title: pick('.job-name'),
                    salary: pick('.salary'),
                    location: pick('.job-area'),
                    company: pick('.company-name'),
                    tags: Array.from(card.querySelectorAll('.tag-list li')).map(text).filter(Boolean),
                    href: link?.href || null,
                    source: 'legacy'
                  };
                });
              if (legacy.some((item) => item.title && item.salary)) return legacy;

              const results = [];
              const seen = new Set();
              const salaryNodes = Array.from(
                document.querySelectorAll('[class*="salary"], .salary')
              );
              for (const salaryNode of salaryNodes) {
                const salary = text(salaryNode);
                if (!salary || !(/\d/.test(salary) || /面议|薪/.test(salary))) continue;
                let card = salaryNode;
                let link = null;
                for (let depth = 0; card && depth < 10; depth += 1) {
                  link = card.querySelector?.('a[href*="/job_detail/"]') || null;
                  if (link) break;
                  card = card.parentElement;
                }
                if (!card || !link) continue;
                const href = link.href || link.getAttribute('href');
                if (!href || seen.has(href)) continue;
                seen.add(href);
                const company = card.querySelector(
                  '[class*="company"], [class*="brand"], .company-name'
                );
                const location = card.querySelector(
                  '[class*="area"], [class*="location"], [class*="address"], .job-area'
                );
                const tags = Array.from(
                  card.querySelectorAll('[class*="tag"] li, [class*="tag"] span')
                ).map(text).filter(Boolean).slice(0, 20);
                results.push({
                  position: results.length + 1,
                  title: text(link),
                  salary,
                  location: text(location),
                  company: text(company),
                  tags,
                  href,
                  source: 'salary_anchor_fallback'
                });
                if (results.length >= 100) break;
              }
              return results;
            }
            """
        )
        records: list[CrawlRecord] = []
        for item in items:
            title = clean_text(item.get("title"))
            salary = clean_text(item.get("salary"))
            if not title or not salary:
                continue
            record = CrawlRecord(
                site_name="boss",
                content_category="full_time_job",
                title_raw=title,
                list_url=start_url,
                detail_url=absolute_url(start_url, item.get("href")),
                page_no=1,
                list_position=int(item.get("position") or len(records) + 1),
                publisher_name_raw=clean_text(item.get("company")),
                location_raw=clean_text(item.get("location")),
                capture_method=(
                    "playwright_dom_fallback"
                    if item.get("source") == "salary_anchor_fallback"
                    else "playwright_dom"
                ),
                metadata={"labels": item.get("tags") or []},
                record_id=make_record_id(
                    "boss",
                    str(
                        item.get("href")
                        or f"{title}|{item.get('company')}|{item.get('position')}"
                    ),
                ),
            )
            add_price(
                record,
                salary,
                source="list_page",
                url=start_url,
                location=(
                    "[class*=salary] nearest job_detail container"
                    if item.get("source") == "salary_anchor_fallback"
                    else ".job-card-wrapper .salary"
                ),
                kind="salary_range",
            )
            records.append(record)
        return dedupe_records(records)

    @staticmethod
    def _session_block_reason(page: Page) -> str | None:
        url = page.url
        path = urlparse(url).path
        try:
            body = clean_text(page.locator("body").inner_text(timeout=5_000)) or ""
        except PlaywrightError:
            body = ""
        if "/web/passport/" in path and path.endswith("/verify.html"):
            if "code=35" in urlparse(url).query:
                return (
                    "BOSS network identity blocked (code=35); "
                    "human handoff required"
                )
            return "BOSS security verification requires human handoff"
        if "/web/user/" in url or "当前登录状态已失效" in body:
            return "BOSS login session is missing or expired"
        if any(token in body for token in ("扫码登录", "手机号登录")) and not page.locator(
            ".job-card-wrapper"
        ).count():
            return "BOSS requires normal interactive login"
        if any(
            token in body
            for token in ("安全检查", "验证码", "拖动滑块", "加载中，请稍候")
        ):
            return "BOSS security verification requires human handoff"
        return None

    def crawl(
        self,
        limit: int,
        *,
        with_details: bool = True,
        numeric_price_only: bool = False,
    ) -> SiteResult:
        result = self.result(limit)
        accepted_record_ids: set[str] = set()
        seen_candidate_keys: set[tuple[str, str]] = set()
        start_url = str(self.site_config["start_url"])
        self._captured_payloads.clear()
        self._captured_api_urls.clear()
        self._capture_summaries.clear()
        if not bool(self.site_config.get("anonymous_live_enabled", True)):
            if self._archive_fallback_enabled():
                result.runtime_notes.append(
                    "Official live BOSS probe disabled; using the configured archive source."
                )
                try:
                    self._append_archive_fallback(
                        result,
                        limit=limit,
                        numeric_price_only=numeric_price_only,
                        accepted_record_ids=accepted_record_ids,
                        seen_candidate_keys=seen_candidate_keys,
                    )
                except Exception as exc:
                    self.failure(
                        result, stage="archive_fallback", exc=exc, url=start_url
                    )
            else:
                result.runtime_notes.append(
                    "Official live BOSS probe disabled; archive fallback is disabled, "
                    "so no data source was queried."
                )
            result.finish()
            self.checkpoint(result, "finished")
            return result
        try:
            self.client.ensure_allowed(start_url)
        except Exception as exc:
            self.failure(result, stage="robots", exc=exc, url=start_url)
            if self._archive_fallback_enabled():
                try:
                    self._append_archive_fallback(
                        result,
                        limit=limit,
                        numeric_price_only=numeric_price_only,
                        accepted_record_ids=accepted_record_ids,
                        seen_candidate_keys=seen_candidate_keys,
                    )
                except Exception as archive_exc:
                    self.failure(
                        result,
                        stage="archive_fallback",
                        exc=archive_exc,
                        url=start_url,
                    )
            else:
                result.runtime_notes.append(
                    "BOSS live-only mode: archive fallback is disabled; "
                    "no historical records were substituted."
                )
            result.finish()
            self.checkpoint(result, "finished")
            return result
        state_path = self._storage_state_for_run()
        external_edge = str(self.site_config.get("auth_mode", "")) == "external_edge_cdp"
        with sync_playwright() as playwright:
            if external_edge:
                context = _launch_persistent_edge_context(
                    playwright, self.config, self.site_config
                )
                context.add_init_script(_BOSS_AUTH_KEEP_OPEN_SCRIPT)
                browser = context.browser
                result.runtime_notes.append(
                    "browser_context=persistent_edge_profile; cookies and local storage "
                    "loaded from the manual Edge profile; secret values were not logged"
                )
            else:
                browser = _launch_browser(playwright, self.config, headed=False)
                context = _new_context(
                    browser, self.config, storage_state=state_path
                )
                if state_path is None and bool(self.site_config.get("anonymous_only", True)):
                    result.runtime_notes.append(
                        "browser_context=fresh_anonymous; no account storage state loaded"
                    )
                elif state_path is None:
                    result.runtime_notes.append(
                        "browser_context=no_saved_storage_state; run `auth --site boss` "
                        "once to capture the session"
                    )
                else:
                    result.runtime_notes.append(
                        "browser_context=persisted_storage_state; cookies and local storage "
                        "loaded; secret values were not logged"
                    )
            context.on("response", self._on_response)
            if external_edge:
                page = context.pages[0] if context.pages else context.new_page()
            else:
                page = context.new_page()
            try:
                try:
                    page.goto(
                        start_url,
                        wait_until="domcontentloaded",
                        timeout=int(
                            self.config.section("playwright").get(
                                "navigation_timeout_ms", 30_000
                            )
                        ),
                    )
                    page.wait_for_timeout(4_000)
                except PlaywrightError:
                    self._replay_captured_api(context, start_url)
                    recovered = self._records_from_api(start_url)
                    if not recovered:
                        raise
                    self._append_candidate_records(
                        result,
                        recovered,
                        numeric_price_only=numeric_price_only,
                        accepted_record_ids=accepted_record_ids,
                        seen_candidate_keys=seen_candidate_keys,
                    )
                    result.pages_fetched = 1
                    result.runtime_notes.append(
                        "Edge page closed after the successful job-list API response; "
                        "records were recovered from the captured response."
                    )
                    result.runtime_notes.append(
                        f"network_capture={self._capture_summaries[:10]}"
                    )
                    result.finish()
                    self.checkpoint(result, "finished")
                    return result
                self._replay_captured_api(context, start_url)
                recovered = self._records_from_api(start_url)
                if recovered:
                    self._append_candidate_records(
                        result,
                        recovered,
                        numeric_price_only=numeric_price_only,
                        accepted_record_ids=accepted_record_ids,
                        seen_candidate_keys=seen_candidate_keys,
                    )
                    if not numeric_price_only or len(result.records) >= limit:
                        result.pages_fetched = 1
                        result.runtime_notes.append(
                            "Recovered live BOSS records from the captured/replayed job-list API."
                        )
                        result.runtime_notes.append(
                            f"network_capture={self._capture_summaries[:10]}"
                        )
                        result.finish()
                        self.checkpoint(result, "finished")
                        return result
                for _ in range(12):
                    if "/web/user/" in page.url:
                        raise SessionRequiredError(
                            "BOSS login session is missing or expired"
                        )
                    try:
                        if page.locator(".job-card-wrapper").count() > 0:
                            break
                    except PlaywrightError:
                        pass
                    page.wait_for_timeout(500)
                self._replay_captured_api(context, start_url)
                for capture in self._capture_summaries:
                    code = capture.get("code")
                    message = str(capture.get("message") or "")
                    if code not in (None, 0):
                        if code == 35 or "IP地址" in message or "异常行为" in message:
                            raise BlockedResponseError(
                                f"BOSS job-list API blocked this network identity (code={code})",
                                status_code=403,
                                url=start_url,
                            )
                        if code == 7 or "登录" in message or "失效" in message:
                            raise SessionRequiredError(
                                f"BOSS job-list API rejected the session (code={code})"
                            )
                        raise CrawlerError(
                            f"BOSS job-list API returned non-success code={code}"
                        )
                reason = self._session_block_reason(page)
                if reason:
                    raise SessionRequiredError(reason)

                candidates = self._records_from_api(start_url)
                try:
                    candidates.extend(self._records_from_dom(page, start_url))
                except PlaywrightError as exc:
                    if "/web/user/" in page.url:
                        raise SessionRequiredError(
                            "BOSS redirected to login while rendering the list"
                        ) from exc
                    raise
                self._append_candidate_records(
                    result,
                    candidates,
                    numeric_price_only=numeric_price_only,
                    accepted_record_ids=accepted_record_ids,
                    seen_candidate_keys=seen_candidate_keys,
                )
                if not candidates:
                    if page.locator("iframe").count() > 0:
                        raise SessionRequiredError(
                            "BOSS security/login interstitial requires headed human handoff"
                        )
                    raise CrawlerError("BOSS rendered no job records")

                max_scrolls = int(self.site_config.get("max_scrolls", 4))
                for _ in range(max_scrolls):
                    if len(result.records) >= limit:
                        break
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1_800)
                    candidates = self._records_from_api(start_url)
                    candidates.extend(self._records_from_dom(page, start_url))
                    self._append_candidate_records(
                        result,
                        candidates,
                        numeric_price_only=numeric_price_only,
                        accepted_record_ids=accepted_record_ids,
                        seen_candidate_keys=seen_candidate_keys,
                    )
                result.pages_fetched = 1
                result.runtime_notes.append(
                    "Captured only allowlisted job-list response summaries; no cookies or auth headers were logged."
                )
                result.runtime_notes.append(
                    f"network_capture={self._capture_summaries[:10]}"
                )
            except Exception as exc:
                self.failure(result, stage="browser_sample", exc=exc, url=start_url)
                result.runtime_notes.append(f"final_path={urlparse(page.url).path}")
                if self._capture_summaries:
                    result.runtime_notes.append(
                        f"network_capture={self._capture_summaries[:10]}"
                    )
                if isinstance(exc, SessionRequiredError):
                    if bool(self.site_config.get("anonymous_only", True)):
                        result.runtime_notes.append(
                            "Anonymous live route was challenged; account login was not requested."
                        )
                    else:
                        result.runtime_notes.append(
                            "Run the headed auth command once, then repeat the sample."
                        )
            finally:
                try:
                    context.close()
                except PlaywrightError:
                    pass
                if browser is not None:
                    try:
                        browser.close()
                    except PlaywrightError:
                        pass
        if len(result.records) < limit:
            if self._archive_fallback_enabled():
                try:
                    self._append_archive_fallback(
                        result,
                        limit=limit,
                        numeric_price_only=numeric_price_only,
                        accepted_record_ids=accepted_record_ids,
                        seen_candidate_keys=seen_candidate_keys,
                    )
                except Exception as exc:
                    self.failure(
                        result, stage="archive_fallback", exc=exc, url=start_url
                    )
            else:
                result.runtime_notes.append(
                    "BOSS live-only mode: archive fallback is disabled; "
                    "no historical records were substituted."
                )
        result.records = dedupe_records(result.records)[:limit]
        result.finish()
        self.checkpoint(result, "finished")
        return result


def absolute_url(base: str, value: str | None) -> str | None:
    return urljoin(base, value) if value else None
