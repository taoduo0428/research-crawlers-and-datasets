import json
import tempfile
import unittest
from pathlib import Path

import facebook_release_gate as gate

ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = (ROOT / "run_facebook_release_gate.ps1").read_text(encoding="utf-8-sig")
PREDEPLOY = (ROOT / "validate_facebook_predeploy.py").read_text(encoding="utf-8-sig")
TARGET_POST_ID = "122106880826437311"


class FacebookReleaseGateTests(unittest.TestCase):
    def write_json(self, path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_blocked_probe_fixture_stops_without_page2(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self.write_json(
                run_dir / "readonly-ui" / "exploration-diagnostics.json",
                {
                    "reaction_screening": {
                        "probe_only": True,
                        "probe_status": "session-restricted",
                        "query_records": 1,
                        "user_count": 0,
                        "session_restricted": True,
                        "session_restriction_friendly_name": "FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPFullScreenSEScreenQuery",
                        "deep_capture_requested": False,
                    },
                    "reaction_preopened_source_proof": {"status": "matched"},
                    "pagination_runs": [],
                },
            )
            result = gate.inspect_probe(run_dir, TARGET_POST_ID)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "blocked-session-restriction")
        self.assertTrue(result["session_restricted"])
        self.assertEqual(result["page2_requests"], 0)
        self.assertEqual(result["pagination_runs"], 0)
        self.assertEqual(
            result["session_restriction_friendly_name"],
            "FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPFullScreenSEScreenQuery",
        )

    def test_source_less_page2_rate_limit_fixture_never_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "safe-facebook-http-capture.jsonl").write_text(
                json.dumps(
                    {
                        "friendly_name": "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection",
                        "request_cursor_present": True,
                        "previous_end_cursor_available": True,
                        "request_cursor_matches_previous_end": True,
                        "response_rate_limited": True,
                        "response_graphql_error_code": 1675004,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = gate.inspect_page2(run_dir, TARGET_POST_ID)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "blocked-rate-limit-or-restriction")
        self.assertGreaterEqual(result["page2_requests"], 1)
        self.assertTrue(result["latest_rate_limited"])
        self.assertEqual(result["latest_graphql_error_code"], 1675004)

    def make_terminal_full_fixture(self, run_dir: Path) -> None:
        self.write_json(
            run_dir / "analysis" / "facebook-sample-patterns.json",
            {"enumeration_status": {"reactions": {"enumerated_identity_count": 3001}}},
        )
        self.write_json(
            run_dir / "readonly-ui" / "exploration-diagnostics.json",
            {
                "reaction_screening": {"deep_capture_requested": True},
                "pagination_runs": [
                    {
                        "reaction_key": "__ALL__",
                        "terminal_seen": True,
                        "stop_reason": "terminal",
                        "elapsed_ms": 120000,
                        "growths_per_minute": 10.0,
                        "effective_swipes_per_growth": 1.5,
                        "native_scroll_success": 3,
                    }
                ],
            },
        )

    def test_terminal_3000plus_fixture_passes_full_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self.make_terminal_full_fixture(run_dir)
            result = gate.inspect_full(run_dir, 3000)
        self.assertTrue(result["passed"])
        self.assertEqual(result["reason"], "terminal-complete")
        self.assertGreaterEqual(result["reaction_user_count"], 3000)
        self.assertTrue(result["deep_capture_completed"])
        self.assertEqual(result["deep_completion_status"], "terminal-complete")
        self.assertIn("elapsed_ms", result)
        self.assertIn("growths_per_minute", result)
        self.assertIn("effective_swipes_per_growth", result)
        self.assertIn("native_scroll_success", result)

    def test_full_gate_rejects_unrealistic_minimum(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self.make_terminal_full_fixture(run_dir)
            result = gate.inspect_full(run_dir, 4000)
        self.assertFalse(result["passed"])

    def test_share_visible_subset_terminal_fixture_passes_regression(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self.write_json(
                run_dir / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "shares": {
                            "classification": "visible-subset-terminal",
                            "enumerated_identity_count": 33,
                            "identity_count_gap": 91,
                            "terminal_page_seen": True,
                        }
                    }
                },
            )
            result = gate.inspect_share(run_dir)
        self.assertTrue(result["passed"])
        self.assertEqual(result["classification"], "visible-subset-terminal")
        self.assertTrue(result["share_terminal_page_seen"])
        self.assertEqual(result["share_user_count"], 33)
        self.assertEqual(result["share_identity_gap"], 91)

    def test_probe_invariant_rejects_accidental_page2(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            readonly = run_dir / "readonly-ui"
            readonly.mkdir(parents=True)
            (readonly / "exploration-diagnostics.json").write_text(
                json.dumps(
                    {
                        "reaction_screening": {
                            "probe_only": True,
                            "probe_status": "initial-ready",
                            "query_records": 1,
                            "user_count": 25,
                            "session_restricted": False,
                            "latest_rate_limited": False,
                            "deep_capture_requested": False,
                        },
                        "reaction_preopened_source_proof": {"status": "matched"},
                        "pagination_runs": [],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "safe-facebook-http-capture.jsonl").write_text(
                json.dumps(
                    {
                        "friendly_name": "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection",
                        "request_feedback_post_id": TARGET_POST_ID,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = gate.inspect_probe(run_dir, TARGET_POST_ID)
            self.assertFalse(result["passed"])
            self.assertEqual(result["reason"], "probe-invariant-violated")
            self.assertEqual(result["page2_requests"], 1)

    def test_release_script_orders_low_pressure_gate_before_heavy_runs(self):
        probe = RUN_SCRIPT.index("$probe = Invoke-ProbeStage 'probe'")
        page2 = RUN_SCRIPT.index("$page2Run = Invoke-Capture 'page2'")
        full_adb = RUN_SCRIPT.index("$adb = Invoke-FullReactionStage 'full-adb' 0")
        post_adb_probe = RUN_SCRIPT.index("$postAdbProbe = Invoke-ProbeStage 'post-adb-probe'")
        full_native = RUN_SCRIPT.index("$native = Invoke-FullReactionStage 'full-native' 1")
        pre_share_probe = RUN_SCRIPT.index("$preShareProbe = Invoke-ProbeStage 'pre-share-probe'")
        share = RUN_SCRIPT.index("$share = Invoke-ShareStage")
        self.assertLess(probe, page2)
        self.assertLess(page2, full_adb)
        self.assertLess(full_adb, post_adb_probe)
        self.assertLess(post_adb_probe, full_native)
        self.assertLess(full_native, pre_share_probe)
        self.assertLess(pre_share_probe, share)

    def test_page2_stage_removes_speed_variables(self):
        start = RUN_SCRIPT.index("$page2Run = Invoke-Capture 'page2'")
        end = RUN_SCRIPT.index("$page2 = Invoke-Inspector 'page2'", start)
        block = RUN_SCRIPT[start:end]
        self.assertIn("'-ReactionPipelineBurstSize', '1'", block)
        self.assertIn("'-ReactionNativeScrollEnabled', '0'", block)
        self.assertIn("'-ReactionAbsoluteCap', '2'", block)
        self.assertIn("'-ReactionRateLimitMaxConsecutive', '1'", block)

    def test_heavy_stages_use_probe_boundaries(self):
        self.assertIn("Wait-StageCooldown 'post-adb-probe'", RUN_SCRIPT)
        self.assertIn("Wait-StageCooldown 'pre-share-probe'", RUN_SCRIPT)
        self.assertIn("Stop-Gate $StageName ([string]$result.reason) 10", RUN_SCRIPT)

    def test_capture_child_logs_do_not_contaminate_run_dir_return_value(self):
        start = RUN_SCRIPT.index("function Invoke-Capture")
        end = RUN_SCRIPT.index("function Invoke-ProbeStage", start)
        block = RUN_SCRIPT[start:end]
        self.assertIn("Invoke-FacebookPowerShellFileCaptured", block)
        self.assertIn("Write-FacebookChildProcessOutput -Result $child", block)
        self.assertIn("return $runDir", block)

    def test_release_gate_uses_deterministic_windows_powershell_executable(self):
        self.assertIn("System32\\WindowsPowerShell\\v1.0\\powershell.exe", RUN_SCRIPT)
        self.assertIn("Invoke-FacebookPowerShellFileCaptured", RUN_SCRIPT)
        self.assertNotIn("& $PowerShellExe", RUN_SCRIPT)
        self.assertNotIn("& powershell", RUN_SCRIPT)

    def test_release_gate_uses_shared_deterministic_python_resolver(self):
        self.assertIn("$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'", RUN_SCRIPT)
        self.assertIn("Resolve-FacebookPythonExe -BasePath $Base", RUN_SCRIPT)
        self.assertIn("& $PythonExe", RUN_SCRIPT)
        self.assertNotIn("& py", RUN_SCRIPT)
        self.assertNotIn("& python", RUN_SCRIPT)

    def test_unhandled_local_error_is_persisted_and_lock_is_released(self):
        self.assertIn("trap {", RUN_SCRIPT)
        self.assertIn("$state.status = 'failed'", RUN_SCRIPT)
        self.assertIn("$state.stop_reason = \"unhandled-error: $unhandledMessage\"", RUN_SCRIPT)
        self.assertIn("failed to persist unhandled error state", RUN_SCRIPT)
        self.assertIn("Write-Warning \"[release-gate] unhandled local error", RUN_SCRIPT)
        self.assertIn("Release-GateLock\n    exit 70", RUN_SCRIPT)
        self.assertNotIn("Write-Error \"[release-gate] unhandled local error", RUN_SCRIPT)

    def test_release_gate_has_exclusive_lock_before_any_live_capture(self):
        lock = RUN_SCRIPT.index("$script:GateLockHandle = [IO.File]::Open")
        first_capture = RUN_SCRIPT.index("function Invoke-Capture")
        self.assertLess(lock, first_capture)
        self.assertIn("[IO.FileShare]::None", RUN_SCRIPT)
        self.assertIn("another release gate is already active; no live work started", RUN_SCRIPT)
        self.assertIn("exit 60", RUN_SCRIPT)
        self.assertIn("function Release-GateLock", RUN_SCRIPT)
        self.assertIn("Release-GateLock\n    exit $ExitCode", RUN_SCRIPT)
        self.assertIn("Release-GateLock\nexit 0", RUN_SCRIPT)

    def test_heavy_terminal_stages_resume_only_after_fresh_health_gates(self):
        self.assertIn("[int]$ResumeMaxAgeHours = 24", RUN_SCRIPT)
        self.assertIn("function Get-ReusablePriorStage", RUN_SCRIPT)
        self.assertIn("$entry.result.passed", RUN_SCRIPT)
        self.assertIn("$ageHours -gt $ResumeMaxAgeHours", RUN_SCRIPT)
        self.assertIn("$rechecked = Invoke-Inspector 'full' $priorRunDir $MinFullReactionUsers", RUN_SCRIPT)
        self.assertIn("reused_prior_stage", RUN_SCRIPT)
        self.assertIn("reused_from_gate_id", RUN_SCRIPT)
        probe = RUN_SCRIPT.index("$probe = Invoke-ProbeStage 'probe'")
        page2 = RUN_SCRIPT.index("$page2Run = Invoke-Capture 'page2'")
        reuse_adb = RUN_SCRIPT.index("$adb = Invoke-FullReactionStage 'full-adb' 0")
        self.assertLess(probe, page2)
        self.assertLess(page2, reuse_adb)

    def test_resume_checkpoints_survive_repeated_blocked_gate_state_overwrites(self):
        self.assertIn("facebook-release-gate-checkpoints.json", RUN_SCRIPT)
        self.assertIn("function Save-CheckpointState", RUN_SCRIPT)
        self.assertIn("function Set-PersistentCheckpoint", RUN_SCRIPT)
        self.assertIn("$checkpointEntry = $checkpointState.stages[$Name]", RUN_SCRIPT)
        self.assertIn("$candidates += $checkpointEntry", RUN_SCRIPT)
        self.assertIn("$stageTime -gt $bestTime", RUN_SCRIPT)
        self.assertIn("$Timestamp = ''", RUN_SCRIPT)
        self.assertIn("$SourceGateId = ''", RUN_SCRIPT)
        self.assertIn("$sourceTimestamp = [string]$priorEntry.timestamp", RUN_SCRIPT)
        self.assertIn("Set-PersistentCheckpoint $StageName $rechecked $priorRunDir $sourceTimestamp $sourceGateId", RUN_SCRIPT)
        self.assertIn("Set-PersistentCheckpoint $StageName $result $runDir", RUN_SCRIPT)
        self.assertIn("Set-PersistentCheckpoint 'share' $result $runDir", RUN_SCRIPT)

    def test_share_checkpoint_is_reused_only_after_fresh_pre_share_probe(self):
        self.assertIn("function Invoke-ShareStage", RUN_SCRIPT)
        self.assertIn("$rechecked = Invoke-Inspector 'share' $priorRunDir", RUN_SCRIPT)
        pre_share_probe = RUN_SCRIPT.index("$preShareProbe = Invoke-ProbeStage 'pre-share-probe'")
        share_stage = RUN_SCRIPT.index("$share = Invoke-ShareStage")
        self.assertLess(pre_share_probe, share_stage)

    def test_predeploy_contract_tracks_current_resume_entrypoint(self):
        self.assertIn("$adb = Invoke-FullReactionStage 'full-adb' 0", PREDEPLOY)
        self.assertNotIn("$adbRun = Invoke-Capture 'full-adb'", PREDEPLOY)

    def test_ab_stage_requires_real_native_controller_and_records_speed(self):
        self.assertIn("$nativeControllerUsed = ([int]$native.native_scroll_success -gt 0)", RUN_SCRIPT)
        self.assertIn("$nativeFallbackFree = ([int]$native.native_scroll_fallbacks -eq 0)", RUN_SCRIPT)
        self.assertIn("$abComparable = ($adbElapsedMs -gt 0 -and $nativeElapsedMs -gt 0 -and $nativeControllerUsed)", RUN_SCRIPT)
        self.assertIn("[double]$NativeRecommendationMinSpeedupPercent = 5.0", RUN_SCRIPT)
        self.assertIn("$nativeHasMeaningfulWin", RUN_SCRIPT)
        self.assertIn("native_speedup_percent", RUN_SCRIPT)
        self.assertIn("adb_users_per_minute", RUN_SCRIPT)
        self.assertIn("native_users_per_minute", RUN_SCRIPT)
        self.assertIn("native_fallback_free", RUN_SCRIPT)
        self.assertIn("recommendation_reason", RUN_SCRIPT)
        self.assertIn("recommended_mode", RUN_SCRIPT)
        self.assertIn("Set-StageResult 'ab-comparison'", RUN_SCRIPT)
        self.assertIn("ab-telemetry-or-native-controller-unproven", RUN_SCRIPT)


if __name__ == "__main__":
    unittest.main()
