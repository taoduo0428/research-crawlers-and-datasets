import argparse
import csv
import json
from pathlib import Path

from analyze_facebook_capture import iter_records
from facebook_capture_progress import summarize_reaction_records, summarize_share_records


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def latest_action(diagnostics, action):
    for item in reversed(diagnostics.get("actions") or []):
        if isinstance(item, dict) and item.get("action") == action:
            return item
    return {}


def select_post(posts, source_post_id):
    source = str(source_post_id or "")
    if source:
        for post in posts or []:
            if str(post.get("post_id") or "") == source:
                return post
    if not source and len(posts or []) == 1:
        return posts[0]
    return {}


def family_target_validation_status(permalink_fallback, family, target_post_id):
    """Return a conservative per-family target verdict for permalink fallback."""
    explicit = permalink_fallback.get(f"{family}_target_validation_status")
    if explicit:
        return explicit
    if not permalink_fallback.get(f"{family}_eligible"):
        return None
    global_status = permalink_fallback.get("target_validation_status")
    if global_status in ("mismatch", "no-family-query"):
        return global_status
    if global_status == "matched":
        observed = [
            str(value)
            for value in (permalink_fallback.get(f"observed_{family}_source_post_ids") or [])
            if value
        ]
        if len(observed) == 1 and target_post_id and observed[0] == str(target_post_id):
            return "matched"
        if observed:
            return "mismatch"
        return "no-family-query"
    return None


def derive_deep_capture_completion(screening, pagination_runs):
    """Recompute completion conservatively, including legacy diagnostics."""
    requested = bool((screening or {}).get("deep_capture_requested"))
    runs = [item for item in (pagination_runs or []) if isinstance(item, dict)]
    if not requested:
        return False, "not-requested", None
    all_runs = [item for item in runs if str(item.get("reaction_key") or "") == "__ALL__"]
    scope = all_runs or runs
    if not scope:
        return False, "no-pagination-run", None
    tail = scope[-1]
    reason = str(tail.get("stop_reason") or "") or None
    if bool(tail.get("terminal_seen")) and reason == "terminal":
        return True, "terminal-complete", reason
    if reason == "session-restricted":
        status = "session-restricted"
    elif reason == "rate-limited":
        status = "rate-limited"
    elif reason == "target-mismatch":
        status = "target-mismatch"
    elif bool(tail.get("open_seen")):
        status = "partial-open-tail"
    else:
        status = "incomplete"
    return False, status, reason


