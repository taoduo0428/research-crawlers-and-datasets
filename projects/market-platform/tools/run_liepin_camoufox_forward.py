#!/usr/bin/env python3
"""Collect Liepin details from the top with a persistent headed Camoufox."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import Error as PlaywrightError

from tools.liepin_coordination import FORWARD_OWNER, LiepinCoordination
from tools.run_liepin_browser_single import (
    SOURCE_DEFAULT,
    WORKER_OUTPUTS,
    append_checkpoint,
    challenge,
    classify_empty_detail,
    completed_ids,
    iter_rows,
    parse_detail_description,
    profile_run_lock,
    record_key,
    wait_for_manual_verification,
)


STATE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-camoufox-forward"
PROFILE_DEFAULT = STATE_DEFAULT / "profile"
OUTPUT_DEFAULT = WORKER_OUTPUTS / "worker-camoufox-forward.jsonl"
CHECKPOINT_DEFAULT = STATE_DEFAULT / "checkpoint.jsonl"
COORD_DB_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-shared" / "coordination.sqlite3"


def append_output(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def run(args: argparse.Namespace) -> int:
    from camoufox.sync_api import Camoufox

    done = completed_ids(args.output)
    args.profile.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    processed = written = failed = 0
    consecutive_empty = 0

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
        page = context.pages[0] if context.pages else context.new_page()
        print("Camoufox 正向 worker 已启动", flush=True)
        for source_index, row in enumerate(iter_rows(args.source), 1):
            if coordination.has_met(args.coord_owner, source_index):
                print(f"正反向进度已相遇，停止于源位置 {source_index}", flush=True)
                break
            coordination.advance(args.coord_owner, source_index)
            record_id = record_key(row)
            url = str(row.get("detail_url") or "").strip()
            if not record_id or not url or record_id in done:
                continue
            if not coordination.claim(record_id, args.coord_owner, source_index):
                continue
            processed += 1
            print(f"[{processed}] source_index={source_index} {url}", flush=True)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=args.navigation_timeout_ms)
                page.wait_for_timeout(args.page_settle_ms)
                if challenge(page) and not wait_for_manual_verification(
                    page, args.verification_timeout
                ):
                    coordination.finish(record_id, args.coord_owner, "verification")
                    return 4
                html = page.content()
                jd = parse_detail_description(html)
                if not jd:
                    status = classify_empty_detail(html)
                    if status == "empty" and args.empty_retry_ms > 0:
                        page.reload(wait_until="domcontentloaded", timeout=args.navigation_timeout_ms)
                        page.wait_for_timeout(args.empty_retry_ms)
                        html = page.content()
                        jd = parse_detail_description(html)
                if not jd:
                    status = classify_empty_detail(html)
                    append_checkpoint(args.checkpoint, record_id, url, status)
                    coordination.finish(record_id, args.coord_owner, status)
                    failed += 1
                    print(f"  {status}", flush=True)
                    consecutive_empty = consecutive_empty + 1 if status == "empty" else 0
                    if consecutive_empty >= args.empty_streak_limit:
                        print(f"连续空页，冷却 {args.empty_cooldown:.0f} 秒后继续", flush=True)
                        time.sleep(args.empty_cooldown)
                        consecutive_empty = 0
                    continue
                enriched = dict(row)
                enriched.update(
                    {
                        "description_raw": jd,
                        "jd": jd,
                        "detail_source": "headed_camoufox_forward",
                    }
                )
                append_output(args.output, enriched)
                done.add(record_id)
                coordination.finish(record_id, args.coord_owner, "success")
                written += 1
                consecutive_empty = 0
                print(f"  JD: {len(jd)} 字", flush=True)
            except (PlaywrightError, OSError) as exc:
                append_checkpoint(args.checkpoint, record_id, url, "error")
                coordination.finish(record_id, args.coord_owner, "error")
                failed += 1
                print(f"  详情失败: {type(exc).__name__}: {str(exc)[:180]}", flush=True)
                if page.is_closed():
                    return 5
    print(
        json.dumps({"processed": processed, "written": written, "failed": failed}, ensure_ascii=False),
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--state-dir", type=Path, default=STATE_DEFAULT)
    parser.add_argument("--profile", type=Path, default=PROFILE_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT_DEFAULT)
    parser.add_argument("--coord-db", type=Path, default=COORD_DB_DEFAULT)
    parser.add_argument("--coord-owner", default=FORWARD_OWNER, choices=(FORWARD_OWNER,))
    parser.add_argument("--page-settle-ms", type=int, default=500)
    parser.add_argument("--empty-retry-ms", type=int, default=500)
    parser.add_argument("--empty-streak-limit", type=int, default=5)
    parser.add_argument("--empty-cooldown", type=float, default=60.0)
    parser.add_argument("--navigation-timeout-ms", type=int, default=30000)
    parser.add_argument("--verification-timeout", type=int, default=300)
    args = parser.parse_args()
    if (
        not args.source.is_file()
        or args.page_settle_ms < 0
        or args.empty_retry_ms < 0
        or args.empty_streak_limit < 1
        or args.empty_cooldown < 0
        or args.navigation_timeout_ms < 1000
        or args.verification_timeout < 10
    ):
        parser.error("invalid source/settle/navigation/verification settings")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
