from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from mitmproxy import http

import facebook_capture_addon_safe as capture_addon
import facebook_capture_api as api
import facebook_release_gate as release_gate
from analyze_facebook_capture import iter_records
from facebook_batch_summary import summarize_run
from facebook_capture_progress import summarize_reaction_records

BASE = Path(__file__).resolve().parent
TARGET_POST_ID = "122106880826437311"
FULL_RUNS = {
    "20260818-192150": {"min_users": 3000, "expected_status": "terminal-complete"},
    "20260818-225729": {"min_users": 3000, "expected_status": "terminal-complete"},
}
NEGATIVE_RUNS = {
    "20260818-155010": "partial-open-tail",
    "20260819-014815": "rate-limited",
}
RATE_LIMIT_REGRESSION_RUN = "20260819-122854"
RATE_LIMIT_REGRESSION_MIN_LINE = 68


def check(name: str, passed: bool, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def preferred_analysis(run_dir: Path) -> Path:
    audit = run_dir / "analysis-audit"
    normal = run_dir / "analysis"
    if (audit / "facebook-reactor-users.csv").exists():
        return audit
    return normal


def field_completeness(run_dir: Path) -> dict[str, Any]:
    analysis = preferred_analysis(run_dir)
    rows = read_csv(analysis / "facebook-reactor-users.csv")
    aliases = {
        "id": "user_id",
        "name": "user_name",
        "avatar": "avatar_url",
        "source_post_id": "source_post_id",
    }
    counts = {
        label: sum(1 for row in rows if str(row.get(field) or "").strip())
        for label, field in aliases.items()
    }
    return {"rows": len(rows), "counts": counts, "all_required_complete": bool(rows) and all(v == len(rows) for v in counts.values())}


def api_e2e(run_dir: Path) -> dict[str, Any]:
    # build_api_result consumes the deployed API result contract and performs no network I/O.
    result = api.build_api_result(
        run_dir,
        {"post_url": "", "post_id": TARGET_POST_ID, "collect": ["reactions"]},
    )
    json.dumps(result, ensure_ascii=False)
    completeness = (result.get("completeness") or {}).get("reactions") or {}
    return {
        "reaction_users": len(result.get("reaction_users") or []),
        "completion_claim_allowed": bool(completeness.get("completion_claim_allowed")),
        "integrity_status": completeness.get("integrity_status"),
        "terminal_page_seen": bool(completeness.get("terminal_page_seen")),
    }


def validate() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    historical: dict[str, Any] = {}
    required_files = [
        "facebook_runtime.ps1",
        "run_facebook_capture.ps1",
        "run_facebook_batch.ps1",
        "run_facebook_post_queue.ps1",
        "explore_facebook_readonly.ps1",
        "open_facebook_post_readonly.ps1",
        "facebook_capture_addon_safe.py",
        "facebook_capture_progress.py",
        "probe_facebook_reaction_health.ps1",
        "facebook_release_gate.py",
        "facebook_release_report.py",
        "run_facebook_release_gate.ps1",
        "analyze_facebook_capture.py",
        "facebook_capture_api.py",
        "facebook_batch_summary.py",
        "facebook_batch_pressure.py",
        "start_facebook_capture_api.ps1",
    ]
    missing = [name for name in required_files if not (BASE / name).exists()]
    checks.append(check("core-files", not missing, {"missing": missing}))

    probe_text = (BASE / "probe_facebook_reaction_health.ps1").read_text(encoding="utf-8-sig")
    run_text = (BASE / "run_facebook_capture.ps1").read_text(encoding="utf-8-sig")
    batch_text = (BASE / "run_facebook_batch.ps1").read_text(encoding="utf-8-sig")
    queue_text = (BASE / "run_facebook_post_queue.ps1").read_text(encoding="utf-8-sig")
    runtime_text = (BASE / "facebook_runtime.ps1").read_text(encoding="utf-8-sig")
    pressure_text = (BASE / "facebook_batch_pressure.py").read_text(encoding="utf-8-sig")
    explore_text = (BASE / "explore_facebook_readonly.ps1").read_text(encoding="utf-8-sig")
    open_text = (BASE / "open_facebook_post_readonly.ps1").read_text(encoding="utf-8-sig")
    start_api_text = (BASE / "start_facebook_capture_api.ps1").read_text(encoding="utf-8-sig")
    probe_contract = {
        "wrapper_probe_only": "'-ReactionProbeOnly'" in probe_text,
        "wrapper_feed_scroll_zero": "'-ScrollCount', '0'" in probe_text,
        "wrapper_screen_scroll_zero": "'-ReactionScreenScrollCount', '0'" in probe_text,
        "wrapper_reports_result": "[probe-result]" in probe_text,
        "wrapper_detects_page2": "FeedbackReactorsGraphService_At_Connection_Pagination" in probe_text,
        "wrapper_fails_on_pagination": "$probeExitCode = 2" in probe_text,
        "run_forwards_probe_only": 'if ($ReactionProbeOnly) { $readonlyArgs += "-ReactionProbeOnly" }' in run_text,
        "explorer_skips_category_tab": "$screenTab = if ($ReactionProbeOnly)" in explore_text,
        "explorer_skips_screen_swipe": "$screenProbeScrollLimit = if ($ReactionProbeOnly) { 0 }" in explore_text,
        "explorer_never_requests_deep": "deep_capture_requested = [bool]($deepCaptureAllowed -and -not $ReactionProbeOnly)" in explore_text,
    }
    checks.append(check("reaction-probe-only-contract", all(probe_contract.values()), probe_contract))

    release_text = (BASE / "run_facebook_release_gate.ps1").read_text(encoding="utf-8-sig")
    deterministic_powershell_contract = {
        "capture_resolves_system_powershell": "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in run_text and "& powershell" not in run_text,
        "batch_resolves_system_powershell": "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in batch_text and "& powershell" not in batch_text,
        "probe_resolves_system_powershell": "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in probe_text and "& powershell" not in probe_text,
        "release_resolves_system_powershell": "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in release_text and "& powershell" not in release_text,
    }
    checks.append(check("deterministic-powershell-entrypoints", all(deterministic_powershell_contract.values()), deterministic_powershell_contract))

    deterministic_python_contract = {
        "capture_resolves_system_py": "Join-Path $env:SystemRoot 'py.exe'" in run_text and "& python" not in run_text and "& py" not in run_text,
        "batch_resolves_system_py": "Join-Path $env:SystemRoot 'py.exe'" in batch_text and "& python" not in batch_text and "& py" not in batch_text,
        "explore_resolves_shared_python": "Resolve-FacebookPythonExe -BasePath $Base" in explore_text and "& python" not in explore_text and "& py" not in explore_text,
        "target_open_resolves_shared_python": "Resolve-FacebookPythonExe -BasePath $Base" in open_text and "& python" not in open_text and "& py" not in open_text,
        "api_start_resolves_system_py": "Join-Path $env:SystemRoot 'py.exe'" in start_api_text and "& python" not in start_api_text and "& py" not in start_api_text,
        "release_resolves_shared_python": "Resolve-FacebookPythonExe -BasePath $Base" in release_text and "& python" not in release_text and "& py" not in release_text,
        "queue_resolves_shared_python": "Resolve-FacebookPythonExe -BasePath $Base" in queue_text and "& python" not in queue_text and "& py" not in queue_text,
    }
    checks.append(check("deterministic-python-entrypoints", all(deterministic_python_contract.values()), deterministic_python_contract))

    batch_queue_contract = {
        "shared_live_lock_defined": "facebook-live-capture.lock" in runtime_text and "Acquire-FacebookLiveCaptureLock" in runtime_text and "Release-FacebookLiveCaptureLock" in runtime_text,
        "release_uses_shared_live_lock": "-Owner 'release-gate'" in release_text,
        "batch_uses_shared_live_lock": "-Owner 'batch'" in batch_text,
        "queue_uses_shared_live_lock": "-Owner 'post-queue'" in queue_text,
        "batch_pressure_controller_enabled": "facebook_batch_pressure.py" in batch_text and "remaining live candidates paused" in batch_text,
        "pressure_blocks_known_platform_codes": "1675004" in pressure_text and "3252001" in pressure_text and "368" in pressure_text,
        "queue_fresh_probe_before_full": queue_text.index("Invoke-QueueCapture -Kind 'probe'") < queue_text.index("Invoke-QueueCapture -Kind 'full'"),
        "queue_skips_terminal_checkpoint": "skip terminal checkpoint" in queue_text and "$task.status -eq 'terminal-complete'" in queue_text,
        "queue_terminal_only_checkpoint": "if (-not [bool]$full.passed)" in queue_text and "$task.status = 'terminal-complete'" in queue_text,
        "queue_persists_resume_state": "Save-QueueState" in queue_text and "terminal_run_dir" in queue_text and "blocked-live" in queue_text,
    }
    checks.append(check("batch-pressure-post-queue-contract", all(batch_queue_contract.values()), batch_queue_contract))

    mitmdump_contract = {
        "runtime_python_discovery": "Resolve-FacebookPythonExe -BasePath $Base" in run_text,
        "runtime_scripts_candidate": "Scripts\\mitmdump.exe" in run_text,
        "path_fallback_only": "$mitmdumpCommand = Get-Command mitmdump.exe" in run_text,
        "resolved_executable_started": "Start-Process -FilePath $MitmdumpExe" in run_text,
        "legacy_path_only_guard_removed": 'throw "mitmdump is not on PATH"' not in run_text,
    }
    checks.append(check("deterministic-mitmdump-entrypoint", all(mitmdump_contract.values()), mitmdump_contract))

    blocked_probe = release_gate.inspect_probe(BASE / "runs" / "20260819-130426", TARGET_POST_ID)
    blocked_page2 = release_gate.inspect_page2(BASE / "runs" / RATE_LIMIT_REGRESSION_RUN, TARGET_POST_ID)
    full_release = release_gate.inspect_full(BASE / "runs" / "20260818-225729", 3000)
    share_release = release_gate.inspect_share(BASE / "runs" / "20260817-121052")
    release_contract = {
        "probe_before_page2": release_text.index("$probe = Invoke-ProbeStage 'probe'") < release_text.index("$page2Run = Invoke-Capture 'page2'"),
        "page2_before_full": release_text.index("$page2Run = Invoke-Capture 'page2'") < release_text.index("$adb = Invoke-FullReactionStage 'full-adb' 0"),
        "page2_burst_one": "'-ReactionPipelineBurstSize', '1'" in release_text,
        "page2_native_off": "'-ReactionNativeScrollEnabled', '0'" in release_text,
        "post_heavy_probe": "Invoke-ProbeStage 'post-adb-probe'" in release_text,
        "pre_share_probe": "Invoke-ProbeStage 'pre-share-probe'" in release_text,
        "resume_is_bounded": "[int]$ResumeMaxAgeHours = 24" in release_text and "$ageHours -gt $ResumeMaxAgeHours" in release_text,
        "resume_uses_persistent_checkpoint_file": "facebook-release-gate-checkpoints.json" in release_text and "function Save-CheckpointState" in release_text,
        "resume_preserves_original_checkpoint_age": "$sourceTimestamp = [string]$priorEntry.timestamp" in release_text and "Set-PersistentCheckpoint $StageName $rechecked $priorRunDir $sourceTimestamp $sourceGateId" in release_text,
        "resume_revalidates_terminal": "$rechecked = Invoke-Inspector 'full' $priorRunDir $MinFullReactionUsers" in release_text,
        "resume_happens_after_fresh_page2": release_text.index("$page2Run = Invoke-Capture 'page2'") < release_text.index("$adb = Invoke-FullReactionStage 'full-adb' 0"),
        "share_checkpoint_after_fresh_probe": release_text.index("$preShareProbe = Invoke-ProbeStage 'pre-share-probe'") < release_text.index("$share = Invoke-ShareStage") and "$rechecked = Invoke-Inspector 'share' $priorRunDir" in release_text,
        "final_report_required": "$FinalReportScript = Join-Path $Base 'facebook_release_report.py'" in release_text and "Stop-Gate 'final-report'" in release_text,
        "final_report_outputs": "facebook-release-final-report.json" in release_text and "facebook-release-final-report.md" in release_text,
        "exclusive_gate_lock": "facebook-release-gate.lock" in release_text and "[IO.FileShare]::None" in release_text and "exit 60" in release_text,
        "gate_lock_released": "Release-GateLock\n    exit $ExitCode" in release_text and "Release-GateLock\nexit 0" in release_text,
        "unhandled_errors_persist_and_release_lock": "trap {" in release_text and "$state.stop_reason = \"unhandled-error: $unhandledMessage\"" in release_text and "Release-GateLock\n    exit 70" in release_text,
        "ab_requires_native_success": "$nativeControllerUsed = ([int]$native.native_scroll_success -gt 0)" in release_text,
        "ab_requires_fallback_free_native_for_switch": "$nativeFallbackFree = ([int]$native.native_scroll_fallbacks -eq 0)" in release_text,
        "ab_native_win_threshold": "[double]$NativeRecommendationMinSpeedupPercent = 5.0" in release_text and "$nativeHasMeaningfulWin" in release_text,
        "ab_records_user_throughput": "adb_users_per_minute" in release_text and "native_users_per_minute" in release_text,
        "ab_records_recommendation": "Set-StageResult 'ab-comparison'" in release_text and "recommended_mode" in release_text and "recommendation_reason" in release_text,
        "blocked_probe_rejected": not bool(blocked_probe.get("passed")) and blocked_probe.get("reason") == "blocked-session-restriction",
        "source_less_rate_limit_rejected": not bool(blocked_page2.get("passed")) and bool(blocked_page2.get("latest_rate_limited")),
        "historical_3000plus_accepted": bool(full_release.get("passed")) and int(full_release.get("reaction_user_count") or 0) >= 3000,
        "historical_share_terminal_accepted": bool(share_release.get("passed")) and bool(share_release.get("share_terminal_page_seen")),
    }
    checks.append(check("release-gate-contract", all(release_contract.values()), release_contract))

    request = {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]}
    command = api.build_capture_command(request, run_dir=BASE / "runs" / "api-predeploy-contract")
    expected_zero = ["-ReactionAbsoluteCap", "-MaxFlows", "-DurationSeconds", "-ScrollCount"]
    command_ok = all(flag in command and command[command.index(flag) + 1] == "0" for flag in expected_zero)
    command_ok = command_ok and "-OpenReactionList" in command and "-AutoScreenThenDeep" in command and "-OutputDir" in command
    command_ok = command_ok and "-ReactionNoGrowthUiDumpEveryN" in command and command[command.index("-ReactionNoGrowthUiDumpEveryN") + 1] == "1"
    checks.append(check("api-automatic-unlimited-command", command_ok, {flag: command[command.index(flag) + 1] if flag in command else None for flag in expected_zero}))

    for run_id, spec in FULL_RUNS.items():
        run_dir = BASE / "runs" / run_id
        summary = summarize_run(run_dir)
        fields = field_completeness(run_dir)
        api_result = api_e2e(run_dir)
        passed = (
            summary.get("deep_completion_status") == spec["expected_status"]
            and bool(summary.get("deep_capture_completed"))
            and int(summary.get("reaction_user_count") or 0) >= spec["min_users"]
            and fields["all_required_complete"]
            and api_result["reaction_users"] >= spec["min_users"]
            and api_result["completion_claim_allowed"]
            and api_result["terminal_page_seen"]
        )
        historical[run_id] = {
            "summary": {
                "reaction_user_count": summary.get("reaction_user_count"),
                "reaction_page_count": summary.get("reaction_page_count"),
                "deep_completion_status": summary.get("deep_completion_status"),
                "deep_completion_reason": summary.get("deep_completion_reason"),
            },
            "fields": fields,
            "api_e2e": api_result,
        }
        checks.append(check(f"historical-full-{run_id}", passed, historical[run_id]))

    for run_id, expected_status in NEGATIVE_RUNS.items():
        run_dir = BASE / "runs" / run_id
        summary = summarize_run(run_dir)
        passed = summary.get("deep_completion_status") == expected_status and not bool(summary.get("deep_capture_completed"))
        historical[run_id] = {
            "reaction_user_count": summary.get("reaction_user_count"),
            "reaction_page_count": summary.get("reaction_page_count"),
            "deep_completion_status": summary.get("deep_completion_status"),
            "deep_completion_reason": summary.get("deep_completion_reason"),
        }
        checks.append(check(f"historical-negative-{run_id}", passed, historical[run_id]))

    rate_run_dir = BASE / "runs" / RATE_LIMIT_REGRESSION_RUN
    rate_capture = rate_run_dir / "safe-facebook-http-capture.jsonl"
    rate_records, rate_parse_errors = iter_records(rate_capture, 10000, min_line=RATE_LIMIT_REGRESSION_MIN_LINE)
    rate_summary = summarize_reaction_records(rate_records, TARGET_POST_ID)
    rate_limit_regression = {
        "reaction_user_count": rate_summary.get("user_count"),
        "reaction_page_count": rate_summary.get("page_count"),
        "latest_rate_limited": rate_summary.get("latest_rate_limited"),
        "latest_graphql_error_code": rate_summary.get("latest_graphql_error_code"),
        "latest_rate_limit_line": rate_summary.get("latest_rate_limit_line"),
        "parse_errors": rate_parse_errors,
    }
    rate_limit_passed = (
        rate_parse_errors == 0
        and int(rate_summary.get("user_count") or 0) == 33
        and int(rate_summary.get("page_count") or 0) == 2
        and bool(rate_summary.get("latest_rate_limited"))
        and int(rate_summary.get("latest_graphql_error_code") or 0) == 1675004
        and int(rate_summary.get("latest_rate_limit_line") or 0) == 70
    )
    historical.setdefault(RATE_LIMIT_REGRESSION_RUN, {})["source_less_rate_limit"] = rate_limit_regression
    checks.append(check("historical-source-less-rate-limit-20260819-122854", rate_limit_passed, rate_limit_regression))

    cursor_friendly = "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection"
    cursor_value = "predeploy-cursor-not-persisted"
    cursor_context = {"request_feedback_post_id": TARGET_POST_ID, "request_reaction_id": "all"}
    capture_addon._reaction_cursor_response_probe(
        {"friendly_name": cursor_friendly, **cursor_context},
        json.dumps(
            {
                "data": {
                    "node": {
                        "reactors": {
                            "edges": [],
                            "page_info": {"has_next_page": True, "end_cursor": cursor_value},
                        }
                    }
                }
            }
        ),
    )
    cursor_body = urlencode(
        {
            "variables": json.dumps(
                {"feedback_id": TARGET_POST_ID, "reaction_id": "all", "after": cursor_value}
            )
        }
    ).encode("utf-8")
    cursor_request = http.Request.make(
        "POST",
        "https://graph.facebook.com/graphql",
        cursor_body,
        {"content-encoding": "gzip", "content-type": "application/x-www-form-urlencoded"},
    )
    decoded_cursor_body = capture_addon._request_body_text(cursor_request)
    decoded_cursor_context = capture_addon._public_request_context_from_body(decoded_cursor_body)
    cursor_probe = capture_addon._reaction_cursor_request_probe(
        cursor_friendly, decoded_cursor_context, decoded_cursor_body
    )
    cursor_detail = {
        "raw_gzip_magic": bool((cursor_request.raw_content or b"").startswith(b"\x1f\x8b")),
        "decoded_form": decoded_cursor_body.startswith("variables="),
        "cursor_present": cursor_probe.get("request_cursor_present"),
        "previous_end_available": cursor_probe.get("previous_end_cursor_available"),
        "cursor_matches_previous_end": cursor_probe.get("request_cursor_matches_previous_end"),
        "raw_cursor_persisted": cursor_value in json.dumps(cursor_probe, sort_keys=True),
    }
    cursor_passed = (
        cursor_detail["raw_gzip_magic"]
        and cursor_detail["decoded_form"]
        and cursor_detail["cursor_present"]
        and cursor_detail["previous_end_available"]
        and cursor_detail["cursor_matches_previous_end"]
        and not cursor_detail["raw_cursor_persisted"]
    )
    checks.append(check("gzip-reaction-cursor-probe", cursor_passed, cursor_detail))

    required_api_fields = {"id", "name", "avatar_url", "reaction_id", "source_field", "source_post_id"}
    sample_api = api.build_api_result(BASE / "runs" / "20260818-192150", {"post_url": "", "post_id": TARGET_POST_ID, "collect": ["reactions"]})
    first_user = (sample_api.get("reaction_users") or [{}])[0]
    checks.append(check("api-reaction-user-schema", required_api_fields.issubset(first_user.keys()), sorted(first_user.keys())))

    blocked_live_checks = [
        {
            "name": "post-optimization-3000plus-live-speed-regression",
            "status": "BLOCKED_PLATFORM",
            "reason": "Current Facebook session produced GraphQL 1675004 rate limiting and separate temporary feature restriction 368; no additional live pressure is generated by predeploy validation.",
        },
        {
            "name": "direct-cursor-page2-plus-live-validation",
            "status": "BLOCKED_PLATFORM",
            "reason": "Cursor equality is verified, but the current server/session state returns 1675004 on connection pagination.",
        },
    ]
    overall = "PASS_OFFLINE_BLOCKED_LIVE" if all(item["status"] == "PASS" for item in checks) else "FAIL"
    return {
        "overall_status": overall,
        "checks": checks,
        "historical_runs": historical,
        "blocked_live_checks": blocked_live_checks,
        "notes": {
            "responsible_person_acceptance": [
                "automatic reaction collection without continuous human scrolling",
                "1000+ deep pagination must not stop at a fixed small cap",
                "return user name/id/avatar/source post and reaction id when present",
                "only terminal ALL pagination may be claimed complete",
            ],
            "safety": "No CAPTCHA/access-control/rate-limit bypass, account rotation, IP rotation, fingerprint spoofing, or token forgery is used by this validator.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Facebook capture deployment preflight")
    parser.add_argument("--out", default="predeploy-validation.json")
    args = parser.parse_args()
    report = validate()
    out = Path(args.out)
    if not out.is_absolute():
        out = BASE / out
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"overall_status": report["overall_status"], "out": str(out), "checks": len(report["checks"])}, ensure_ascii=False))
    return 0 if report["overall_status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
