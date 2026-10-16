"""Collect Liepin details through one persistent headed browser session."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from urllib3.util.retry import Retry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from drv_crawler.liepin.parser import (
    classify_access_gate,
    looks_like_challenge,
    parse_detail_description,
)
from tools.liepin_coordination import FORWARD_OWNER, LiepinCoordination


SOURCE_DEFAULT = PROJECT_ROOT / "outputs" / "招聘--完整数据" / "猎聘" / "岗位数据.jsonl"
OUTPUT_DEFAULT = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "猎聘" / "workers" / "worker-1.jsonl"
PRIOR_DEFAULT = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "猎聘" / "详情补充.jsonl"
WORKER_OUTPUTS = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "猎聘" / "workers"
COOKIE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-accounts" / "account-1.json"
PROFILE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-browser-single" / "profile"
STATE_DIR_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-browser-single"
API_OUTPUTS = PROJECT_ROOT / "API抓取" / "output"
COORD_DB_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-shared" / "coordination.sqlite3"
COOLDOWN_DEFAULT_SECONDS = 3600.0
EXIT_CHALLENGE = 86
TRANSIT_WAIT_MS = 5000
TRANSIT_POLL_MS = 250
DYNAMIC_DETAIL_WAIT_MS = 3000


@contextmanager
def profile_run_lock(path: Path) -> Iterable[None]:
    """Prevent two processes from mutating the same Liepin session family."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise RuntimeError(f"Liepin profile family is already running: {path}") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def iter_rows(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def record_key(row: dict[str, Any]) -> str:
    return str(row.get("record_id") or row.get("job_id") or row.get("detail_url") or "").strip()


def has_detail(row: dict[str, Any]) -> bool:
    return bool(str(row.get("description_raw") or row.get("jd") or "").strip())


def completed_ids(output: Path) -> set[str]:
    paths = [
        PRIOR_DEFAULT,
        *sorted(WORKER_OUTPUTS.glob("worker-*.jsonl")),
        *sorted(API_OUTPUTS.glob("*.jsonl")),
    ]
    if output not in paths:
        paths.append(output)
    return {
        record_key(row)
        for path in paths
        for row in iter_rows(path)
        if record_key(row) and has_detail(row)
    }


def load_empty_attempts(path: Path) -> dict[str, int]:
    attempts: dict[str, int] = {}
    if not path.is_file():
        return attempts
    for row in iter_rows(path):
        record_id = record_key(row)
        if record_id and row.get("status") == "empty":
            attempts[record_id] = attempts.get(record_id, 0) + 1
    return attempts


def load_terminal_ids(path: Path) -> set[str]:
    """Load jobs that are permanently unavailable and must not be retried."""
    return {
        record_key(row)
        for row in iter_rows(path)
        if record_key(row) and row.get("status") in {"unavailable", "expired"}
    }


def classify_empty_detail(html: str) -> str:
    """Distinguish a temporary empty page from an expired/unavailable listing."""
    markers = (
        "\u6682\u505c\u62db\u8058",
        "\u804c\u4f4d\u5df2\u4e0b\u7ebf",
        "\u804c\u4f4d\u4e0d\u5b58\u5728",
        "\u62db\u8058\u5df2\u7ed3\u675f",
        "\u8be5\u804c\u4f4d\u5df2\u5931\u6548",
    )
    text = html.lower()
    return "unavailable" if any(marker in text for marker in markers) else "empty"


def append_checkpoint(path: Path, record_id: str, url: str, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"record_id": record_id, "detail_url": url, "status": status}, ensure_ascii=False) + "\n")
        handle.flush()


