from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analyze_facebook_capture import iter_records
from facebook_batch_summary import summarize_run
from facebook_capture_progress import summarize_reaction_records

PAGE2_MARKER = "FeedbackReactorsGraphService_At_Connection_Pagination"


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_records(run_dir: Path):
    capture = run_dir / "safe-facebook-http-capture.jsonl"
    if not capture.exists():
        return [], 0
    return iter_records(capture, 100000, min_line=1)


def page2_records(records):
    return [
        (line, record)
        for line, record in records
        if PAGE2_MARKER in str(record.get("friendly_name") or "")
    ]


def inspect_probe(run_dir: Path, expected_post_id: str) -> dict[str, Any]:
    diag = load_json(run_dir / "readonly-ui" / "exploration-diagnostics.json")
    screen = diag.get("reaction_screening") or {}
    proof = diag.get("reaction_preopened_source_proof") or {}
    pagination_runs = [item for item in (diag.get("pagination_runs") or []) if isinstance(item, dict)]
    records, parse_errors = load_records(run_dir)
    page2 = page2_records(records)
    replay_progress = summarize_reaction_records(records, expected_post_id)

    source_ok = str(proof.get("status") or "") == "matched"
    probe_only = bool(screen.get("probe_only"))
    no_deep = not bool(screen.get("deep_capture_requested")) and not pagination_runs and not page2
    session_restricted = bool(screen.get("session_restricted")) or bool(replay_progress.get("session_restricted"))
    rate_limited = bool(screen.get("latest_rate_limited")) or bool(replay_progress.get("latest_rate_limited"))
    status = str(screen.get("probe_status") or "")
    initial_ready = (
        status == "initial-ready"
        and probe_only
        and source_ok
        and no_deep
        and not session_restricted
        and not rate_limited
        and int(screen.get("query_records") or 0) > 0
        and int(screen.get("user_count") or 0) > 0
        and parse_errors == 0
    )

    result = {
        "stage": "probe",
        "run_dir": str(run_dir),
        "passed": initial_ready,
        "probe_status": status,
        "source_proof": proof.get("status"),
        "user_count": int(screen.get("user_count") or 0),
        "query_records": int(screen.get("query_records") or 0),
        "session_restricted": session_restricted,
        "session_restriction_friendly_name": screen.get("session_restriction_friendly_name") or replay_progress.get("session_restriction_friendly_name"),
        "latest_rate_limited": rate_limited,
        "latest_graphql_error_code": screen.get("latest_graphql_error_code") or replay_progress.get("latest_graphql_error_code"),
        "pagination_runs": len(pagination_runs),
        "page2_requests": len(page2),
        "parse_errors": parse_errors,
    }
    if initial_ready:
        result["reason"] = "initial-ready"
    elif session_restricted:
        result["reason"] = "blocked-session-restriction"
    elif rate_limited:
        result["reason"] = "blocked-rate-limit"
    elif not source_ok:
        result["reason"] = "source-proof-not-matched"
    elif not no_deep:
        result["reason"] = "probe-invariant-violated"
    else:
        result["reason"] = status or "not-ready"
    return result


def inspect_page2(run_dir: Path, expected_post_id: str) -> dict[str, Any]:
    records, parse_errors = load_records(run_dir)
    progress = summarize_reaction_records(records, expected_post_id)
    candidates = page2_records(records)

    cursor_present = any(bool(record.get("request_cursor_present")) for _, record in candidates)
    previous_end = any(bool(record.get("previous_end_cursor_available")) for _, record in candidates)
    cursor_match = any(record.get("request_cursor_matches_previous_end") is True for _, record in candidates)
    source_match = any(
        str(record.get("request_feedback_post_id") or record.get("response_feedback_post_id") or "")
        == str(expected_post_id)
        for _, record in candidates
    )
    page2_rate_limited = any(
        bool(record.get("response_rate_limited"))
        or record.get("response_graphql_error_code") == 1675004
        for _, record in candidates
    )
    page2_restricted = any(
        bool(record.get("response_temporary_feature_block"))
        or record.get("response_graphql_error_code") == 3252001
        or record.get("response_api_error_code") == 368
        for _, record in candidates
    )
    session_restricted = bool(progress.get("session_restricted"))
    rate_limited = bool(progress.get("latest_rate_limited")) or page2_rate_limited
    passed = (
        len(candidates) >= 1
        and cursor_present
        and previous_end
        and cursor_match
        and source_match
        and not session_restricted
        and not rate_limited
        and not page2_restricted
        and int(progress.get("user_count") or 0) > 0
        and parse_errors == 0
    )

    result = {
        "stage": "page2",
        "run_dir": str(run_dir),
        "passed": passed,
        "page2_requests": len(candidates),
        "cursor_present": cursor_present,
        "previous_end_cursor_available": previous_end,
        "cursor_matches_previous_end": cursor_match,
        "source_match": source_match,
        "reaction_user_count": int(progress.get("user_count") or 0),
        "reaction_page_count": int(progress.get("page_count") or 0),
        "session_restricted": session_restricted,
        "session_restriction_friendly_name": progress.get("session_restriction_friendly_name"),
        "latest_rate_limited": rate_limited,
        "latest_graphql_error_code": progress.get("latest_graphql_error_code"),
        "parse_errors": parse_errors,
    }
    if passed:
        result["reason"] = "page2-cursor-verified"
    elif session_restricted:
        result["reason"] = "blocked-session-restriction"
    elif rate_limited or page2_restricted:
        result["reason"] = "blocked-rate-limit-or-restriction"
    elif not candidates:
        result["reason"] = "page2-not-triggered"
    elif not cursor_present or not previous_end or not cursor_match:
        result["reason"] = "cursor-continuity-unproven"
    elif not source_match:
        result["reason"] = "page2-source-unproven"
    else:
        result["reason"] = "page2-incomplete"
    return result