def summarize_run(run_dir):
    run_dir = Path(run_dir)
    report = load_json(run_dir / "analysis" / "facebook-sample-patterns.json")
    diagnostics = load_json(run_dir / "readonly-ui" / "exploration-diagnostics.json")
    fallback_diagnostics = load_json(run_dir / "readonly-ui-fallback" / "exploration-diagnostics.json")
    permalink_fallback = load_json(run_dir / "native-permalink-fallback.json")
    metadata = load_json(run_dir / "batch-run-metadata.json")
    permalink_target_post_id = str(permalink_fallback.get("post_id") or "") or None

    reaction_progress = {}
    share_progress = {}
    capture_path = run_dir / "safe-facebook-http-capture.jsonl"
    if capture_path.exists():
        records, _ = iter_records(capture_path, 20000)
        reaction_progress = summarize_reaction_records(records, permalink_target_post_id)
        share_progress = summarize_share_records(records, permalink_target_post_id)

    reaction_enum = ((report.get("enumeration_status") or {}).get("reactions") or {})
    share_enum = ((report.get("enumeration_status") or {}).get("shares") or {})
    source_post_id = permalink_target_post_id or reaction_progress.get("source_post_id") or share_progress.get("source_post_id")
    if not source_post_id:
        posts = report.get("posts") or []
        if len(posts) == 1:
            source_post_id = posts[0].get("post_id")

    post = select_post(report.get("posts") or [], source_post_id)
    reaction_action = latest_action(diagnostics, "reaction")
    share_action = latest_action(diagnostics, "share")
    screening = diagnostics.get("reaction_screening") or {}
    share_screening = diagnostics.get("share_screening") or {}
    reaction_native_fallback = diagnostics.get("reaction_native_fallback") or {}
    share_native_fallback = diagnostics.get("share_native_fallback") or {}
    fallback_reaction_action = latest_action(fallback_diagnostics, "reaction")
    fallback_share_action = latest_action(fallback_diagnostics, "share")
    fallback_target_guard = fallback_diagnostics.get("target_guard") or {}
    fallback_target_guard_events = fallback_diagnostics.get("target_guard_events") or []
    permalink_reaction_eligible = bool(permalink_fallback.get("reaction_eligible"))
    permalink_share_eligible = bool(permalink_fallback.get("share_eligible"))
    permalink_target_validation_status = permalink_fallback.get("target_validation_status")
    reaction_target_validation_status = family_target_validation_status(
        permalink_fallback, "reaction", permalink_target_post_id
    )
    share_target_validation_status = family_target_validation_status(
        permalink_fallback, "share", permalink_target_post_id
    )
    pagination = reaction_enum.get("pagination") or {}
    if permalink_target_post_id and reaction_progress:
        progress_groups = reaction_progress.get("groups") or {}
        progress_open_groups = sorted(
            key for key, group in progress_groups.items()
            if group.get("open_page_seen") and not group.get("terminal_page_seen")
        )
        progress_terminal_groups = sorted(
            key for key, group in progress_groups.items() if group.get("terminal_page_seen")
        )
        pagination = {
            "open_groups": progress_open_groups,
            "terminal_groups": progress_terminal_groups,
            "all_observed_groups_terminal": bool(progress_groups) and not progress_open_groups,
        }
    pagination_runs = list(diagnostics.get("pagination_runs") or [])
    if reaction_target_validation_status == "matched":
        pagination_runs.extend(fallback_diagnostics.get("pagination_runs") or [])
    stop_reasons = []
    strategy_attempts = {}
    strategy_growth = {}
    budget_extensions = 0
    max_final_budget = 0
    max_absolute_cap = 0
    for item in pagination_runs:
        reason = item.get("stop_reason") if isinstance(item, dict) else None
        if reason and reason not in stop_reasons:
            stop_reasons.append(reason)
        if not isinstance(item, dict):
            continue
        for key, value in (item.get("strategy_attempts") or {}).items():
            strategy_attempts[key] = strategy_attempts.get(key, 0) + int(value or 0)
        for key, value in (item.get("strategy_growth") or {}).items():
            strategy_growth[key] = strategy_growth.get(key, 0) + int(value or 0)
        budget_extensions += int(item.get("budget_extensions") or 0)
        max_final_budget = max(max_final_budget, int(item.get("final_budget") or 0))
        max_absolute_cap = max(max_absolute_cap, int(item.get("absolute_cap") or 0))

    deep_capture_completed, deep_completion_status, deep_completion_reason = derive_deep_capture_completion(
        screening, pagination_runs
    )

    reaction_status = reaction_enum.get("status")
    reaction_visibility_hint = reaction_enum.get("ui_visibility_hint") or reaction_action.get("visibility_hint")
    friends_only = reaction_visibility_hint == "friends-only-names"
    reaction_query_records = reaction_progress.get("query_records", reaction_enum.get("query_records", 0))
    reaction_user_count = reaction_progress.get("user_count", reaction_enum.get("enumerated_identity_count", 0))
    if permalink_target_post_id:
        if reaction_query_records > 0:
            reaction_status = "visible-users-enumerated" if reaction_user_count > 0 else "query-triggered-no-visible-users"
        else:
            reaction_status = permalink_fallback.get("initial_reaction_status") or reaction_status
    reaction_source_matches_target = bool(
        permalink_target_post_id
        and reaction_query_records > 0
        and str(reaction_progress.get("source_post_id") or "") == permalink_target_post_id
    )
    reaction_permalink_recovered = bool(
        permalink_reaction_eligible
        and not friends_only
        and reaction_target_validation_status == "matched"
        and reaction_source_matches_target
        and (
            reaction_user_count > 0
            or (
                reaction_query_records > 0
                and bool(fallback_reaction_action.get("confirmed"))
            )
        )
    )
    final_share_query_records = share_progress.get("query_records", share_enum.get("query_records", 0))
    final_share_user_count = share_progress.get("user_count", share_enum.get("enumerated_identity_count", 0))
    share_source_matches_target = bool(
        permalink_target_post_id
        and final_share_query_records > 0
        and str(share_progress.get("source_post_id") or "") == permalink_target_post_id
    )
    share_permalink_recovered = bool(
        permalink_share_eligible
        and share_target_validation_status == "matched"
        and share_source_matches_target
        and (
            final_share_user_count > 0
            or (
                final_share_query_records > 0
                and bool(fallback_share_action.get("confirmed"))
            )
        )
    )
    screening_classification = screening.get("classification")
    decision_reason = screening.get("decision_reason")
    if not screening_classification:
        if friends_only:
            screening_classification = "visibility-restricted"
            decision_reason = decision_reason or "friends-only-names"
        elif reaction_user_count > 0:
            screening_classification = "enumerable"
            decision_reason = decision_reason or "visible-reactor-users"
        elif reaction_status == "query-not-triggered":
            screening_classification = "not-enumerable"
            decision_reason = decision_reason or "reaction-query-not-triggered"
        elif reaction_query_records > 0:
            screening_classification = "not-enumerable"
            decision_reason = decision_reason or "query-without-visible-users"

    return {
        "run_dir": str(run_dir),
        "batch_id": metadata.get("batch_id"),
        "candidate_index": metadata.get("candidate_index"),
        "feed_scroll_offset": metadata.get("feed_scroll_offset"),
        "started_at": metadata.get("started_at"),
        "finished_at": metadata.get("finished_at"),
        "error": metadata.get("error"),
        "capture_attempts": int(metadata.get("capture_attempts") or 0),
        "capture_retried": bool(metadata.get("capture_retried")),
        "source_post_id": source_post_id,
        "author_name": post.get("author_name"),
        "post_url": post.get("url"),
        "message_excerpt": (post.get("message") or "")[:240],
        "ui_reaction_count": reaction_enum.get("ui_total_count"),
        "ui_share_count": share_enum.get("ui_total_count"),
        "reaction_status": reaction_status,
        "reaction_visibility_hint": reaction_visibility_hint,
        "friends_only": friends_only,
        "screening_classification": screening_classification,
        "decision_reason": decision_reason,
        "screen_probe_attempts": int(screening.get("probe_attempts") or 0),
        "deep_capture_requested": bool(screening.get("deep_capture_requested")),
        "deep_capture_completed": deep_capture_completed,
        "deep_completion_status": deep_completion_status,
        "deep_completion_reason": deep_completion_reason,
        "reaction_query_records": reaction_query_records,
        "reaction_page_count": reaction_progress.get("page_count", len(report.get("reactor_pages") or [])),
        "reaction_user_count": reaction_user_count,
        "reaction_latest_rate_limited": bool(reaction_progress.get("latest_rate_limited")),
        "reaction_rate_limit_count": int(reaction_progress.get("rate_limit_count") or 0),
        "reaction_latest_graphql_error_code": reaction_progress.get("latest_graphql_error_code"),
        "session_restricted": bool(reaction_progress.get("session_restricted")),
        "session_restriction_graphql_code": reaction_progress.get("session_restriction_graphql_code"),
        "session_restriction_api_error_code": reaction_progress.get("session_restriction_api_error_code"),
        "session_restriction_friendly_name": reaction_progress.get("session_restriction_friendly_name"),
        "reaction_open_groups": pagination.get("open_groups") or [],
        "reaction_terminal_groups": pagination.get("terminal_groups") or [],
        "all_observed_groups_terminal": pagination.get("all_observed_groups_terminal"),
        "pagination_stop_reasons": stop_reasons,
        "pagination_strategy_attempts": strategy_attempts,
        "pagination_strategy_growth": strategy_growth,
        "pagination_budget_extensions": budget_extensions,
        "pagination_max_final_budget": max_final_budget,
        "pagination_absolute_cap": max_absolute_cap,
        "reaction_entry_scan_scrolls": reaction_action.get("entry_scan_scrolls"),
        "reaction_native_fallback_attempted": bool(reaction_native_fallback.get("attempted")),
        "reaction_native_fallback_succeeded": bool(reaction_native_fallback.get("succeeded")),
        "reaction_native_fallback_method": reaction_native_fallback.get("method"),
        "reaction_list_ready_attempts": int(reaction_native_fallback.get("attempts") or 0),
        "native_permalink_fallback_attempted": bool(permalink_fallback.get("attempted")),
        "native_permalink_reaction_eligible": permalink_reaction_eligible,
        "native_permalink_share_eligible": permalink_share_eligible,
        "native_permalink_source": permalink_fallback.get("source"),
        "native_permalink_post_id": permalink_fallback.get("post_id"),
        "native_permalink_author_id": permalink_fallback.get("author_id"),
        "native_permalink_composite_story_id": permalink_fallback.get("composite_story_id"),
        "native_permalink_fallback_start_line": permalink_fallback.get("fallback_start_line"),
        "native_permalink_family_capture_start_line": permalink_fallback.get("family_capture_start_line"),
        "native_permalink_target_open_method": permalink_fallback.get("target_open_method"),
        "native_permalink_target_open_verified": bool(permalink_fallback.get("target_open_verified")),
        "native_permalink_target_open_exit_code": permalink_fallback.get("target_open_exit_code"),
        "native_permalink_target_open_diagnostics": permalink_fallback.get("target_open_diagnostics"),
        "native_permalink_composite_deeplink_attempted": bool(permalink_fallback.get("composite_deeplink_attempted")),
        "native_permalink_explorer_exit_code": permalink_fallback.get("explorer_exit_code"),
        "native_permalink_target_validation_status": permalink_target_validation_status,
        "native_permalink_reaction_target_validation_status": reaction_target_validation_status,
        "native_permalink_share_target_validation_status": share_target_validation_status,
        "native_permalink_observed_reaction_source_post_ids": permalink_fallback.get("observed_reaction_source_post_ids") or [],
        "native_permalink_observed_share_source_post_ids": permalink_fallback.get("observed_share_source_post_ids") or [],
        "native_permalink_runtime_target_guard": fallback_target_guard,
        "native_permalink_runtime_target_guard_events": fallback_target_guard_events,
        "native_permalink_unavailable_reason": permalink_fallback.get("unavailable_reason"),
        "native_permalink_error": permalink_fallback.get("error"),
        "reaction_permalink_fallback_recovered": reaction_permalink_recovered,
        "share_permalink_fallback_recovered": share_permalink_recovered,
        "share_status": share_enum.get("status"),
        # Analyzer classification is the final result because it can combine
        # UI total count with terminal pagination (for example turning the
        # immediate UI screening result "enumerable" into the more precise
        # "visible-subset-terminal"). Diagnostics remain a fallback for runs
        # that have not been analyzed yet.
        "share_screening_classification": share_enum.get("classification") or share_screening.get("classification"),
        "share_decision_reason": share_enum.get("decision_reason") or share_screening.get("decision_reason"),
        "share_list_confirmed": bool(
            share_screening.get("list_confirmed")
            if "list_confirmed" in share_screening
            else share_enum.get("list_confirmed")
        ),
        "share_entry_method": share_screening.get("entry_method") or share_enum.get("entry_method") or share_action.get("entry_method"),
        "share_query_records": final_share_query_records,
        "share_page_count": share_progress.get("page_count", len(report.get("share_pages") or [])),
        "share_user_count": final_share_user_count,
        "share_identity_gap": share_enum.get("identity_count_gap"),
        "share_terminal_page_seen": share_progress.get("terminal_page_seen", share_enum.get("terminal_page_seen")),
        "share_entry_scan_scrolls": share_action.get("entry_scan_scrolls"),
        "share_native_fallback_attempted": bool(share_native_fallback.get("attempted")),
        "share_native_fallback_succeeded": bool(share_native_fallback.get("succeeded")),
        "share_native_fallback_method": share_native_fallback.get("method"),
        "share_native_fallback_attempts": int(share_native_fallback.get("attempts") or 0),
        "transport_repaired": metadata.get("transport_repaired"),
        "transport_health": metadata.get("transport_health"),
    }


