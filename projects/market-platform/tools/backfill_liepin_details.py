"""Backfill real Liepin detail-page JD into a derived JSONL export.

The source list export is never modified.  The derived file is append-safe:
completed record IDs are read from the output on restart, so an interrupted
run can resume without duplicate rows.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import random
import sys
import time
from typing import Any, Iterator
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from drv_crawler.liepin.errors import (  # noqa: E402
    AccessDeniedError,
    ChallengeRequiredError,
    LiepinCrawlerError,
)
from drv_crawler.liepin.http import LiepinHttpClient  # noqa: E402
from drv_crawler.liepin.parser import (  # noqa: E402
    looks_like_challenge,
    parse_detail_description,
)


LOGGER = logging.getLogger("backfill_liepin_details")


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            yield value


def _existing_ids(path: Path) -> set[str]:
    """Return records that finished parsing, excluding retryable failures."""
    if not path.is_file():
        return set()
    completed: set[str] = set()
    for row in _iter_jsonl(path):
        record_id = row.get("record_id")
        if not record_id:
            continue
        status = str(row.get("detail_fetch_status") or "")
        if status in {"ok", "empty"} or row.get("description_raw"):
            completed.add(str(record_id))
    return completed


def _is_official_liepin_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "www.liepin.com",
        "liepin.com",
    }


def backfill(
    source: Path,
    output: Path,
    *,
    limit: int | None,
    delay_min: float,
    delay_max: float,
    proxy_policy: str,
    proxy_url: str | None,
    cookie_file: Path | None,
) -> dict[str, int | str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    done = _existing_ids(output)
    client = LiepinHttpClient(
        proxy_policy=proxy_policy,
        proxy_url=proxy_url,
        cookie_file=cookie_file,
        connect_timeout=5.0,
        read_timeout=20.0,
        retries=2,
    )
    scanned = written = skipped = failed = no_detail = 0
    try:
        with output.open("a", encoding="utf-8") as handle:
            for row in _iter_jsonl(source):
                scanned += 1
                record_id = str(row.get("record_id") or "")
                if record_id in done:
                    skipped += 1
                    continue
                if limit is not None and written >= limit:
                    break
                detail_url = str(row.get("detail_url") or "")
                if not _is_official_liepin_url(detail_url):
                    row["detail_fetch_status"] = "invalid_url"
                    failed += 1
                else:
                    try:
                        page = client.get(detail_url, referer=str(row.get("list_url") or ""))
                        if looks_like_challenge(page.text, page.final_url):
                            raise ChallengeRequiredError("Liepin detail page requires verification")
                        description = parse_detail_description(page.text)
                        row["description_raw"] = description
                        row["detail_fetch_status"] = "ok" if description else "empty"
                        row["detail_capture_method"] = "detail_html"
                        if not description:
                            no_detail += 1
                    except (AccessDeniedError, ChallengeRequiredError, LiepinCrawlerError) as exc:
                        row["detail_fetch_status"] = type(exc).__name__
                        failed += 1
                        LOGGER.error("Stopping after session failure at %s: %s", detail_url, exc)
                        # Keep the source row untouched and retry it after the
                        # browser/session has been refreshed; failed rows are
                        # deliberately not appended to the derived export.
                        break
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                written += 1
                done.add(record_id)
                if written and delay_max > 0:
                    time.sleep(random.uniform(delay_min, delay_max))
    finally:
        client.session.close()
    return {
        "source": str(source),
        "output": str(output),
        "scanned": scanned,
        "written": written,
        "resumed_skipped": skipped,
        "failed": failed,
        "empty_detail": no_detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("outputs/招聘--完整数据/猎聘/岗位数据.jsonl"))
    parser.add_argument("--output", type=Path, default=Path(".crawler-state/recruitment-details/猎聘/详情补充.jsonl"))
    parser.add_argument("--limit", type=int, default=None, help="写入最多多少条；用于抽样验证")
    parser.add_argument("--delay-min", type=float, default=2.0)
    parser.add_argument("--delay-max", type=float, default=5.0)
    parser.add_argument("--proxy-policy", choices=("auto", "direct", "required"), default="auto")
    parser.add_argument("--proxy-url", default=None)
    parser.add_argument("--cookie-file", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    summary = backfill(
        args.source,
        args.output,
        limit=args.limit,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        proxy_policy=args.proxy_policy,
        proxy_url=args.proxy_url,
        cookie_file=args.cookie_file,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
