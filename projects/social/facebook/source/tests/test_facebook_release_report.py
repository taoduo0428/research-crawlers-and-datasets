import unittest

import facebook_release_report as report


class FacebookReleaseReportTests(unittest.TestCase):
    def base_state(self):
        return {
            "gate_id": "gate-test",
            "status": "passed",
            "post_id": "p1",
            "post_url": "https://www.facebook.com/example/posts/p1",
            "run_native_ab": True,
            "run_share_regression": True,
            "stages": {
                "probe": {
                    "result": {
                        "passed": True,
                        "probe_status": "initial-ready",
                        "user_count": 25,
                        "query_records": 2,
                        "page2_requests": 0,
                    }
                },
                "page2": {
                    "result": {
                        "passed": True,
                        "page2_requests": 1,
                        "cursor_present": True,
                        "previous_end_cursor_available": True,
                        "cursor_matches_previous_end": True,
                        "source_match": True,
                        "reaction_user_count": 50,
                        "reaction_page_count": 3,
                    }
                },
                "full-adb": {
                    "result": {
                        "passed": True,
                        "reaction_user_count": 3216,
                        "reaction_page_count": 130,
                        "deep_completion_status": "terminal-complete",
                        "elapsed_ms": 600000,
                        "growths_per_minute": 12.0,
                        "effective_swipes_per_growth": 1.1,
                    }
                },
                "full-native": {
                    "result": {
                        "passed": True,
                        "reaction_user_count": 3216,
                        "reaction_page_count": 130,
                        "deep_completion_status": "terminal-complete",
                        "elapsed_ms": 540000,
                        "growths_per_minute": 13.0,
                        "effective_swipes_per_growth": 1.0,
                        "native_scroll_success": 120,
                        "native_scroll_fallbacks": 0,
                    }
                },
                "ab-comparison": {
                    "result": {
                        "passed": True,
                        "recommended_mode": "native",
                        "recommendation_reason": "native-meaningfully-faster",
                        "native_speedup_percent": 10.0,
                        "adb_users_per_minute": 321.6,
                        "native_users_per_minute": 357.3,
                    }
                },
                "share": {
                    "result": {
                        "passed": True,
                        "classification": "visible-subset-terminal",
                        "share_user_count": 33,
                        "share_page_count": 3,
                        "share_terminal_page_seen": True,
                        "share_identity_gap": 91,
                    }
                },
                "predeploy": {
                    "result": {
                        "passed": True,
                        "overall_status": "PASS",
                    }
                },
            },
        }

    def test_complete_default_gate_report_passes_and_preserves_metrics(self):
        result = report.build_report(self.base_state(), {"stages": {"full-adb": {}, "full-native": {}, "share": {}}}, {"checks": [{}] * 11})
        self.assertTrue(result["passed"])
        self.assertEqual(result["reaction"]["comparison"]["recommended_mode"], "native")
        self.assertEqual(result["reaction"]["adb"]["elapsed_seconds"], 600.0)
        self.assertEqual(result["reaction"]["native"]["elapsed_seconds"], 540.0)
        self.assertEqual(result["share"]["identity_gap"], 91)
        self.assertEqual(result["predeploy"]["checks"], 11)
        self.assertEqual(result["checkpoint_count"], 3)

    def test_required_native_failure_blocks_final_report(self):
        state = self.base_state()
        state["stages"]["full-native"]["result"]["passed"] = False
        result = report.build_report(state, {}, {"checks": []})
        self.assertFalse(result["passed"])

    def test_explicit_native_and_share_skip_does_not_fail_report(self):
        state = self.base_state()
        state["run_native_ab"] = False
        state["run_share_regression"] = False
        state["stages"].pop("full-native")
        state["stages"].pop("ab-comparison")
        state["stages"].pop("share")
        result = report.build_report(state, {}, {"checks": []})
        self.assertTrue(result["passed"])
        self.assertFalse(result["requirements"]["native_ab"])
        self.assertFalse(result["requirements"]["share_regression"])

    def test_probe_or_cursor_failure_never_reports_pass(self):
        state = self.base_state()
        state["stages"]["page2"]["result"]["passed"] = False
        state["stages"]["page2"]["result"]["cursor_matches_previous_end"] = False
        result = report.build_report(state, {}, {"checks": []})
        self.assertFalse(result["passed"])
        self.assertFalse(result["page2"]["cursor_matches_previous_end"])

    def test_markdown_contains_selection_and_share_verdict(self):
        result = report.build_report(self.base_state(), {}, {"checks": [{}]})
        text = report.markdown(result)
        self.assertIn("Recommended mode: `native`", text)
        self.assertIn("visible-subset-terminal", text)
        self.assertIn("Cursor continuity: `True`", text)


if __name__ == "__main__":
    unittest.main()
