#!/usr/bin/env python3
"""Run one persistent Camoufox with two bounded Liepin detail lanes."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import Error as PlaywrightError

from tools.liepin_coordination import (
    REVERSE_OWNER,
    REVERSE_OWNERS,
    REVERSE_SECONDARY_OWNER,
    LiepinCoordination,
)
from tools.run_liepin_browser_single import (
    SOURCE_DEFAULT,
    WORKER_OUTPUTS,
    append_checkpoint,
    build_static_session,
    classify_empty_detail,
    completed_ids,
    fetch_static_description,
    open_public_detail,
    parse_detail_description,
    profile_run_lock,
    record_key,
)
from tools.run_liepin_camoufox_reverse import (
    COORD_DB_DEFAULT,
    append_output,
    iter_jsonl,
    iter_worker_rows,
)


STATE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-camoufox-reverse"
PROFILE_DEFAULT = STATE_DEFAULT / "profile"
OUTPUT_DEFAULT = WORKER_OUTPUTS / "worker-reverse.jsonl"
CHECKPOINT_DEFAULT = STATE_DEFAULT / "checkpoint.jsonl"
EXIT_CHALLENGE = 86


def resolve_start_index(
    args: argparse.Namespace,
    coordination: LiepinCoordination,
    total_rows: int,
) -> int:
    if args.start_index > 0:
        return min(args.start_index, total_rows)
    progress = coordination.status()["progress"]
    prior = progress.get(args.coord_owner)
    return int(prior) if prior is not None else total_rows


def run(args: argparse.Namespace) -> int:
    from camoufox.sync_api import Camoufox

    total_rows = sum(1 for _ in iter_jsonl(args.source))
    done = completed_ids(args.output)
    args.profile.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    processed = written = failed = consecutive_empty = 0
    stop_reason: str | None = None

    with (
        LiepinCoordination(args.coord_db) as coordination,
        profile_run_lock(args.state_dir / "run.lock"),
        Camoufox(
            headless=False,
            persistent_context=True,
            user_data_dir=str(args.profile.resolve()),
            locale="zh-CN",
            window=(1365, 860),
            block_images=False,
            humanize=False,
        ) as context,
    ):
        if args.coord_owner == REVERSE_SECONDARY_OWNER:
            migrated = coordination.migrate_legacy_middle_claims()
            if migrated:
                print(f"migrated {migrated} legacy midpoint claims", flush=True)
        old_pages = list(context.pages)
        pages = [context.new_page() for _ in range(args.lanes)]
        for old_page in old_pages:
            try:
                old_page.close()
            except PlaywrightError:
                pass
        sessions = [build_static_session() for _ in range(args.lanes)]
        try:
            start_index = resolve_start_index(args, coordination, total_rows)
            rows = iter_worker_rows(
                args.source,
                total_rows,
                args.direction,
                start_index,
            )
            exhausted = met = False
            print(
                f"Camoufox {args.coord_owner} started at {start_index} with {args.lanes} lanes",
                flush=True,
            )
            with ThreadPoolExecutor(max_workers=args.lanes) as executor:
                while not exhausted and not met:
                    batch: list[dict[str, Any]] = []
                    while len(batch) < args.lanes:
                        try:
                            source_index, sequence, row = next(rows)
                        except StopIteration:
                            exhausted = True
                            break
                        if coordination.has_met(args.coord_owner, source_index):
                            met = True
                            break
                        coordination.advance(args.coord_owner, source_index)
                        record_id = record_key(row)
                        url = str(row.get("detail_url") or "").strip()
                        if not record_id or not url or record_id in done:
                            continue
                        if not coordination.claim(record_id, args.coord_owner, source_index):
                            continue
                        lane = len(batch)
                        batch.append(
                            {
                                "lane": lane,
                                "source_index": source_index,
                                "sequence": sequence,
                                "row": row,
                                "record_id": record_id,
                                "url": url,
                            }
                        )
                    if not batch:
                        continue

                    futures = [
                        executor.submit(
                            fetch_static_description,
                            job["url"],
                            3.0,
                            sessions[job["lane"]],
                            2,
                        )
                        for job in batch
                    ]
                    for job, future in zip(batch, futures):
                        processed += 1
                        lane = int(job["lane"])
                        url = str(job["url"])
                        record_id = str(job["record_id"])
                        html = ""
                        try:
                            jd = future.result()
                            detail_source = f"profile_http_{args.coord_owner}_lane_{lane + 1}"
                            if not jd:
                                page = pages[lane]
                                html, gate, retried_anonymously = open_public_detail(
                                    context,
                                    page,
                                    url,
                                    args.navigation_timeout_ms,
                                )
                                if retried_anonymously:
                                    for refresh_session in sessions:
                                        refresh_session.cookies.clear()
                                if args.page_settle_ms:
                                    page.wait_for_timeout(args.page_settle_ms)
                                    html = page.content()
                                if gate is not None:
                                    deferred_status = f"deferred_{gate}"
                                    append_checkpoint(
                                        args.checkpoint,
                                        record_id,
                                        url,
                                        deferred_status,
                                    )
                                    coordination.finish(
                                        record_id,
                                        args.coord_owner,
                                        deferred_status,
                                    )
                                    coordination.advance(
                                        args.coord_owner,
                                        max(1, int(job["source_index"]) - 1),
                                    )
                                    stop_reason = "challenge"
                                    print(
                                        f"[{processed}] lane={lane + 1} {gate}; "
                                        "deferred; recycling this anonymous browser",
                                        flush=True,
                                    )
                                    break
                                jd = parse_detail_description(html)
                                detail_source = f"headed_{args.coord_owner}_lane_{lane + 1}"
                            if not jd:
                                status = classify_empty_detail(html)
                                append_checkpoint(args.checkpoint, record_id, url, status)
                                coordination.finish(record_id, args.coord_owner, status)
                                failed += 1
                                consecutive_empty = consecutive_empty + 1 if status == "empty" else 0
                                print(f"[{processed}] lane={lane + 1} {status}", flush=True)
                                if consecutive_empty >= args.empty_streak_limit:
                                    if args.empty_cooldown:
                                        time.sleep(args.empty_cooldown)
                                    for refresh_session in sessions:
                                        refresh_session.cookies.clear()
                                    consecutive_empty = 0
                                continue
                            enriched = dict(job["row"])
                            enriched.update(
                                {
                                    "description_raw": jd,
                                    "jd": jd,
                                    "detail_source": detail_source,
                                }
                            )
                            append_output(args.output, enriched)
                            done.add(record_id)
                            coordination.finish(record_id, args.coord_owner, "success")
                            written += 1
                            consecutive_empty = 0
                            print(
                                f"[{processed}] lane={lane + 1} source_index={job['source_index']} JD={len(jd)}",
                                flush=True,
                            )
                        except (PlaywrightError, OSError) as exc:
                            append_checkpoint(args.checkpoint, record_id, url, "error")
                            coordination.finish(record_id, args.coord_owner, "error")
                            failed += 1
                            print(
                                f"[{processed}] lane={lane + 1} error={type(exc).__name__}",
                                flush=True,
                            )
                            if pages[lane].is_closed():
                                return 5
                    if stop_reason:
                        break
                    if args.recycle_after and processed >= args.recycle_after:
                        return 75
        finally:
            for session in sessions:
                session.close()
    summary = {
        "processed": processed,
        "written": written,
        "failed": failed,
        "stop_reason": stop_reason,
    }
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
        ),
        flush=True,
    )
    if stop_reason == "challenge":
        return EXIT_CHALLENGE
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--state-dir", type=Path, default=STATE_DEFAULT)
    parser.add_argument("--profile", type=Path, default=PROFILE_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT_DEFAULT)
    parser.add_argument("--coord-db", type=Path, default=COORD_DB_DEFAULT)
    parser.add_argument("--coord-owner", default=REVERSE_OWNER, choices=tuple(REVERSE_OWNERS))
    parser.add_argument("--direction", choices=("reverse",), default="reverse")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--lanes", type=int, default=2, choices=(1, 2))
    parser.add_argument("--page-settle-ms", type=int, default=0)
    parser.add_argument("--empty-retry-ms", type=int, default=0)
    parser.add_argument("--empty-streak-limit", type=int, default=5)
    parser.add_argument("--empty-cooldown", type=float, default=5.0)
    parser.add_argument("--navigation-timeout-ms", type=int, default=30000)
    parser.add_argument("--verification-timeout", type=int, default=300)
    parser.add_argument("--startup-wait", type=float, default=0.0)
    parser.add_argument("--recycle-after", type=int, default=0)
    args = parser.parse_args()
    if (
        not args.source.is_file()
        or args.start_index < 0
        or args.page_settle_ms < 0
        or args.empty_streak_limit < 1
        or args.empty_cooldown < 0
        or args.navigation_timeout_ms < 1000
        or args.recycle_after < 0
        or args.coord_owner not in REVERSE_OWNERS
        or args.direction != "reverse"
    ):
        parser.error("invalid worker settings")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
