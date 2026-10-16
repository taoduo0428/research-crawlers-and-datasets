from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from facebook_batch_summary import summarize_run


BLOCKING_GRAPHQL_CODES = {1675004, 3252001}
BLOCKING_API_CODES = {368}
BLOCKING_STOP_REASONS = {"session-restricted", "rate-limited"}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def platform_block_reasons(summary: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    graphql_code = _int_or_none(summary.get("reaction_latest_graphql_error_code"))
    session_graphql_code = _int_or_none(summary.get("session_restriction_graphql_code"))
    api_code = _int_or_none(summary.get("session_restriction_api_error_code"))
    stop_reasons = {str(item) for item in (summary.get("pagination_stop_reasons") or [])}

    if summary.get("session_restricted"):
        reasons.append("session-restricted")
    if summary.get("reaction_latest_rate_limited"):
        reasons.append("reaction-rate-limited")
    if graphql_code in BLOCKING_GRAPHQL_CODES:
        reasons.append(f"graphql-{graphql_code}")
    if session_graphql_code in BLOCKING_GRAPHQL_CODES:
        reasons.append(f"graphql-{session_graphql_code}")
    if api_code in BLOCKING_API_CODES:
        reasons.append(f"api-{api_code}")
    for reason in sorted(stop_reasons & BLOCKING_STOP_REASONS):
        reasons.append(reason)
    if str(summary.get("deep_completion_status") or "") in BLOCKING_STOP_REASONS:
        reasons.append(str(summary["deep_completion_status"]))

    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped


def next_state(
    summary: dict[str, Any],
    previous: dict[str, Any] | None = None,
    *,
    base_cooldown_seconds: int = 20,
    min_cooldown_seconds: int = 10,
    max_cooldown_seconds: int = 120,
) -> dict[str, Any]:
    previous = previous or {}
    previous_cooldown = int(previous.get("next_cooldown_seconds") or base_cooldown_seconds)
    previous_clean_streak = int(previous.get("clean_streak") or 0)
    blockers = platform_block_reasons(summary)

    terminal = bool(summary.get("deep_capture_completed")) and summary.get("deep_completion_status") == "terminal-complete"
    has_reaction_data = int(summary.get("reaction_query_records") or 0) > 0

    if blockers:
        decision = "stop-batch"
        next_action = "fresh-probe-before-resume"
        clean_streak = 0
        cooldown = max_cooldown_seconds
        health = "blocked-live"
    elif terminal:
        decision = "continue"
        next_action = "next-candidate-after-cooldown"
        clean_streak = previous_clean_streak + 1
        # Only accelerate gradually after proven terminal runs; never below the floor.
        cooldown = max(min_cooldown_seconds, int(round(previous_cooldown * 0.85)))
        health = "terminal-clean"
    elif has_reaction_data:
        decision = "continue-cautious"
        next_action = "next-candidate-after-cooldown"
        clean_streak = 0
        cooldown = min(max_cooldown_seconds, max(base_cooldown_seconds, int(round(previous_cooldown * 1.5))))
        health = "nonterminal-observed"
    else:
        decision = "continue-cautious"
        next_action = "next-candidate-after-cooldown"
        clean_streak = 0
        cooldown = min(max_cooldown_seconds, max(base_cooldown_seconds, previous_cooldown + 15))
        health = "low-confidence"

    return {
        "decision": decision,
        "next_action": next_action,
        "health": health,
        "block_reasons": blockers,
        "next_cooldown_seconds": cooldown,
        "clean_streak": clean_streak,
        "source_post_id": summary.get("source_post_id"),
        "reaction_users": int(summary.get("reaction_user_count") or 0),
        "reaction_pages": int(summary.get("reaction_page_count") or 0),
        "deep_completion_status": summary.get("deep_completion_status"),
        "session_restricted": bool(summary.get("session_restricted")),
        "latest_rate_limited": bool(summary.get("reaction_latest_rate_limited")),
        "latest_graphql_error_code": summary.get("reaction_latest_graphql_error_code"),
        "session_restriction_graphql_code": summary.get("session_restriction_graphql_code"),
        "session_restriction_api_error_code": summary.get("session_restriction_api_error_code"),
        "session_restriction_friendly_name": summary.get("session_restriction_friendly_name"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive a fail-safe batch pressure decision from one completed Facebook capture run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--state")
    parser.add_argument("--out")
    parser.add_argument("--base-cooldown-seconds", type=int, default=20)
    parser.add_argument("--min-cooldown-seconds", type=int, default=10)
    parser.add_argument("--max-cooldown-seconds", type=int, default=120)
    args = parser.parse_args()

    summary = summarize_run(args.run_dir)
    previous = load_json(Path(args.state)) if args.state else {}
    result = next_state(
        summary,
        previous,
        base_cooldown_seconds=max(0, args.base_cooldown_seconds),
        min_cooldown_seconds=max(0, args.min_cooldown_seconds),
        max_cooldown_seconds=max(args.min_cooldown_seconds, args.max_cooldown_seconds),
    )
    result["run_dir"] = str(Path(args.run_dir))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 10 if result["decision"] == "stop-batch" else 0


if __name__ == "__main__":
    raise SystemExit(main())
