"""Backfill BOSS job descriptions through the public job-card XHR.

The crawler reads the existing job catalog, never opens a login profile, and
uses only the public ``view/job/card.json`` response. Results and checkpoints
are separate from the source catalog so the run is resumable and idempotent.
Concurrency is deliberately bounded and a circuit breaker stops the run when
the upstream starts returning verification, throttling, or access-blocking
signals.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import os
import random
import re
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DEFAULT = PROJECT_ROOT / "outputs" / "招聘--完整数据" / "BOSS" / "岗位数据.jsonl"
STATE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "boss-public-card"
OUTPUT_DEFAULT = STATE_DEFAULT / "卡片详情补充.jsonl"
CHECKPOINT_DEFAULT = STATE_DEFAULT / "checkpoint.jsonl"
SUMMARY_DEFAULT = STATE_DEFAULT / "summary.json"
LOCK_DEFAULT = STATE_DEFAULT / "run.lock"

DEFAULT_HOST = "https://www.zhipin.com"
DEFAULT_WORKERS = 12
MAX_WORKERS = 16
DEFAULT_MAX_INFLIGHT = 24
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MIN_JD_LENGTH = 1
RETRY_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
BLOCK_HTTP_STATUSES = frozenset({401, 403, 418})
BLOCK_RESPONSE_CODES = frozenset({35})
BLOCK_STATUSES = frozenset(
    {
        "blocked_code_35",
        "blocked_http_401",
        "blocked_http_403",
        "blocked_http_418",
        "rate_limited",
    }
)
CARD_PATH = "/wapi/zpgeek/view/job/card.json"
ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u2060\ufeff]")
SPACE_RE = re.compile(r"[^\S\r\n]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
JOB_ID_RE = re.compile(r"/job_detail/([^/?#]+?)(?:\.html)?(?:[/?#]|$)", re.IGNORECASE)


class RunLock:
    """Small exclusive lock that does not depend on a third-party package."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = self.path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError as exc:
            raise RuntimeError(f"run lock exists: {self.path}") from exc
        self.handle.write(json.dumps({"pid": os.getpid(), "started_at": now_iso()}))
        self.handle.flush()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is not None:
            self.handle.close()
        self.path.unlink(missing_ok=True)


class JsonlAppender:
    """Append JSONL records and periodically fsync for crash recovery."""

    def __init__(self, path: Path, *, sync_every: int = 20) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.handle = path.open("a", encoding="utf-8", newline="\n")
        self.sync_every = max(1, sync_every)
        self.count = 0

    def write(self, value: dict[str, Any]) -> None:
        self.handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.count += 1
        if self.count % self.sync_every == 0:
            self.flush()

    def flush(self) -> None:
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self) -> None:
        self.flush()
        self.handle.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is not valid JSONL") from exc
            if isinstance(value, dict):
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


def build_card_url(row: dict[str, Any], host: str = DEFAULT_HOST) -> str:
    jid = encrypted_job_id(row)
    if not jid:
        raise ValueError(f"record {record_key(row) or '<unknown>'} has no encrypt_job_id")
    return f"{host.rstrip('/')}{CARD_PATH}?jid={quote(jid, safe='')}&lid=&type=3"


def extract_detail_text(raw_html: str) -> str:
    """Extract readable text from the public card's detail-bottom-text node."""
    if not raw_html:
        return ""
    # Normalize breaks before parsing; ``html.parser`` can otherwise treat a
    # self-closing ``<br/>`` as an unclosed node and swallow trailing text.
    normalized_html = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
    soup = BeautifulSoup(normalized_html, "html.parser")
    node = soup.select_one(".detail-bottom-text")
    if node is None:
        return ""
    text = html.unescape(node.get_text("\n"))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = ZERO_WIDTH_RE.sub("", text)
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return BLANK_LINES_RE.sub("\n\n", text).strip()


def _base_result(row: dict[str, Any], url: str) -> dict[str, Any]:
    # Keep source metadata for later joins, but do not copy the list security
    # token into the detail artifact.
    result = {key: value for key, value in row.items() if key != "security_id"}
    result.update(
        {
            "record_id": record_key(row),
            "job_id": row.get("job_id") or row.get("record_id") or "",
            "title": row.get("title_raw") or row.get("title") or "",
            "company": row.get("boss_name") or row.get("company") or "",
            "salary": row.get("salary") or "",
            "tags_list": row.get("tags") or row.get("job_labels") or "",
            "job_link": row.get("job_link") or row.get("detail_url") or "",
            "link": row.get("detail_url") or row.get("job_link") or "",
            "jd": "",
            "description_raw": "",
            "detail_fetch_status": "pending",
            "detail_capture_method": "public_job_card_xhr",
            "source_endpoint": url,
            "fetched_at": now_iso(),
        }
    )
    return result


