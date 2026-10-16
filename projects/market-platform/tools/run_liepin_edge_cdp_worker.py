#!/usr/bin/env python3
"""Collect Liepin details in one tab of an already running local Edge CDP browser."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route, sync_playwright

from tools.liepin_coordination import (
    FORWARD_OWNER,
    REVERSE_OWNER,
    REVERSE_OWNERS,
    REVERSE_SECONDARY_OWNER,
    LiepinCoordination,
)
from tools.run_liepin_browser_single import (
    COORD_DB_DEFAULT,
    OUTPUT_DEFAULT,
    SOURCE_DEFAULT,
    STATE_DIR_DEFAULT,
    WORKER_OUTPUTS,
    append_checkpoint,
    build_static_session,
    browser_session_closed,
    classify_empty_detail,
    completed_ids,
    fetch_static_description,
    iter_rows,
    load_terminal_ids,
    page_access_gate,
    parse_detail_description,
    profile_run_lock,
    record_key,
    sync_static_session_from_browser,
    wait_for_dynamic_detail,
    wait_out_security_transit,
)
from tools.run_liepin_camoufox_reverse import append_output, iter_jsonl, iter_worker_rows


EXIT_CHALLENGE = 86
CDP_DEFAULT = "http://127.0.0.1:9223"
WORKER_CONFIGS = {
    FORWARD_OWNER: {
        "direction": "forward",
        "state_dir": STATE_DIR_DEFAULT,
        "output": OUTPUT_DEFAULT,
    },
    REVERSE_OWNER: {
        "direction": "reverse",
        "state_dir": PROJECT_ROOT / ".crawler-state" / "liepin-cdp-reverse-1",
        "output": WORKER_OUTPUTS / "worker-reverse.jsonl",
    },
    REVERSE_SECONDARY_OWNER: {
        "direction": "reverse",
        "state_dir": PROJECT_ROOT / ".crawler-state" / "liepin-cdp-reverse-2",
        "output": WORKER_OUTPUTS / "worker-reverse-2.jsonl",
    },
}


def block_heavy_resources(route: Route) -> None:
    if route.request.resource_type in {"image", "font", "media"}:
        route.abort()
    else:
        route.continue_()


def worker_rows(
    source: Path,
    total_rows: int,
    direction: str,
    start_index: int,
) -> Iterable[tuple[int, int, dict[str, Any]]]:
    if direction == "reverse":
        yield from iter_worker_rows(source, total_rows, direction, start_index)
        return
    sequence = 0
    for source_index, row in enumerate(iter_rows(source), 1):
        if source_index < start_index:
            continue
        sequence += 1
        yield source_index, sequence, row


def run(args: argparse.Namespace) -> int:
    config = WORKER_CONFIGS[args.owner]
    direction = str(config["direction"])
    state_dir = Path(config["state_dir"])
    output = Path(config["output"])
    checkpoint = state_dir / "checkpoint.jsonl"
    state_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    total_rows = sum(1 for _ in iter_jsonl(args.source))
    done = completed_ids(output)
    terminal_ids = load_terminal_ids(checkpoint)
    processed = written = unavailable = deferred = failed = 0
    stop_reason: str | None = None

    with LiepinCoordination(args.coord_db) as coordination:
        if args.owner == REVERSE_SECONDARY_OWNER:
            coordination.migrate_legacy_middle_claims()
        progress = coordination.status()["progress"]
        prior = progress.get(args.owner)
        start_index = int(prior) if prior is not None else (
            total_rows if direction == "reverse" else 1
        )

    with (
        profile_run_lock(state_dir / "run.lock"),
        build_static_session() as static_session,
    ):
        playwright = sync_playwright().start()
        page = None
        try:
            browser = playwright.chromium.connect_over_cdp(
                args.cdp_endpoint,
                timeout=args.cdp_timeout_ms,
            )
            if not browser.contexts:
                raise RuntimeError("CDP Edge has no default browser context")
            context = browser.contexts[0]
            page = context.new_page()
            page.route("**/*", block_heavy_resources)
            sync_static_session_from_browser(context, page, static_session)
            print(
                f"Edge CDP owner={args.owner} direction={direction} start={start_index}",
                flush=True,
            )

            for source_index, sequence, row in worker_rows(
                args.source,
                total_rows,
                direction,
                start_index,
            ):
                if args.limit and processed >= args.limit:
                    break
                with LiepinCoordination(args.coord_db) as coordination:
                    if coordination.has_met(args.owner, source_index):
                        stop_reason = "workers_met"
                        break
                    coordination.advance(args.owner, source_index)
                    record_id = record_key(row)
                    url = str(row.get("detail_url") or "").strip()
                    if (
                        not record_id
                        or not url
                        or record_id in done
                        or record_id in terminal_ids
                    ):
                        continue
                    if not coordination.claim(record_id, args.owner, source_index):
                        continue

                processed += 1
                try:
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
                                "detail_source": f"public_http_{args.owner}",
                            }
                        )
                        append_output(output, enriched)
                        done.add(record_id)
                        with LiepinCoordination(args.coord_db) as coordination:
                            coordination.finish(record_id, args.owner, "success")
                        written += 1
                        print(
                            f"[{processed}] index={source_index} HTTP_JD={len(jd)}",
                            flush=True,
                        )
                        if args.delay_max:
                            time.sleep(random.uniform(args.delay_min, args.delay_max))
                        continue

                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=args.navigation_timeout_ms,
                    )
                    gate = wait_out_security_transit(page)
                    html = page.content()
                    if gate is None:
                        html = wait_for_dynamic_detail(page, html)
                    if gate is not None:
                        status = f"deferred_{gate}"
                        with LiepinCoordination(args.coord_db) as coordination:
                            coordination.finish(record_id, args.owner, status)
                            next_index = source_index + (1 if direction == "forward" else -1)
                            coordination.advance(args.owner, max(1, next_index))
                        append_checkpoint(checkpoint, record_id, url, status)
                        deferred += 1
                        stop_reason = gate
                        print(
                            f"[{processed}] index={source_index} {gate}; deferred",
                            flush=True,
                        )
                        return EXIT_CHALLENGE

                    jd = parse_detail_description(html)
                    if jd:
                        enriched = dict(row)
                        enriched.update(
                            {
                                "description_raw": jd,
                                "jd": jd,
                                "detail_source": f"local_edge_cdp_{args.owner}",
                            }
                        )
                        append_output(output, enriched)
                        done.add(record_id)
                        with LiepinCoordination(args.coord_db) as coordination:
                            coordination.finish(record_id, args.owner, "success")
                        written += 1
                        print(
                            f"[{processed}] index={source_index} JD={len(jd)}",
                            flush=True,
                        )
                    else:
                        status = classify_empty_detail(html)
                        if status == "empty":
                            status = "deferred_empty"
                            deferred += 1
                        else:
                            terminal_ids.add(record_id)
                            unavailable += 1
                        append_checkpoint(checkpoint, record_id, url, status)
                        with LiepinCoordination(args.coord_db) as coordination:
                            coordination.finish(record_id, args.owner, status)
                        print(
                            f"[{processed}] index={source_index} status={status}",
                            flush=True,
                        )
                except PlaywrightError as exc:
                    with LiepinCoordination(args.coord_db) as coordination:
                        coordination.finish(record_id, args.owner, "error")
                    if browser_session_closed(page, exc):
                        stop_reason = "cdp_page_closed"
                        return 5
                    append_checkpoint(checkpoint, record_id, url, "error")
                    failed += 1
                    print(
                        f"[{processed}] index={source_index} error={type(exc).__name__}",
                        flush=True,
                    )

                if args.delay_max:
                    time.sleep(random.uniform(args.delay_min, args.delay_max))
        finally:
            if page is not None:
                try:
                    page.close()
                except PlaywrightError:
                    pass
            playwright.stop()

    print(
        json.dumps(
            {
                "processed": processed,
                "written": written,
                "unavailable": unavailable,
                "deferred": deferred,
                "failed": failed,
                "stop_reason": stop_reason,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", choices=tuple(WORKER_CONFIGS), required=True)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--coord-db", type=Path, default=COORD_DB_DEFAULT)
    parser.add_argument("--cdp-endpoint", default=CDP_DEFAULT)
    parser.add_argument("--cdp-timeout-ms", type=int, default=10000)
    parser.add_argument("--navigation-timeout-ms", type=int, default=30000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay-min", type=float, default=0.0)
    parser.add_argument("--delay-max", type=float, default=0.0)
    args = parser.parse_args()
    expected_direction = "reverse" if args.owner in REVERSE_OWNERS else "forward"
    if (
        not args.source.is_file()
        or args.cdp_timeout_ms < 1000
        or args.navigation_timeout_ms < 1000
        or args.limit < 0
        or args.delay_min < 0
        or args.delay_max < args.delay_min
        or WORKER_CONFIGS[args.owner]["direction"] != expected_direction
    ):
        parser.error("invalid Edge CDP worker settings")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
