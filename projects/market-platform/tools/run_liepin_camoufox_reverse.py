#!/usr/bin/env python3
"""Collect Liepin details from the bottom with a persistent headed Camoufox."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable


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
    classify_empty_detail,
    completed_ids,
    build_static_session,
    fetch_static_description,
    open_public_detail,
    parse_detail_description,
    profile_run_lock,
    record_key,
)
STATE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-camoufox-reverse"
PROFILE_DEFAULT = STATE_DEFAULT / "profile"
OUTPUT_DEFAULT = WORKER_OUTPUTS / "worker-reverse.jsonl"
CHECKPOINT_DEFAULT = STATE_DEFAULT / "checkpoint.jsonl"
COORD_DB_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-shared" / "coordination.sqlite3"
EXIT_CHALLENGE = 86


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def iter_jsonl_reverse(
    path: Path,
    chunk_size: int = 1024 * 1024,
) -> Iterable[dict[str, Any]]:
    """Read JSONL from the end without loading the entire source into memory."""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        remainder = b""
        while position > 0:
            size = min(chunk_size, position)
            position -= size
            handle.seek(position)
            parts = (handle.read(size) + remainder).split(b"\n")
            remainder = parts[0]
            for raw_line in reversed(parts[1:]):
                if raw_line.strip():
                    value = json.loads(raw_line.decode("utf-8-sig"))
                    if isinstance(value, dict):
                        yield value
        if remainder.strip():
            value = json.loads(remainder.decode("utf-8-sig"))
            if isinstance(value, dict):
                yield value


def append_output(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def iter_worker_rows(
    path: Path,
    total_rows: int,
    direction: str,
    start_index: int,
) -> Iterable[tuple[int, int, dict[str, Any]]]:
    if direction == "reverse":
        for offset, row in enumerate(iter_jsonl_reverse(path), 1):
            source_index = total_rows - offset + 1
            if source_index > start_index:
                continue
            yield source_index, offset, row
        return
    sequence = 0
    for source_index, row in enumerate(iter_jsonl(path), 1):
        if source_index < start_index:
            continue
        sequence += 1
        yield source_index, sequence, row


def sync_browser_session(context: Any, page: Any, static_session: Any) -> None:
    """Align the public HTTP client with the browser UA and current site cookies."""
    try:
        static_session.headers["User-Agent"] = str(page.evaluate("navigator.userAgent"))
    except PlaywrightError:
        pass
    for cookie in context.cookies():
        domain = str(cookie.get("domain") or "")
        if domain.lstrip(".").endswith("liepin.com"):
            static_session.cookies.set(
                str(cookie.get("name") or ""),
                str(cookie.get("value") or ""),
                domain=domain,
                path=str(cookie.get("path") or "/"),
            )


def run(args: argparse.Namespace) -> int:
    from camoufox.sync_api import Camoufox

    total_rows = sum(1 for _ in iter_jsonl(args.source))
    done = completed_ids(args.output)
    args.profile.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    processed = written = failed = 0
    consecutive_empty = 0

    with (
        LiepinCoordination(args.coord_db) as coordination,
        profile_run_lock(args.state_dir / "run.lock"),
        build_static_session() as static_session,
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
        page = context.new_page()
        for old_page in old_pages:
            try:
                old_page.close()
            except PlaywrightError:
                pass
        if args.startup_wait:
            try:
                page.goto(
                    "https://www.liepin.com/",
                    wait_until="domcontentloaded",
                    timeout=args.navigation_timeout_ms,
                )
            except PlaywrightError as exc:
                print(f"猎聘首页打开异常，仍保留登录等待: {type(exc).__name__}", flush=True)
            print(f"首次页面已打开，等待 {args.startup_wait:.0f} 秒供人工检查", flush=True)
            time.sleep(args.startup_wait)
        progress = coordination.status()["progress"]
        prior_index = progress.get(args.coord_owner)
        start_index = args.start_index or (
            int(prior_index) if prior_index is not None else total_rows
        )
        print(
            f"Camoufox {args.direction} worker 已启动，起点 {start_index}",
            flush=True,
        )
        for source_index, sequence, row in iter_worker_rows(
            args.source,
            total_rows,
            args.direction,
            start_index,
        ):
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
            print(f"[{processed}] sequence={sequence} source_index={source_index} {url}", flush=True)
            try:
                html = ""
                jd = fetch_static_description(url, timeout=3.0, session=static_session)
                detail_source = f"public_http_camoufox_{args.direction}"
                if not jd and args.disable_browser_fallback:
                    append_checkpoint(args.checkpoint, record_id, url, "empty")
                    coordination.finish(record_id, args.coord_owner, "empty")
                    failed += 1
                    consecutive_empty += 1
                    print("  empty（匿名模式，不进入登录页）", flush=True)
                    if consecutive_empty >= args.empty_streak_limit:
                        if args.empty_cooldown:
                            time.sleep(args.empty_cooldown)
                        consecutive_empty = 0
                    continue
                if not jd:
                    html, gate, retried_anonymously = open_public_detail(
                        context,
                        page,
                        url,
                        args.navigation_timeout_ms,
                    )
                    if retried_anonymously:
                        static_session.cookies.clear()
                    if args.page_settle_ms:
                        page.wait_for_timeout(args.page_settle_ms)
                        html = page.content()
                    if gate is not None:
                        deferred_status = f"deferred_{gate}"
                        append_checkpoint(args.checkpoint, record_id, url, deferred_status)
                        coordination.finish(record_id, args.coord_owner, deferred_status)
                        coordination.advance(args.coord_owner, max(1, source_index - 1))
                        print(
                            f"  {gate}（已延后；重建匿名实例后继续下一条）",
                            flush=True,
                        )
                        return EXIT_CHALLENGE
                    jd = parse_detail_description(html)
                    detail_source = f"headed_camoufox_{args.direction}"
                    if not jd:
                        status = classify_empty_detail(html)
                        if status == "empty" and args.empty_retry_ms > 0:
                            page.reload(
                                wait_until="domcontentloaded",
                                timeout=args.navigation_timeout_ms,
                            )
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
                        "detail_source": detail_source,
                    }
                )
                append_output(args.output, enriched)
                done.add(record_id)
                coordination.finish(record_id, args.coord_owner, "success")
                written += 1
                consecutive_empty = 0
                print(f"  JD: {len(jd)} 字", flush=True)
                if args.recycle_after and processed >= args.recycle_after:
                    print(
                        f"已处理 {processed} 个页面，主动重建 Camoufox 释放内存",
                        flush=True,
                    )
                    return 75
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
    parser.add_argument(
        "--coord-owner",
        default=REVERSE_OWNER,
        choices=tuple(REVERSE_OWNERS),
    )
    parser.add_argument("--direction", choices=("reverse",), default="reverse")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--page-settle-ms", type=int, default=500)
    parser.add_argument("--empty-retry-ms", type=int, default=500)
    parser.add_argument("--empty-streak-limit", type=int, default=5)
    parser.add_argument("--empty-cooldown", type=float, default=60.0)
    parser.add_argument("--navigation-timeout-ms", type=int, default=30000)
    parser.add_argument("--verification-timeout", type=int, default=300)
    parser.add_argument(
        "--disable-browser-fallback",
        action="store_true",
        help="keep the reverse worker anonymous and never navigate to a login/challenge page",
    )
    parser.add_argument("--startup-wait", type=float, default=0.0)
    parser.add_argument(
        "--recycle-after",
        type=int,
        default=0,
        help="recreate the browser after this many visited pages; 0 disables recycling",
    )
    args = parser.parse_args()
    if (
        not args.source.is_file()
        or args.page_settle_ms < 0
        or args.empty_retry_ms < 0
        or args.empty_streak_limit < 1
        or args.empty_cooldown < 0
        or args.navigation_timeout_ms < 1000
        or args.verification_timeout < 10
        or args.startup_wait < 0
        or args.recycle_after < 0
        or args.start_index < 0
        or args.coord_owner not in REVERSE_OWNERS
        or args.direction != "reverse"
    ):
        parser.error("invalid source/settle/navigation/verification settings")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
