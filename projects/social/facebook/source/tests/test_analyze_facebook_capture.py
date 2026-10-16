import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from analyze_facebook_capture import (
    classify_share_visibility,
    diagnostic_visible_count,
    enumeration_status,
    extract_posts,
    extract_reactors,
    extract_share_pages,
    extract_shares,
    feedback_post_id,
    iter_records,
    query_family_count,
    parse_display_count,
    target_url_tokens,
    target_url_digest,
    is_facebook_share_navigation_url,
    verify_target_posts,
    verify_target_capture,
    record_original_content_id,
    record_source_post_id,
    response_feedback_post_id,
    summarize_reaction_page_groups,
    summarize_reaction_integrity,
    target_post_reported_count,
)
from facebook_capture_addon_safe import (
    _public_navigation_context_from_values,
    _public_request_context_from_body,
    _redact_text,
)
from facebook_capture_progress import NULL_REACTION_KEY, summarize_records
from mine_facebook_query_shapes import inspect_record


def reactor_record(line, variables, response):
    return (
        line,
        {
            "friendly_name": "FeedbackReactorsGraphService",
            "request_body": urlencode({"variables": json.dumps(variables)}),
            "response_body": json.dumps(response),
        },
    )


def paginated_reactor_record(line, variables, response):
    line_number, record = reactor_record(line, variables, response)
    record["friendly_name"] = (
        "FeedbackReactorsGraphService_At_Connection_Pagination_"
        "Feedback_reactors_connection"
    )
    return line_number, record


def paginated_reshare_record(line, variables, response):
    return (
        line,
        {
            "friendly_name": "ResharesOfContentFeedQuery_At_Connection_Pagination",
            "request_body": urlencode({"variables": json.dumps(variables)}),
            "response_body": json.dumps(response),
        },
    )