def collect_run_dirs(args):
    run_dirs = [Path(value) for value in (args.runs or [])]
    if args.runs_root:
        root = Path(args.runs_root)
        if root.exists():
            run_dirs.extend(path for path in sorted(root.iterdir()) if path.is_dir())
    unique = []
    seen = set()
    for path in run_dirs:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def write_csv(path, rows):
    fields = [
        "run_dir", "batch_id", "candidate_index", "feed_scroll_offset", "started_at", "finished_at", "error",
        "capture_attempts", "capture_retried", "source_post_id", "author_name", "post_url", "message_excerpt", "ui_reaction_count", "ui_share_count",
        "reaction_status", "reaction_visibility_hint", "friends_only", "screening_classification", "decision_reason",
        "screen_probe_attempts", "deep_capture_requested", "deep_capture_completed", "deep_completion_status", "deep_completion_reason", "reaction_query_records", "reaction_page_count",
        "reaction_user_count", "reaction_open_groups", "reaction_terminal_groups", "all_observed_groups_terminal",
        "pagination_stop_reasons", "reaction_entry_scan_scrolls", "share_status", "share_screening_classification",
        "reaction_native_fallback_attempted", "reaction_native_fallback_succeeded", "reaction_native_fallback_method", "reaction_list_ready_attempts",
        "native_permalink_fallback_attempted", "native_permalink_reaction_eligible", "native_permalink_share_eligible",
        "native_permalink_source", "native_permalink_post_id", "native_permalink_author_id", "native_permalink_composite_story_id",
        "native_permalink_fallback_start_line", "native_permalink_family_capture_start_line",
        "native_permalink_target_open_method", "native_permalink_target_open_verified", "native_permalink_target_open_exit_code",
        "native_permalink_target_open_diagnostics", "native_permalink_composite_deeplink_attempted",
        "native_permalink_explorer_exit_code", "native_permalink_target_validation_status",
        "native_permalink_reaction_target_validation_status", "native_permalink_share_target_validation_status",
        "native_permalink_observed_reaction_source_post_ids", "native_permalink_observed_share_source_post_ids",
        "native_permalink_runtime_target_guard", "native_permalink_runtime_target_guard_events",
        "native_permalink_unavailable_reason", "native_permalink_error",
        "reaction_permalink_fallback_recovered", "share_permalink_fallback_recovered",
        "share_decision_reason", "share_list_confirmed", "share_entry_method", "share_query_records",
        "pagination_strategy_attempts", "pagination_strategy_growth", "pagination_budget_extensions",
        "pagination_max_final_budget", "pagination_absolute_cap",
        "share_page_count", "share_user_count", "share_identity_gap", "share_terminal_page_seen", "share_entry_scan_scrolls",
        "share_native_fallback_attempted", "share_native_fallback_succeeded", "share_native_fallback_method", "share_native_fallback_attempts",
        "transport_repaired", "transport_health",
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            for key, value in list(serialized.items()):
                if isinstance(value, (list, dict)):
                    serialized[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            writer.writerow(serialized)


def parse_args():
    parser = argparse.ArgumentParser(description="Aggregate Facebook capture run summaries without replaying requests.")
    parser.add_argument("--runs", nargs="*")
    parser.add_argument("--runs-root")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-csv", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    rows = [summarize_run(path) for path in collect_run_dirs(args)]
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"runs": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(args.out_csv, rows)
    print(json.dumps({"runs": len(rows), "out_json": str(out_json), "out_csv": str(args.out_csv)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
