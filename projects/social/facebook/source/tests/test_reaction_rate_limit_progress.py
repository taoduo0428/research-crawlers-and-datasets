import json
import unittest
from urllib.parse import urlencode

from mitmproxy import http

import facebook_capture_addon_safe as addon
from facebook_capture_progress import summarize_reaction_records


class ReactionRateLimitProgressTests(unittest.TestCase):
    def test_source_less_rate_limit_is_visible_without_reassigning_source_data(self):
        first = {
            "friendly_name": "FeedbackReactorsGraphService",
            "request_feedback_post_id": "p-window-rate",
            "response_body": '{"data":{"node":{"reactors":{"edges":[{"node":{"id":"u1","name":"One"}}],"page_info":{"has_next_page":true}}}}}',
        }
        throttled = {
            "friendly_name": "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection",
            "response_rate_limited": True,
            "response_graphql_error_code": 1675004,
        }

        summary = summarize_reaction_records([(70, first), (71, throttled)], "p-window-rate")

        self.assertEqual(summary["source_post_id"], "p-window-rate")
        self.assertEqual(summary["user_count"], 1)
        self.assertEqual(summary["page_count"], 1)
        self.assertTrue(summary["latest_rate_limited"])
        self.assertEqual(summary["rate_limit_count"], 1)
        self.assertEqual(summary["latest_graphql_error_code"], 1675004)
        self.assertEqual(summary["latest_rate_limit_line"], 71)

    def test_rate_limit_outside_supplied_window_does_not_leak_in(self):
        current = {
            "friendly_name": "FeedbackReactorsGraphService",
            "request_feedback_post_id": "p-current",
            "response_body": '{"data":{"node":{"reactors":{"edges":[{"node":{"id":"u2","name":"Two"}}],"page_info":{"has_next_page":true}}}}}',
        }

        summary = summarize_reaction_records([(80, current)], "p-current")

        self.assertFalse(summary["latest_rate_limited"])
        self.assertEqual(summary["rate_limit_count"], 0)
        self.assertEqual(summary["latest_rate_limit_line"], 0)

    def test_gzip_reaction_request_body_restores_cursor_probe_input(self):
        context = {"request_feedback_post_id": "123456789", "request_reaction_id": "all"}
        friendly = "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection"
        cursor = "cursor-value-never-persisted"
        page_one = json.dumps(
            {
                "data": {
                    "node": {
                        "reactors": {
                            "edges": [],
                            "page_info": {"has_next_page": True, "end_cursor": cursor},
                        }
                    }
                }
            }
        )
        addon._reaction_cursor_response_probe({"friendly_name": friendly, **context}, page_one)
        body = urlencode(
            {
                "variables": json.dumps(
                    {"feedback_id": "123456789", "reaction_id": "all", "after": cursor}
                )
            }
        ).encode("utf-8")

        request = http.Request.make(
            "POST",
            "https://graph.facebook.com/graphql",
            body,
            {"content-encoding": "gzip", "content-type": "application/x-www-form-urlencoded"},
        )

        self.assertTrue((request.raw_content or b"").startswith(b"\x1f\x8b"))
        decoded = addon._request_body_text(request)
        parsed_context = addon._public_request_context_from_body(decoded)
        probe = addon._reaction_cursor_request_probe(friendly, parsed_context, decoded)

        self.assertEqual(parsed_context["request_feedback_post_id"], "123456789")
        self.assertTrue(probe["request_cursor_present"])
        self.assertTrue(probe["previous_end_cursor_available"])
        self.assertTrue(probe["request_cursor_matches_previous_end"])
        self.assertNotIn(cursor, json.dumps(probe, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
