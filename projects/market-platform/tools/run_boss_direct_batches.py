#!/usr/bin/env python3
"""Run the verified BOSS detail extractor in resumable, CDP-safe batches."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = PROJECT_ROOT / ".crawler-state" / "boss-original-run" / "plan.json"


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def key(row: dict) -> str:
    for field in ("record_id", "job_id", "detail_url", "job_link"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def successful(path: Path) -> set[str]:
    return {
        key(row)
        for row in iter_jsonl(path)
        if key(row) and str(row.get("jd") or "").strip()
    }


def cdp_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def run(plan_path: Path, batch_size: int, cooldown: float, gap_min: float, gap_max: float) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    spec = plan["worker_specs"][0]
    shard = PROJECT_ROOT / spec["shard"]
    output = Path(spec["output"])
    checkpoint = Path(spec["checkpoint"])
    port = int(spec["port"])
    profile = Path(spec["profile"])
    scraper = PROJECT_ROOT / "drv_crawler" / "boss_cdp" / "scripts" / "boss_cdp_raw.py"
    log_path = PROJECT_ROOT / ".crawler-state" / "boss-original-run" / "logs" / "direct-batches.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = successful(output)
    rows = [row for row in iter_jsonl(shard) if key(row) and key(row) not in completed]
    print(f"BOSS direct batches: pending={len(rows)} port={port} gap={gap_min}-{gap_max}s batch={batch_size}", flush=True)
    batch_no = 0
    while rows:
        if not cdp_ready(port):
            print("BOSS CDP unavailable; stopping without retry loop", flush=True)
            return 2
        batch_no += 1
        before = len(successful(output))
        command = [
            sys.executable, str(scraper), "--input", str(shard), "--detail",
            "--detail-output", str(output), "--detail-checkpoint", str(checkpoint),
            "--cdp-port", str(port), "--cdp-data-dir", str(profile),
            "--detail-gap-min", str(gap_min), "--detail-gap-max", str(gap_max),
            "--max-details", str(min(batch_size, len(rows))),
        ]
        event = {"batch": batch_no, "pending": len(rows), "started_at": time.time()}
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(event, ensure_ascii=False) + "\n")
            log.flush()
            result = subprocess.run(command, cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
        after = len(successful(output))
        progress = after - before
        rows = [row for row in iter_jsonl(shard) if key(row) and key(row) not in successful(output)]
        print(f"BOSS batch {batch_no} finished: new={progress} remaining={len(rows)}", flush=True)
        if result.returncode != 0 or progress <= 0:
            print(f"BOSS batch stopped: returncode={result.returncode} progress={progress}", flush=True)
            return 3
        if rows:
            print(f"BOSS cooling {cooldown:.0f}s", flush=True)
            time.sleep(cooldown)
    print("BOSS direct batches complete", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--cooldown", type=float, default=300)
    parser.add_argument("--gap-min", type=float, default=12)
    parser.add_argument("--gap-max", type=float, default=18)
    args = parser.parse_args()
    if args.batch_size < 1 or args.cooldown < 0 or args.gap_min < 0 or args.gap_max < args.gap_min:
        parser.error("invalid batch/cooldown/gap values")
    return run(args.plan, args.batch_size, args.cooldown, args.gap_min, args.gap_max)


if __name__ == "__main__":
    raise SystemExit(main())
