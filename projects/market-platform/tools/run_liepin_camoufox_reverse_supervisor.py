#!/usr/bin/env python3
"""Restart the reverse Camoufox worker after planned recycling or a crash."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


WORKER = Path(__file__).with_name("run_liepin_camoufox_dual.py")
EXIT_CHALLENGE = 86
DEFAULT_WORKER_ARGS = (
    "--page-settle-ms",
    "0",
    "--empty-retry-ms",
    "0",
    "--empty-streak-limit",
    "5",
    "--empty-cooldown",
    "5",
    "--startup-wait",
    "0",
    "--lanes",
    "2",
)


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
            return 0
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
                f"Camoufox reverse-1 will cool down for {delay:.1f}s, then resume.",
                flush=True,
            )
            time.sleep(delay)
            continue
        challenge_streak = 0
        if attempt >= max_restarts:
            print(
                f"Camoufox restart budget exhausted: exit={result.returncode}",
                flush=True,
            )
            return result.returncode
        reason = "planned recycle" if result.returncode == 75 else "unexpected exit"
        print(
            f"Camoufox {reason}: exit={result.returncode}; "
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
