"""Keep the anonymous BOSS public-card backfill alive with block-aware cooldown.

The supervisor never uses a login profile or Cookie.  It probes one pending
record before starting the four-worker backfill.  A blocking response keeps
the process alive but backs off instead of consuming the whole queue as
failed records.
"""

from __future__ import annotations

import argparse
import json
import signal
import threading
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))

from tools.backfill_boss_public_card_jd import (  # noqa: E402
    BLOCK_STATUSES,
    DEFAULT_HOST,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_INFLIGHT,
    DEFAULT_WORKERS,
    RunLock,
    SOURCE_DEFAULT,
    STATE_DEFAULT,
    encrypted_job_id,
    fetch_card,
    iter_pending_rows,
    load_attempt_counts,
    load_success_keys,
    now_iso,
    run_backfill,
)


OUTPUT_DEFAULT = STATE_DEFAULT / "\u5361\u7247\u8be6\u60c5\u8865\u5145.jsonl"
CHECKPOINT_DEFAULT = STATE_DEFAULT / "checkpoint.jsonl"
SUMMARY_DEFAULT = STATE_DEFAULT / "summary.json"
RUN_LOCK_DEFAULT = STATE_DEFAULT / "run.lock"
SUPERVISOR_LOCK_DEFAULT = STATE_DEFAULT / "supervisor.lock"
LOG_DEFAULT = STATE_DEFAULT / "supervisor.log"
PRIOR_DETAILS_DEFAULT = (
    PROJECT_ROOT
    / ".crawler-state"
    / "recruitment-details"
    / "BOSS"
    / "\u8be6\u60c5\u8865\u5145-\u5361\u7247\u5408\u5e76.jsonl"
)
DEFAULT_COOLDOWN_SECONDS = 120.0
MAX_COOLDOWN_SECONDS = 900.0


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def sleep_interruptibly(seconds: float, stop_event: threading.Event) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while not stop_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        stop_event.wait(min(1.0, remaining))


def next_probe_row(
    source: Path,
    output: Path,
    prior_details: Path | None,
    checkpoint: Path,
    max_attempts: int,
) -> dict[str, Any] | None:
    completed = load_success_keys([path for path in (prior_details, output) if path is not None])
    attempts = load_attempt_counts(checkpoint)
    for row in iter_pending_rows(
        source,
        completed=completed,
        attempt_counts=attempts,
        max_attempts=max_attempts,
        limit=None,
    ):
        if encrypted_job_id(row):
            return row
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT_DEFAULT)
    parser.add_argument("--summary", type=Path, default=SUMMARY_DEFAULT)
    parser.add_argument("--run-lock", type=Path, default=RUN_LOCK_DEFAULT)
    parser.add_argument("--supervisor-lock", type=Path, default=SUPERVISOR_LOCK_DEFAULT)
    parser.add_argument("--prior-details", type=Path, default=PRIOR_DETAILS_DEFAULT)
    parser.add_argument("--log", type=Path, default=LOG_DEFAULT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-inflight", type=int, default=DEFAULT_MAX_INFLIGHT)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--read-timeout", type=float, default=20.0)
    parser.add_argument("--cooldown-seconds", type=float, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--max-cooldown-seconds", type=float, default=MAX_COOLDOWN_SECONDS)
    args = parser.parse_args(argv)

    if not args.source.is_file():
        parser.error(f"source does not exist: {args.source}")
    if not 1 <= args.workers <= 16:
        parser.error("workers must be between 1 and 16")
    if args.max_inflight < args.workers:
        parser.error("max-inflight must be at least workers")
    if args.cooldown_seconds < 30 or args.max_cooldown_seconds < args.cooldown_seconds:
        parser.error("cooldown must be at least 30 seconds and max-cooldown must be >= cooldown")

    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)

    cooldown = args.cooldown_seconds
    args.prior_details = args.prior_details if args.prior_details.is_file() else None

    try:
        with RunLock(args.supervisor_lock):
            append_event(args.log, {"at": now_iso(), "event": "supervisor_started", "workers": args.workers})
            while not stop_event.is_set():
                row = next_probe_row(
                    args.source,
                    args.output,
                    args.prior_details,
                    args.checkpoint,
                    args.max_attempts,
                )
                if row is None:
                    append_event(args.log, {"at": now_iso(), "event": "complete"})
                    return 0

                probe = fetch_card(
                    row,
                    host=args.host,
                    proxy=args.proxy,
                    timeout=(args.connect_timeout, args.read_timeout),
                    max_attempts=1,
                    min_jd_length=1,
                    stop_event=stop_event,
                )
                probe_status = str(probe.get("detail_fetch_status") or "unknown")
                append_event(
                    args.log,
                    {
                        "at": now_iso(),
                        "event": "probe",
                        "record_id": probe.get("record_id"),
                        "status": probe_status,
                        "response_code": probe.get("response_code"),
                    },
                )

                if probe_status in BLOCK_STATUSES:
                    append_event(args.log, {"at": now_iso(), "event": "cooldown", "seconds": cooldown})
                    sleep_interruptibly(cooldown, stop_event)
                    cooldown = min(args.max_cooldown_seconds, max(args.cooldown_seconds, cooldown * 1.5))
                    continue

                if probe_status != "success":
                    append_event(args.log, {"at": now_iso(), "event": "transient_wait", "seconds": 30})
                    sleep_interruptibly(30, stop_event)
                    continue

                cooldown = args.cooldown_seconds
                try:
                    summary = run_backfill(
                        source=args.source,
                        output=args.output,
                        checkpoint=args.checkpoint,
                        summary_path=args.summary,
                        lock_path=args.run_lock,
                        prior_details=args.prior_details,
                        host=args.host,
                        proxy=args.proxy,
                        workers=args.workers,
                        max_inflight=args.max_inflight,
                        max_attempts=args.max_attempts,
                        timeout=(args.connect_timeout, args.read_timeout),
                        min_jd_length=1,
                        limit=None,
                    )
                except RuntimeError as exc:
                    append_event(args.log, {"at": now_iso(), "event": "run_lock_busy", "error": str(exc)})
                    sleep_interruptibly(30, stop_event)
                    continue

                append_event(args.log, {"at": now_iso(), "event": "backfill", "summary": summary})
                if summary.get("status") == "complete":
                    return 0
                if summary.get("stopped_reason") in BLOCK_STATUSES:
                    append_event(args.log, {"at": now_iso(), "event": "cooldown", "seconds": cooldown})
                    sleep_interruptibly(cooldown, stop_event)
                    cooldown = min(args.max_cooldown_seconds, max(args.cooldown_seconds, cooldown * 1.5))
                else:
                    sleep_interruptibly(30, stop_event)
    except RuntimeError as exc:
        append_event(args.log, {"at": now_iso(), "event": "not_started", "error": str(exc)})
        return 2

    append_event(args.log, {"at": now_iso(), "event": "stopped_by_signal"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