def load_cookies(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict):
        raw = raw.get("cookies", [])
    if not isinstance(raw, list):
        raise RuntimeError(f"invalid Liepin cookie state in {path}")
    cookies: list[dict[str, Any]] = []
    for item in raw:
        domain = str(item.get("domain") or "")
        if not domain.lstrip(".").endswith("liepin.com") or not item.get("name"):
            continue
        cookie: dict[str, Any] = {
            "name": str(item["name"]),
            "value": str(item.get("value") or ""),
            "domain": domain,
            "path": str(item.get("path") or "/"),
            "secure": bool(item.get("secure")),
            "httpOnly": bool(item.get("httpOnly")),
        }
        same_site = item.get("sameSite")
        if same_site in {"Strict", "Lax", "None"}:
            cookie["sameSite"] = same_site
        expires = item.get("expirationDate", item.get("expires"))
        if isinstance(expires, (int, float)) and expires > time.time():
            cookie["expires"] = float(expires)
        cookies.append(cookie)
    if not cookies:
        raise RuntimeError(f"no usable Liepin cookies in {path}")
    return cookies


def load_storage_origins(path: Path) -> dict[str, dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for item in raw.get("origins", []):
        if not isinstance(item, dict):
            continue
        origin = str(item.get("origin") or "").rstrip("/")
        host = (urlparse(origin).hostname or "").lower()
        if host != "liepin.com" and not host.endswith(".liepin.com"):
            continue
        values = {
            str(entry["name"]): str(entry.get("value") or "")
            for entry in item.get("localStorage", [])
            if isinstance(entry, dict) and entry.get("name")
        }
        if values:
            result[origin] = values
    return result


def storage_init_script(origins: dict[str, dict[str, str]]) -> str:
    encoded = json.dumps(origins, ensure_ascii=False, separators=(",", ":"))
    return (
        "(() => { const states = " + encoded + "; "
        "const values = states[location.origin]; if (!values) return; "
        "for (const [key, value] of Object.entries(values)) "
        "localStorage.setItem(key, value); })();"
    )


def page_access_gate(page: Any) -> str | None:
    """Return the actual login/challenge class; browser errors remain errors."""
    html = page.content()
    return classify_access_gate(html, page.url)


def challenge(page: Any) -> bool:
    try:
        return page_access_gate(page) is not None
    except PlaywrightError:
        return False


def sync_static_session_from_browser(
    context: Any,
    page: Any,
    static_session: requests.Session,
) -> None:
    """Align the HTTP fast path with the one persistent Edge session."""
    static_session.headers["User-Agent"] = str(page.evaluate("navigator.userAgent"))
    static_session.cookies.clear()
    for cookie in context.cookies():
        domain = str(cookie.get("domain") or "")
        name = str(cookie.get("name") or "")
        if name and domain.lstrip(".").endswith("liepin.com"):
            static_session.cookies.set(
                name,
                str(cookie.get("value") or ""),
                domain=domain,
                path=str(cookie.get("path") or "/"),
            )


def wait_out_security_transit(
    page: Any,
    timeout_ms: int = TRANSIT_WAIT_MS,
) -> str | None:
    """Allow Liepin's short-lived wow transit page to return to the public detail."""
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    gate = page_access_gate(page)
    while gate == "security_transit" and time.monotonic() < deadline:
        page.wait_for_timeout(TRANSIT_POLL_MS)
        gate = page_access_gate(page)
    return gate


def wait_for_dynamic_detail(
    page: Any,
    html: str,
    timeout_ms: int = DYNAMIC_DETAIL_WAIT_MS,
) -> str:
    """Wait only when a rendered page is still an empty application shell."""
    if parse_detail_description(html) or classify_empty_detail(html) == "unavailable":
        return html
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    while time.monotonic() < deadline:
        page.wait_for_timeout(TRANSIT_POLL_MS)
        html = page.content()
        if parse_detail_description(html) or classify_empty_detail(html) == "unavailable":
            break
    return html


def open_public_detail(
    context: Any,
    page: Any,
    url: str,
    navigation_timeout_ms: int,
) -> tuple[str, str | None, bool]:
    """Open one public detail without mutating the browser-wide login state."""
    page.goto(url, wait_until="domcontentloaded", timeout=navigation_timeout_ms)
    gate = wait_out_security_transit(page)
    html = page.content()
    if gate is None and not parse_detail_description(html):
        html = wait_for_dynamic_detail(page, html)
    return html, gate, False


def build_static_session() -> requests.Session:
    """Create a pooled public HTTP client with one bounded transient retry."""
    session = requests.Session()
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        backoff_factor=0.05,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_static_description(
    url: str,
    timeout: float = 3.0,
    session: requests.Session | None = None,
    attempts: int = 2,
) -> str | None:
    """Recover JD when the headed Edge DOM remains an empty application shell."""
    client = session or requests
    default_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    )
    user_agent = session.headers.get("User-Agent", default_user_agent) if session else default_user_agent
    for attempt in range(max(1, attempts)):
        try:
            response = client.get(
                url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": "https://www.liepin.com/",
                    "Cache-Control": "no-cache" if attempt else "max-age=0",
                },
                timeout=(1.0, timeout),
            )
            response.raise_for_status()
            if looks_like_challenge(response.text, response.url):
                return None
            description = parse_detail_description(response.text)
            if description:
                return description
        except requests.RequestException:
            continue
    return None


