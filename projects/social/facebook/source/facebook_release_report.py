from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def stage_result(state: dict[str, Any], name: str) -> dict[str, Any]:
    stage = (state.get("stages") or {}).get(name) or {}
    result = stage.get("result") or {}
    return result if isinstance(result, dict) else {}


def seconds(ms: Any) -> float | None:
    try:
        value = float(ms)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return round(value / 1000.0, 2)


def build_report(state: dict[str, Any], checkpoints: dict[str, Any], predeploy: dict[str, Any]) -> dict[str, Any]:
    probe = stage_result(state, "probe")
    page2 = stage_result(state, "page2")
    adb = stage_result(state, "full-adb")
    native = stage_result(state, "full-native")
    ab = stage_result(state, "ab-comparison")
    share = stage_result(state, "share")
    predeploy_stage = stage_result(state, "predeploy")

    status = str(state.get("status") or "unknown")
    recommended_mode = ab.get("recommended_mode") or ("adb" if adb.get("passed") else "unproven")
    report = {
        "gate_id": state.get("gate_id"),
        "status": status,
        "post_id": state.get("post_id"),
        "post_url": state.get("post_url"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "stop_reason": state.get("stop_reason") or "",
        "checkpoint_count": len((checkpoints.get("stages") or {})),
        "probe": {
            "passed": bool(probe.get("passed")),
            "status": probe.get("probe_status"),
            "users": int(probe.get("user_count") or 0),
            "queries": int(probe.get("query_records") or 0),
            "page2_requests": int(probe.get("page2_requests") or 0),
            "session_restricted": bool(probe.get("session_restricted")),
        },
        "page2": {
            "passed": bool(page2.get("passed")),
            "requests": int(page2.get("page2_requests") or 0),
            "cursor_present": bool(page2.get("cursor_present")),
            "previous_end_cursor_available": bool(page2.get("previous_end_cursor_available")),
            "cursor_matches_previous_end": bool(page2.get("cursor_matches_previous_end")),
            "source_match": bool(page2.get("source_match")),
            "users": int(page2.get("reaction_user_count") or 0),
            "pages": int(page2.get("reaction_page_count") or 0),
        },
        "reaction": {
            "adb": {
                "passed": bool(adb.get("passed")),
                "users": int(adb.get("reaction_user_count") or 0),
                "pages": int(adb.get("reaction_page_count") or 0),
                "terminal": adb.get("deep_completion_status") == "terminal-complete",
                "elapsed_seconds": seconds(adb.get("elapsed_ms")),
                "growths_per_minute": adb.get("growths_per_minute"),
                "swipes_per_growth": adb.get("effective_swipes_per_growth"),
                "reused_checkpoint": bool(adb.get("reused_prior_stage")),
            },
            "native": {
                "passed": bool(native.get("passed")),
                "users": int(native.get("reaction_user_count") or 0),
                "pages": int(native.get("reaction_page_count") or 0),
                "terminal": native.get("deep_completion_status") == "terminal-complete",
                "elapsed_seconds": seconds(native.get("elapsed_ms")),
                "growths_per_minute": native.get("growths_per_minute"),
                "swipes_per_growth": native.get("effective_swipes_per_growth"),
                "native_scroll_success": int(native.get("native_scroll_success") or 0),
                "native_scroll_fallbacks": int(native.get("native_scroll_fallbacks") or 0),
                "reused_checkpoint": bool(native.get("reused_prior_stage")),
            },
            "comparison": {
                "passed": bool(ab.get("passed")),
                "recommended_mode": recommended_mode,
                "recommendation_reason": ab.get("recommendation_reason"),
                "native_speedup_percent": ab.get("native_speedup_percent"),
                "adb_users_per_minute": ab.get("adb_users_per_minute"),
                "native_users_per_minute": ab.get("native_users_per_minute"),
            },
        },
        "share": {
            "passed": bool(share.get("passed")),
            "classification": share.get("classification"),
            "users": int(share.get("share_user_count") or 0),
            "pages": int(share.get("share_page_count") or 0),
            "terminal": bool(share.get("share_terminal_page_seen")),
            "identity_gap": share.get("share_identity_gap"),
            "reused_checkpoint": bool(share.get("reused_prior_stage")),
        },
        "predeploy": {
            "passed": bool(predeploy_stage.get("passed")),
            "overall_status": predeploy_stage.get("overall_status") or predeploy.get("overall_status"),
            "checks": len(predeploy.get("checks") or []),
        },
    }
    require_native = bool(state.get("run_native_ab", True))
    require_share = bool(state.get("run_share_regression", True))
    report["requirements"] = {
        "native_ab": require_native,
        "share_regression": require_share,
    }
    report["passed"] = (
        status == "passed"
        and bool(report["probe"]["passed"])
        and bool(report["page2"]["passed"])
        and bool(report["reaction"]["adb"]["passed"])
        and bool(report["predeploy"]["passed"])
        and (not require_native or bool(report["reaction"]["native"]["passed"]))
        and (not require_share or bool(report["share"]["passed"]))
    )
    return report


def markdown(report: dict[str, Any]) -> str:
    reaction = report["reaction"]
    adb = reaction["adb"]
    native = reaction["native"]
    comparison = reaction["comparison"]
    share = report["share"]
    page2 = report["page2"]
    lines = [
        "# Facebook Release Gate Final Report",
        "",
        f"- Gate: `{report.get('gate_id')}`",
        f"- Status: `{report.get('status')}`",
        f"- Final pass: `{report.get('passed')}`",
        f"- Post ID: `{report.get('post_id')}`",
        "",
        "## Page2 / Cursor",
        "",
        f"- Passed: `{page2.get('passed')}`",
        f"- Requests: `{page2.get('requests')}`",
        f"- Cursor continuity: `{page2.get('cursor_present') and page2.get('previous_end_cursor_available') and page2.get('cursor_matches_previous_end')}`",
        f"- Source match: `{page2.get('source_match')}`",
        "",
        "## Reaction terminal / performance",
        "",
        f"- ADB: users `{adb.get('users')}`, pages `{adb.get('pages')}`, terminal `{adb.get('terminal')}`, elapsed `{adb.get('elapsed_seconds')}` s",
        f"- Native: users `{native.get('users')}`, pages `{native.get('pages')}`, terminal `{native.get('terminal')}`, elapsed `{native.get('elapsed_seconds')}` s",
        f"- Recommended mode: `{comparison.get('recommended_mode')}`",
        f"- Reason: `{comparison.get('recommendation_reason')}`",
        f"- Native speedup: `{comparison.get('native_speedup_percent')}`%",
        "",
        "## Share",
        "",
        f"- Passed: `{share.get('passed')}`",
        f"- Classification: `{share.get('classification')}`",
        f"- Visible users: `{share.get('users')}` / pages `{share.get('pages')}` / terminal `{share.get('terminal')}`",
        f"- Identity gap: `{share.get('identity_gap')}`",
        "",
        "## Predeploy",
        "",
        f"- Passed: `{report['predeploy'].get('passed')}`",
        f"- Status: `{report['predeploy'].get('overall_status')}`",
        f"- Checks: `{report['predeploy'].get('checks')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the final human/machine Facebook release-gate report.")
    parser.add_argument("--state", required=True)
    parser.add_argument("--checkpoints", required=True)
    parser.add_argument("--predeploy", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    state = load_json(Path(args.state))
    checkpoints = load_json(Path(args.checkpoints))
    predeploy = load_json(Path(args.predeploy))
    report = build_report(state, checkpoints, predeploy)
    Path(args.out_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.out_md).write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"passed": report.get("passed"), "out_json": args.out_json, "out_md": args.out_md}, ensure_ascii=False))
    return 0 if report.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