def parse_card_payload(
    row: dict[str, Any],
    *,
    url: str,
    http_status: int,
    payload: Any,
    elapsed_ms: int,
    attempt: int,
    min_jd_length: int = DEFAULT_MIN_JD_LENGTH,
) -> dict[str, Any]:
    result = _base_result(row, url)
    result.update({"http_status": http_status, "elapsed_ms": elapsed_ms, "request_attempt": attempt})
    if http_status in BLOCK_HTTP_STATUSES:
        result["detail_fetch_status"] = f"blocked_http_{http_status}"
        return result
    if http_status == 429:
        result["detail_fetch_status"] = "rate_limited"
        return result
    if not isinstance(payload, dict):
        result.update({"detail_fetch_status": "invalid_json", "error": "response is not an object"})
        return result

    response_code = payload.get("code")
    result["response_code"] = response_code
    if response_code in BLOCK_RESPONSE_CODES or str(response_code) in {str(code) for code in BLOCK_RESPONSE_CODES}:
        result["detail_fetch_status"] = "blocked_code_35"
        return result
    if http_status != 200:
        result["detail_fetch_status"] = f"http_{http_status}"
        return result
    if response_code != 0:
        result["detail_fetch_status"] = f"api_code_{response_code}"
        return result

    raw_html = str((payload.get("zpData") or {}).get("html") or "")
    result["html_length"] = len(raw_html)
    jd = extract_detail_text(raw_html)
    if len(jd) < min_jd_length:
        result["detail_fetch_status"] = "empty_detail"
        return result
    result.update({"jd": jd, "description_raw": jd, "detail_fetch_status": "success"})
    return result


def _session(proxy: str | None) -> requests.Session:
    session = requests.Session()
    # Anonymous card requests must not inherit login cookies or environment
    # proxy credentials. A proxy can still be supplied explicitly by CLI.
    session.trust_env = False
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            return min(30.0, max(0.5, float(retry_after)))
        except ValueError:
            pass
    return min(15.0, 0.5 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.25))