def inspect_full(run_dir: Path, min_users: int) -> dict[str, Any]:
    summary = summarize_run(run_dir)
    diag = load_json(run_dir / "readonly-ui" / "exploration-diagnostics.json")
    pagination_runs = [item for item in (diag.get("pagination_runs") or []) if isinstance(item, dict)]
    all_runs = [item for item in pagination_runs if str(item.get("reaction_key") or "") == "__ALL__"]
    telemetry = (all_runs or pagination_runs)[-1] if (all_runs or pagination_runs) else {}
    passed = (
        bool(summary.get("deep_capture_completed"))
        and summary.get("deep_completion_status") == "terminal-complete"
        and summary.get("deep_completion_reason") == "terminal"
        and int(summary.get("reaction_user_count") or 0) >= min_users
    )
    return {
        "stage": "full",
        "run_dir": str(run_dir),
        "passed": passed,
        "reason": "terminal-complete" if passed else str(summary.get("deep_completion_status") or "incomplete"),
        "reaction_user_count": int(summary.get("reaction_user_count") or 0),
        "reaction_page_count": int(summary.get("reaction_page_count") or 0),
        "deep_capture_completed": bool(summary.get("deep_capture_completed")),
        "deep_completion_status": summary.get("deep_completion_status"),
        "deep_completion_reason": summary.get("deep_completion_reason"),
        "pagination_stop_reasons": summary.get("pagination_stop_reasons") or [],
        "elapsed_ms": int(telemetry.get("elapsed_ms") or 0),
        "seconds_per_growth": telemetry.get("seconds_per_growth"),
        "growths_per_minute": telemetry.get("growths_per_minute"),
        "effective_swipes_per_growth": telemetry.get("effective_swipes_per_growth"),
        "attempts": int(telemetry.get("attempts") or 0),
        "growth_events": int(telemetry.get("growth_events") or 0),
        "native_scroll_enabled": bool(telemetry.get("native_scroll_enabled")),
        "native_scroll_attempts": int(telemetry.get("native_scroll_attempts") or 0),
        "native_scroll_success": int(telemetry.get("native_scroll_success") or 0),
        "native_scroll_fallbacks": int(telemetry.get("native_scroll_fallbacks") or 0),
    }


def inspect_share(run_dir: Path) -> dict[str, Any]:
    summary = summarize_run(run_dir)
    classification = str(summary.get("share_screening_classification") or "")
    terminal = bool(summary.get("share_terminal_page_seen"))
    query_records = int(summary.get("share_query_records") or 0)
    allowed_final_classifications = {
        "visible-subset-terminal",
        "no-visible-share-users",
    }
    # A screening-only "enumerable" result is not enough for a full Share regression.
    # Require terminal pagination or an explicit final visibility classification.
    passed = terminal or classification in allowed_final_classifications
    return {
        "stage": "share",
        "run_dir": str(run_dir),
        "passed": passed,
        "reason": classification or ("terminal" if terminal else "share-not-verified"),
        "classification": classification,
        "query_records": query_records,
        "share_user_count": int(summary.get("share_user_count") or 0),
        "share_page_count": int(summary.get("share_page_count") or 0),
        "share_terminal_page_seen": terminal,
        "share_identity_gap": summary.get("share_identity_gap"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Machine-readable release gate for passive Facebook capture runs.")
    parser.add_argument("stage", choices=("probe", "page2", "full", "share"))
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--expected-post-id", default="")
    parser.add_argument("--min-users", type=int, default=3000)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if args.stage in {"probe", "page2"} and not args.expected_post_id:
        parser.error("--expected-post-id is required for probe/page2")

    if args.stage == "probe":
        result = inspect_probe(run_dir, args.expected_post_id)
    elif args.stage == "page2":
        result = inspect_page2(run_dir, args.expected_post_id)
    elif args.stage == "full":
        result = inspect_full(run_dir, args.min_users)
    else:
        result = inspect_share(run_dir)

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
