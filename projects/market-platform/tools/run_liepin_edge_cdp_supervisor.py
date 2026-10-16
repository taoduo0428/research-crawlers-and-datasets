#!/usr/bin/env python3
"""Restart one independent Liepin Edge-CDP tab worker without restarting Edge."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.edge_cdp_runtime import ensure_edge_cdp


WORKER = Path(__file__).with_name("run_liepin_edge_cdp_worker.py")
DEFERRED_WORKER = Path(__file__).with_name("run_liepin_deferred_public.py")
EXIT_CHALLENGE = 86


def run_deferred(args: argparse.Namespace) -> int:
    stable_empty_runs = 0
    last_returncode = 2
    command = [
        sys.executable,
        str(DEFERRED_WORKER),
        "--cdp-endpoint",
        args.cdp_endpoint,
        "--max-rounds",
        "3",
        "--round-cooldown",
        "15",
    ]
    for attempt in range(args.max_restarts + 1):
        try:
            state = ensure_edge_cdp(args.cdp_endpoint, timeout=30.0)
            if state.get("launched"):
                print(
                    f"Edge CDP watchdog restored browser pid={state.get('pid')}",
                    flush=True,
                )
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            if attempt >= args.max_restarts:
                return 6
            print(
                f"owner={args.owner} Edge CDP unavailable: {type(exc).__name__}; "
                f"retry in {max(15.0, args.restart_delay):.1f}s",
                flush=True,
            )
            time.sleep(max(15.0, args.restart_delay))
            continue
        result = subprocess.run(command, check=False)
        last_returncode = result.returncode
        if result.returncode == 0:
            stable_empty_runs += 1
            if stable_empty_runs >= 2:
                return 0
            time.sleep(2)
            continue
        stable_empty_runs = 0
        if attempt >= args.max_restarts:
            break
        time.sleep(max(15.0, args.challenge_cooldown))
    return last_returncode


def run(args: argparse.Namespace) -> int:
    challenge_streak = 0
    command = [
        sys.executable,
        str(WORKER),
        "--owner",
        args.owner,
        "--cdp-endpoint",
        args.cdp_endpoint,
        "--delay-min",
        str(args.delay_min),
        "--delay-max",
        str(args.delay_max),
    ]
    for attempt in range(args.max_restarts + 1):
        try:
            state = ensure_edge_cdp(args.cdp_endpoint, timeout=30.0)
            if state.get("launched"):
                print(
                    f"owner={args.owner} Edge CDP restored pid={state.get('pid')}",
                    flush=True,
                )
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            if attempt >= args.max_restarts:
                return 6
            delay = max(15.0, args.restart_delay)
            print(
                f"owner={args.owner} Edge CDP unavailable: {type(exc).__name__}; "
                f"retry in {delay:.1f}s",
                flush=True,
            )
            time.sleep(delay)
            continue

        started_at = time.monotonic()
        result = subprocess.run(command, check=False)
        if result.returncode == 0:
            return run_deferred(args) if args.owner == "browser_forward" else 0
        if result.returncode == EXIT_CHALLENGE:
            if attempt >= args.max_restarts:
                return result.returncode
            if time.monotonic() - started_at >= 30.0:
                challenge_streak = 0
            delay = min(
                args.max_challenge_cooldown,
                args.challenge_cooldown * (2 ** min(challenge_streak, 3)),
            )
            challenge_streak += 1
            print(
                f"owner={args.owner} public transit; tab cooldown {delay:.1f}s",
                flush=True,
            )
            time.sleep(delay)
            continue
        challenge_streak = 0
        if attempt >= args.max_restarts:
            return result.returncode
        print(
            f"owner={args.owner} worker exit={result.returncode}; "
            f"restart in {args.restart_delay:.1f}s",
            flush=True,
        )
        time.sleep(args.restart_delay)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner",
        choices=("browser_forward", "api_reverse", "camoufox_reverse_2"),
        required=True,
    )
    parser.add_argument("--cdp-endpoint", default="http://127.0.0.1:9223")
    parser.add_argument("--max-restarts", type=int, default=500)
    parser.add_argument("--restart-delay", type=float, default=2.0)
    parser.add_argument("--challenge-cooldown", type=float, default=15.0)
    parser.add_argument("--max-challenge-cooldown", type=float, default=120.0)
    parser.add_argument("--delay-min", type=float, default=0.0)
    parser.add_argument("--delay-max", type=float, default=0.0)
    args = parser.parse_args()
    if (
        args.max_restarts < 0
        or args.restart_delay < 0
        or args.challenge_cooldown < 0
        or args.max_challenge_cooldown < args.challenge_cooldown
        or args.delay_min < 0
        or args.delay_max < args.delay_min
    ):
        parser.error("invalid supervisor settings")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
