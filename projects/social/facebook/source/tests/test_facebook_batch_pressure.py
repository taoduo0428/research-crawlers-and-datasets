import unittest
from pathlib import Path

import facebook_batch_pressure as pressure


BASE = Path(__file__).resolve().parents[1]
RUN_BATCH = (BASE / "run_facebook_batch.ps1").read_text(encoding="utf-8-sig")


class FacebookBatchPressureTests(unittest.TestCase):
    def test_1675004_stops_batch(self):
        result = pressure.next_state(
            {
                "reaction_latest_rate_limited": True,
                "reaction_latest_graphql_error_code": 1675004,
                "reaction_query_records": 2,
            }
        )
        self.assertEqual(result["decision"], "stop-batch")
        self.assertEqual(result["next_action"], "fresh-probe-before-resume")
        self.assertIn("graphql-1675004", result["block_reasons"])

    def test_3252001_or_368_stops_batch(self):
        result = pressure.next_state(
            {
                "session_restricted": True,
                "session_restriction_graphql_code": 3252001,
                "session_restriction_api_error_code": 368,
            }
        )
        self.assertEqual(result["health"], "blocked-live")
        self.assertIn("graphql-3252001", result["block_reasons"])
        self.assertIn("api-368", result["block_reasons"])

    def test_terminal_clean_accelerates_only_to_floor(self):
        summary = {
            "deep_capture_completed": True,
            "deep_completion_status": "terminal-complete",
            "reaction_query_records": 20,
            "reaction_user_count": 500,
            "reaction_page_count": 21,
        }
        first = pressure.next_state(summary, {"next_cooldown_seconds": 20, "clean_streak": 0})
        self.assertEqual(first["decision"], "continue")
        self.assertEqual(first["next_cooldown_seconds"], 17)
        current = first
        for _ in range(20):
            current = pressure.next_state(summary, current)
        self.assertGreaterEqual(current["next_cooldown_seconds"], 10)

    def test_nonterminal_observed_increases_cooldown(self):
        result = pressure.next_state(
            {"reaction_query_records": 2, "deep_completion_status": "partial-open-tail"},
            {"next_cooldown_seconds": 20, "clean_streak": 3},
        )
        self.assertEqual(result["decision"], "continue-cautious")
        self.assertEqual(result["clean_streak"], 0)
        self.assertEqual(result["next_cooldown_seconds"], 30)

    def test_batch_main_loop_honors_pressure_stop_before_next_candidate(self):
        self.assertIn("facebook_batch_pressure.py", RUN_BATCH)
        self.assertIn("if ([string]$pressure.decision -eq 'stop-batch')", RUN_BATCH)
        self.assertIn("remaining live candidates paused", RUN_BATCH)
        self.assertIn("fresh-probe-before-resume", Path(BASE / "facebook_batch_pressure.py").read_text(encoding="utf-8-sig"))
        stop = RUN_BATCH.index("if ([string]$pressure.decision -eq 'stop-batch')")
        cooldown = RUN_BATCH.index("Start-Sleep -Seconds ([int]$pressure.next_cooldown_seconds)")
        self.assertLess(stop, cooldown)


if __name__ == "__main__":
    unittest.main()