def fetch_card(
    row: dict[str, Any],
    *,
    host: str,
    proxy: str | None,
    timeout: tuple[float, float],
    max_attempts: int,
    min_jd_length: int,
    stop_event: threading.Event,
) -> dict[str, Any]:
    key = record_key(row)
    try:
        url = build_card_url(row, host)
    except ValueError as exc:
        return {"record_id": key, "detail_fetch_status": "missing_encrypt_job_id", "error": str(exc)}

    session = _session(proxy)
    try:
        for attempt in range(1, max_attempts + 1):
            if stop_event.is_set():
                result = _base_result(row, url)
                result.update({"detail_fetch_status": "circuit_stopped", "request_attempt": attempt})
                return result
            started = time.perf_counter()
            response: requests.Response | None = None
            try:
                session.cookies.clear()
                response = session.get(
                    url,
                    headers={"Referer": row.get("detail_url") or row.get("job_link") or f"{host}/"},
                    timeout=timeout,
                    allow_redirects=False,
                    cookies={},
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                result = parse_card_payload(
                    row,
                    url=url,
                    http_status=response.status_code,
                    payload=payload,
                    elapsed_ms=elapsed_ms,
                    attempt=attempt,
                    min_jd_length=min_jd_length,
                )
                status = result.get("detail_fetch_status")
                if status == "success" or status in BLOCK_STATUSES:
                    return result
                if response.status_code in RETRY_HTTP_STATUSES or status in {"empty_detail", "invalid_json"}:
                    if attempt < max_attempts:
                        time.sleep(_retry_delay(response, attempt))
                        continue
                return result
            except requests.RequestException as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                result = _base_result(row, url)
                result.update(
                    {
                        "detail_fetch_status": "network_error" if attempt >= max_attempts else "retrying",
                        "error": f"{type(exc).__name__}: {exc}",
                        "elapsed_ms": elapsed_ms,
                        "request_attempt": attempt,
                    }
                )
                if attempt < max_attempts:
                    time.sleep(_retry_delay(response, attempt))
                    continue
                return result
            finally:
                session.cookies.clear()
    finally:
        session.close()
    return {"record_id": key, "detail_fetch_status": "unknown_error"}


def load_success_keys(paths: Iterable[Path]) -> set[str]:
    completed: set[str] = set()
    for path in paths:
        for row in iter_jsonl(path):
            key = record_key(row)
            if key and str(row.get("jd") or row.get("description_raw") or "").strip():
                completed.add(key)
    return completed


def load_attempt_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in iter_jsonl(path):
        key = record_key(row)
        if key and row.get("detail_fetch_status") not in {"success", "circuit_stopped"}:
            counts[key] += 1
    return counts


def iter_pending_rows(
    source: Path,
    *,
    completed: set[str],
    attempt_counts: Counter[str],
    max_attempts: int,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    seen: set[str] = set()
    selected = 0
    for row in iter_jsonl(source):
        key = record_key(row)
        if not key or key in seen or key in completed or attempt_counts.get(key, 0) >= max_attempts:
            continue
        seen.add(key)
        yield row
        selected += 1
        if limit is not None and selected >= limit:
            return


def run_backfill(
    *,
    source: Path,
    output: Path,
    checkpoint: Path,
    summary_path: Path,
    lock_path: Path,
    prior_details: Path | None,
    host: str,
    proxy: str | None,
    workers: int,
    max_inflight: int,
    max_attempts: int,
    timeout: tuple[float, float],
    min_jd_length: int,
    limit: int | None,
) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source)
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
    if max_inflight < workers:
        raise ValueError("max_inflight must be at least workers")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    completed_paths = [path for path in (prior_details, output) if path is not None]
    completed = load_success_keys(completed_paths)
    attempt_counts = load_attempt_counts(checkpoint)
    pending = iter_pending_rows(
        source,
        completed=completed,
        attempt_counts=attempt_counts,
        max_attempts=max_attempts,
        limit=limit,
    )
    stop_event = threading.Event()
    stats: Counter[str] = Counter()
    started_at = now_iso()
    submitted = 0
    finished = 0
    stopped_reason = "completed"
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)

    with RunLock(lock_path):
        with _jsonl_pair(output, checkpoint) as writers:
            output_writer, checkpoint_writer = writers
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="boss-card") as executor:
                future_rows: dict[concurrent.futures.Future[dict[str, Any]], dict[str, Any]] = {}

                def fill_queue() -> None:
                    nonlocal submitted
                    while not stop_event.is_set() and len(future_rows) < max_inflight:
                        try:
                            row = next(pending)
                        except StopIteration:
                            return
                        future = executor.submit(
                            fetch_card,
                            row,
                            host=host,
                            proxy=proxy,
                            timeout=timeout,
                            max_attempts=max_attempts,
                            min_jd_length=min_jd_length,
                            stop_event=stop_event,
                        )
                        future_rows[future] = row
                        submitted += 1

                fill_queue()
                while future_rows:
                    done, _ = wait(tuple(future_rows), return_when=FIRST_COMPLETED)
                    for future in done:
                        row = future_rows.pop(future)
                        try:
                            result = future.result()
                        except concurrent.futures.CancelledError:
                            result = _base_result(row, build_card_url(row, host))
                            result.update({"detail_fetch_status": "circuit_stopped", "request_attempt": 0})
                        except Exception as exc:  # keep one worker failure from losing the run
                            result = _base_result(row, build_card_url(row, host))
                            result.update({"detail_fetch_status": "worker_error", "error": f"{type(exc).__name__}: {exc}"})
                        output_writer.write(result)
                        checkpoint_writer.write(
                            {
                                "record_id": result.get("record_id") or record_key(row),
                                "detail_fetch_status": result.get("detail_fetch_status"),
                                "http_status": result.get("http_status"),
                                "response_code": result.get("response_code"),
                                "request_attempt": result.get("request_attempt", 0),
                                "updated_at": now_iso(),
                            }
                        )
                        status = str(result.get("detail_fetch_status") or "unknown")
                        stats[status] += 1
                        finished += 1
                        if status in BLOCK_STATUSES:
                            stop_event.set()
                            stopped_reason = status
                    if stop_event.is_set():
                        for future in future_rows:
                            future.cancel()
                    else:
                        fill_queue()

    summary = {
        "status": "stopped" if stopped_reason != "completed" else "complete",
        "stopped_reason": stopped_reason,
        "started_at": started_at,
        "finished_at": now_iso(),
        "source": str(source),
        "output": str(output),
        "checkpoint": str(checkpoint),
        "workers": workers,
        "max_inflight": max_inflight,
        "submitted": submitted,
        "finished": finished,
        "already_completed": len(completed),
        "status_counts": dict(stats),
    }
    atomic_json_write(summary_path, summary)
    return summary


class _jsonl_pair:
    """Context manager for two appenders without introducing a new dependency."""

    def __init__(self, output: Path, checkpoint: Path) -> None:
        self.output = output
        self.checkpoint = checkpoint
        self.writers: tuple[JsonlAppender, JsonlAppender] | None = None

    def __enter__(self) -> tuple[JsonlAppender, JsonlAppender]:
        output_writer = JsonlAppender(self.output)
        try:
            checkpoint_writer = JsonlAppender(self.checkpoint)
        except Exception:
            output_writer.close()
            raise
        self.writers = (output_writer, checkpoint_writer)
        return self.writers

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.writers is None:
            return
        for writer in self.writers:
            writer.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT_DEFAULT)
    parser.add_argument("--summary", type=Path, default=SUMMARY_DEFAULT)
    parser.add_argument("--lock", type=Path, default=LOCK_DEFAULT)
    parser.add_argument("--prior-details", type=Path, default=None)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-inflight", type=int, default=DEFAULT_MAX_INFLIGHT)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--read-timeout", type=float, default=20.0)
    parser.add_argument("--min-jd-length", type=int, default=DEFAULT_MIN_JD_LENGTH)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    try:
        summary = run_backfill(
            source=args.source,
            output=args.output,
            checkpoint=args.checkpoint,
            summary_path=args.summary,
            lock_path=args.lock,
            prior_details=args.prior_details,
            host=args.host,
            proxy=args.proxy,
            workers=args.workers,
            max_inflight=args.max_inflight,
            max_attempts=args.max_attempts,
            timeout=(args.connect_timeout, args.read_timeout),
            min_jd_length=args.min_jd_length,
            limit=args.limit,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "not_started", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
