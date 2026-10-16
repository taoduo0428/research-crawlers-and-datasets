#!/usr/bin/env python3
"""Resolve deferred Liepin details through one fresh anonymous Edge context per job."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route, sync_playwright

from tools.liepin_coordination import DEFERRED_OWNER, LiepinCoordination
from tools.run_liepin_browser_single import (
    COORD_DB_DEFAULT,
    SOURCE_DEFAULT,
    WORKER_OUTPUTS,
    append_checkpoint,
    build_static_session,
    classify_empty_detail,
    completed_ids,
    fetch_static_description,
    iter_rows,
    parse_detail_description,
    record_key,
    wait_for_dynamic_detail,
    wait_out_security_transit,
)
from tools.run_liepin_camoufox_reverse import append_output


OUTPUT_DEFAULT = WORKER_OUTPUTS / "worker-deferred.jsonl"
STATE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-deferred"


def block_heavy_resources(route: Route) -> None:
    if route.request.resource_type in {"image", "font", "media"}:
        route.abort()
    else:
        route.continue_()


def source_rows_by_id(source: Path, wanted: set[str]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in iter_rows(source):
        key = record_key(row)
        if key in wanted:
            rows[key] = row
            if len(rows) == len(wanted):
                break
    return rows


def run(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = args.state_dir / "checkpoint.jsonl"
    done = completed_ids(args.output)

    with LiepinCoordination(args.coord_db) as coordination:
        pending = coordination.deferred_claims()
    if args.limit:
        pending = pending[: args.limit]
    if not pending:
        print(json.dumps({"pending": 0, "written": 0}, ensure_ascii=False))
        return 0

    rows = source_rows_by_id(args.source, {record_id for record_id, _, _ in pending})
    written = unavailable = failed = 0
    with build_static_session() as static_session, sync_playwright() as playwright:
        if args.cdp_endpoint:
            browser = playwright.chromium.connect_over_cdp(
                args.cdp_endpoint,
                timeout=args.cdp_timeout_ms,
            )
            if not browser.contexts:
                raise RuntimeError("CDP Edge has no default browser context")
            shared_context = browser.contexts[0]
            owns_browser = False
        else:
            browser = playwright.chromium.launch(channel="msedge", headless=False)
            shared_context = None
            owns_browser = True
        try:
            for round_number in range(1, args.max_rounds + 1):
                unresolved: list[tuple[str, int, str]] = []
                for record_id, source_index, _prior_status in pending:
                    row = rows.get(record_id)
                    with LiepinCoordination(args.coord_db) as coordination:
                        if record_id in done:
                            if coordination.claim_deferred(record_id):
                                coordination.finish(record_id, DEFERRED_OWNER, "success")
                            continue
                        if row is None:
                            if coordination.claim_deferred(record_id):
                                coordination.finish(record_id, DEFERRED_OWNER, "blocked")
                            continue
                        if not coordination.claim_deferred(record_id):
                            continue

                    url = str(row.get("detail_url") or "").strip()
                    jd = fetch_static_description(
                        url,
                        timeout=3.0,
                        session=static_session,
                        attempts=1,
                    )
                    if jd:
                        enriched = dict(row)
                        enriched.update(
                            {
                                "description_raw": jd,
                                "jd": jd,
                                "detail_source": "public_http_deferred",
                            }
                        )
                        append_output(args.output, enriched)
                        done.add(record_id)
                        with LiepinCoordination(args.coord_db) as coordination:
                            coordination.finish(record_id, DEFERRED_OWNER, "success")
                        written += 1
                        print(
                            f"round={round_number} index={source_index} HTTP_JD={len(jd)}",
                            flush=True,
                        )
                        continue

                    context = shared_context or browser.new_context(locale="zh-CN")
                    owns_context = shared_context is None
                    page = context.new_page()
                    page.route("**/*", block_heavy_resources)
                    try:
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=args.navigation_timeout_ms,
                        )
                        gate = wait_out_security_transit(page)
                        html = page.content()
                        if gate is None:
                            html = wait_for_dynamic_detail(page, html)
                        jd = parse_detail_description(html) if gate is None else None
                        with LiepinCoordination(args.coord_db) as coordination:
                            if jd:
                                enriched = dict(row)
                                enriched.update(
                                    {
                                        "description_raw": jd,
                                        "jd": jd,
                                        "detail_source": "fresh_anonymous_edge_deferred",
                                    }
                                )
                                append_output(args.output, enriched)
                                done.add(record_id)
                                coordination.finish(record_id, DEFERRED_OWNER, "success")
                                written += 1
                                print(
                                    f"round={round_number} index={source_index} JD={len(jd)}",
                                    flush=True,
                                )
                                continue

                            status = (
                                f"deferred_{gate}"
                                if gate is not None
                                else classify_empty_detail(html)
                            )
                            if status == "unavailable":
                                coordination.finish(record_id, DEFERRED_OWNER, status)
                                unavailable += 1
                            else:
                                if status == "empty":
                                    status = "deferred_empty"
                                coordination.finish(record_id, DEFERRED_OWNER, status)
                                unresolved.append((record_id, source_index, status))
                            append_checkpoint(checkpoint, record_id, url, status)
                            print(
                                f"round={round_number} index={source_index} status={status}",
                                flush=True,
                            )
                    except (PlaywrightError, OSError) as exc:
                        status = "deferred_error"
                        with LiepinCoordination(args.coord_db) as coordination:
                            coordination.finish(record_id, DEFERRED_OWNER, status)
                        append_checkpoint(checkpoint, record_id, url, status)
                        unresolved.append((record_id, source_index, status))
                        failed += 1
                        print(
                            f"round={round_number} index={source_index} "
                            f"error={type(exc).__name__}",
                            flush=True,
                        )
                    finally:
                        try:
                            page.close()
                        except PlaywrightError:
                            pass
                        if owns_context:
                            context.close()

                pending = unresolved
                if not pending:
                    break
                if round_number < args.max_rounds and args.round_cooldown:
                    time.sleep(args.round_cooldown)
        finally:
            if owns_browser:
                browser.close()

    summary = {
        "written": written,
        "unavailable": unavailable,
        "failed": failed,
        "remaining": len(pending),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if not pending else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--state-dir", type=Path, default=STATE_DEFAULT)
    parser.add_argument("--coord-db", type=Path, default=COORD_DB_DEFAULT)
    parser.add_argument("--cdp-endpoint", default=None)
    parser.add_argument("--cdp-timeout-ms", type=int, default=10000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--round-cooldown", type=float, default=15.0)
    parser.add_argument("--navigation-timeout-ms", type=int, default=30000)
    args = parser.parse_args()
    if (
        not args.source.is_file()
        or args.limit < 0
        or args.max_rounds < 1
        or args.round_cooldown < 0
        or args.navigation_timeout_ms < 1000
        or args.cdp_timeout_ms < 1000
    ):
        parser.error("invalid deferred worker settings")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