class ExtractReactorsTests(unittest.TestCase):
    def test_iter_records_zero_limit_reads_the_complete_capture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            capture = Path(temp_dir) / "capture.jsonl"
            capture.write_text(
                "".join(json.dumps({"index": index}) + "\n" for index in range(7)),
                encoding="utf-8",
            )
            complete, errors = iter_records(capture, 0)
            limited, _ = iter_records(capture, 3)
        self.assertEqual(errors, 0)
        self.assertEqual(len(complete), 7)
        self.assertEqual(len(limited), 3)

    def test_extracts_node_reaction_id_and_enriches_duplicate(self):
        records = [
            paginated_reactor_record(
                1,
                {"feedback_id": "ZmVlZGJhY2s6MTIz"},
                {"data": {"node": {"reactors": {"edges": [
                    {"node": {"id": "u1", "name": "One", "profile_picture": {"uri": "https://img/1"}}},
                ], "page_info": {"has_next_page": True, "end_cursor": "a"}}}}},
            ),
            paginated_reactor_record(
                2,
                {"feedback_id": "ZmVlZGJhY2s6MTIz"},
                {"data": {"node": {"reactors": {"edges": [
                    {"node": {
                        "id": "u1",
                        "name": "One",
                        "profile_picture": {"uri": "https://img/1"},
                        "feedback_reaction_info": {"id": "reaction-like"},
                    }},
                    {"node": {
                        "id": "u2",
                        "name": "Two",
                        "profile_picture": {"uri": "https://img/2"},
                        "feedback_reaction_info": {"id": "reaction-love"},
                    }},
                ], "page_info": {"has_next_page": False, "end_cursor": None}}}}},
            ),
        ]

        reactors, _ = extract_reactors(records, redact=False)

        self.assertEqual(len(reactors), 2)
        by_id = {row["user_id"]: row for row in reactors}
        self.assertEqual(by_id["u1"]["reaction_id"], "reaction-like")
        self.assertEqual(by_id["u2"]["reaction_id"], "reaction-love")
        self.assertEqual(by_id["u1"]["avatar_url"], "https://img/1")

    def test_extracts_current_reel_reactor_query_and_binds_source(self):
        response = {
            "data": {
                "feedback": {
                    "id": "ZmVlZGJhY2s6MTIyMTIyMDUwNzM1MzM5OTQ2",
                    "legacy_api_post_id": "122122050735339946",
                    "reactors": {"count": 1},
                    "unified_reactors": {
                        "edges": [
                            {
                                "node": {
                                    "id": "100066697773937",
                                    "name": "Er Prashant Kumar",
                                    "profile_picture": {"uri": "https://img/reel"},
                                },
                                "feedback_reaction_info": {"id": "1635855486666999"},
                            }
                        ],
                        "page_info": {"has_next_page": False, "end_cursor": "terminal"},
                    },
                }
            }
        }
        records = [
            (10, {"friendly_name": "FBShortsMediaPrefetchQuery", "status_code": 200, "response_body": '{"video":{"id":"1054985397317048"}}'}),
            (20, {
                "friendly_name": "FbShortsIGMediaReactorsQuery",
                "status_code": 200,
                "request_feedback_post_id": "122122050735339946",
                "response_body": json.dumps(response),
            }),
        ]

        reactors, pages = extract_reactors(records, redact=False)
        progress = summarize_records(records, "reaction")
        verified = verify_target_capture([], records, expected_url="https://www.facebook.com/reel/1054985397317048/")

        self.assertEqual(len(reactors), 1)
        self.assertEqual(reactors[0]["source_post_id"], "122122050735339946")
        self.assertEqual(reactors[0]["reaction_id"], "1635855486666999")
        self.assertEqual(pages[0]["source_field"], "unified_reactors")
        self.assertFalse(pages[0]["has_next_page"])
        self.assertEqual(progress["query_records"], 1)
        self.assertEqual(progress["user_count"], 1)
        self.assertEqual(verified["matched_post_id"], "122122050735339946")

    def test_query_shape_miner_counts_only_visible_identities(self):
        record = {
            "response_body": json.dumps(
                {
                    "data": {
                        "node": {
                            "reshares": {
                                "edges": [
                                    {"node": {"actors": [{"id": "s1", "name": "Share One"}]}}
                                ],
                                "page_info": {"has_next_page": False},
                            },
                            "reactors": {
                                "edges": [{"node": {"id": "r1", "name": "React One"}}],
                                "page_info": {"has_next_page": True},
                            },
                            "important_reactors": {
                                "edges": [],
                                "page_info": {"has_next_page": False},
                            },
                        }
                    }
                }
            )
        }
        evidence = inspect_record(record)
        self.assertEqual(evidence["share_connections"], 1)
        self.assertEqual(evidence["share_identities"], 1)
        self.assertEqual(evidence["reaction_connections"], 2)
        self.assertEqual(evidence["reaction_identities"], 1)
        self.assertEqual(evidence["share_page_info"], 1)
        self.assertEqual(evidence["reaction_page_info"], 2)

    def test_share_classification_distinguishes_subset_empty_and_ui_failures(self):
        subset = classify_share_visibility(
            {"confirmed": True, "entry_method": "comments-normalized"},
            2,
            15,
            total_count=97,
            terminal_page_seen=True,
        )
        self.assertEqual(subset["classification"], "visible-subset-terminal")
        self.assertTrue(subset["list_confirmed"])

        empty = classify_share_visibility({"confirmed": True}, 1, 0, total_count=19, terminal_page_seen=True)
        self.assertEqual(empty["classification"], "no-visible-share-users")
        self.assertEqual(empty["decision_reason"], "terminal-without-visible-share-users")

        cached = classify_share_visibility({"confirmed": True, "entry_method": "feed-node"}, 0, 0)
        self.assertEqual(cached["classification"], "entry-or-query-failed")
        self.assertEqual(cached["decision_reason"], "share-list-confirmed-query-not-triggered")

        no_entry = classify_share_visibility(
            {"confirmed": False, "failure_reason": "no-visible-share-count-entry"}, 0, 0
        )
        self.assertEqual(no_entry["classification"], "no-share-entry")

        not_confirmed = classify_share_visibility(
            {"confirmed": False, "failure_reason": "share-list-not-confirmed"}, 0, 0
        )
        self.assertEqual(not_confirmed["classification"], "entry-or-query-failed")

    def test_target_verification_by_id_and_url_token(self):
        posts = [
            {
                "post_id": "1616705546476602",
                "url": "https://m.facebook.com/story.php?story_fbid=898880469955528&id=100044115173545",
            }
        ]
        self.assertEqual(
            verify_target_posts(posts, expected_post_id="1616705546476602")["matched_by"],
            "post_id",
        )
        self.assertEqual(
            verify_target_posts(
                posts,
                expected_url="https://m.facebook.com/story.php?story_fbid=898880469955528&id=100044115173545",
            )["matched_by"],
            "url-token",
        )
        self.assertEqual(target_url_tokens("https://www.facebook.com/reel/1383444270262441/"), ["1383444270262441"])
        self.assertEqual(target_url_tokens("https://www.facebook.com/example/posts/123456789/"), ["123456789"])
        self.assertEqual(target_url_tokens("https://www.facebook.com/watch/videos/987654321/"), ["987654321"])
        self.assertEqual(target_url_tokens("https://m.facebook.com/story.php"), [])
        self.assertEqual(verify_target_posts(posts, expected_post_id="999")["status"], "not-matched")

    def test_target_verification_requires_exact_numeric_url_component(self):
        posts = [
            {
                "post_id": "9123",
                "url": "https://www.facebook.com/example/posts/9123/",
            }
        ]

        verified = verify_target_posts(
            posts,
            expected_url="https://www.facebook.com/example/posts/123/",
        )

        self.assertEqual(verified["status"], "not-matched")

    def test_target_verification_requires_facebook_host_for_url_components(self):
        external = verify_target_posts(
            [{"post_id": "999", "url": "https://example.invalid/posts/123/"}],
            expected_url="https://www.facebook.com/page/posts/123/",
        )
        self.assertEqual(external["status"], "not-matched")

        aliases = verify_target_posts(
            [{"post_id": "123", "url": "https://m.facebook.com/page/posts/123/"}],
            expected_url="https://www.facebook.com/page/posts/123/",
        )
        self.assertEqual(aliases["status"], "matched")

    def test_target_verification_requires_exact_opaque_url_component(self):
        posts = [
            {
                "post_id": "9123",
                "url": "https://www.facebook.com/example/posts/pfbidABCDEF/",
            }
        ]

        verified = verify_target_posts(
            posts,
            expected_url="https://www.facebook.com/example/posts/pfbidABC/",
        )

        self.assertEqual(verified["status"], "not-matched")

    def test_target_verification_rejects_url_token_when_explicit_id_differs(self):
        posts = [
            {
                "post_id": "1476320540987565",
                "url": "https://www.facebook.com/example/posts/1476320540987565/",
            }
        ]
        verified = verify_target_posts(
            posts,
            expected_post_id="122122050735339946",
            expected_url="https://www.facebook.com/example/posts/1476320540987565/",
        )
        self.assertEqual(verified["status"], "not-matched")

    def test_target_capture_ignores_unrelated_body_id_text(self):
        records = [
            (
                1,
                {
                    "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                    "status_code": 200,
                    "response_body": json.dumps({"metadata": {"id": "123"}}),
                },
            )
        ]

        verified = verify_target_capture(
            [],
            records,
            expected_post_id="123",
            expected_url="https://www.facebook.com/example/posts/123/",
        )

        self.assertEqual(verified["status"], "not-matched")

    def test_target_capture_requires_exact_numeric_body_url_component(self):
        records = [
            (
                1,
                {
                    "friendly_name": "PermalinkSectionGraphQL",
                    "status_code": 200,
                    "response_body": json.dumps(
                        {"metadata": {"url": "https://www.facebook.com/example/posts/9123/"}}
                    ),
                },
            )
        ]

        verified = verify_target_capture(
            [],
            records,
            expected_url="https://www.facebook.com/example/posts/123/",
        )

        self.assertEqual(verified["status"], "not-matched")

    def test_target_capture_never_accepts_search_result_as_open_post(self):
        posts = [{"post_id": "777", "url": "https://www.facebook.com/example/posts/777"}]
        records = [
            (10, {"friendly_name": "SearchResultsGraphQL-main_query", "status_code": 200, "response_body": '{"legacy_api_post_id":"777"}'}),
            (11, {"friendly_name": "Fb4aSearchTypeaheadQuery", "status_code": 200, "response_body": '{"legacy_api_post_id":"777"}'}),
        ]
        verified = verify_target_capture(posts, records, expected_post_id="777")
        self.assertEqual(verified["status"], "not-matched")

    def test_target_capture_accepts_non_search_navigation_after_search(self):
        story = {
            "__typename": "Story",
            "post_id": "777",
            "legacy_api_story_id": "42_777",
            "actors": [{"id": "42"}],
            "feedback": {"legacy_api_post_id": "777"},
        }
        records = [
            (10, {"friendly_name": "SearchResultsGraphQL-main_query", "status_code": 200, "response_body": '{"legacy_api_post_id":"777"}'}),
            (12, {"friendly_name": "FBVideoHomeExternalPostLinkQuery", "status_code": 200, "response_body": json.dumps({"data": {"node": story}})}),
        ]
        verified = verify_target_capture([], records, expected_post_id="777")
        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_line"], 12)

    def test_target_capture_rejects_external_link_author_fallback_for_wrong_explicit_id(self):
        expected_url = "https://www.facebook.com/100058288982749/posts/1476320540987565/"
        body = json.dumps(
            {
                "data": {
                    "node": {
                        "__typename": "Story",
                        "post_id": "1476320540987565",
                        "actors": [{"id": "100058288982749"}],
                    }
                }
            }
        )
        verified = verify_target_capture(
            [],
            [(1, {"friendly_name": "FBVideoHomeExternalPostLinkQuery", "status_code": 200, "response_body": body})],
            expected_post_id="122122050735339946",
            expected_url=expected_url,
        )
        self.assertEqual(verified["status"], "not-matched")

    def test_external_post_link_author_fallback_rejects_conflicts_in_either_order(self):
        target_url = "https://www.facebook.com/100/posts/123/"

        def story(author_id):
            return {
                "__typename": "Story",
                "post_id": "123",
                "actors": [{"id": author_id}],
                "feedback": {"legacy_api_post_id": "123"},
            }

        for stories in ((story("100"), story("200")), (story("200"), story("100"))):
            with self.subTest(stories=stories):
                verified = verify_target_capture(
                    [],
                    [
                        (
                            1,
                            {
                                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                                "status_code": 200,
                                "response_body": json.dumps({"data": {"stories": list(stories)}}),
                            },
                        )
                    ],
                    expected_url=target_url,
                )
                self.assertEqual(verified["status"], "not-matched")

        same_author = verify_target_capture(
            [],
            [
                (
                    2,
                    {
                        "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                        "status_code": 200,
                        "response_body": json.dumps(
                            {"data": {"stories": [story("100"), story("100")]}}
                        ),
                    },
                )
            ],
            expected_url=target_url,
        )
        self.assertEqual(same_author["status"], "matched")
        self.assertEqual(same_author["matched_by"], "external-post-link-author")

    def test_target_capture_rejects_url_token_fallback_when_explicit_id_differs(self):
        expected_url = "https://www.facebook.com/example/posts/1476320540987565/"
        verified = verify_target_capture(
            [],
            [
                (
                    1,
                    {
                        "friendly_name": "PermalinkSectionGraphQL",
                        "status_code": 200,
                        "response_body": "navigation token 1476320540987565",
                    },
                )
            ],
            expected_post_id="122122050735339946",
            expected_url=expected_url,
        )
        self.assertEqual(verified["status"], "not-matched")

    def test_pfbid_navigation_accepts_rotated_canonical_token_from_exact_request(self):
        requested_url = "https://www.facebook.com/example/posts/pfbidOldToken123"
        response = {
            "data": {
                "video_home_deeplink": {
                    "__typename": "Story",
                    "post_id": "123456789",
                    "legacy_api_story_id": "987654321_123456789",
                    "actors": [{"id": "987654321"}],
                    "feedback": {"legacy_api_post_id": "123456789"},
                    "wwwURL": "https://www.facebook.com/987654321/posts/pfbidNewToken456",
                }
            }
        }
        records = [
            (
                12,
                {
                    "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                    "status_code": 200,
                    "request_navigation_url_hash": target_url_digest(requested_url),
                    "response_body": json.dumps(response),
                },
            )
        ]

        verified = verify_target_capture([], records, expected_url=requested_url)

        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "pfbid-external-post-link-navigation")
        self.assertEqual(verified["matched_post_id"], "123456789")
        self.assertEqual(verified["resolved_author_id"], "987654321")
        self.assertEqual(verified["matched_legacy_api_story_id"], "987654321_123456789")

    def test_pfbid_navigation_rejects_unbound_ambiguous_or_wrong_post_responses(self):
        requested_url = "https://www.facebook.com/example/posts/pfbidOldToken123"

        def story(post_id, author_id):
            return {
                "__typename": "Story",
                "post_id": post_id,
                "legacy_api_story_id": f"{author_id}_{post_id}",
                "actors": [{"id": author_id}],
                "feedback": {"legacy_api_post_id": post_id},
            }

        cases = {
            "missing-hash": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "response_body": json.dumps({"data": {"node": story("1", "11")}}),
            },
            "wrong-hash": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "request_navigation_url_hash": target_url_digest(requested_url + "?wrong=1"),
                "response_body": json.dumps({"data": {"node": story("1", "11")}}),
            },
            "feed-query": {
                "friendly_name": "fresh_feed_new_data_fetch",
                "status_code": 200,
                "request_navigation_url_hash": target_url_digest(requested_url),
                "response_body": json.dumps({"data": {"node": story("1", "11")}}),
            },
            "multiple-posts": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "request_navigation_url_hash": target_url_digest(requested_url),
                "response_body": json.dumps({"data": [story("1", "11"), story("2", "22")]}),
            },
        }

        for name, record in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    verify_target_capture([], [(20, record)], expected_url=requested_url)["status"],
                    "not-matched",
                )

        exact = dict(cases["missing-hash"])
        exact["request_navigation_url_hash"] = target_url_digest(requested_url)
        self.assertEqual(
            verify_target_capture(
                [],
                [(21, exact)],
                expected_post_id="999",
                expected_url=requested_url,
            )["status"],
            "not-matched",
        )

    def test_share_short_url_is_navigation_only_and_resolves_unique_post_owner(self):
        share_url = "https://www.facebook.com/share/p/1JQTGGqbeA/"
        self.assertTrue(is_facebook_share_navigation_url(share_url))
        self.assertEqual(target_url_tokens(share_url), [])
        response = {
            "data": {
                "node": {
                    "__typename": "Story",
                    "post_id": "1517562760400198",
                    "legacy_api_story_id": "100064394663784_1517562760400198",
                    "actors": [{"id": "100064394663784", "name": "Xi's Moments"}],
                    "feedback": {"legacy_api_post_id": "1517562760400198"},
                }
            }
        }
        records = [
            (9, {"friendly_name": "SearchResultsGraphQL-main_query", "status_code": 200, "response_body": json.dumps(response)}),
            (10, {"friendly_name": "PermalinkSectionGraphQL", "status_code": 200, "response_body": json.dumps(response)}),
        ]
        verified = verify_target_capture([], records, expected_url=share_url)
        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "share-navigation-graphql")
        self.assertEqual(verified["matched_post_id"], "1517562760400198")
        self.assertEqual(verified["expected_url_author_id"], "100064394663784")
        self.assertEqual(verified["matched_line"], 10)

    def test_share_short_url_rejects_conflicting_navigation_posts_and_expected_id_mismatch(self):
        share_url = "https://www.facebook.com/share/p/abc123/"
        response = {
            "data": [
                {"__typename": "Story", "post_id": "1", "actors": [{"id": "11"}], "feedback": {"legacy_api_post_id": "1"}},
                {"__typename": "Story", "post_id": "2", "actors": [{"id": "22"}], "feedback": {"legacy_api_post_id": "2"}},
            ]
        }
        records = [(20, {"friendly_name": "PermalinkSectionGraphQL", "status_code": 200, "response_body": json.dumps(response)})]
        self.assertEqual(verify_target_capture([], records, expected_url=share_url)["status"], "not-matched")
        single = {"data": {"node": {"__typename": "Story", "post_id": "1", "actors": [{"id": "11"}], "feedback": {"legacy_api_post_id": "1"}}}}
        single_records = [(21, {"friendly_name": "PermalinkSectionGraphQL", "status_code": 200, "response_body": json.dumps(single)})]
        self.assertEqual(verify_target_capture([], single_records, expected_post_id="999", expected_url=share_url)["status"], "not-matched")

    def test_share_post_url_accepts_facebook_555_resolver_then_matching_static_story(self):
        share_url = "https://www.facebook.com/share/p/1DVLR4FNo6/"
        relay_id = base64.b64encode(b"S:_I100063690504939:1691321539667513:1691321539667513").decode("ascii")
        resolver = {"data": {"urlResolver": {"__typename": "Story", "id": relay_id}}}
        story = {
            "data": {
                "node": {
                    "__typename": "Story",
                    "id": relay_id,
                    "actors": [{"id": "100063690504939"}],
                    "feedback": {"legacy_api_post_id": "1691321539667513"},
                }
            }
        }
        records = [
            (10, {"friendly_name": "GetURLResolverInfo", "status_code": 200, "request_navigation_url_hash": target_url_digest(share_url), "response_body": json.dumps(resolver)}),
            (11, {"friendly_name": "StaticGraphQlStoryFeedbackAtConnectionQuery", "status_code": 200, "response_body": json.dumps(story)}),
        ]

        verified = verify_target_capture([], records, expected_url=share_url)

        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "share-url-resolver-story")
        self.assertEqual(verified["matched_post_id"], "1691321539667513")
        self.assertEqual(verified["expected_url_author_id"], "100063690504939")

    def test_share_post_url_rejects_mismatched_resolver_and_static_story(self):
        share_url = "https://www.facebook.com/share/p/abc123/"
        resolver_id = base64.b64encode(b"S:_I11:111:111").decode("ascii")
        other_id = base64.b64encode(b"S:_I22:222:222").decode("ascii")
        records = [
            (10, {"friendly_name": "GetURLResolverInfo", "status_code": 200, "request_navigation_url_hash": target_url_digest(share_url), "response_body": json.dumps({"data": {"urlResolver": {"__typename": "Story", "id": resolver_id}}})}),
            (11, {"friendly_name": "StaticGraphQlStoryFeedbackAtConnectionQuery", "status_code": 200, "response_body": json.dumps({"data": {"node": {"__typename": "Story", "id": other_id, "actors": [{"id": "22"}], "feedback": {"legacy_api_post_id": "222"}}}})}),
        ]

        self.assertEqual(verify_target_capture([], records, expected_url=share_url)["status"], "not-matched")

    def test_share_reel_url_accepts_only_story_matching_fresh_seed_video(self):
        share_url = "https://www.facebook.com/share/r/1Dg59X6ysJ/"
        relay_id = base64.b64encode(b"S:_I100064850801279:1523219529849726:1523219529849726").decode("ascii")
        response = {
            "data": {
                "viewer": {
                    "video_feed_unit_feed": {
                        "edges": [
                            {"node": {"video_media_attachment_info": [{"media": {"id": "2103292653592007", "container_story": {"id": relay_id}}}]}}
                        ]
                    }
                }
            }
        }
        records = [
            (20, {"friendly_name": "ReelsVDDChainingQuery", "status_code": 200, "request_seed_video_id": "2103292653592007", "response_body": json.dumps(response)})
        ]

        verified = verify_target_capture([], records, expected_url=share_url)

        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "share-reel-seed-story")
        self.assertEqual(verified["matched_post_id"], "1523219529849726")
        self.assertEqual(verified["expected_url_author_id"], "100064850801279")

    def test_share_reel_url_rejects_missing_or_wrong_seed_video(self):
        share_url = "https://www.facebook.com/share/r/abc123/"
        relay_id = base64.b64encode(b"S:_I11:111:111").decode("ascii")
        body = json.dumps({"data": {"viewer": {"video_feed_unit_feed": {"edges": [{"node": {"video_media_attachment_info": [{"media": {"id": "99", "container_story": {"id": relay_id}}}]}}]}}}})
        missing_seed = [(20, {"friendly_name": "ReelsVDDChainingQuery", "status_code": 200, "response_body": body})]
        wrong_seed = [(20, {"friendly_name": "ReelsVDDChainingQuery", "status_code": 200, "request_seed_video_id": "88", "response_body": body})]
        self.assertEqual(verify_target_capture([], missing_seed, expected_url=share_url)["status"], "not-matched")
        self.assertEqual(verify_target_capture([], wrong_seed, expected_url=share_url)["status"], "not-matched")

    def test_canonical_reel_rejects_generic_feedback_source(self):
        reel_url = "https://www.facebook.com/reel/1054985397317048/"
        records = [
            (
                10,
                {
                    "friendly_name": "FBShortsMediaPrefetchQuery",
                    "status_code": 200,
                    "response_body": '{"data":{"video":{"id":"1054985397317048"}}}',
                },
            ),
            (
                20,
                {
                    "friendly_name": "FeedbackReactorsGraphService",
                    "status_code": 200,
                    "request_feedback_post_id": "122110462113369502",
                    "response_body": "{}",
                },
            ),
        ]

        verified = verify_target_capture([], records, expected_url=reel_url)

        self.assertEqual(verified["status"], "not-matched")

        explicit = verify_target_capture(
            [], records, expected_post_id="122110462113369502", expected_url=reel_url
        )
        self.assertEqual(explicit["status"], "not-matched")
        wrong_explicit = verify_target_capture(
            [], records, expected_post_id="999999999999999999", expected_url=reel_url
        )
        self.assertEqual(wrong_explicit["status"], "not-matched")

    def test_reel_explicit_source_accepts_verified_url_token_before_feedback(self):
        reel_url = "https://www.facebook.com/reel/1054985397317048/"
        records = [
            (18, {"status_code": 200, "friendly_name": "FBShortsInlineCommentsQuery",
                  "response_body": json.dumps({"data": {"video": {"id": "1054985397317048"}}})}),
            (33, {"status_code": 200, "friendly_name": "FbShortsIGMediaReactorsQuery",
                  "request_feedback_post_id": "122122050735339946",
                  "response_body": json.dumps({"data": {"feedback": {"id": "122122050735339946"}}})}),
        ]
        verified = verify_target_capture([], records, expected_post_id="122122050735339946", expected_url=reel_url)
        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "reel-video-token-and-feedback-source")

    def test_reel_explicit_source_keeps_first_token_evidence_with_real_response_shape(self):
        reel_url = "https://www.facebook.com/reel/1054985397317048/"
        reel_video_id = "1054985397317048"
        feedback_post_id = "122122050735339946"
        play_count_response = {
            "data": {
                "node": {
                    "__typename": "Story",
                    "strong_id__": "1032209144229399604",
                    "cache_id": "1032209144229399604",
                    "id": "UzpfSTYxNTkwMTk4NDA2MDM4OlZLOjEwNTQ5ODUzOTczMTcwNDg=",
                    "video_media_attachment": [
                        {
                            "media": {
                                "__typename": "Video",
                                "strong_id__": reel_video_id,
                                "id": reel_video_id,
                                "play_count": 476,
                            }
                        }
                    ],
                }
            }
        }
        records = [
            (
                18,
                {
                    "status_code": 200,
                    "friendly_name": "FBShortsInlineCommentsQuery",
                    "response_body": json.dumps(
                        {
                            "data": {
                                "video": {
                                    "id": reel_video_id,
                                    "creation_story": {
                                        "cache_id": "-3259081084035758280",
                                        "feedback_context": {
                                            "interesting_top_level_comments": []
                                        },
                                    },
                                }
                            },
                            "extensions": {
                                "server_metadata": {"is_final": True}
                            },
                        }
                    ),
                },
            ),
            (
                30,
                {
                    "status_code": 200,
                    "friendly_name": "FbShortsReactionsCountQuery",
                    "request_feedback_post_id": feedback_post_id,
                    "response_body": json.dumps(
                        {
                            "data": {
                                "feedback": {
                                    "id": base64.b64encode(
                                        f"feedback:{feedback_post_id}".encode("ascii")
                                    ).decode("ascii"),
                                    "unified_reactors": {"count": 2},
                                }
                            }
                        }
                    ),
                },
            ),
            (
                31,
                {
                    "status_code": 200,
                    "friendly_name": "FbShortsPlayCountInfoQuery",
                    "response_body": json.dumps(play_count_response),
                },
            ),
            (
                33,
                {
                    "status_code": 200,
                    "friendly_name": "FbShortsIGMediaReactorsQuery",
                    "request_feedback_post_id": feedback_post_id,
                    "response_body": json.dumps(
                        {
                            "data": {
                                "feedback": {
                                    "id": base64.b64encode(
                                        f"feedback:{feedback_post_id}".encode("ascii")
                                    ).decode("ascii"),
                                    "unified_reactors": {
                                        "edges": [],
                                        "page_info": {"has_next_page": False},
                                    },
                                    "reactors": {"count": 2},
                                    "legacy_api_post_id": feedback_post_id,
                                }
                            }
                        }
                    ),
                },
            ),
            (
                34,
                {
                    "status_code": 200,
                    "friendly_name": "FbShortsPlayCountInfoQuery",
                    "response_body": json.dumps(play_count_response),
                },
            ),
        ]

        verified = verify_target_capture(
            [],
            records,
            expected_post_id=feedback_post_id,
            expected_url=reel_url,
        )

        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "reel-video-token-and-feedback-source")
        self.assertEqual(verified["matched_line"], 18)
        self.assertEqual(verified["matched_post_id"], feedback_post_id)

    def test_canonical_reel_does_not_choose_ambiguous_feedback_source(self):
        reel_url = "https://www.facebook.com/reel/1054985397317048/"
        records = [
            (10, {"friendly_name": "FBShortsMediaPrefetchQuery", "status_code": 200, "response_body": '{"data":{"video":{"id":"1054985397317048"}}}'}),
            (20, {"friendly_name": "FeedbackReactorsGraphService", "status_code": 200, "request_feedback_post_id": "111", "response_body": "{}"}),
            (21, {"friendly_name": "FeedbackReactorsGraphService", "status_code": 200, "request_feedback_post_id": "222", "response_body": "{}"}),
        ]

        verified = verify_target_capture([], records, expected_url=reel_url)

        self.assertEqual(verified["status"], "not-matched")

    def test_canonical_reel_rejects_unstructured_video_token(self):
        reel_url = "https://www.facebook.com/reel/1054985397317048/"
        records = [
            (10, {"friendly_name": "FBShortsMediaPrefetchQuery", "status_code": 200, "response_body": "1054985397317048"}),
            (20, {"friendly_name": "FeedbackReactorsGraphService", "status_code": 200, "request_feedback_post_id": "122110462113369502", "response_body": "{}"}),
        ]

        verified = verify_target_capture([], records, expected_url=reel_url)

        self.assertEqual(verified["status"], "not-matched")

    def test_safe_navigation_context_hashes_url_and_keeps_bounded_public_seed(self):
        share_url = "https://www.facebook.com/share/p/abc123/"
        context = _public_navigation_context_from_values(
            {"variables": json.dumps({"url": share_url, "seed_video_id": "123456"})}
        )
        self.assertEqual(context["request_navigation_url_hash"], target_url_digest(share_url))
        self.assertEqual(context["request_seed_video_id"], "123456")
        self.assertNotIn(share_url, json.dumps(context))

        fallback = _public_navigation_context_from_values(
            {"variables": json.dumps({"seed_video_id": None, "video_feed_context_data": {"video_id_to_filter": "654321"}})}
        )
        self.assertEqual(fallback["request_seed_video_id"], "654321")

    def test_target_verification_falls_back_to_successful_graphql_response(self):
        story = {
            "__typename": "Story",
            "post_id": "1616705546476602",
            "legacy_api_story_id": "100044115173545_1616705546476602",
            "actors": [{"id": "100044115173545"}],
            "feedback": {"legacy_api_post_id": "1616705546476602"},
        }
        records = [
            (
                7,
                {
                    "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                    "status_code": 200,
                    "response_body": json.dumps({"data": {"node": story}}),
                },
            )
        ]
        verified = verify_target_capture(
            [],
            records,
            expected_post_id="1616705546476602",
            expected_url="https://m.facebook.com/story.php?story_fbid=898880469955528&id=100044115173545",
        )
        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "graphql-response-post-id")
        self.assertEqual(verified["matched_friendly_name"], "FBVideoHomeExternalPostLinkQuery")

    def test_pfbid_vanity_post_uses_exact_navigation_query_identity(self):
        target_url = "https://www.facebook.com/vanity-page/posts/pfbid021Qabc123"
        post_id = "122106880826437311"
        author_id = "61563119350848"
        legacy_story_id = f"{author_id}_{post_id}"
        response = {
            "data": {
                "node": {
                    "__typename": "Story",
                    "post_id": post_id,
                    "legacy_api_story_id": legacy_story_id,
                    "actors": [{"id": author_id}],
                    "feedback": {"legacy_api_post_id": post_id},
                    "wwwURL": "https://www.facebook.com/vanity-page/posts/pfbid0rotatedToken",
                }
            }
        }
        records = [
            (
                30,
                {
                    "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                    "status_code": 200,
                    "request_navigation_url_hash": target_url_digest(target_url),
                    "response_body": json.dumps(response),
                },
            )
        ]

        verified = verify_target_capture(
            [], records, expected_post_id=post_id, expected_url=target_url
        )

        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "pfbid-external-post-link-navigation")
        self.assertEqual(verified["matched_post_id"], post_id)
        self.assertEqual(verified["resolved_author_id"], author_id)
        self.assertEqual(verified["matched_legacy_api_story_id"], legacy_story_id)
        self.assertEqual(
            verify_target_capture(
                [], records, expected_post_id="999999999999999999", expected_url=target_url
            )["status"],
            "not-matched",
        )

    def test_pfbid_vanity_post_rejects_weak_or_ambiguous_evidence(self):
        target_url = "https://www.facebook.com/vanity-page/posts/pfbid021Qabc123"
        post_id = "122106880826437311"
        author_id = "61563119350848"
        base_story = {
            "__typename": "Story",
            "post_id": post_id,
            "legacy_api_story_id": f"{author_id}_{post_id}",
            "actors": [{"id": author_id}],
            "feedback": {"legacy_api_post_id": post_id},
        }

        def record(friendly_name, response, navigation_hash="present"):
            value = {
                "friendly_name": friendly_name,
                "status_code": 200,
                "response_body": json.dumps(response),
            }
            if navigation_hash == "present":
                value["request_navigation_url_hash"] = target_url_digest(target_url)
            elif navigation_hash == "wrong":
                value["request_navigation_url_hash"] = target_url_digest(
                    "https://www.facebook.com/vanity-page/posts/pfbid0other"
                )
            return [(31, value)]

        cases = {
            "missing navigation hash": record(
                "FBVideoHomeExternalPostLinkQuery", {"data": {"node": base_story}}, "missing"
            ),
            "wrong navigation hash": record(
                "FBVideoHomeExternalPostLinkQuery", {"data": {"node": base_story}}, "wrong"
            ),
            "search query": record(
                "SearchResultsGraphQL-main_query", {"data": {"node": base_story}}
            ),
            "feed query": record("FeedUnitQuery", {"data": {"node": base_story}}),
            "preload query": record(
                "FBVideoHomeExternalPostLinkQueryPreload", {"data": {"node": base_story}}
            ),
            "multiple posts": record(
                "FBVideoHomeExternalPostLinkQuery",
                {
                    "data": {
                        "stories": [
                            base_story,
                            {
                                **base_story,
                                "post_id": "999999999999999999",
                                "legacy_api_story_id": f"{author_id}_999999999999999999",
                                "feedback": {"legacy_api_post_id": "999999999999999999"},
                            },
                        ]
                    }
                },
            ),
            "multiple authors": record(
                "FBVideoHomeExternalPostLinkQuery",
                {
                    "data": {
                        "stories": [
                            base_story,
                            {
                                **base_story,
                                "actors": [{"id": "700000000000000"}],
                                "legacy_api_story_id": "700000000000000_122106880826437311",
                            },
                        ]
                    }
                },
            ),
        }

        for label, records in cases.items():
            with self.subTest(label=label):
                verified = verify_target_capture(
                    [], records, expected_post_id=post_id, expected_url=target_url
                )
                self.assertEqual(verified["status"], "not-matched")

    def test_target_verification_ignores_failed_response(self):
        records = [(1, {"friendly_name": "Query", "status_code": 500, "response_body": "999"})]
        self.assertEqual(verify_target_capture([], records, expected_post_id="999")["status"], "not-matched")

    def test_display_count_and_subset_status(self):
        self.assertEqual(parse_display_count("97次分享"), 97)
        self.assertEqual(parse_display_count("9,723"), 9723)
        self.assertEqual(parse_display_count("1.5K shares"), 1500)
        self.assertEqual(
            diagnostic_visible_count({"candidate": {"label": "2.3万次分享"}}),
            23000,
        )
        self.assertEqual(
            enumeration_status(2, 15, total_count=97, terminal_page_seen=True),
            "visible-users-enumerated-subset",
        )
        self.assertEqual(
            enumeration_status(2, 3, "friends-only-names", total_count=100),
            "friends-only-visible-users-enumerated",
        )

    def test_target_post_reported_count_uses_exact_source_only(self):
        posts = [
            {"post_id": "wrong", "reaction_count": 9999, "share_count": 88},
            {"post_id": "target", "reaction_count": 3217, "share_count": 10},
        ]
        self.assertEqual(target_post_reported_count(posts, "target", "reaction_count"), 3217)
        self.assertEqual(target_post_reported_count(posts, "target", "share_count"), 10)
        self.assertIsNone(target_post_reported_count(posts, "missing", "reaction_count"))

    def test_enumeration_status_distinguishes_visibility_from_missing_query(self):
        counts = {
            "FeedbackReactorsGraphService": 2,
            "FeedbackReactorsGraphService_Pagination": 1,
            "OtherQuery": 9,
        }
        self.assertEqual(query_family_count(counts, "FeedbackReactorsGraphService"), 3)
        self.assertEqual(enumeration_status(0, 0), "query-not-triggered")
        self.assertEqual(enumeration_status(3, 0), "query-triggered-no-visible-users")
        self.assertEqual(
            enumeration_status(3, 0, "friends-only-names"),
            "query-triggered-friends-only-visibility",
        )
        self.assertEqual(enumeration_status(3, 2), "visible-users-enumerated")

    def test_extracts_edges_nodes_and_friend_reactors(self):
        records = [
            reactor_record(
                10,
                {"feedback_id": "ZmVlZGJhY2s6MTIz", "reaction_id": "like"},
                {
                    "data": {
                        "node": {
                            "reactors": {
                                "edges": [{"node": {"id": "1", "name": "Edge User"}}],
                            },
                            "important_reactors": {
                                "nodes": [{"id": "2", "name": "Important User"}],
                            },
                            "friend_reactors": [
                                {"id": "3", "name": "Friend User"},
                            ],
                        }
                    }
                },
            )
        ]

        reactors, _ = extract_reactors(records, redact=False)

        self.assertEqual({row["user_id"] for row in reactors}, {"1", "2", "3"})
        self.assertEqual(
            {row["source_field"] for row in reactors},
            {"reactors", "important_reactors", "friend_reactors"},
        )
        self.assertTrue(all(row["reaction_id"] == "like" for row in reactors))

    def test_extracts_nested_user_and_nodes_connection(self):
        records = [
            reactor_record(
                11,
                {"feedback_id": "ZmVlZGJhY2s6NDU2"},
                {
                    "data": {
                        "node": {
                            "reactors": {
                                "nodes": [
                                    {"actor": {"id": "4", "name": "Nested User"}},
                                ],
                                "page_info": {
                                    "has_next_page": True,
                                    "end_cursor": "cursor-1",
                                },
                            }
                        }
                    }
                },
            )
        ]

        reactors, pages = extract_reactors(records, redact=False)

        self.assertEqual(reactors[0]["user_id"], "4")
        self.assertEqual(reactors[0]["user_name"], "Nested User")
        self.assertEqual(reactors[0]["source_field"], "reactors")
        self.assertEqual(pages[0]["node_count"], 1)
        self.assertEqual(pages[0]["source_post_id"], "456")
        self.assertTrue(pages[0]["has_next_page"])
        self.assertTrue(pages[0]["end_cursor_present"])

    def test_deduplicates_paged_rows_and_prefers_specific_reaction(self):
        records = [
            reactor_record(
                20,
                {"feedback_id": "ZmVlZGJhY2s6Nzg5"},
                {
                    "data": {
                        "node": {
                            "reactors": {
                                "edges": [
                                    {"node": {"id": "5", "name": "Paged User"}},
                                ],
                                "page_info": {
                                    "has_next_page": True,
                                    "end_cursor": "cursor-1",
                                },
                            }
                        }
                    }
                },
            ),
            reactor_record(
                21,
                {"feedback_id": "ZmVlZGJhY2s6Nzg5", "reaction_id": "love"},
                {
                    "data": {
                        "node": {
                            "reactors": {
                                "edges": [
                                    {"node": {"id": "5", "name": "Paged User"}},
                                ],
                                "page_info": {
                                    "has_next_page": False,
                                    "end_cursor": None,
                                },
                            }
                        }
                    }
                },
            ),
        ]

        reactors, pages = extract_reactors(records, redact=False)

        self.assertEqual(len(reactors), 1)
        self.assertEqual(reactors[0]["user_id"], "5")
        self.assertEqual(reactors[0]["reaction_id"], "love")
        self.assertEqual(reactors[0]["source_post_id"], "789")
        self.assertEqual(len(pages), 2)
        self.assertEqual([page["line"] for page in pages], [20, 21])

    def test_accepts_paginated_feedback_reactors_query_name(self):
        records = [
            paginated_reactor_record(
                30,
                {"feedback_id": "ZmVlZGJhY2s6OTk5"},
                {
                    "data": {
                        "node": {
                            "reactors": {
                                "edges": [
                                    {"node": {"id": "9", "name": "Pagination User"}},
                                ],
                                "page_info": {
                                    "has_next_page": False,
                                    "end_cursor": None,
                                },
                            }
                        }
                    }
                },
            )
        ]

        reactors, pages = extract_reactors(records, redact=False)

        self.assertEqual([row["user_name"] for row in reactors], ["Pagination User"])
        self.assertEqual(pages[0]["line"], 30)

    def test_accepts_paginated_reshares_query_name(self):
        records = [
            paginated_reshare_record(
                31,
                {
                    "feedback_id": "ZmVlZGJhY2s6MTAwMQ==",
                    "original_content_id": "9001",
                },
                {
                    "data": {
                        "node": {
                            "reshares": {
                                "edges": [
                                    {
                                        "node": {
                                            "id": "share-1",
                                            "actors": [{"id": "42", "name": "Share User"}],
                                        }
                                    }
                                ],
                                "page_info": {"has_next_page": False, "end_cursor": "done"},
                            }
                        }
                    }
                },
            )
        ]

        shares = extract_shares(records, redact=False)

        self.assertEqual(len(shares), 1)
        self.assertEqual(shares[0]["actor_name"], "Share User")
        self.assertEqual(shares[0]["source_post_id"], "1001")
        self.assertEqual(shares[0]["original_content_id"], "9001")
        pages = extract_share_pages(records, redact=False)
        self.assertEqual(len(pages), 1)
        self.assertFalse(pages[0]["has_next_page"])
        self.assertTrue(pages[0]["end_cursor_present"])

    def test_redaction_preserves_json_and_does_not_match_sensitive_substrings(self):
        payload = r'{"is_groups_ifr":false,"url":"https:\/\/m.facebook.com\/profile?secret=value","fr":"secret","token":"secret"}'

        redacted = _redact_text(payload)
        decoded = json.loads(redacted)

        self.assertFalse(decoded["is_groups_ifr"])
        self.assertEqual(decoded["url"], "https://m.facebook.com/profile")
        self.assertEqual(decoded["fr"], "[REDACTED]")
        self.assertEqual(decoded["token"], "[REDACTED]")

    def test_reel_redaction_preserves_embedded_playlist_and_target_identity(self):
        video_id = "1054985397317048"
        playlist = ('<MPD xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                    'media="https://cdn.example/video.mp4?signature=private">'
                    '<BaseURL>https://cdn.example/</BaseURL></MPD>')
        document = {"data": {"video": {"__typename": "Video", "id": video_id,
                                     "playlist": playlist}}}
        for escaped_slashes in (False, True):
            with self.subTest(escaped_slashes=escaped_slashes):
                payload = json.dumps(document)
                if escaped_slashes:
                    payload = payload.replace("/", r"\/")
                redacted = _redact_text(payload)
                decoded = json.loads(redacted)
                self.assertEqual(decoded["data"]["video"]["playlist"],
                                 playlist.replace("?signature=private", ""))
                self.assertNotIn("private", redacted)
                self.assertEqual(_redact_text(redacted), redacted)
                records = [(1, {"friendly_name": "FBShortsMediaPrefetchQuery",
                                "status_code": 200, "response_body": redacted})]
                verified = verify_target_capture(
                    [], records, expected_url=f"https://www.facebook.com/reel/{video_id}/")
                self.assertEqual(verified["status"], "not-matched")
                wrong = verify_target_capture(
                    [], records, expected_url="https://www.facebook.com/reel/999999/")
                self.assertEqual(wrong["status"], "not-matched")

    def test_url_redaction_preserves_queryless_urls_and_stream_documents(self):
        from analyze_facebook_capture import decode_stream

        documents = [
            {"url": "https://cdn.example/video", "nested": 'href="https://example.com/path"'},
            {"url": "https://cdn.example/video?signature=private", "token": "private"},
        ]
        for escaped_slashes in (False, True):
            with self.subTest(escaped_slashes=escaped_slashes):
                payload = "\n".join(json.dumps(doc) for doc in documents)
                if escaped_slashes:
                    payload = payload.replace("/", r"\/")
                redacted = _redact_text(payload)
                decoded = decode_stream(redacted)
                self.assertEqual(len(decoded), 2)
                self.assertEqual(decoded[0], documents[0])
                self.assertEqual(decoded[1], {"url": "https://cdn.example/video", "token": "[REDACTED]"})
                self.assertNotIn("private", redacted)

    def test_extracts_partial_story_metadata_from_nested_feedback(self):
        documents = [
            {
                "data": {
                    "viewer": {
                        "news_feed": {
                            "edges": [
                                {
                                    "node": {
                                        "__typename": "PartialStory",
                                        "feedback": {
                                            "legacy_api_post_id": "1457629006413147",
                                            "owning_profile": {
                                                "id": "100064981830080",
                                                "name": "South China Morning Post",
                                            },
                                            "reactors": {"count": 1585},
                                            "reshares": {"count": 112},
                                        },
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        ]

        posts = extract_posts(documents, redact=False)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["post_id"], "1457629006413147")
        self.assertEqual(posts[0]["author_id"], "100064981830080")
        self.assertEqual(posts[0]["author_name"], "South China Morning Post")
        self.assertEqual(posts[0]["reaction_count"], 1585)
        self.assertEqual(posts[0]["share_count"], 112)

    def test_capture_context_keeps_public_feedback_reference_only(self):
        body = urlencode(
            {
                "variables": json.dumps(
                    {
                        "feedback_id": "ZmVlZGJhY2s6MTIz",
                        "original_content_id": "456",
                        "reaction_id": "like",
                        "access_token": "secret",
                    }
                )
            }
        )

        context = _public_request_context_from_body(body)

        self.assertEqual(context["request_feedback_post_id"], "123")
        self.assertEqual(context["request_original_content_id"], "456")
        self.assertEqual(context["request_reaction_id"], "like")
        self.assertNotIn("access_token", context)

    def test_source_post_id_prefers_safe_capture_context(self):
        self.assertEqual(
            record_source_post_id(
                {
                    "request_feedback_post_id": "123",
                    "request_body": urlencode({"variables": json.dumps({"feedback_id": "ZmVlZGJhY2s6OTk5"})}),
                }
            ),
            "123",
        )

    def test_feedback_post_id_requires_exact_numeric_feedback_global_id(self):
        encoded = base64.b64encode(b"feedback:122106880826437311").decode("ascii")
        self.assertEqual(feedback_post_id(encoded), "122106880826437311")
        self.assertEqual(feedback_post_id(encoded.rstrip("=")), "122106880826437311")
        self.assertEqual(feedback_post_id(encoded.replace("+", "-").replace("/", "_")), "122106880826437311")
        self.assertIsNone(feedback_post_id(base64.b64encode(b"user:122106880826437311").decode("ascii")))
        self.assertIsNone(feedback_post_id(base64.b64encode(b"feedback:not-numeric").decode("ascii")))
        self.assertIsNone(feedback_post_id(base64.b64encode(b"feedback:123:456").decode("ascii")))
        self.assertIsNone(feedback_post_id("***not-base64***"))

    def test_response_feedback_post_id_requires_one_unique_feedback_id(self):
        target = base64.b64encode(b"feedback:122106880826437311").decode("ascii")
        record = {
            "friendly_name": "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection",
            "response_body": json.dumps(
                {
                    "data": {
                        "node": {
                            "id": target,
                            "strong_id__": target,
                            "reactors": {
                                "edges": [{"node": {"id": "10001", "name": "Visible User"}}],
                                "page_info": {"has_next_page": True},
                            },
                        }
                    }
                }
            ),
        }
        self.assertEqual(response_feedback_post_id(record), "122106880826437311")
        self.assertEqual(record_source_post_id(record), "122106880826437311")

        other = base64.b64encode(b"feedback:999999").decode("ascii")
        ambiguous = dict(record)
        ambiguous["response_body"] = json.dumps(
            {"data": {"first": {"id": target}, "second": {"strong_id__": other}}}
        )
        self.assertIsNone(response_feedback_post_id(ambiguous))
        self.assertIsNone(record_source_post_id(ambiguous))

    def test_response_feedback_post_id_accepts_group_feedback_root(self):
        composite = base64.b64encode(b"feedback:1415834113749566_1415856620413982").decode("ascii")
        record = {
            "friendly_name": "FeedbackReactorsGraphService",
            "response_body": json.dumps({
                "data": {
                    "node": {
                        "__typename": "Feedback",
                        "id": composite,
                        "associated_group": {"id": "132423725423951"},
                        "reactors": {"edges": [], "page_info": {"has_next_page": False}},
                    }
                }
            }),
        }
        self.assertEqual(response_feedback_post_id(record), "1415834113749566")
        self.assertEqual(record_source_post_id(record), "1415834113749566")

    def test_source_post_id_request_precedes_response_and_wrong_response_is_not_normalized(self):
        self.assertEqual(
            record_source_post_id(
                {"request_feedback_post_id": "123", "response_feedback_post_id": "456"}
            ),
            "123",
        )
        self.assertEqual(record_source_post_id({"response_feedback_post_id": "456"}), "456")

    def test_original_content_id_prefers_safe_capture_context(self):
        self.assertEqual(
            record_original_content_id(
                {
                    "request_original_content_id": "456",
                    "request_body": urlencode(
                        {"variables": json.dumps({"original_content_id": "999"})}
                    ),
                }
            ),
            "456",
        )

    def test_progress_summary_marks_share_terminal_and_growth_fields(self):
        records = [
            paginated_reshare_record(40, {"feedback_id": "ZmVlZGJhY2s6MjAwMQ=="}, {"data": {"node": {"reshares": {"edges": [{"node": {"id": "s1", "actors": [{"id": "1", "name": "A"}]}}], "page_info": {"has_next_page": True, "end_cursor": "c1"}}}}}),
            paginated_reshare_record(41, {"feedback_id": "ZmVlZGJhY2s6MjAwMQ=="}, {"data": {"node": {"reshares": {"edges": [{"node": {"id": "s2", "actors": [{"id": "2", "name": "B"}]}}], "page_info": {"has_next_page": False, "end_cursor": "done"}}}}}),
        ]
        progress = summarize_records(records, "share")
        self.assertEqual(progress["source_post_id"], "2001")
        self.assertEqual(progress["query_records"], 2)
        self.assertEqual(progress["page_count"], 2)
        self.assertEqual(progress["user_count"], 2)
        self.assertEqual(progress["latest_line"], 41)
        self.assertTrue(progress["terminal_page_seen"])
        self.assertTrue(progress["open_page_seen"])

    def test_progress_summary_tracks_reaction_groups_independently(self):
        records = [
            paginated_reactor_record(50, {"feedback_id": "ZmVlZGJhY2s6MzAwMQ==", "reaction_id": "like"}, {"data": {"node": {"reactors": {"edges": [{"node": {"id": "10", "name": "Like User"}}], "page_info": {"has_next_page": True, "end_cursor": "like-c1"}}}}}),
            paginated_reactor_record(51, {"feedback_id": "ZmVlZGJhY2s6MzAwMQ==", "reaction_id": "love"}, {"data": {"node": {"reactors": {"edges": [{"node": {"id": "11", "name": "Love User"}}], "page_info": {"has_next_page": False, "end_cursor": None}}}}}),
        ]
        progress = summarize_records(records, "reaction")
        self.assertEqual(progress["source_post_id"], "3001")
        self.assertEqual(progress["latest_group_key"], "love")
        self.assertTrue(progress["groups"]["like"]["open_page_seen"])
        self.assertFalse(progress["groups"]["like"]["terminal_page_seen"])
        self.assertTrue(progress["groups"]["love"]["terminal_page_seen"])
        self.assertEqual(progress["groups"]["love"]["latest_line"], 51)

    def test_progress_summary_uses_all_key_for_null_reaction(self):
        records = [
            paginated_reactor_record(60, {"feedback_id": "ZmVlZGJhY2s6NDAwMQ=="}, {"data": {"node": {"reactors": {"edges": [], "page_info": {"has_next_page": False, "end_cursor": None}}}}}),
        ]
        progress = summarize_records(records, "reaction")
        self.assertEqual(progress["latest_group_key"], NULL_REACTION_KEY)
        self.assertTrue(progress["groups"][NULL_REACTION_KEY]["terminal_page_seen"])

    def test_progress_summary_can_pin_exact_source_post(self):
        records = [
            paginated_reactor_record(70, {"feedback_id": "ZmVlZGJhY2s6NzAwMQ=="}, {"data": {"node": {"reactors": {"edges": [{"node": {"id": "a", "name": "Target User"}}], "page_info": {"has_next_page": False}}}}}),
            paginated_reactor_record(71, {"feedback_id": "ZmVlZGJhY2s6NzAwMg=="}, {"data": {"node": {"reactors": {"edges": [{"node": {"id": "b", "name": "Wrong User"}}], "page_info": {"has_next_page": False}}}}}),
        ]
        progress = summarize_records(records, "reaction", "7001")
        self.assertEqual(progress["source_post_id"], "7001")
        self.assertEqual(progress["query_records"], 1)
        self.assertEqual(progress["user_count"], 1)
        self.assertEqual(progress["observed_source_post_ids"], ["7001", "7002"])

    def test_progress_window_exposes_fresh_fallback_mismatch(self):
        target = paginated_reactor_record(129, {"feedback_id": "ZmVlZGJhY2s6MTUxMTE3NDg1NzY5MDkyNg=="}, {"data": {"node": {"reactors": {"edges": [], "page_info": {"has_next_page": False}}}}})
        wrong = paginated_reactor_record(130, {"feedback_id": "ZmVlZGJhY2s6MTIyMjcyNjIzNjQ0MTQ0NTY4"}, {"data": {"node": {"reactors": {"edges": [{"node": {"id": "wrong", "name": "Wrong Post User"}}], "page_info": {"has_next_page": True}}}}})
        fresh_records = [wrong]
        progress = summarize_records(fresh_records, "reaction")
        self.assertEqual(progress["source_post_id"], "122272623644144568")
        self.assertEqual(progress["observed_source_post_ids"], ["122272623644144568"])
        self.assertEqual(progress["user_count"], 1)

    def test_iter_records_supports_min_line_window(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.jsonl"
            path.write_text("\n".join(json.dumps({"n": n}) for n in range(1, 6)) + "\n", encoding="utf-8")
            records, errors = iter_records(path, 10, min_line=4)
            self.assertEqual(errors, 0)
            self.assertEqual([line for line, _ in records], [4, 5])

    def test_reaction_page_group_summary_distinguishes_open_and_terminal_groups(self):
        summary = summarize_reaction_page_groups(
            [
                {"reaction_id": "like", "node_count": 25, "edge_count": 25, "has_next_page": True},
                {"reaction_id": "love", "node_count": 14, "edge_count": 14, "has_next_page": False},
                {"reaction_id": None, "node_count": 10, "edge_count": 10, "has_next_page": False},
            ]
        )
        self.assertEqual(summary["open_groups"], ["like"])
        self.assertEqual(summary["terminal_groups"], ["__ALL__", "love"])
        self.assertEqual(summary["open_group_count"], 1)
        self.assertEqual(summary["terminal_group_count"], 2)
        self.assertFalse(summary["all_observed_groups_terminal"])
        self.assertEqual(summary["groups"]["like"]["node_count"], 25)

    def test_reaction_integrity_distinguishes_terminal_open_tail_rate_limit_and_duplicates(self):
        terminal_records = [
            paginated_reactor_record(
                1,
                {"feedback_id": "ZmVlZGJhY2s6MTIz"},
                {"data": {"node": {"reactors": {"edges": [
                    {"node": {"id": "u1", "name": "One"}},
                    {"node": {"id": "u2", "name": "Two"}},
                ], "page_info": {"has_next_page": True, "end_cursor": "a"}}}}},
            ),
            paginated_reactor_record(
                2,
                {"feedback_id": "ZmVlZGJhY2s6MTIz"},
                {"data": {"node": {"reactors": {"edges": [
                    {"node": {"id": "u2", "name": "Two"}},
                ], "page_info": {"has_next_page": False, "end_cursor": None}}}}},
            ),
        ]
        reactors, pages = extract_reactors(terminal_records, redact=False)
        terminal = summarize_reaction_integrity(terminal_records, reactors, pages, reported_total_count=2, source_post_id="123")
        self.assertEqual(terminal["integrity_status"], "complete")
        self.assertEqual(terminal["raw_reactor_rows"], 3)
        self.assertEqual(terminal["unique_reactor_identities"], 2)
        self.assertEqual(terminal["duplicate_rows"], 1)
        self.assertTrue(terminal["terminal_page_seen"])

        open_records = terminal_records[:1]
        open_reactors, open_pages = extract_reactors(open_records, redact=False)
        open_tail = summarize_reaction_integrity(open_records, open_reactors, open_pages, reported_total_count=10, source_post_id="123")
        self.assertEqual(open_tail["integrity_status"], "partial-open-tail")
        self.assertTrue(open_tail["open_tail_without_terminal"])
        self.assertTrue(open_tail["partial_data_valid"])

        throttled = list(open_records) + [(2, {
            "friendly_name": "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection",
            "request_feedback_post_id": "123",
            "response_rate_limited": True,
            "response_graphql_error_code": 1675004,
            "response_body": '{"errors":[{"code":1675004,"message":"Rate limit exceeded"}]}',
        })]
        limited = summarize_reaction_integrity(throttled, open_reactors, open_pages, reported_total_count=10, source_post_id="123")
        self.assertEqual(limited["integrity_status"], "rate-limited")
        self.assertEqual(limited["rate_limited_records"], 1)

        unrelated_restricted = list(open_records) + [(3, {
            "friendly_name": "OtherQuery",
            "response_temporary_feature_block": True,
            "response_graphql_error_code": 3252001,
            "response_api_error_code": 368,
        })]
        recovered = summarize_reaction_integrity(unrelated_restricted, open_reactors, open_pages, reported_total_count=10, source_post_id="123")
        self.assertEqual(recovered["integrity_status"], "partial-open-tail")
        self.assertEqual(recovered["temporary_feature_block_records"], 0)

        restricted = list(open_records) + [(3, {
            "friendly_name": "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection",
            "response_temporary_feature_block": True,
            "response_graphql_error_code": 3252001,
            "response_api_error_code": 368,
        })]
        session = summarize_reaction_integrity(restricted, open_reactors, open_pages, reported_total_count=10, source_post_id="123")
        self.assertEqual(session["integrity_status"], "session-restricted")
        self.assertEqual(session["temporary_feature_block_records"], 1)


if __name__ == "__main__":
    unittest.main()


class ReelNamespaceMappingTests(unittest.TestCase):
    def _story(self):
        return {"__typename": "Story", "id": base64.b64encode(b"S:_I42:VK:777").decode(),
                "post_id": "888", "actors": [{"id": "42"}],
                "feedback": {"legacy_api_post_id": "888", "owning_profile": {"id": "42"}},
                "video_media_attachment": [{"media": {"__typename": "Video", "strong_id__": "777"}}]}

    def _verify(self, stories, **kwargs):
        return verify_target_capture([], [(1, {"status_code": 200,
            "friendly_name": "WatchDeeplinkFeedUnitSectionComponentQuery",
            "response_body": json.dumps({"data": stories})})],
            expected_url="https://www.facebook.com/reel/777/", **kwargs)

    def test_same_story_video_and_feedback_namespaces_resolve(self):
        result = self._verify([self._story()])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matched_by"], "reel-video-story-mapping")
        self.assertEqual(result["matched_post_id"], "888")
        self.assertEqual(result["resolved_author_id"], "42")

    def test_namespace_mapping_rejects_missing_or_conflicting_proof(self):
        import copy
        for case in ("wrong-video", "wrong-relay", "feedback-post", "feedback-owner",
                     "missing-owner", "missing-story-post", "legacy-conflict", "actor"):
            with self.subTest(case=case):
                story = copy.deepcopy(self._story())
                if case == "wrong-video": story["video_media_attachment"][0]["media"]["strong_id__"] = "999"
                elif case == "wrong-relay": story["id"] = base64.b64encode(b"S:_I42:VK:999").decode()
                elif case == "feedback-post": story["feedback"]["legacy_api_post_id"] = "999"
                elif case == "feedback-owner": story["feedback"]["owning_profile"]["id"] = "99"
                elif case == "missing-owner": story["feedback"].pop("owning_profile")
                elif case == "missing-story-post": story.pop("post_id")
                elif case == "legacy-conflict": story["legacy_api_post_id"] = "999"
                else: story["actors"][0]["id"] = "99"
                self.assertEqual(self._verify([story])["status"], "not-matched")
        self.assertEqual(self._verify([self._story()], expected_post_id="999")["status"], "not-matched")

    def test_two_distinct_feedback_mappings_are_not_unique(self):
        import copy
        first = self._story(); second = copy.deepcopy(first)
        second["post_id"] = second["feedback"]["legacy_api_post_id"] = "889"
        self.assertEqual(self._verify([first, second])["status"], "not-matched")
