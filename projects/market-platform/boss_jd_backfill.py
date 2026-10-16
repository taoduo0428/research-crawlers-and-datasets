"""Backfill missing job descriptions from the public job-card endpoint.

The crawler is standalone: it reads the JSONL catalog beside this file, uses
bounded request workers, and appends results to a resumable checkpoint. The
optional Camoufox mode reproduces list-page context for diagnostics. The apply
command updates JSONL, CSV, and XLSX only after making timestamped backups and
validating temporary replacements.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import random
import re
import shutil
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote

import requests


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(r"C:\Users\lenovo\Desktop\外包+岗位爬虫\outputs\招聘--完整数据\BOSS")
CONFIG_PATH = BASE_DIR / "boss_jd_config.json"
SOURCE_JSONL = DATA_DIR / "岗位数据.jsonl"
SOURCE_CSV = DATA_DIR / "岗位数据.csv"
SOURCE_XLSX = DATA_DIR / "岗位数据.xlsx"
STATE_DIR = DATA_DIR / "jd_backfill_state"
RESULTS_PATH = STATE_DIR / "results.jsonl"
SUMMARY_PATH = STATE_DIR / "run_summary.json"
APPLY_SUMMARY_PATH = STATE_DIR / "apply_summary.json"
LOCK_PATH = STATE_DIR / "run.lock"
BACKUP_ROOT = DATA_DIR / "jd_backfill_backups"

HOST = "https://www.zhipin.com"
CARD_PATH = "/wapi/zpgeek/view/job/card.json"
JOB_ID_RE = re.compile(r"/job_detail/([^/?#]+?)(?:\.html)?(?:[/?#]|$)", re.IGNORECASE)
SPACE_RE = re.compile(r"[^\S\r\n]+")
BLANK_RE = re.compile(r"\n{3,}")
ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u2060\ufeff]")
RETRY_HTTP = frozenset({429, 500, 502, 503, 504})
BLOCK_HTTP = frozenset({401, 403, 418})
BLOCK_API_CODES = frozenset({35, "35"})
BLOCK_STATUSES = frozenset(
    {"blocked_code_35", "blocked_list_context", "blocked_http_401", "blocked_http_403", "blocked_http_418"}
)
TERMINAL_STATUSES = frozenset({"expired_http_404", "expired_http_410", "unavailable", "missing_job_id"})

_THREAD_STATE = threading.local()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row is not an object at {path}:{line_number}")
            yield value


def record_key(row: dict[str, Any]) -> str:
    for field in ("record_id", "job_id", "detail_url", "job_link"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def encrypted_job_id(row: dict[str, Any]) -> str:
    value = str(row.get("encrypt_job_id") or "").strip()
    if value:
        return value
    for field in ("detail_url", "job_link"):
        match = JOB_ID_RE.search(str(row.get(field) or ""))
        if match:
            return match.group(1)
    return ""


def build_card_url(row: dict[str, Any]) -> str:
    job_id = encrypted_job_id(row)
    if not job_id:
        raise ValueError("missing encrypted job id")
    return f"{HOST}{CARD_PATH}?jid={quote(job_id, safe='')}&lid=&type=3"


def is_list_challenge(final_url: str, body: str) -> bool:
    lowered_url = final_url.lower()
    sample = body[:20_000]
    return (
        "/passport/zp/verify.html" in lowered_url
        or "code=35" in lowered_url
        or ("安全验证" in sample and ("异常访问" in sample or "IP 地址" in sample))
    )


class DetailTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.capture = False
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = ""
        for name, value in attrs:
            if name == "class":
                classes = value or ""
                break
        if not self.capture and "detail-bottom-text" in classes.split():
            self.capture = True
            self.depth = 1
            return
        if self.capture:
            if tag.lower() == "br":
                self.parts.append("\n")
            elif tag.lower() not in {"img", "meta", "link", "input", "hr"}:
                self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.capture and tag.lower() == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self.capture or tag.lower() == "br":
            return
        self.depth -= 1
        if self.depth <= 0:
            self.capture = False

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)


def extract_jd(raw_html: str) -> str:
    if not raw_html:
        return ""
    parser = DetailTextParser()
    parser.feed(raw_html)
    parser.close()
    text = html.unescape("".join(parser.parts))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = ZERO_WIDTH_RE.sub("", text)
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return BLANK_RE.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def base_result(row: dict[str, Any], status: str, *, attempt: int = 0) -> dict[str, Any]:
    return {
        "record_id": record_key(row),
        "encrypt_job_id": encrypted_job_id(row),
        "detail_url": str(row.get("detail_url") or row.get("job_link") or ""),
        "status": status,
        "jd": "",
        "attempt": attempt,
        "fetched_at": now_iso(),
    }


def parse_response(
    row: dict[str, Any],
    *,
    http_status: int,
    payload: Any,
    elapsed_ms: int,
    attempt: int,
) -> dict[str, Any]:
    if http_status in {404, 410}:
        result = base_result(row, f"expired_http_{http_status}", attempt=attempt)
    elif http_status in BLOCK_HTTP:
        result = base_result(row, f"blocked_http_{http_status}", attempt=attempt)
    elif not isinstance(payload, dict):
        result = base_result(row, "invalid_json", attempt=attempt)
    else:
        code = payload.get("code")
        if code in BLOCK_API_CODES:
            result = base_result(row, "blocked_code_35", attempt=attempt)
        elif http_status != 200:
            result = base_result(row, f"http_{http_status}", attempt=attempt)
        elif code != 0:
            result = base_result(row, f"api_code_{code}", attempt=attempt)
        else:
            raw_html = str((payload.get("zpData") or {}).get("html") or "")
            jd = extract_jd(raw_html)
            result = base_result(row, "success" if jd else "unavailable", attempt=attempt)
            result["jd"] = jd
            result["html_length"] = len(raw_html)
        result["api_code"] = code
        message = payload.get("message") or payload.get("msg")
        if message:
            result["api_message"] = str(message)[:240]
    result["http_status"] = http_status
    result["elapsed_ms"] = elapsed_ms
    return result


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("config must be a JSON object")
    return value


def _session(proxy: str) -> requests.Session:
    existing = getattr(_THREAD_STATE, "session", None)
    existing_proxy = getattr(_THREAD_STATE, "proxy", None)
    if existing is not None and existing_proxy == proxy:
        return existing
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    session.proxies.update({"http": proxy, "https": proxy})
    _THREAD_STATE.session = session
    _THREAD_STATE.proxy = proxy
    _THREAD_STATE.last_start = 0.0
    return session


def _pace(delay_min: float, delay_max: float) -> None:
    last_start = float(getattr(_THREAD_STATE, "last_start", 0.0))
    if last_start:
        remaining = random.uniform(delay_min, delay_max) - (time.monotonic() - last_start)
        if remaining > 0:
            time.sleep(remaining)
    _THREAD_STATE.last_start = time.monotonic()


def _retry_delay(attempt: int, retry_after: str = "") -> float:
    try:
        return min(30.0, max(0.5, float(retry_after)))
    except (TypeError, ValueError):
        return min(8.0, 0.5 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.25))


def block_threshold_reached(consecutive_blocks: int, threshold: int) -> bool:
    return threshold > 0 and consecutive_blocks >= threshold


class CamoufoxListHoverFetcher:
    def __init__(self, *, proxy: str, headed: bool, timeout_ms: int) -> None:
        self.proxy = proxy
        self.headed = headed
        self.timeout_ms = timeout_ms
        self.manager: Any | None = None
        self.browser: Any | None = None
        self.context: Any | None = None
        self.page: Any | None = None
        self.playwright_error: type[Exception] = Exception
        self.playwright_timeout: type[Exception] = TimeoutError

    def _start(self) -> None:
        if self.page is not None:
            return
        from camoufox.sync_api import Camoufox
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        launch: dict[str, Any] = {
            "headless": not self.headed,
            "humanize": True,
            "geoip": False,
        }
        if self.proxy:
            launch["proxy"] = {"server": self.proxy}
        self.manager = Camoufox(**launch)
        self.browser = self.manager.__enter__()
        self.context = self.browser.new_context(locale="zh-CN")
        self.page = self.context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        self.playwright_error = PlaywrightError
        self.playwright_timeout = PlaywrightTimeoutError

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
        if self.manager is not None:
            self.manager.__exit__(None, None, None)
        self.manager = None
        self.browser = None
        self.context = None
        self.page = None

    def _fetch_card(self, row: dict[str, Any]) -> tuple[int, str, str]:
        assert self.page is not None
        job_id = encrypted_job_id(row)
        card_url = build_card_url(row)
        card_link = self.page.locator(f'a[href*="/job_detail/{job_id}.html"]').first
        if card_link.count() > 0:
            try:
                with self.page.expect_response(
                    lambda response: CARD_PATH in response.url and f"jid={job_id}" in response.url,
                    timeout=min(self.timeout_ms, 5_000),
                ) as response_info:
                    card_link.hover(timeout=min(self.timeout_ms, 3_000))
                response = response_info.value
                return response.status, response.text(), "list_hover"
            except self.playwright_timeout:
                pass
        fetched = self.page.evaluate(
            """async (url) => {
                const response = await fetch(url, {
                    credentials: 'include',
                    headers: {'Accept': 'application/json, text/plain, */*'}
                });
                return {status: response.status, text: await response.text()};
            }""",
            card_url,
        )
        return int(fetched["status"]), str(fetched["text"]), "list_context_fetch"

    def fetch_one(
        self,
        row: dict[str, Any],
        *,
        proxy: str,
        delay_min: float,
        delay_max: float,
        connect_timeout: float,
        read_timeout: float,
        max_attempts: int,
        stop_event: threading.Event,
    ) -> dict[str, Any]:
        del proxy, connect_timeout, read_timeout
        if not encrypted_job_id(row):
            return base_result(row, "missing_job_id")
        list_url = str(row.get("list_url") or "").strip()
        if not list_url:
            return base_result(row, "missing_list_url")

        self._start()
        assert self.page is not None and self.context is not None
        for attempt in range(1, max_attempts + 1):
            if stop_event.is_set():
                return base_result(row, "circuit_stopped", attempt=attempt)
            _pace(delay_min, delay_max)
            started = time.perf_counter()
            try:
                list_response = self.page.goto(list_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self.page.wait_for_timeout(750)
                body = self.page.locator("body").inner_text(timeout=min(self.timeout_ms, 5_000))
                if is_list_challenge(self.page.url, body):
                    result = base_result(row, "blocked_list_context", attempt=attempt)
                    result.update(
                        {
                            "transport": "camoufox-list-hover",
                            "list_http_status": list_response.status if list_response else None,
                            "list_final_url": self.page.url,
                            "session_cookie_count": len(self.context.cookies()),
                            "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        }
                    )
                    return result

                http_status, response_text, triggered_by = self._fetch_card(row)
                try:
                    payload = json.loads(response_text)
                except json.JSONDecodeError:
                    payload = None
                result = parse_response(
                    row,
                    http_status=http_status,
                    payload=payload,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    attempt=attempt,
                )
                result.update(
                    {
                        "transport": "camoufox-list-hover",
                        "triggered_by": triggered_by,
                        "list_final_url": self.page.url,
                        "session_cookie_count": len(self.context.cookies()),
                    }
                )
                status = str(result["status"])
                if status == "success" or status in BLOCK_STATUSES or status in TERMINAL_STATUSES:
                    return result
                if attempt < max_attempts:
                    time.sleep(_retry_delay(attempt))
                    continue
                return result
            except self.playwright_timeout:
                if attempt < max_attempts:
                    time.sleep(_retry_delay(attempt))
                    continue
                result = base_result(row, "browser_timeout", attempt=attempt)
                result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
                return result
            except self.playwright_error as exc:
                result = base_result(row, "browser_error", attempt=attempt)
                result.update({"elapsed_ms": int((time.perf_counter() - started) * 1000), "error": type(exc).__name__})
                return result
        return base_result(row, "browser_error", attempt=max_attempts)


def fetch_one(
    row: dict[str, Any],
    *,
    proxy: str,
    delay_min: float,
    delay_max: float,
    connect_timeout: float,
    read_timeout: float,
    max_attempts: int,
    stop_event: threading.Event,
) -> dict[str, Any]:
    if not encrypted_job_id(row):
        return base_result(row, "missing_job_id")
    url = build_card_url(row)
    session = _session(proxy)
    for attempt in range(1, max_attempts + 1):
        if stop_event.is_set():
            return base_result(row, "circuit_stopped", attempt=attempt)
        _pace(delay_min, delay_max)
        started = time.perf_counter()
        response: requests.Response | None = None
        try:
            session.cookies.clear()
            response = session.get(
                url,
                headers={"Referer": str(row.get("detail_url") or row.get("job_link") or HOST)},
                timeout=(connect_timeout, read_timeout),
                allow_redirects=False,
                cookies={},
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            try:
                payload = response.json()
            except ValueError:
                payload = None
            result = parse_response(
                row,
                http_status=response.status_code,
                payload=payload,
                elapsed_ms=elapsed_ms,
                attempt=attempt,
            )
            status = str(result["status"])
            if status == "success" or status in BLOCK_STATUSES or status in TERMINAL_STATUSES or status.startswith("api_code_"):
                return result
            if response.status_code in RETRY_HTTP or status == "invalid_json":
                if attempt < max_attempts:
                    time.sleep(_retry_delay(attempt, response.headers.get("Retry-After", "")))
                    continue
            return result
        except requests.RequestException as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if attempt < max_attempts:
                time.sleep(_retry_delay(attempt))
                continue
            result = base_result(row, "network_error", attempt=attempt)
            result.update({"elapsed_ms": elapsed_ms, "error": type(exc).__name__})
            return result
        finally:
            session.cookies.clear()
    return base_result(row, "network_error", attempt=max_attempts)


class FileLock(AbstractContextManager["FileLock"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"another run holds {self.path}") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        if os.name == "nt":
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


class JsonlAppender(AbstractContextManager["JsonlAppender"]):
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a", encoding="utf-8", newline="\n")

    def write(self, value: dict[str, Any]) -> None:
        self.handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.handle.close()


def load_completed(results_path: Path) -> tuple[set[str], set[str]]:
    successful: set[str] = set()
    terminal: set[str] = set()
    if not results_path.is_file():
        return successful, terminal
    for row in iter_jsonl(results_path):
        key = record_key(row)
        status = str(row.get("status") or "")
        if not key:
            continue
        if status == "success" and str(row.get("jd") or "").strip():
            successful.add(key)
            terminal.discard(key)
        elif status in TERMINAL_STATUSES and key not in successful:
            terminal.add(key)
    return successful, terminal


def iter_pending(
    source: Path,
    *,
    successful: set[str],
    terminal: set[str],
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    seen: set[str] = set()
    selected = 0
    for row in iter_jsonl(source):
        key = record_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        if str(row.get("description_raw") or row.get("jd") or "").strip():
            continue
        if key in successful or key in terminal:
            continue
        yield row
        selected += 1
        if limit is not None and selected >= limit:
            return


def run_crawl(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if not args.source.is_file():
        raise FileNotFoundError(args.source)
    if args.workers < 1 or args.max_inflight < 1:
        raise ValueError("workers and max in-flight requests must be positive")
    if args.mode == "camoufox-list-hover" and (args.workers != 1 or args.max_inflight != 1):
        raise ValueError("camoufox-list-hover requires exactly one worker and one in-flight request")
    if not 0 <= args.delay_min <= args.delay_max:
        raise ValueError("delay range is invalid")
    if args.max_attempts < 1:
        raise ValueError("max attempts must be positive")
    if args.block_threshold < 0:
        raise ValueError("block threshold cannot be negative")

    successful, terminal = load_completed(args.results)
    pending = iter_pending(args.source, successful=successful, terminal=terminal, limit=args.limit)
    stop_event = threading.Event()
    stats: Counter[str] = Counter()
    started_at = now_iso()
    submitted = 0
    finished = 0
    consecutive_blocks = 0
    stopped_reason = "completed"
    print(json.dumps({"event": "crawl_started", "source": str(args.source), "results": str(args.results), "mode": args.mode, "workers": args.workers, "max_inflight": args.max_inflight, "block_threshold": args.block_threshold}, ensure_ascii=False), flush=True)
    browser_fetcher: CamoufoxListHoverFetcher | None = None
    fetch_callable: Any = fetch_one
    if args.mode == "camoufox-list-hover":
        browser_fetcher = CamoufoxListHoverFetcher(
            proxy=args.proxy,
            headed=args.headed,
            timeout_ms=args.browser_timeout_ms,
        )
        fetch_callable = browser_fetcher.fetch_one

    with FileLock(args.lock), JsonlAppender(args.results) as writer:
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="boss-jd") as executor:
            futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}

            def fill() -> None:
                nonlocal submitted
                while not stop_event.is_set() and len(futures) < args.max_inflight:
                    try:
                        row = next(pending)
                    except StopIteration:
                        return
                    future = executor.submit(
                        fetch_callable,
                        row,
                        proxy=args.proxy,
                        delay_min=args.delay_min,
                        delay_max=args.delay_max,
                        connect_timeout=args.connect_timeout,
                        read_timeout=args.read_timeout,
                        max_attempts=args.max_attempts,
                        stop_event=stop_event,
                    )
                    futures[future] = row
                    submitted += 1

            try:
                fill()
                while futures:
                    done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                    batch_results: list[dict[str, Any]] = []
                    for future in done:
                        row = futures.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = base_result(row, "worker_error")
                            result["error"] = type(exc).__name__
                        batch_results.append(result)
                    for result in batch_results:
                        writer.write(result)
                        status = str(result.get("status") or "unknown")
                        stats[status] += 1
                        finished += 1
                        if status in BLOCK_STATUSES:
                            consecutive_blocks += 1
                        elif status == "success":
                            consecutive_blocks = 0
                    print(json.dumps({"event": "result", "finished": finished, "submitted": submitted, "status": status, "record_id": result.get("record_id"), "api_code": result.get("api_code")}, ensure_ascii=False), flush=True)
                    if block_threshold_reached(consecutive_blocks, args.block_threshold):
                        stopped_reason = "upstream_blocked"
                        stop_event.set()
                    if stop_event.is_set():
                        for future in futures:
                            future.cancel()
                    else:
                        fill()
            finally:
                if browser_fetcher is not None:
                    executor.submit(browser_fetcher.close).result()

    summary = {
        "status": "blocked" if stopped_reason != "completed" else "complete",
        "stopped_reason": stopped_reason,
        "started_at": started_at,
        "finished_at": now_iso(),
        "source": str(args.source),
        "results": str(args.results),
        "proxy": args.proxy,
        "mode": args.mode,
        "workers": args.workers,
        "max_inflight": args.max_inflight,
        "delay_seconds": [args.delay_min, args.delay_max],
        "block_threshold": args.block_threshold,
        "submitted": submitted,
        "finished": finished,
        "already_successful": len(successful),
        "already_terminal": len(terminal),
        "status_counts": dict(stats),
    }
    atomic_json_write(args.summary, summary)
    return summary, 0 if summary["status"] == "complete" else 3


def latest_successes(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for row in iter_jsonl(path):
        key = record_key(row)
        jd = str(row.get("jd") or "").strip()
        if key and row.get("status") == "success" and jd:
            values[key] = jd
    return values


def _write_jsonl_with_jd(source: Path, target: Path, jd_by_key: dict[str, str]) -> tuple[int, int]:
    rows = 0
    filled = 0
    with target.open("w", encoding="utf-8", newline="\n") as output:
        for row in iter_jsonl(source):
            rows += 1
            key = record_key(row)
            if not str(row.get("description_raw") or "").strip() and key in jd_by_key:
                row["description_raw"] = jd_by_key[key]
                filled += 1
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        output.flush()
        os.fsync(output.fileno())
    return rows, filled


def _write_csv_with_jd(source: Path, target: Path, jd_by_key: dict[str, str]) -> tuple[int, int]:
    rows = 0
    filled = 0
    with source.open("r", encoding="utf-8-sig", newline="") as input_handle:
        reader = csv.DictReader(input_handle)
        fieldnames = list(reader.fieldnames or [])
        if "record_id" not in fieldnames or "description_raw" not in fieldnames:
            raise ValueError("CSV requires record_id and description_raw columns")
        with target.open("w", encoding="utf-8-sig", newline="") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                rows += 1
                key = str(row.get("record_id") or "").strip()
                if not str(row.get("description_raw") or "").strip() and key in jd_by_key:
                    row["description_raw"] = jd_by_key[key]
                    filled += 1
                writer.writerow(row)
            output_handle.flush()
            os.fsync(output_handle.fileno())
    return rows, filled


def _write_xlsx_with_jd(source: Path, target: Path, jd_by_key: dict[str, str]) -> tuple[int, int]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is required for XLSX update") from exc
    workbook = load_workbook(source)
    sheet = workbook.active
    headers = {str(cell.value or ""): index for index, cell in enumerate(sheet[1], 1)}
    if "record_id" not in headers or "description_raw" not in headers:
        workbook.close()
        raise ValueError("XLSX requires record_id and description_raw columns")

    def xml_compatible(value: str) -> str:
        return "".join(
            ch
            for ch in value
            if ord(ch) in (9, 10, 13)
            or 0x20 <= ord(ch) <= 0xD7FF
            or 0xE000 <= ord(ch) <= 0xFFFD
            or 0x10000 <= ord(ch) <= 0x10FFFF
        )

    rows = 0
    filled = 0
    for row_number in range(2, sheet.max_row + 1):
        rows += 1
        key = str(sheet.cell(row_number, headers["record_id"]).value or "").strip()
        description_cell = sheet.cell(row_number, headers["description_raw"])
        current = str(description_cell.value or "").strip()
        if key in jd_by_key and (not current or description_cell.data_type == "f"):
            safe_jd = xml_compatible(jd_by_key[key])
            description_cell.value = safe_jd
            if safe_jd.startswith("="):
                description_cell.data_type = "s"
            filled += 1
    workbook.save(target)
    workbook.close()
    return rows, filled


def _count_xlsx_rows(path: Path) -> int:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    rows = max(0, workbook.active.max_row - 1)
    workbook.close()
    return rows


def run_apply(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    successes = latest_successes(args.results)
    if not successes:
        raise ValueError("no successful JD results are available")
    sources = (args.source, args.csv, args.xlsx)
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    before_hashes = {str(path): sha256_file(path) for path in sources}
    for source in sources:
        shutil.copy2(source, backup_dir / source.name)

    json_temp = args.source.with_name(f".{args.source.name}.jdtmp")
    csv_temp = args.csv.with_name(f".{args.csv.name}.jdtmp")
    xlsx_temp = args.xlsx.with_name(f".{args.xlsx.name}.jdtmp.xlsx")
    replaced = False
    try:
        json_rows, json_filled = _write_jsonl_with_jd(args.source, json_temp, successes)
        csv_rows, csv_filled = _write_csv_with_jd(args.csv, csv_temp, successes)
        xlsx_rows, xlsx_filled = _write_xlsx_with_jd(args.xlsx, xlsx_temp, successes)
        if len({json_rows, csv_rows, xlsx_rows}) != 1:
            raise ValueError(f"row count mismatch: {json_rows}, {csv_rows}, {xlsx_rows}")
        if _count_xlsx_rows(xlsx_temp) != xlsx_rows:
            raise ValueError("temporary XLSX validation failed")
        os.replace(json_temp, args.source)
        os.replace(csv_temp, args.csv)
        os.replace(xlsx_temp, args.xlsx)
        replaced = True
    except Exception:
        if replaced:
            for source in sources:
                shutil.copy2(backup_dir / source.name, source)
        raise

    after_hashes = {str(path): sha256_file(path) for path in sources}
    summary = {
        "status": "complete",
        "applied_at": now_iso(),
        "results": str(args.results),
        "successful_results": len(successes),
        "backup_dir": str(backup_dir),
        "rows": json_rows,
        "filled": {"jsonl": json_filled, "csv": csv_filled, "xlsx": xlsx_filled},
        "before_sha256": before_hashes,
        "after_sha256": after_hashes,
    }
    atomic_json_write(args.apply_summary, summary)
    return summary, 0


def status_report(args: argparse.Namespace) -> dict[str, Any]:
    successful, terminal = load_completed(args.results)
    counts: Counter[str] = Counter()
    if args.results.is_file():
        for row in iter_jsonl(args.results):
            counts[str(row.get("status") or "unknown")] += 1
    report: dict[str, Any] = {
        "results": str(args.results),
        "successful_unique": len(successful),
        "terminal_unique": len(terminal),
        "result_status_counts": dict(counts),
    }
    if args.summary.is_file():
        with args.summary.open("r", encoding="utf-8") as handle:
            report["last_run"] = json.load(handle)
    return report


def _jsonl_jd_map(path: Path) -> tuple[int, dict[str, str]]:
    rows = 0
    values: dict[str, str] = {}
    for row in iter_jsonl(path):
        rows += 1
        key = record_key(row)
        jd = str(row.get("description_raw") or "").strip()
        if key and jd:
            values[key] = jd
    return rows, values


def _csv_jd_map(path: Path) -> tuple[int, dict[str, str]]:
    rows = 0
    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            key = str(row.get("record_id") or "").strip()
            jd = str(row.get("description_raw") or "").strip()
            if key and jd:
                values[key] = jd
    return rows, values


def _xlsx_jd_map(path: Path) -> tuple[int, dict[str, str]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    header_values = [str(cell.value or "") for cell in sheet[1]]
    header = {name: index for index, name in enumerate(header_values)}
    if "record_id" not in header or "description_raw" not in header:
        workbook.close()
        raise ValueError("XLSX requires record_id and description_raw columns")
    values: dict[str, str] = {}
    rows = 0
    for cells in sheet.iter_rows(min_row=2, values_only=True):
        rows += 1
        key = str(cells[header["record_id"]] or "").strip()
        jd = str(cells[header["description_raw"]] or "").strip()
        if key and jd:
            values[key] = jd
    workbook.close()
    return rows, values


def _jd_map_hash(values: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for key in sorted(values):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(values[key].encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def run_verify(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    json_rows, json_values = _jsonl_jd_map(args.source)
    csv_rows, csv_values = _csv_jd_map(args.csv)
    xlsx_rows, xlsx_values = _xlsx_jd_map(args.xlsx)

    def xml_compatible(value: str) -> str:
        return "".join(
            ch
            for ch in value
            if ord(ch) in (9, 10, 13)
            or 0x20 <= ord(ch) <= 0xD7FF
            or 0xE000 <= ord(ch) <= 0xFFFD
            or 0x10000 <= ord(ch) <= 0x10FFFF
        )

    normalized_json = {key: xml_compatible(value) for key, value in json_values.items()}
    normalized_csv = {key: xml_compatible(value) for key, value in csv_values.items()}
    normalized_xlsx = {key: xml_compatible(value) for key, value in xlsx_values.items()}
    same_rows = len({json_rows, csv_rows, xlsx_rows}) == 1
    same_values = json_values == csv_values and normalized_json == normalized_xlsx and normalized_csv == normalized_xlsx
    result: dict[str, Any] = {
        "status": "complete" if same_rows and same_values else "failed",
        "verified_at": now_iso(),
        "row_counts": {"jsonl": json_rows, "csv": csv_rows, "xlsx": xlsx_rows},
        "jd_counts": {"jsonl": len(json_values), "csv": len(csv_values), "xlsx": len(xlsx_values)},
        "jd_map_sha256": {
            "jsonl": _jd_map_hash(json_values),
            "csv": _jd_map_hash(csv_values),
            "xlsx": _jd_map_hash(xlsx_values),
        },
        "normalized_jd_map_sha256": {
            "jsonl": _jd_map_hash(normalized_json),
            "csv": _jd_map_hash(normalized_csv),
            "xlsx": _jd_map_hash(normalized_xlsx),
        },
        "data_sha256": {
            "jsonl": sha256_file(args.source),
            "csv": sha256_file(args.csv),
            "xlsx": sha256_file(args.xlsx),
        },
        "same_row_counts": same_rows,
        "same_jd_values": same_values,
        "xlsx_xml_normalization": "XML 1.0 incompatible characters are omitted from XLSX comparison",
    }
    if args.summary.is_file():
        with args.summary.open("r", encoding="utf-8") as handle:
            result["last_crawl"] = json.load(handle)
    if args.apply_summary.is_file():
        with args.apply_summary.open("r", encoding="utf-8") as handle:
            result["last_apply"] = json.load(handle)
    atomic_json_write(args.verification, result)
    return result, 0 if result["status"] == "complete" else 1


def run_import(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if not args.import_from:
        raise ValueError("import requires at least one --from-jsonl path")
    successful, _ = load_completed(args.results)
    imported = 0
    skipped = 0
    source_counts: dict[str, int] = {}
    with JsonlAppender(args.results) as writer:
        for source in args.import_from:
            if not source.is_file():
                raise FileNotFoundError(source)
            source_imported = 0
            for row in iter_jsonl(source):
                key = record_key(row)
                jd = str(row.get("jd") or row.get("description_raw") or "").strip()
                if not key or not jd or key in successful:
                    skipped += 1
                    continue
                result = {
                    "record_id": key,
                    "encrypt_job_id": encrypted_job_id(row),
                    "detail_url": str(row.get("detail_url") or row.get("job_link") or ""),
                    "status": "success",
                    "jd": jd,
                    "attempt": 0,
                    "fetched_at": str(row.get("fetched_at") or now_iso()),
                    "imported_from": str(source),
                }
                writer.write(result)
                successful.add(key)
                imported += 1
                source_imported += 1
            source_counts[str(source)] = source_imported
    return {
        "status": "complete",
        "imported": imported,
        "skipped": skipped,
        "successful_unique": len(successful),
        "sources": source_counts,
        "results": str(args.results),
    }, 0


def build_parser(config: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("crawl", "import", "apply", "status", "verify"), default="crawl")
    parser.add_argument("--source", type=Path, default=SOURCE_JSONL)
    parser.add_argument("--csv", type=Path, default=SOURCE_CSV)
    parser.add_argument("--xlsx", type=Path, default=SOURCE_XLSX)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--apply-summary", type=Path, default=APPLY_SUMMARY_PATH)
    parser.add_argument("--verification", type=Path, default=STATE_DIR / "data_verification.json")
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--proxy", default=config.get("proxy", "http://127.0.0.1:7897"))
    parser.add_argument(
        "--mode",
        choices=("direct", "camoufox-list-hover", "ip-pool"),
        default=str(config.get("mode", "direct")),
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--browser-timeout-ms", type=int, default=int(config.get("browser_timeout_ms", 30_000)))
    parser.add_argument("--workers", type=int, default=int(config.get("workers", 4)))
    parser.add_argument("--max-inflight", type=int, default=int(config.get("max_inflight", 4)))
    parser.add_argument("--delay-min", type=float, default=float(config.get("delay_min_seconds", 0.3)))
    parser.add_argument("--delay-max", type=float, default=float(config.get("delay_max_seconds", 0.5)))
    parser.add_argument("--connect-timeout", type=float, default=float(config.get("connect_timeout_seconds", 5.0)))
    parser.add_argument("--read-timeout", type=float, default=float(config.get("read_timeout_seconds", 15.0)))
    parser.add_argument("--max-attempts", type=int, default=int(config.get("max_attempts", 2)))
    parser.add_argument("--block-threshold", type=int, default=int(config.get("block_threshold", 0)))
    parser.add_argument("--pool-ttl-seconds", type=int, default=int(config.get("pool_ttl_seconds", 300)))
    parser.add_argument("--pool-max-row-retries", type=int, default=int(config.get("pool_max_row_retries", 8)))
    parser.add_argument("--pool-max-replacements", type=int, default=int(config.get("pool_max_replacements", 2048)))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--from-jsonl", dest="import_from", type=Path, action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser(load_config()).parse_args(argv)
        if args.limit is not None and args.limit < 1:
            raise ValueError("limit must be positive")
        if args.command == "crawl":
            if args.mode == "ip-pool":
                from boss_ip_pool_crawl import run_pool_crawl

                result, exit_code = run_pool_crawl(args)
            else:
                result, exit_code = run_crawl(args)
        elif args.command == "import":
            result, exit_code = run_import(args)
        elif args.command == "apply":
            result, exit_code = run_apply(args)
        elif args.command == "verify":
            result, exit_code = run_verify(args)
        else:
            result, exit_code = status_report(args), 0
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "not_started", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