def browser_session_closed(page: Any, exc: BaseException) -> bool:
    message = str(exc).lower()
    if "target page, context or browser has been closed" in message:
        return True
    try:
        return bool(page.is_closed())
    except PlaywrightError:
        return True


def wait_for_manual_verification(page: Any, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    print("检测到猎聘安全中心，请在当前有头浏览器中手动完成验证。", flush=True)
    while time.monotonic() < deadline:
        time.sleep(2)
        if not challenge(page):
            return True
    return False


def cooldown_file(state_dir: Path) -> Path:
    return state_dir / "cooldown.json"


def read_cooldown_remaining(state_dir: Path, *, now: float | None = None) -> dict[str, Any] | None:
    path = cooldown_file(state_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    until = float(payload.get("cooldown_until") or 0)
    current = time.time() if now is None else now
    remaining = until - current
    if remaining <= 0:
        return None
    return {
        "remaining_seconds": remaining,
        "reason": str(payload.get("reason") or "cooldown"),
        "cooldown_until": until,
    }


def write_cooldown(state_dir: Path, *, seconds: float, reason: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cooldown_until": time.time() + max(60.0, float(seconds)),
        "seconds": max(60.0, float(seconds)),
        "reason": reason,
        "updated_at": time.time(),
    }
    path = cooldown_file(state_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear_cooldown(state_dir: Path) -> None:
    cooldown_file(state_dir).unlink(missing_ok=True)


def collect(
    source: Path,
    output: Path,
    cookie_file: Path,
    profile: Path,
    limit: int,
    wait_seconds: int,
    empty_retries: int,
    max_attempts: int | None,
    detail_delay_min: float,
    detail_delay_max: float,
    page_settle_ms: int,
    empty_settle_retry_ms: int,
    batch_size: int,
    batch_cooldown: float,
    reuse_profile_cookies: bool,
    consecutive_empty_limit: int,
    consecutive_error_limit: int,
    coord_db: Path,
    coord_owner: str,
    browser_channel: str | None = None,
    cooldown_seconds: float = COOLDOWN_DEFAULT_SECONDS,
    ignore_cooldown: bool = False,
    soft_fail_cooldown: float = 60.0,
) -> int:
    state_dir = profile.parent
    if not ignore_cooldown:
        active = read_cooldown_remaining(state_dir)
        if active:
            minutes = max(1, int(active["remaining_seconds"] // 60) + 1)
            print(
                f"猎聘详情处于冷却中（原因: {active['reason']}，约剩余 {minutes} 分钟）。"
                "冷却用于降低安全中心触发概率；确认已人工登录后可用 --ignore-cooldown。",
                flush=True,
            )
            return 3
    done = completed_ids(output)
    checkpoint = state_dir / "checkpoint.jsonl"
    empty_attempts = load_empty_attempts(checkpoint)
    terminal_ids = load_terminal_ids(checkpoint)
    output.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    written = 0
    failures = 0
    consecutive_empty = 0
    consecutive_errors = 0
    stop_reason: str | None = None
    with (
        LiepinCoordination(coord_db) as coordination,
        profile_run_lock(state_dir / "run.lock"),
        build_static_session() as static_session,
        sync_playwright() as playwright,
    ):
        profile.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict[str, Any] = {
            "headless": False,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1365, "height": 860},
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel
        context = playwright.chromium.launch_persistent_context(
            str(profile.resolve()), **launch_kwargs
        )
        try:
            if reuse_profile_cookies:
                print("复用持久浏览器 profile，不注入 Cookie（公开详情无需登录）", flush=True)
            else:
                print("警告: 正在注入 Cookie 文件，验证风险高于纯 profile 登录", flush=True)
                context.add_cookies(load_cookies(cookie_file))
                origins = load_storage_origins(cookie_file)
                if origins:
                    context.add_init_script(script=storage_init_script(origins))
            page = context.pages[0] if context.pages else context.new_page()
            sync_static_session_from_browser(context, page, static_session)
            progress = coordination.status()["progress"]
            resume_index = max(1, int(progress.get(coord_owner, 1)))
            for source_index, row in enumerate(iter_rows(source), 1):
                if source_index < resume_index:
                    continue
                if processed >= limit or (max_attempts is not None and processed >= max_attempts):
                    break
                if coordination.has_met(coord_owner, source_index):
                    stop_reason = "met_reverse_worker"
                    print(f"正反向进度已相遇，停止于源位置 {source_index}", flush=True)
                    break
                coordination.advance(coord_owner, source_index)
                record_id = record_key(row)
                url = str(row.get("detail_url") or "").strip()
                if (not record_id or not url or record_id in done or record_id in terminal_ids
                        or empty_attempts.get(record_id, 0) >= empty_retries):
                    continue
                if not coordination.claim(record_id, coord_owner, source_index):
                    continue
                processed += 1
                print(f"[{processed}/{limit}] {url}", flush=True)
                try:
                    html = ""
                    description = fetch_static_description(
                        url,
                        timeout=3.0,
                        session=static_session,
                    )
                    detail_source = "public_http_forward"
                    if not description:
                        html, gate, retried_anonymously = open_public_detail(
                            context,
                            page,
                            url,
                            30_000,
                        )
                        if retried_anonymously:
                            sync_static_session_from_browser(context, page, static_session)
                        if page_settle_ms:
                            page.wait_for_timeout(page_settle_ms)
                        if gate is not None:
                            deferred_status = f"deferred_{gate}"
                            append_checkpoint(checkpoint, record_id, url, deferred_status)
                            coordination.finish(record_id, coord_owner, deferred_status)
                            coordination.advance(coord_owner, source_index + 1)
                            stop_reason = "challenge"
                            print(
                                f"  {gate}（已加入延后队列；重建匿名 Edge 后继续下一条）",
                                flush=True,
                            )
                            break
                        if page_settle_ms:
                            html = page.content()
                        description = parse_detail_description(html)
                        detail_source = "headed_browser_fallback"
                        if not description and empty_settle_retry_ms > 0:
                            page.wait_for_timeout(empty_settle_retry_ms)
                            html = page.content()
                            description = parse_detail_description(html)
                    if not description:
                        failures += 1
                        status = classify_empty_detail(html)
                        if status == "unavailable":
                            terminal_ids.add(record_id)
                        else:
                            empty_attempts[record_id] = empty_attempts.get(record_id, 0) + 1
                            consecutive_empty += 1
                        append_checkpoint(checkpoint, record_id, url, status)
                        coordination.finish(record_id, coord_owner, status)
                        if status != "unavailable" and consecutive_empty >= consecutive_empty_limit:
                            print(
                                f"连续空 JD 达到阈值，当前浏览器冷却 {soft_fail_cooldown:.0f} 秒后继续",
                                flush=True,
                            )
                            time.sleep(soft_fail_cooldown)
                            consecutive_empty = 0
                        print("  空 JD，跳过", flush=True)
                    else:
                        enriched = dict(row)
                        enriched["description_raw"] = description
                        enriched["jd"] = description
                        enriched["detail_source"] = detail_source
                        with output.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(enriched, ensure_ascii=False, separators=(",", ":")) + "\n")
                            handle.flush()
                        done.add(record_id)
                        coordination.finish(record_id, coord_owner, "success")
                        written += 1
                        consecutive_empty = 0
                        consecutive_errors = 0
                        print(f"  JD: {len(description)} 字", flush=True)
                except PlaywrightError as exc:
                    if browser_session_closed(page, exc):
                        print("浏览器页面或上下文已关闭，停止本次采集且不记录岗位失败。", flush=True)
                        coordination.finish(record_id, coord_owner, "error")
                        stop_reason = "browser_closed"
                        break
                    failures += 1
                    consecutive_errors += 1
                    append_checkpoint(checkpoint, record_id, url, "error")
                    coordination.finish(record_id, coord_owner, "error")
                    print(f"  详情失败: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
                except OSError as exc:
                    failures += 1
                    consecutive_errors += 1
                    append_checkpoint(checkpoint, record_id, url, "error")
                    coordination.finish(record_id, coord_owner, "error")
                    print(f"  详情失败: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
                if consecutive_errors >= consecutive_error_limit:
                    print(
                        f"连续详情失败达到阈值，当前浏览器冷却 {soft_fail_cooldown:.0f} 秒后继续",
                        flush=True,
                    )
                    time.sleep(soft_fail_cooldown)
                    consecutive_errors = 0
                if detail_delay_max:
                    time.sleep(random.uniform(detail_delay_min, detail_delay_max))
                if (
                    batch_size
                    and processed % batch_size == 0
                    and processed < limit
                    and (max_attempts is None or processed < max_attempts)
                ):
                    if batch_cooldown:
                        print(f"达到 {batch_size} 次访问，冷却 {batch_cooldown:.0f} 秒", flush=True)
                        time.sleep(batch_cooldown)
        finally:
            try:
                context.close()
            except PlaywrightError:
                pass
    if stop_reason == "browser_closed":
        print(json.dumps({"processed": processed, "written": written, "failed": failures, "stop_reason": stop_reason}, ensure_ascii=False))
        return 5
    if stop_reason == "challenge":
        print(
            json.dumps(
                {
                    "processed": processed,
                    "written": written,
                    "failed": failures,
                    "stop_reason": stop_reason,
                },
                ensure_ascii=False,
            )
        )
        return EXIT_CHALLENGE
    if stop_reason == "met_reverse_worker":
        print(
            json.dumps(
                {
                    "processed": processed,
                    "written": written,
                    "failed": failures,
                    "stop_reason": stop_reason,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if stop_reason == "verification_timeout":
        write_cooldown(state_dir, seconds=cooldown_seconds, reason=stop_reason)
        print(
            json.dumps(
                {
                    "processed": processed,
                    "written": written,
                    "failed": failures,
                    "stop_reason": stop_reason,
                    "cooldown_seconds": cooldown_seconds,
                },
                ensure_ascii=False,
            )
        )
        return 4
    print(json.dumps({"processed": processed, "written": written, "failed": failures}, ensure_ascii=False))
    return 0 if written else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--cookie-file", type=Path, default=COOKIE_DEFAULT)
    parser.add_argument("--profile", type=Path, default=PROFILE_DEFAULT)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--verification-timeout", type=int, default=300)
    parser.add_argument("--empty-retries", type=int, default=2)
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="maximum detail pages to visit; 0 scans the bounded source file",
    )
    parser.add_argument("--detail-delay-min", type=float, default=20.0)
    parser.add_argument("--detail-delay-max", type=float, default=40.0)
    parser.add_argument(
        "--page-settle-ms",
        type=int,
        default=1500,
        help="milliseconds to wait after DOMContentLoaded before parsing (default 1500)",
    )
    parser.add_argument(
        "--empty-settle-retry-ms",
        type=int,
        default=500,
        help="extra wait only when the first JD parse is empty (default 500)",
    )
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--batch-cooldown", type=float, default=900.0)
    parser.add_argument("--consecutive-empty-limit", type=int, default=3)
    parser.add_argument("--consecutive-error-limit", type=int, default=3)
    parser.add_argument("--soft-fail-cooldown", type=float, default=60.0)
    parser.add_argument("--coord-db", type=Path, default=COORD_DB_DEFAULT)
    parser.add_argument("--coord-owner", default=FORWARD_OWNER, choices=(FORWARD_OWNER,))
    parser.add_argument(
        "--browser-channel",
        choices=("chromium", "msedge"),
        default="msedge",
        help="browser engine channel; msedge uses a separate Edge profile",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=COOLDOWN_DEFAULT_SECONDS,
        help="risk cooldown after verification/soft-fail stop (default 3600)",
    )
    parser.add_argument(
        "--ignore-cooldown",
        action="store_true",
        help="allow run even when a risk cooldown is active",
    )
    parser.add_argument(
        "--clear-cooldown",
        action="store_true",
        help="clear the risk cooldown file and exit",
    )
    parser.add_argument(
        "--reuse-profile-cookies",
        dest="reuse_profile_cookies",
        action="store_true",
        default=True,
        help="use the existing headed profile session (default; no cookie injection)",
    )
    parser.add_argument(
        "--inject-cookie-state",
        dest="reuse_profile_cookies",
        action="store_false",
        help="explicitly opt into importing --cookie-file for one controlled recovery run",
    )
    args = parser.parse_args()
    if args.clear_cooldown:
        clear_cooldown(args.profile.parent)
        print(f"cleared cooldown under {args.profile.parent}")
        return 0
    if (
        args.limit < 1
        or args.verification_timeout < 10
        or args.empty_retries < 1
        or args.max_attempts < 0
        or args.detail_delay_min < 0
        or args.detail_delay_max < args.detail_delay_min
        or args.page_settle_ms < 0
        or args.empty_settle_retry_ms < 0
        or args.batch_size < 1
        or args.batch_cooldown < 0
        or args.cooldown_seconds < 0
        or args.consecutive_empty_limit < 1
        or args.consecutive_error_limit < 1
        or args.soft_fail_cooldown < 0
    ):
        parser.error(
            "limit must be positive, verification-timeout must be >= 10, "
            "empty-retries must be positive, max-attempts must be non-negative, "
            "detail delays must be ordered and non-negative, batch-size must be positive, "
            "and batch-cooldown/cooldown-seconds must be non-negative"
        )
    return collect(
        args.source,
        args.output,
        args.cookie_file,
        args.profile,
        args.limit,
        args.verification_timeout,
        args.empty_retries,
        args.max_attempts or None,
        args.detail_delay_min,
        args.detail_delay_max,
        args.page_settle_ms,
        args.empty_settle_retry_ms,
        args.batch_size,
        args.batch_cooldown,
        args.reuse_profile_cookies,
        args.consecutive_empty_limit,
        args.consecutive_error_limit,
        args.coord_db,
        args.coord_owner,
        browser_channel=None if args.browser_channel == "chromium" else args.browser_channel,
        cooldown_seconds=args.cooldown_seconds,
        ignore_cooldown=args.ignore_cooldown,
        soft_fail_cooldown=args.soft_fail_cooldown,
    )


if __name__ == "__main__":
    raise SystemExit(main())
