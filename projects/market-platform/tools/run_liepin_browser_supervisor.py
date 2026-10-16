#!/usr/bin/env python3
"""Restart the forward Liepin browser worker after bounded cooldowns until workers meet."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.liepin_coordination import FORWARD_OWNER, REVERSE_OWNERS, LiepinCoordination

CHILD_SCRIPT = PROJECT_ROOT / "tools" / "run_liepin_browser_single.py"
COORD_DB_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-shared" / "coordination.sqlite3"
COOLDOWN_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-browser-single" / "cooldown.json"
EXIT_CHALLENGE = 86


def cooldown_remaining(path: Path) -> float:
    if not path.is_file():
        return 0.0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0.0, float(payload.get("cooldown_until") or 0) - time.time())
    except (OSError, ValueError, json.JSONDecodeError):
        return 0.0


def workers_met(coord_db: Path) -> bool:
    if not coord_db.is_file():
        return False
    with LiepinCoordination(coord_db) as coordination:
        progress = coordination.status()["progress"]
    if not isinstance(progress, dict):
        return False
    forward = progress.get(FORWARD_OWNER)
    reverse_positions = [
        progress.get(owner)
        for owner in REVERSE_OWNERS
        if isinstance(progress.get(owner), int)
    ]
    return bool(
        isinstance(forward, int)
        and reverse_positions
        and forward >= min(reverse_positions)
    )


def run(args: argparse.Namespace) -> int:
    child_args = list(args.child_args)
    if child_args and child_args[0] == "--":
        child_args.pop(0)
    if not child_args:
        raise RuntimeError("child crawler arguments are required after --")
    command = [sys.executable, str(CHILD_SCRIPT), *child_args]
    for attempt in range(1, args.max_restarts + 1):
        if workers_met(args.coord_db):
            print("正反向进度已相遇，正向 supervisor 停止", flush=True)
            return 0
        remaining = cooldown_remaining(args.cooldown_file)
        if remaining > 0:
            print(f"正向冷却 {remaining:.0f} 秒后自动重启", flush=True)
            time.sleep(remaining)
        print(f"启动正向猎聘 worker，轮次 {attempt}/{args.max_restarts}", flush=True)
        result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        print(f"正向 worker 退出码 {result.returncode}", flush=True)
        if result.returncode == EXIT_CHALLENGE:
            print("公开页面进入安全中转，停止 supervisor，不要求登录且不自动重启", flush=True)
            return result.returncode
        if workers_met(args.coord_db):
            print("正反向进度已相遇，正向 supervisor 停止", flush=True)
            return 0
        if attempt < args.max_restarts and cooldown_remaining(args.cooldown_file) <= 0:
            time.sleep(args.restart_delay)
    print("正向 supervisor 已达到最大重启次数", flush=True)
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coord-db", type=Path, default=COORD_DB_DEFAULT)
    parser.add_argument("--cooldown-file", type=Path, default=COOLDOWN_DEFAULT)
    parser.add_argument("--restart-delay", type=float, default=120.0)
    parser.add_argument("--max-restarts", type=int, default=1000)
    parser.add_argument("child_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.restart_delay < 0 or args.max_restarts < 1:
        parser.error("restart-delay must be non-negative and max-restarts positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
