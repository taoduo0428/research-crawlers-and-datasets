#!/usr/bin/env python3
"""Keep the headed Edge forward worker running until both directions meet."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


WORKER = Path(__file__).with_name("run_liepin_browser_single.py")
DEFERRED_WORKER = Path(__file__).with_name("run_liepin_deferred_public.py")
EXIT_CHALLENGE = 86
DEFAULT_WORKER_ARGS = (
    "--limit",
    "1000000",
    "--max-attempts",
    "0",
    "--detail-delay-min",
    "0",
    "--detail-delay-max",
    "0",
    "--page-settle-ms",
    "0",
    "--empty-settle-retry-ms",
    "0",
    "--batch-size",
    "1000000",
    "--batch-cooldown",
    "0",
    "--consecutive-empty-limit",
    "5",
    "--consecutive-error-limit",
    "5",
    "--soft-fail-cooldown",
    "5",
    "--browser-channel",
    "msedge",
    "--reuse-profile-cookies",
    "--ignore-cooldown",
)


def run_deferred(max_runs: int, retry_cooldown: float) -> int:
    stable_empty_runs = 0
    last_returncode = 2
    for attempt in range(max_runs + 1):
        result = subprocess.run(
            [
                sys.executable,
                str(DEFERRED_WORKER),
                "--max-rounds",
                "3",
                "--round-cooldown",
                "15",
            ],
            check=False,
        )
        last_returncode = result.returncode
        if result.returncode == 0:
            stable_empty_runs += 1
            if stable_empty_runs >= 2:
                return 0
            time.sleep(2)
            continue
        stable_empty_runs = 0
        if attempt >= max_runs:
            break
        print(
            f"Deferred anonymous Edge still has pending rows; retry in "
            f"{retry_cooldown:.1f}s",
            flush=True,
        )
        time.sleep(retry_cooldown)
    return last_returncode


def run(
    max_restarts: int,
    restart_delay: float,
    challenge_cooldown: float = 15.0,
    max_challenge_cooldown: float = 120.0,
) -> int:
    challenge_streak = 0
    for attempt in range(max_restarts + 1):
        result = subprocess.run(
            [sys.executable, str(WORKER), *DEFAULT_WORKER_ARGS],
            check=False,
        )
        if result.returncode == 0:
            return run_deferred(max_restarts, max(15.0, challenge_cooldown))
        if result.returncode == EXIT_CHALLENGE:
            if attempt >= max_restarts:
                return result.returncode
            delay = min(
                max_challenge_cooldown,
                challenge_cooldown * (2 ** min(challenge_streak, 3)),
            )
            challenge_streak += 1
            print(
                "Public Liepin page entered a challenge; no login is required. "
                f"Edge will cool down for {delay:.1f}s, then resume its checkpoint.",
                flush=True,
            )
            time.sleep(delay)
            continue
        challenge_streak = 0
        if attempt >= max_restarts:
            print(f"Edge restart budget exhausted: exit={result.returncode}", flush=True)
            return result.returncode
        print(
            f"Edge worker exit={result.returncode}; "
            f"restart {attempt + 1}/{max_restarts} in {restart_delay:.1f}s",
            flush=True,
        )
        time.sleep(restart_delay)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-restarts", type=int, default=500)
    parser.add_argument("--restart-delay", type=float, default=2.0)
    parser.add_argument("--challenge-cooldown", type=float, default=15.0)
    parser.add_argument("--max-challenge-cooldown", type=float, default=120.0)
    args = parser.parse_args()
    if (
        args.max_restarts < 0
        or args.restart_delay < 0
        or args.challenge_cooldown < 0
        or args.max_challenge_cooldown < args.challenge_cooldown
    ):
        parser.error("restart settings must be non-negative")
    return run(
        args.max_restarts,
        args.restart_delay,
        args.challenge_cooldown,
        args.max_challenge_cooldown,
    )


if __name__ == "__main__":
    raise SystemExit(main())
