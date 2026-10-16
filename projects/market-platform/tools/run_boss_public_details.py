"""Probe and collect publicly visible BOSS job details without login state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_boss_detail_workers import (  # noqa: E402
    DETAILS_DEFAULT,
    DETAIL_OUTPUT_ROOT,
    SOURCE_DEFAULT,
    import_boss_module,
    iter_jsonl,
    load_success_keys,
    record_key,
)
from tools.run_liepin_browser_single import profile_run_lock  # noqa: E402


STATE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "boss-public"
PROFILE_DEFAULT = STATE_DEFAULT / "profile"
OUTPUT_DEFAULT = DETAIL_OUTPUT_ROOT / "worker-public.jsonl"
CHECKPOINT_DEFAULT = STATE_DEFAULT / "checkpoint.jsonl"


def has_detail_url(row: dict[str, Any]) -> bool:
    return bool(str(row.get("detail_url") or row.get("job_link") or "").strip())


def select_pending_rows(
    source: Path,
    success_paths: Iterable[Path],
    limit: int,
) -> list[dict[str, Any]]:
    completed = load_success_keys(success_paths)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in iter_jsonl(source):
        key = record_key(row)
        if not key or key in completed or key in seen or not has_detail_url(row):
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def classify_runtime_error(exc: BaseException) -> str:
    message = str(exc).lower()
    if "login" in message or "登录" in message:
        return "blocked_login_required"
    if "verification" in message or "验证" in message or "access" in message:
        return "blocked_verification_required"
    if "cdp endpoint unavailable" in message or "connection" in message:
        return "transport_error"
    return "runtime_error"


def final_status(status: str, written: int) -> str:
    if status == "ok" and written == 0:
        return "no_public_full_jd"
    return status


def success_paths(output: Path) -> list[Path]:
    paths = [DETAILS_DEFAULT, *sorted(DETAIL_OUTPUT_ROOT.glob("worker-*.jsonl"))]
    if output not in paths:
        paths.append(output)
    return paths


def collect_public_details(
    source: Path,
    output: Path,
    checkpoint: Path,
    profile: Path,
    port: int,
    limit: int,
    gap_min: float,
    gap_max: float,
) -> int:
    paths = success_paths(output)
    completed_before = load_success_keys(paths)
    pending = select_pending_rows(source, paths, limit)
    if not pending:
        print(json.dumps({"status": "complete", "attempted": 0, "written": 0}, ensure_ascii=False))
        return 0

    profile.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    boss = import_boss_module()

    print("BOSS 匿名公开模式：不读取账号文件，不注入 Cookie。", flush=True)
    with profile_run_lock(profile.parent / "run.lock"):
        setup_result = boss.run_setup_chrome(
            cdp_port=port,
            cdp_data_dir=str(profile),
            wait_login=False,
            reset_profile=False,
        )
        if setup_result != 0:
            print(json.dumps({"status": "browser_start_failed", "attempted": 0, "written": 0}, ensure_ascii=False))
            return 2

        status = "ok"
        try:
            try:
                boss.scrape_details(
                    {"jobs": pending},
                    max_details=limit,
                    output_path=str(output),
                    checkpoint_path=str(checkpoint),
                    cdp_port=port,
                    load_timeout=15.0,
                    min_text_length=40,
                    retries=0,
                    gap_min=gap_min,
                    gap_max=gap_max,
                    resume=False,
                )
            except RuntimeError as exc:
                status = classify_runtime_error(exc)
        finally:
            boss.stop_cdp_chrome(str(profile))

    completed_after = load_success_keys(success_paths(output))
    written = len(completed_after - completed_before)
    status = final_status(status, written)
    summary = {
        "status": status,
        "attempted_budget": len(pending),
        "written": written,
        "output": str(output),
        "checkpoint": str(checkpoint),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if status == "ok" and written else 3


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect only fully visible BOSS JDs from a fresh anonymous Chrome profile."
    )
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT_DEFAULT)
    parser.add_argument("--profile", type=Path, default=PROFILE_DEFAULT)
    parser.add_argument("--port", type=int, default=9240)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--gap-min", type=float, default=4.0)
    parser.add_argument("--gap-max", type=float, default=7.0)
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        parser.error("limit must be between 1 and 50")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.gap_min < 0 or args.gap_max < args.gap_min:
        parser.error("detail delays must be ordered and non-negative")
    return collect_public_details(
        args.source,
        args.output,
        args.checkpoint,
        args.profile,
        args.port,
        args.limit,
        args.gap_min,
        args.gap_max,
    )


if __name__ == "__main__":
    raise SystemExit(main())
