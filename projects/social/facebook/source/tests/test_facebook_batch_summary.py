import json
import tempfile
import unittest
from pathlib import Path

from facebook_batch_summary import derive_deep_capture_completion, summarize_run


class FacebookBatchSummaryTests(unittest.TestCase):
    def _write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_uses_request_source_post_and_preserves_observed_url_and_open_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run1"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {
                            "status": "visible-users-enumerated",
                            "ui_total_count": 1096,
                            "enumerated_identity_count": 1,
                            "pagination": {
                                "open_groups": ["__ALL__"],
                                "terminal_groups": ["like"],
                                "all_observed_groups_terminal": False,
                            },
                        },
                        "shares": {"status": "query-not-triggered", "query_records": 0},
                    },
                    "posts": [
                        {
                            "post_id": "123",
                            "author_name": "Example Page",
                            "url": "https://www.facebook.com/example/posts/123",
                            "message": "Example message",
                        }
                    ],
                },
            )
            self._write_json(
                run / "readonly-ui" / "exploration-diagnostics.json",
                {
                    "actions": [{"action": "reaction", "entry_scan_scrolls": 1, "visibility_hint": ""}],
                    "pagination_runs": [{"stop_reason": "ui-trigger-exhausted"}],
                    "reaction_screening": {
                        "classification": "enumerable",
                        "decision_reason": "visible-reactor-users",
                        "probe_attempts": 1,
                        "deep_capture_requested": True,
                        "deep_capture_completed": True,
                    },
                },
            )
            run.mkdir(parents=True, exist_ok=True)
            record = {
                "friendly_name": "FeedbackReactorsGraphService",
                "request_feedback_post_id": "123",
                "request_reaction_id": None,
                "response_body": json.dumps(
                    {"data": {"fetch__Feedback": {"reactors": {"edges": [{"node": {"id": "u1", "name": "User One"}}], "page_info": {"has_next_page": True}}}}}
                ),
            }
            (run / "safe-facebook-http-capture.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

            row = summarize_run(run)
            self.assertEqual(row["source_post_id"], "123")
            self.assertEqual(row["author_name"], "Example Page")
            self.assertEqual(row["post_url"], "https://www.facebook.com/example/posts/123")
            self.assertEqual(row["reaction_open_groups"], ["__ALL__"])
            self.assertEqual(row["reaction_terminal_groups"], ["like"])
            self.assertIn("ui-trigger-exhausted", row["pagination_stop_reasons"])
            self.assertEqual(row["screening_classification"], "enumerable")
            self.assertEqual(row["decision_reason"], "visible-reactor-users")
            self.assertEqual(row["screen_probe_attempts"], 1)
            self.assertTrue(row["deep_capture_requested"])
            self.assertFalse(row["deep_capture_completed"])
            self.assertEqual(row["deep_completion_status"], "incomplete")
            self.assertEqual(row["deep_completion_reason"], "ui-trigger-exhausted")

    def test_deep_completion_requires_terminal_all_tail(self):
        screening = {"deep_capture_requested": True, "deep_capture_completed": True}
        incomplete = [{
            "reaction_key": "__ALL__",
            "stop_reason": "absolute-cap",
            "terminal_seen": False,
            "open_seen": True,
        }]
        self.assertEqual(
            derive_deep_capture_completion(screening, incomplete),
            (False, "partial-open-tail", "absolute-cap"),
        )
        recovered = incomplete + [{
            "reaction_key": "__ALL__",
            "stop_reason": "terminal",
            "terminal_seen": True,
            "open_seen": True,
        }]
        self.assertEqual(
            derive_deep_capture_completion(screening, recovered),
            (True, "terminal-complete", "terminal"),
        )
        limited = [{
            "reaction_key": "__ALL__",
            "stop_reason": "rate-limited",
            "terminal_seen": False,
            "open_seen": True,
        }]
        self.assertEqual(
            derive_deep_capture_completion(screening, limited),
            (False, "rate-limited", "rate-limited"),
        )

    def test_friends_only_and_single_post_fallback_without_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run2"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {
                            "status": "query-triggered-friends-only-visibility",
                            "ui_visibility_hint": "friends-only-names",
                            "ui_total_count": 297,
                            "enumerated_identity_count": 0,
                            "pagination": {
                                "open_groups": [],
                                "terminal_groups": ["__ALL__"],
                                "all_observed_groups_terminal": True,
                            },
                        },
                        "shares": {"status": "query-not-triggered", "query_records": 0},
                    },
                    "posts": [{"post_id": "999", "author_name": "Restricted Page", "url": None, "message": "Restricted"}],
                },
            )
            self._write_json(
                run / "readonly-ui" / "exploration-diagnostics.json",
                {"actions": [{"action": "reaction", "entry_scan_scrolls": 0, "visibility_hint": "friends-only-names"}]},
            )

            row = summarize_run(run)
            self.assertEqual(row["source_post_id"], "999")
            self.assertTrue(row["friends_only"])
            self.assertTrue(row["all_observed_groups_terminal"])
            self.assertIsNone(row["post_url"])
            self.assertEqual(row["screening_classification"], "visibility-restricted")
            self.assertEqual(row["decision_reason"], "friends-only-names")
            self.assertFalse(row["deep_capture_requested"])
            self.assertFalse(row["deep_capture_completed"])

    def test_share_summary_prefers_final_analyzer_classification_and_keeps_identity_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "share-run"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {"status": "query-not-triggered", "query_records": 0, "pagination": {}},
                        "shares": {
                            "status": "visible-users-enumerated-subset",
                            "classification": "visible-subset-terminal",
                            "decision_reason": "visible-share-users-terminal-below-ui-total",
                            "list_confirmed": True,
                            "entry_method": "comments-normalized",
                            "query_records": 2,
                            "terminal_page_seen": True,
                            "ui_total_count": 97,
                            "enumerated_identity_count": 15,
                            "identity_count_gap": 82,
                        },
                    },
                    "posts": [{"post_id": "55", "author_name": "Share Page"}],
                    "share_pages": [{"has_next_page": False}],
                },
            )
            self._write_json(
                run / "readonly-ui" / "exploration-diagnostics.json",
                {
                    "actions": [{"action": "share", "confirmed": True, "entry_method": "comments-normalized"}],
                    "reaction_native_fallback": {
                        "attempted": True,
                        "succeeded": True,
                        "method": "native-reaction-loading-wait",
                        "attempts": 2,
                    },
                    "share_native_fallback": {
                        "attempted": True,
                        "succeeded": True,
                        "method": "comments-summary-reveal",
                        "attempts": 3,
                    },
                    "share_screening": {
                        "classification": "enumerable",
                        "decision_reason": "visible-share-users",
                        "list_confirmed": True,
                        "entry_method": "comments-normalized",
                    },
                },
            )

            row = summarize_run(run)
            self.assertEqual(row["share_screening_classification"], "visible-subset-terminal")
            self.assertEqual(row["share_decision_reason"], "visible-share-users-terminal-below-ui-total")
            self.assertTrue(row["share_list_confirmed"])
            self.assertEqual(row["share_entry_method"], "comments-normalized")
            self.assertEqual(row["share_identity_gap"], 82)
            self.assertTrue(row["reaction_native_fallback_attempted"])
            self.assertTrue(row["reaction_native_fallback_succeeded"])
            self.assertEqual(row["reaction_native_fallback_method"], "native-reaction-loading-wait")
            self.assertEqual(row["reaction_list_ready_attempts"], 2)
            self.assertTrue(row["share_native_fallback_attempted"])
            self.assertTrue(row["share_native_fallback_succeeded"])
            self.assertEqual(row["share_native_fallback_method"], "comments-summary-reveal")
            self.assertEqual(row["share_native_fallback_attempts"], 3)

    def test_native_permalink_fallback_fields_and_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "permalink-run"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {
                            "status": "visible-users-enumerated",
                            "query_records": 1,
                            "enumerated_identity_count": 1,
                            "pagination": {},
                        },
                        "shares": {
                            "status": "visible-users-enumerated",
                            "classification": "enumerable",
                            "query_records": 1,
                            "enumerated_identity_count": 1,
                        },
                    },
                    "posts": [{"post_id": "77", "author_id": "88", "author_name": "Fallback Page"}],
                },
            )
            self._write_json(
                run / "readonly-ui-fallback" / "exploration-diagnostics.json",
                {
                    "actions": [
                        {"action": "reaction", "confirmed": True},
                        {"action": "share", "confirmed": True},
                    ]
                },
            )
            self._write_json(
                run / "native-permalink-fallback.json",
                {
                    "source": "android-native-permalink-fallback",
                    "attempted": True,
                    "reaction_eligible": True,
                    "share_eligible": True,
                    "post_id": "77",
                    "author_id": "88",
                    "composite_story_id": "88_77",
                    "fallback_start_line": 10,
                    "family_capture_start_line": 14,
                    "target_open_method": "facebook-search-id",
                    "target_open_verified": True,
                    "target_open_exit_code": 0,
                    "target_open_diagnostics": "fallback-target-open/target-open-diagnostics.json",
                    "composite_deeplink_attempted": False,
                    "explorer_exit_code": 0,
                    "target_validation_status": "matched",
                    "reaction_target_validation_status": "matched",
                    "share_target_validation_status": "matched",
                    "observed_reaction_source_post_ids": ["77"],
                    "observed_share_source_post_ids": ["77"],
                },
            )
            run.mkdir(parents=True, exist_ok=True)
            records = [
                {
                    "friendly_name": "FeedbackReactorsGraphService",
                    "request_feedback_post_id": "77",
                    "response_body": json.dumps({"data": {"node": {"reactors": {"edges": [{"node": {"id": "u1", "name": "R"}}], "page_info": {"has_next_page": False}}}}}),
                },
                {
                    "friendly_name": "ResharesOfContentFeedQuery",
                    "request_feedback_post_id": "77",
                    "response_body": json.dumps({"data": {"node": {"reshares": {"edges": [{"node": {"id": "s1", "actors": [{"id": "u2", "name": "S"}]}}], "page_info": {"has_next_page": False}}}}}),
                },
            ]
            (run / "safe-facebook-http-capture.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

            row = summarize_run(run)
            self.assertTrue(row["native_permalink_fallback_attempted"])
            self.assertTrue(row["native_permalink_reaction_eligible"])
            self.assertTrue(row["native_permalink_share_eligible"])
            self.assertEqual(row["native_permalink_source"], "android-native-permalink-fallback")
            self.assertEqual(row["native_permalink_composite_story_id"], "88_77")
            self.assertEqual(row["native_permalink_fallback_start_line"], 10)
            self.assertEqual(row["native_permalink_family_capture_start_line"], 14)
            self.assertEqual(row["native_permalink_target_open_method"], "facebook-search-id")
            self.assertTrue(row["native_permalink_target_open_verified"])
            self.assertEqual(row["native_permalink_target_open_exit_code"], 0)
            self.assertFalse(row["native_permalink_composite_deeplink_attempted"])
            self.assertEqual(row["native_permalink_explorer_exit_code"], 0)
            self.assertTrue(row["reaction_permalink_fallback_recovered"])
            self.assertTrue(row["share_permalink_fallback_recovered"])
            self.assertEqual(row["native_permalink_reaction_target_validation_status"], "matched")
            self.assertEqual(row["native_permalink_share_target_validation_status"], "matched")

    def test_native_permalink_cross_post_is_not_recovered_and_keeps_target_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "cross-post-run"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {"status": "visible-users-enumerated", "query_records": 1, "enumerated_identity_count": 1, "pagination": {}},
                        "shares": {"status": "visible-users-enumerated", "classification": "enumerable", "query_records": 1, "enumerated_identity_count": 1},
                    },
                    "posts": [
                        {"post_id": "77", "author_id": "88", "author_name": "Target Page", "message": "target"},
                        {"post_id": "99", "author_id": "66", "author_name": "Wrong Page", "message": "wrong"},
                    ],
                },
            )
            self._write_json(
                run / "readonly-ui-fallback" / "exploration-diagnostics.json",
                {"actions": [{"action": "reaction", "confirmed": True}]},
            )
            self._write_json(
                run / "native-permalink-fallback.json",
                {
                    "attempted": True,
                    "reaction_eligible": True,
                    "share_eligible": False,
                    "post_id": "77",
                    "author_id": "88",
                    "initial_reaction_status": "query-not-triggered",
                    "target_validation_status": "mismatch",
                    "observed_reaction_source_post_ids": ["99"],
                    "unavailable_reason": "fallback-target-mismatch",
                },
            )
            run.mkdir(parents=True, exist_ok=True)
            records = [
                {
                    "friendly_name": "ResharesOfContentFeedQuery",
                    "request_feedback_post_id": "77",
                    "response_body": json.dumps({"data": {"node": {"reshares": {"edges": [{"node": {"id": "s1", "actors": [{"id": "u1", "name": "Share"}]}}], "page_info": {"has_next_page": False}}}}}),
                },
                {
                    "friendly_name": "FeedbackReactorsGraphService",
                    "request_feedback_post_id": "99",
                    "response_body": json.dumps({"data": {"node": {"reactors": {"edges": [{"node": {"id": "u9", "name": "Wrong Reactor"}}], "page_info": {"has_next_page": True}}}}}),
                },
            ]
            (run / "safe-facebook-http-capture.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

            row = summarize_run(run)
            self.assertEqual(row["source_post_id"], "77")
            self.assertEqual(row["author_name"], "Target Page")
            self.assertEqual(row["reaction_status"], "query-not-triggered")
            self.assertEqual(row["reaction_query_records"], 0)
            self.assertEqual(row["reaction_user_count"], 0)
            self.assertFalse(row["reaction_permalink_fallback_recovered"])
            self.assertEqual(row["native_permalink_target_validation_status"], "mismatch")
            self.assertEqual(row["native_permalink_unavailable_reason"], "fallback-target-mismatch")

    def test_native_permalink_never_marks_friends_only_reaction_recovered(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "restricted-permalink-run"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {
                            "status": "query-triggered-friends-only-visibility",
                            "ui_visibility_hint": "friends-only-names",
                            "query_records": 2,
                            "enumerated_identity_count": 0,
                            "pagination": {},
                        },
                        "shares": {"status": "query-not-triggered", "query_records": 0},
                    },
                    "posts": [{"post_id": "99", "author_id": "11", "author_name": "Restricted"}],
                },
            )
            self._write_json(
                run / "readonly-ui" / "exploration-diagnostics.json",
                {"actions": [{"action": "reaction", "visibility_hint": "friends-only-names"}]},
            )
            self._write_json(
                run / "readonly-ui-fallback" / "exploration-diagnostics.json",
                {"actions": [{"action": "reaction", "confirmed": True}]},
            )
            self._write_json(
                run / "native-permalink-fallback.json",
                {"attempted": True, "reaction_eligible": True, "post_id": "99", "author_id": "11"},
            )

            row = summarize_run(run)
            self.assertTrue(row["friends_only"])
            self.assertFalse(row["reaction_permalink_fallback_recovered"])

    def test_no_family_query_never_marks_old_target_data_recovered(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "no-family-query-run"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {"status": "visible-users-enumerated", "query_records": 1, "enumerated_identity_count": 1, "pagination": {}},
                        "shares": {"status": "query-not-triggered", "query_records": 0},
                    },
                    "posts": [{"post_id": "77", "author_id": "88", "author_name": "Target"}],
                },
            )
            self._write_json(
                run / "readonly-ui-fallback" / "exploration-diagnostics.json",
                {"actions": [{"action": "reaction", "confirmed": True}]},
            )
            self._write_json(
                run / "native-permalink-fallback.json",
                {
                    "attempted": True,
                    "reaction_eligible": True,
                    "post_id": "77",
                    "initial_reaction_status": "query-not-triggered",
                    "target_validation_status": "no-family-query",
                    "reaction_target_validation_status": "no-family-query",
                },
            )
            run.mkdir(parents=True, exist_ok=True)
            old_record = {
                "friendly_name": "FeedbackReactorsGraphService",
                "request_feedback_post_id": "77",
                "response_body": json.dumps({"data": {"node": {"reactors": {"edges": [{"node": {"id": "u1", "name": "Old Target User"}}], "page_info": {"has_next_page": False}}}}}),
            }
            (run / "safe-facebook-http-capture.jsonl").write_text(json.dumps(old_record) + "\n", encoding="utf-8")

            row = summarize_run(run)
            self.assertEqual(row["reaction_user_count"], 1)
            self.assertEqual(row["native_permalink_reaction_target_validation_status"], "no-family-query")
            self.assertFalse(row["reaction_permalink_fallback_recovered"])

    def test_partial_family_match_only_recovers_matched_family_and_merges_its_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "partial-family-run"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {"status": "visible-users-enumerated", "query_records": 1, "enumerated_identity_count": 1, "pagination": {}},
                        "shares": {"status": "visible-users-enumerated", "classification": "enumerable", "query_records": 1, "enumerated_identity_count": 1},
                    },
                    "posts": [{"post_id": "77", "author_id": "88", "author_name": "Target"}],
                },
            )
            self._write_json(
                run / "readonly-ui-fallback" / "exploration-diagnostics.json",
                {
                    "actions": [{"action": "reaction", "confirmed": True}, {"action": "share", "confirmed": True}],
                    "pagination_runs": [{"stop_reason": "terminal", "strategy_attempts": {"long": 2}, "strategy_growth": {"long": 1}, "final_budget": 2, "absolute_cap": 10}],
                },
            )
            self._write_json(
                run / "native-permalink-fallback.json",
                {
                    "attempted": True,
                    "reaction_eligible": True,
                    "share_eligible": True,
                    "post_id": "77",
                    "target_validation_status": "matched",
                    "reaction_target_validation_status": "matched",
                    "share_target_validation_status": "no-family-query",
                    "observed_reaction_source_post_ids": ["77"],
                },
            )
            run.mkdir(parents=True, exist_ok=True)
            records = [
                {"friendly_name": "FeedbackReactorsGraphService", "request_feedback_post_id": "77", "response_body": json.dumps({"data": {"node": {"reactors": {"edges": [{"node": {"id": "u1", "name": "R"}}], "page_info": {"has_next_page": False}}}}})},
                {"friendly_name": "ResharesOfContentFeedQuery", "request_feedback_post_id": "77", "response_body": json.dumps({"data": {"node": {"reshares": {"edges": [{"node": {"id": "s1", "actors": [{"id": "u2", "name": "S"}]}}], "page_info": {"has_next_page": False}}}}})},
            ]
            (run / "safe-facebook-http-capture.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

            row = summarize_run(run)
            self.assertTrue(row["reaction_permalink_fallback_recovered"])
            self.assertFalse(row["share_permalink_fallback_recovered"])
            self.assertIn("terminal", row["pagination_stop_reasons"])
            self.assertEqual(row["pagination_strategy_attempts"].get("long"), 2)

    def test_mismatch_fallback_pagination_metrics_are_not_merged(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "mismatch-pagination-run"
            self._write_json(
                run / "analysis" / "facebook-sample-patterns.json",
                {
                    "enumeration_status": {
                        "reactions": {"status": "query-not-triggered", "query_records": 0, "enumerated_identity_count": 0, "pagination": {}},
                        "shares": {"status": "query-not-triggered", "query_records": 0},
                    },
                    "posts": [{"post_id": "77", "author_id": "88", "author_name": "Target"}],
                },
            )
            self._write_json(run / "readonly-ui" / "exploration-diagnostics.json", {"pagination_runs": []})
            self._write_json(
                run / "readonly-ui-fallback" / "exploration-diagnostics.json",
                {"pagination_runs": [{"stop_reason": "target-mismatch", "strategy_attempts": {"long": 9}, "strategy_growth": {"long": 0}}]},
            )
            self._write_json(
                run / "native-permalink-fallback.json",
                {
                    "attempted": True,
                    "reaction_eligible": True,
                    "post_id": "77",
                    "target_validation_status": "mismatch",
                    "reaction_target_validation_status": "mismatch",
                    "observed_reaction_source_post_ids": ["99"],
                },
            )

            row = summarize_run(run)
            self.assertNotIn("target-mismatch", row["pagination_stop_reasons"])
            self.assertEqual(row["pagination_strategy_attempts"], {})


if __name__ == "__main__":
    unittest.main()
