import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "boss_cdp_raw.py"


def load_module():
    sys.modules.setdefault("websocket", mock.Mock())
    sys.modules.setdefault("requests", mock.Mock())
    spec = importlib.util.spec_from_file_location("boss_cdp_position_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def position_tree_fixture():
    return {
        "code": 0,
        "message": "Success",
        "zpData": {
            "position": [
                {
                    "code": 1010000,
                    "name": "互联网/AI",
                    "subLevelModelList": [
                        {
                            "code": 1000020,
                            "name": "后端开发",
                            "subLevelModelList": [
                                {"code": 100101, "name": "Java"},
                                {"code": 100109, "name": "Python"},
                            ],
                        }
                    ],
                },
                {
                    "code": 1080000,
                    "name": "项目管理",
                    "subLevelModelList": [
                        {
                            "code": 1001200,
                            "name": "软件项目管理",
                            "subLevelModelList": [
                                {"code": 100101, "name": "Java"},
                            ],
                        }
                    ],
                },
            ]
        },
    }


class PositionCatalogTests(unittest.TestCase):
    def test_cdp_send_preserves_early_network_event_for_listener(self):
        module = load_module()
        session = module.CDPSession.__new__(module.CDPSession)
        session.mid = 0
        session._event_queue = module.deque(maxlen=module.MAX_CDP_EVENT_QUEUE)
        session.ws = mock.Mock()
        session.ws.recv.side_effect = [
            json.dumps({
                "method": "Network.responseReceived",
                "sessionId": "session",
                "params": {"requestId": "early"},
            }),
            json.dumps({"id": 1, "result": {"frameId": "frame"}}),
        ]

        response = session.send(
            "Page.navigate",
            {"url": "https://example.test"},
            "session",
        )
        event = session.recv_event("session", timeout=1)

        self.assertEqual(response["result"]["frameId"], "frame")
        self.assertEqual(event["method"], "Network.responseReceived")
        self.assertEqual(session.ws.recv.call_count, 2)

    def test_catalog_deduplicates_codes_and_preserves_all_official_paths(self):
        module = load_module()
        source_url = module.build_position_tree_url("100010000")

        catalog = module.parse_position_catalog(position_tree_fixture(), source_url)

        self.assertEqual([item["position_code"] for item in catalog], ["100101", "100109"])
        java = catalog[0]
        self.assertEqual(java["category_level_1_raw"], "互联网/AI")
        self.assertEqual(java["category_level_2_raw"], "后端开发")
        self.assertEqual(java["category_level_3_raw"], "Java")
        self.assertEqual(java["category_path_count"], 2)
        self.assertEqual(
            java["category_paths_raw"],
            [
                "互联网/AI > 后端开发 > Java",
                "项目管理 > 软件项目管理 > Java",
            ],
        )
        self.assertTrue(java["category_identity_verified"])
        self.assertEqual(java["category_source_url"], source_url)

    def test_catalog_rejects_partial_schema_instead_of_inventing_categories(self):
        module = load_module()
        malformed = {"code": 0, "zpData": {"position": [{"code": 1, "name": "一级"}]}}

        with self.assertRaises(module.PositionCatalogError):
            module.parse_position_catalog(malformed, "https://example.test/catalog")

    def test_position_api_block_is_not_treated_as_empty_category(self):
        module = load_module()
        response = json.dumps({
            "ok": False,
            "http_status": 200,
            "code": 37,
            "message": "您的环境存在异常.",
            "jobs": [],
        })

        with self.assertRaises(module.PositionFetchBlockedError):
            module.parse_position_page_eval_value(response)

        account_restricted_response = json.dumps({
            "ok": False,
            "http_status": 200,
            "code": "32",
            "message": "您的账户存在异常行为，已暂时被禁止使用.",
            "jobs": [],
        })
        with self.assertRaises(module.PositionFetchBlockedError):
            module.parse_position_page_eval_value(account_restricted_response)

        login_result = module.classify_login_probe_response({
            "code": 37,
            "message": "您的环境存在异常.",
        })
        self.assertIs(login_result.status, module.LoginProbeStatus.RESTRICTED)

        frequent_response = json.dumps({
            "ok": False,
            "http_status": 200,
            "code": 9999,
            "message": "访问频繁，请稍后再试",
            "jobs": [],
        })
        with self.assertRaises(module.PositionFetchBlockedError):
            module.parse_position_page_eval_value(frequent_response)

    def test_retry_after_is_preferred_and_http_date_is_supported(self):
        module = load_module()
        now = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(module.parse_retry_after_seconds("7200", now=now), 7200.0)
        self.assertEqual(
            module.parse_retry_after_seconds("Thu, 23 Jul 2026 01:00:00 GMT", now=now),
            3600.0,
        )
        self.assertEqual(
            module.position_block_cooldown_seconds(
                3,
                retry_after_seconds=90,
                base_seconds=3600,
            ),
            90.0,
        )
        with self.assertRaises(ValueError):
            module.position_block_cooldown_seconds(1, base_seconds=20_000)

    def test_http_401_stops_without_retry(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = json.dumps({
            "ok": False,
            "http_status": 401,
            "code": None,
            "message": "",
            "jobs": [],
        })

        with mock.patch.object(module.time, "sleep") as sleep:
            with self.assertRaises(module.PositionSessionRequiredError):
                module.fetch_position_page(
                    cdp,
                    "session",
                    "/wapi/zpgeek/search/joblist.json",
                    request_limit=10,
                )

        self.assertEqual(cdp.eval_js.call_count, 1)
        sleep.assert_not_called()

    def test_native_page_navigation_captures_own_joblist_without_injected_xhr(self):
        module = load_module()
        cdp = mock.Mock()
        response_url = (
            "https://www.zhipin.com/wapi/zpgeek/search/joblist.json?"
            "scene=1&query=&city=100010000&position=100101&page=1&pageSize=30"
        )
        cdp.recv_event.side_effect = [
            {
                "method": "Network.responseReceived",
                "sessionId": "session",
                "params": {
                    "requestId": "native-request",
                    "response": {"url": response_url, "status": 200, "headers": {}},
                },
            },
            {
                "method": "Network.loadingFinished",
                "sessionId": "session",
                "params": {"requestId": "native-request"},
            },
        ]
        raw_body = json.dumps({
            "code": 0,
            "message": "Success",
            "zpData": {
                "hasMore": False,
                "jobList": [{
                    "jobName": "Java工程师",
                    "salaryDesc": "20-40K",
                    "cityName": "上海",
                    "areaDistrict": "浦东新区",
                    "businessDistrict": "张江",
                    "jobExperience": "3-5年",
                    "jobDegree": "本科",
                    "encryptJobId": "job-id",
                    "encryptBrandId": "brand-id",
                }],
            },
        }, ensure_ascii=False)

        def send(method, params=None, sid=None, timeout=30):
            if method == "Network.getResponseBody":
                return {"result": {"body": raw_body, "base64Encoded": False}}
            return {"result": {}}

        cdp.send.side_effect = send
        module._request_counter = 0
        navigation_url = (
            "https://www.zhipin.com/web/geek/job?"
            "query=&city=100010000&page=1&position=100101"
        )

        jobs, has_more = module.fetch_position_page(
            cdp,
            "session",
            "/wapi/zpgeek/search/joblist.json?scene=1&query=&city=100010000&"
            "position=100101&page=1&pageSize=30",
            request_limit=10,
            retries=0,
            navigation_url=navigation_url,
        )

        self.assertFalse(has_more)
        self.assertEqual(jobs[0]["title"], "Java工程师")
        self.assertEqual(jobs[0]["salary"], "20-40K")
        self.assertEqual(jobs[0]["location"], "上海·浦东新区·张江")
        self.assertEqual(jobs[0]["job_link"], "https://www.zhipin.com/job_detail/job-id.html")
        cdp.eval_js.assert_not_called()
        self.assertIn(
            mock.call(
                "Page.navigate",
                {"url": navigation_url, "transitionType": "link"},
                "session",
                timeout=module.POSITION_NATIVE_RESPONSE_TIMEOUT_SECONDS,
            ),
            cdp.send.call_args_list,
        )

    def test_native_joblist_match_accepts_current_cachebuster_only_url(self):
        module = load_module()
        expected = {"position": "100101", "page": "1"}

        self.assertTrue(
            module.is_expected_joblist_url(
                "https://www.zhipin.com/wapi/zpgeek/search/joblist.json?_=1784751949577",
                expected,
            )
        )
        self.assertFalse(
            module.is_expected_joblist_url(
                "https://www.zhipin.com/wapi/zpgeek/search/joblist.json?"
                "position=100102&page=1&_=1784751949577",
                expected,
            )
        )

    def test_native_http_restriction_wins_over_non_json_body(self):
        module = load_module()

        with self.assertRaises(module.PositionFetchBlockedError):
            module.parse_native_position_response_body(
                "<html>blocked</html>",
                http_status=429,
                retry_after="120",
            )

    def test_native_response_wait_timeout_has_recoverable_error_type(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.send.return_value = {"result": {}}
        cdp.recv_event.side_effect = TimeoutError("network event timeout")

        with self.assertRaises(module.PositionFetchError) as raised:
            module.capture_native_joblist_response(
                cdp,
                "session",
                "https://www.zhipin.com/web/geek/job?city=100010000&position=100101&page=1",
                {"position": "100101", "page": "1"},
                "position=100101, page=1",
                timeout=1,
            )

        self.assertEqual(
            "PositionNativeResponseTimeoutError",
            type(raised.exception).__name__,
        )

    def test_native_response_timeout_escalates_without_retrying_bad_tab(self):
        module = load_module()
        module._request_counter = 0
        timeout_error = module.PositionNativeResponseTimeoutError(
            "未捕获 BOSS 页面原生职位响应: position=100101, page=1"
        )

        with mock.patch.object(
            module,
            "fetch_native_position_page",
            side_effect=timeout_error,
        ) as fetch, mock.patch.object(module.time, "sleep") as sleep:
            with self.assertRaises(module.PositionNativeResponseTimeoutError):
                module.fetch_position_page(
                    mock.Mock(),
                    "session-1",
                    "/wapi/zpgeek/search/joblist.json?position=100101&page=1",
                    request_limit=6,
                    navigation_url="https://www.zhipin.com/web/geek/job?position=100101&page=1",
                )

        fetch.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(1, module._request_counter)

    def test_job_merge_keeps_one_job_and_all_position_matches(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )
        base = {"job_id": "same", "title": "后端工程师", "salary": "20-40K"}
        first = module.decorate_job_with_position(base, catalog[0])
        second = module.decorate_job_with_position(base, catalog[1])

        merged, added = module.merge_position_jobs([first], [second, second])

        self.assertEqual(added, 0)
        self.assertEqual(len(merged), 1)
        self.assertEqual(
            {item["position_code"] for item in merged[0]["category_matches"]},
            {"100101", "100109"},
        )


class PositionCheckpointTests(unittest.TestCase):
    def test_position_limit_50_can_continue_to_bounded_hard_page_limit(self):
        module = load_module()

        self.assertEqual(module.effective_position_page_limit(1, 1), 1)
        self.assertEqual(module.effective_position_page_limit(1, 50), 10)
        self.assertEqual(module.effective_position_page_limit(1, 100), 10)

    def test_position_collector_reaches_50_across_pages_and_deduplicates(self):
        module = load_module()
        category = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[0]

        def job(index):
            return {
                "encrypt_job_id": f"job-{index}",
                "title": f"Java {index}",
                "salary": "20-40K",
                "job_link": f"https://example.test/job-{index}",
            }

        pages = [
            ([job(index) for index in range(30)], True),
            ([job(29), *[job(index) for index in range(30, 50)]], True),
        ]
        with mock.patch.object(module, "fetch_position_page", side_effect=pages) as fetch, \
                mock.patch.object(module.time, "sleep"):
            result = module.collect_position_jobs(
                mock.Mock(), "session", category, "", "100010000",
                2, 50, {}, False, 10,
            )

        self.assertEqual(fetch.call_count, 2)
        self.assertIsNotNone(fetch.call_args_list[0].kwargs["navigation_url"])
        self.assertIsNone(fetch.call_args_list[1].kwargs["navigation_url"])
        self.assertEqual(result["records_fetched"], 50)
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(result["stop_reason"], "quota_reached")
        self.assertEqual(len({job["job_id"] for job in result["jobs"]}), 50)
        self.assertEqual(
            {job["position_code"] for job in result["jobs"]},
            {"100101"},
        )

    def test_position_collector_confirms_first_page_empty_once(self):
        module = load_module()
        category = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[0]
        recovered = {
            "encrypt_job_id": "job-recovered",
            "title": "Hardware Engineer",
            "salary": "20-30K",
            "job_link": "https://example.test/recovered",
        }
        with mock.patch.object(
            module,
            "fetch_position_page",
            side_effect=[([], False), ([recovered], False)],
        ) as fetch, mock.patch.object(module.time, "sleep") as sleep:
            result = module.collect_position_jobs(
                mock.Mock(), "session", category, "", "100010000",
                1, 1, {}, False, 10,
            )

        self.assertEqual(2, fetch.call_count)
        sleep.assert_called_once()
        self.assertEqual(2, result["pages_fetched"])
        self.assertEqual(1, result["records_fetched"])
        self.assertEqual("quota_reached", result["stop_reason"])

    def test_position_collector_reports_exhausted_below_50(self):
        module = load_module()
        category = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[0]

        def job(index):
            return {
                "encrypt_job_id": f"job-{index}",
                "title": f"Java {index}",
                "salary": "20-40K",
                "job_link": f"https://example.test/job-{index}",
            }

        pages = [
            ([job(index) for index in range(30)], True),
            ([job(index) for index in range(30, 40)], False),
        ]
        with mock.patch.object(module, "fetch_position_page", side_effect=pages), \
                mock.patch.object(module.time, "sleep"):
            result = module.collect_position_jobs(
                mock.Mock(), "session", category, "", "100010000",
                2, 50, {}, False, 10,
            )

        self.assertEqual(result["records_fetched"], 40)
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(result["stop_reason"], "exhausted")

    def test_position_collector_continues_past_two_pages_after_deduplication(self):
        module = load_module()
        category = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[0]

        def job(index):
            return {
                "encrypt_job_id": f"job-{index}",
                "title": f"Java {index}",
                "salary": "20-40K",
                "job_link": f"https://example.test/job-{index}",
            }

        pages = [
            ([job(index) for index in range(30)], True),
            ([job(index) for index in range(20, 40)], True),
            ([job(index) for index in range(40, 50)], True),
        ]
        with mock.patch.object(module, "fetch_position_page", side_effect=pages) as fetch, \
                mock.patch.object(module.time, "sleep"):
            result = module.collect_position_jobs(
                mock.Mock(), "session", category, "", "100010000",
                10, 50, {}, False, 40,
            )

        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(result["records_fetched"], 50)
        self.assertEqual(result["stop_reason"], "quota_reached")

    def test_position_collector_reports_page_limit_below_quota_at_hard_cap(self):
        module = load_module()
        category = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[0]
        pages = []
        for page in range(10):
            jobs = [{
                "encrypt_job_id": f"job-{page}-{index}",
                "title": f"Java {page}-{index}",
                "salary": "20-40K",
                "job_link": f"https://example.test/job-{page}-{index}",
            } for index in range(3)]
            pages.append((jobs, True))

        with mock.patch.object(module, "fetch_position_page", side_effect=pages) as fetch, \
                mock.patch.object(module.time, "sleep"):
            result = module.collect_position_jobs(
                mock.Mock(), "session", category, "", "100010000",
                10, 50, {}, False, 40,
            )

        self.assertEqual(fetch.call_count, 10)
        self.assertEqual(result["records_fetched"], 30)
        self.assertEqual(result["stop_reason"], "page_limit")

    def test_quota_coverage_distinguishes_met_empty_exhausted_and_failed(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )
        base = {
            "catalog": catalog,
            "run_config": {"per_position_limit": 50},
            "deferred_positions": {},
        }
        first = module.build_position_coverage_report({
            **base,
            "completed_position_codes": ["100101", "100109"],
            "failed_positions": {},
            "position_stats": {
                "100101": {"records_fetched": 50, "stop_reason": "quota_reached"},
                "100109": {"records_fetched": 0, "stop_reason": "empty"},
            },
        }, [])
        self.assertEqual(first["quota_summary"]["quota_met"], 1)
        self.assertEqual(first["quota_summary"]["empty"], 1)
        self.assertEqual(first["positions"][0]["quota_status"], "quota_met")
        self.assertEqual(first["positions"][1]["quota_status"], "empty")

        second = module.build_position_coverage_report({
            **base,
            "completed_position_codes": ["100101"],
            "failed_positions": {
                "100109": {"terminal": True, "attempt_count": 3, "error": "bad"},
            },
            "position_stats": {
                "100101": {"records_fetched": 40, "stop_reason": "exhausted"},
            },
        }, [])
        self.assertEqual(second["quota_summary"]["exhausted_below_quota"], 1)
        self.assertEqual(second["quota_summary"]["failed"], 1)
        self.assertEqual(second["positions"][0]["quota_status"], "exhausted_below_quota")
        self.assertEqual(second["positions"][1]["quota_status"], "failed")

        third = module.build_position_coverage_report({
            **base,
            "completed_position_codes": ["100101"],
            "failed_positions": {},
            "position_stats": {
                "100101": {"records_fetched": 30, "stop_reason": "page_limit"},
            },
        }, [])
        self.assertEqual(third["quota_summary"]["page_limit_below_quota"], 1)
        self.assertEqual(third["positions"][0]["quota_status"], "page_limit_below_quota")

    def test_scrape_uses_effective_page_budget_in_checkpoint_scope(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[:1]
        collected = {
            "jobs": [],
            "records_fetched": 40,
            "filtered_out_count": 0,
            "pages_fetched": 2,
            "stop_reason": "exhausted",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            with mock.patch.object(module, "CDPSession", return_value=mock.Mock()), \
                    mock.patch.object(
                        module,
                        "create_page_session",
                        return_value=("target", "session"),
                    ) as create_session, \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(module, "collect_position_jobs", return_value=collected) as collector, \
                    mock.patch.object(module.time, "sleep"):
                module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=50,
                    catalog=catalog,
                )

            checkpoint = pathlib.Path(str(output.with_suffix("")) + "_checkpoint.json")
            state = json.loads(checkpoint.read_text(encoding="utf-8"))

        self.assertEqual(collector.call_args.args[5], 10)
        create_session.assert_called_once_with(mock.ANY, background=False)
        self.assertEqual(state["run_config"]["max_pages_per_position"], 10)
        self.assertEqual(state["run_config"]["per_position_limit"], 50)

    def test_scrape_replaces_bad_tab_and_retries_current_category(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[:1]
        fake_cdp = mock.Mock()
        timeout_error = module.PositionFetchError(
            "职业职位页请求失败: 未捕获 BOSS 页面原生职位响应: "
            "position=100101, page=1"
        )
        recovered = {
            "jobs": [],
            "records_fetched": 0,
            "filtered_out_count": 0,
            "pages_fetched": 2,
            "stop_reason": "empty",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            with mock.patch.object(module, "CDPSession", return_value=fake_cdp), \
                    mock.patch.object(
                        module,
                        "create_page_session",
                        side_effect=[("target-1", "session-1"), ("target-2", "session-2")],
                    ) as create_session, \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(
                        module,
                        "collect_position_jobs",
                        side_effect=[timeout_error, recovered],
                    ) as collector, \
                    mock.patch.object(module.time, "sleep"):
                result = module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                    catalog=catalog,
                )

            checkpoint = pathlib.Path(str(output.with_suffix("")) + "_checkpoint.json")
            state = json.loads(checkpoint.read_text(encoding="utf-8"))

        self.assertEqual(2, collector.call_count)
        self.assertEqual(["session-1", "session-2"], [
            call.args[1] for call in collector.call_args_list
        ])
        self.assertEqual(2, create_session.call_count)
        self.assertIn(
            mock.call("Target.closeTarget", {"targetId": "target-1"}),
            fake_cdp.send.call_args_list,
        )
        self.assertEqual(1, result["positions_completed"])
        self.assertEqual(["100101"], state["completed_position_codes"])
        self.assertEqual({}, state["deferred_positions"])

    def test_bad_tab_recovery_is_bounded_before_category_is_deferred(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[:1]
        fake_cdp = mock.Mock()

        def timeout_error():
            return module.PositionFetchError(
                "职业职位页请求失败: 未捕获 BOSS 页面原生职位响应: "
                "position=100101, page=1"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            with mock.patch.object(module, "CDPSession", return_value=fake_cdp), \
                    mock.patch.object(
                        module,
                        "create_page_session",
                        side_effect=[("target-1", "session-1"), ("target-2", "session-2")],
                    ) as create_session, \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(
                        module,
                        "collect_position_jobs",
                        side_effect=[timeout_error(), timeout_error()],
                    ) as collector, \
                    mock.patch.object(module.time, "sleep"):
                module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                    catalog=catalog,
                )

            checkpoint = pathlib.Path(str(output.with_suffix("")) + "_checkpoint.json")
            state = json.loads(checkpoint.read_text(encoding="utf-8"))

        self.assertEqual(2, collector.call_count)
        self.assertEqual(2, create_session.call_count)
        self.assertEqual(
            [6, 6],
            [call.args[-1] for call in collector.call_args_list],
        )
        self.assertEqual({}, state["failed_positions"])
        self.assertEqual(1, state["deferred_positions"]["100101"]["attempt_count"])

    def test_position_collector_uses_position_parameter_and_numeric_filter(self):
        module = load_module()
        category = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[0]
        cdp = mock.Mock()
        page_jobs = ([
            {"title": "面议岗位", "salary": "面议", "job_link": "https://example.test/a"},
            {"title": "Java", "salary": "20-40K", "job_link": "https://example.test/b"},
        ], False)

        with mock.patch.object(module, "fetch_position_page", return_value=page_jobs) as fetch:
            result = module.collect_position_jobs(
                cdp, "session", category, "", "100010000", 3, 1, {}, True, 10,
            )

        api_url = fetch.call_args.args[2]
        navigation_url = fetch.call_args.kwargs["navigation_url"]
        self.assertIn("position=100101", api_url)
        self.assertIn("position=100101", navigation_url)
        cdp.eval_js.assert_not_called()
        self.assertEqual(result["records_fetched"], 1)
        self.assertEqual(result["filtered_out_count"], 1)
        job = result["jobs"][0]
        self.assertEqual(job["category_path_raw"], "互联网/AI > 后端开发 > Java")
        self.assertEqual(job["site_name"], "boss")
        self.assertEqual(job["content_category"], "full_time_job")
        self.assertEqual(job["title_raw"], "Java")
        self.assertTrue(job["record_id"].startswith("boss-"))
        self.assertIn("position=100101", job["list_url"])
        self.assertEqual(job["detail_url"], "https://example.test/b")
        self.assertEqual(job["price_evidence"][0]["raw_text"], "20-40K")
        self.assertEqual(job["price_evidence"][0]["price_source"], "api_response")
        self.assertEqual(job["price_evidence"][0]["evidence_url"], job["list_url"])

    def test_completed_run_resumes_without_opening_cdp_or_duplicating_jobs(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )
        fake_cdp = mock.Mock()

        def collected(category):
            raw = {
                "job_id": f"job-{category['position_code']}",
                "title": category["position_name"],
                "salary": "20-40K",
                "job_link": f"https://example.test/{category['position_code']}",
            }
            return {
                "jobs": [module.decorate_job_with_position(raw, category)],
                "records_fetched": 1,
                "filtered_out_count": 0,
                "pages_fetched": 1,
                "stop_reason": "quota_reached",
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            with mock.patch.object(module, "CDPSession", return_value=fake_cdp), \
                    mock.patch.object(module, "create_page_session", return_value=("target", "session")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(module, "collect_position_jobs", side_effect=lambda *args, **kwargs: collected(args[2])), \
                    mock.patch.object(module.time, "sleep"):
                first = module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                    catalog=catalog,
                    max_positions=1,
                )

            checkpoint = pathlib.Path(str(output.with_suffix("")) + "_checkpoint.json")
            coverage = pathlib.Path(str(output.with_suffix("")) + "_coverage.json")
            output_data = json.loads(output.read_text(encoding="utf-8"))
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            report = json.loads(coverage.read_text(encoding="utf-8"))
            self.assertTrue({
                "mode", "keyword", "city", "city_code", "run_config",
                "catalog_source_url", "catalog_signature", "position_codes_total",
                "position_paths_total", "positions_completed", "filtered_out_count",
                "total", "scraped_at", "jobs",
            }.issubset(output_data))
            self.assertEqual(first["total"], 1)
            self.assertEqual(state["completed_position_codes"], ["100101"])
            self.assertEqual(report["position_code_coverage_rate"], 0.5)

            second_cdp = mock.Mock()
            with mock.patch.object(module, "CDPSession", return_value=second_cdp), \
                    mock.patch.object(module, "create_page_session", return_value=("target-2", "session-2")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(module, "collect_position_jobs", side_effect=lambda *args, **kwargs: collected(args[2])), \
                    mock.patch.object(module.time, "sleep"):
                second = module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                    max_positions=1,
                )

            self.assertEqual(second["total"], 2)

            with mock.patch.object(module, "CDPSession", side_effect=AssertionError("resume opened CDP")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")):
                resumed = module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                    max_positions=1,
                )

            self.assertEqual(resumed["total"], 2)
            self.assertEqual(
                len({job["job_id"] for job in resumed["jobs"]}),
                2,
            )

    def test_category_delay_runs_even_when_each_category_uses_one_page(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )
        fake_cdp = mock.Mock()

        def collected(category):
            return {
                "jobs": [],
                "records_fetched": 0,
                "filtered_out_count": 0,
                "pages_fetched": 1,
                "stop_reason": "empty",
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            with mock.patch.object(module, "CDPSession", return_value=fake_cdp), \
                    mock.patch.object(module, "create_page_session", return_value=("target", "session")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(module, "collect_position_jobs", side_effect=lambda *args, **kwargs: collected(args[2])), \
                    mock.patch.object(module, "position_category_delay_seconds", return_value=17.0), \
                    mock.patch.object(module.time, "sleep") as sleep:
                module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                    catalog=catalog,
                    max_positions=2,
                )

        sleep.assert_any_call(17.0)
        self.assertEqual(sleep.call_args_list.count(mock.call(17.0)), 1)

    def test_failure_is_checkpointed_and_run_stops(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )[:1]
        fake_cdp = mock.Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            with mock.patch.object(module, "CDPSession", return_value=fake_cdp), \
                    mock.patch.object(module, "create_page_session", return_value=("target", "session")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(
                        module,
                        "collect_position_jobs",
                        side_effect=module.PositionFetchBlockedError(
                            "code=37",
                            retry_after_seconds=7200,
                        ),
                    ), \
                    mock.patch.object(module.time, "sleep"):
                with self.assertRaises(module.PositionFetchBlockedError):
                    module.scrape_position_categories(
                        "", "全国", 1, {}, str(output),
                        per_position_limit=1,
                        catalog=catalog,
                    )

            checkpoint = pathlib.Path(str(output.with_suffix("")) + "_checkpoint.json")
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(state["completed_position_codes"], [])
            self.assertEqual(state["failed_positions"], {})
            self.assertIn("100101", state["deferred_positions"])
            self.assertIn("code=37", state["deferred_positions"]["100101"]["error"])
            self.assertEqual(state["deferred_positions"]["100101"]["attempt_count"], 1)
            self.assertEqual(state["deferred_positions"]["100101"]["cooldown_seconds"], 7200.0)
            self.assertEqual(state["site_restriction"]["position_code"], "100101")

            report_path = pathlib.Path(str(output.with_suffix("")) + "_coverage.json")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["position_codes_pending"], 0)
            self.assertEqual(report["position_codes_deferred"], 1)
            self.assertEqual(report["position_codes_failed"], 0)
            self.assertEqual(report["positions"][0]["status"], "deferred")

            with mock.patch.object(module, "CDPSession", side_effect=AssertionError("cooldown opened CDP")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")):
                with self.assertRaises(module.PositionFetchCooldownError):
                    module.scrape_position_categories(
                        "", "全国", 1, {}, str(output),
                        per_position_limit=1,
                    )

    def test_old_checkpoint_failure_is_migrated_and_requeued_after_unattempted(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )
        old_state = {
            "completed_position_codes": [],
            "failed_positions": {
                "100101": {
                    "error": "code=37",
                    "stop_reason": "site_restriction",
                    "attempt_count": 1,
                }
            },
        }

        state = module.normalize_position_checkpoint_state(old_state)
        queue = module.build_position_work_queue(catalog, state)
        report = module.build_position_coverage_report({
            **state,
            "catalog": catalog,
            "run_config": {},
            "position_stats": {},
        }, [])

        self.assertEqual([item["position_code"] for item in queue], ["100109", "100101"])
        self.assertEqual(state["failed_positions"], {})
        self.assertIn("100101", state["deferred_positions"])
        self.assertEqual(report["position_codes_pending"], 1)
        self.assertEqual(report["position_codes_deferred"], 1)
        self.assertEqual(report["position_codes_failed"], 0)
        self.assertEqual(
            report["position_codes_completed"]
            + report["position_codes_pending"]
            + report["position_codes_deferred"]
            + report["position_codes_failed"],
            report["position_codes_total"],
        )

    def test_old_single_probe_empty_is_requeued_once(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )
        state = module.normalize_position_checkpoint_state({
            "completed_position_codes": ["100101", "100109"],
            "failed_positions": {},
            "position_stats": {
                "100101": {
                    "records_fetched": 0,
                    "pages_fetched": 1,
                    "stop_reason": "empty",
                },
                "100109": {
                    "records_fetched": 0,
                    "pages_fetched": 2,
                    "stop_reason": "empty",
                },
            },
        })

        self.assertEqual(["100109"], state["completed_position_codes"])
        self.assertNotIn("100101", state["position_stats"])
        self.assertEqual(
            module.POSITION_EMPTY_CONFIRMATION_VERSION,
            state["empty_confirmation_version"],
        )
        self.assertEqual(
            ["100101"],
            [item["position_code"] for item in module.build_position_work_queue(catalog, state)],
        )

        normalized_again = module.normalize_position_checkpoint_state(state)
        self.assertEqual(["100109"], normalized_again["completed_position_codes"])

    def test_category_error_budget_becomes_terminal_without_blocking_other_categories(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )

        def collect(*args, **kwargs):
            category = args[2]
            if category["position_code"] == "100101":
                raise module.PositionFetchError("category payload malformed")
            return {
                "jobs": [],
                "records_fetched": 0,
                "filtered_out_count": 0,
                "pages_fetched": 1,
                "stop_reason": "empty",
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            with mock.patch.object(module, "CDPSession", return_value=mock.Mock()), \
                    mock.patch.object(module, "create_page_session", return_value=("target", "session")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(module, "collect_position_jobs", side_effect=collect) as collector, \
                    mock.patch.object(module.time, "sleep"):
                module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                    catalog=catalog,
                )
                module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                )
                module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                )

            checkpoint = pathlib.Path(str(output.with_suffix("")) + "_checkpoint.json")
            coverage = pathlib.Path(str(output.with_suffix("")) + "_coverage.json")
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            report = json.loads(coverage.read_text(encoding="utf-8"))

            self.assertEqual(collector.call_count, 4)
            self.assertEqual(state["completed_position_codes"], ["100109"])
            self.assertEqual(state["deferred_positions"], {})
            self.assertEqual(state["failed_positions"]["100101"]["attempt_count"], 3)
            self.assertTrue(state["failed_positions"]["100101"]["terminal"])
            self.assertEqual(report["position_codes_completed"], 1)
            self.assertEqual(report["position_codes_pending"], 0)
            self.assertEqual(report["position_codes_deferred"], 0)
            self.assertEqual(report["position_codes_failed"], 1)

            with mock.patch.object(module, "CDPSession", side_effect=AssertionError("terminal failure opened CDP")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")):
                module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                )

    def test_expired_cooldown_allows_checkpoint_resume(self):
        module = load_module()
        now = datetime.now().astimezone()
        state = {
            "site_restriction": {
                "position_code": "100101",
                "cooldown_until": (now - timedelta(seconds=1)).isoformat(),
            }
        }
        pending = [{"position_code": "100101"}]

        self.assertIsNone(module.active_position_cooldown(state, pending, now=now))

    def test_cli_all_positions_defaults_to_blank_keyword_and_no_details(self):
        module = load_module()
        list_data = {
            "jobs": [{"job_id": "one", "title": "Java", "salary": "20-40K"}],
            "total": 1,
            "filtered_out_count": 0,
        }
        argv = [str(SCRIPT_PATH), "--all-positions", "--city", "全国"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(module, "require_runtime_dependencies", return_value=True), \
                mock.patch.object(
                    module,
                    "check_login_state",
                    return_value=module.LoginProbeResult(module.LoginProbeStatus.AVAILABLE),
                ), \
                mock.patch.object(module, "scrape_position_categories", return_value=list_data) as scrape, \
                mock.patch.object(module, "scrape_details") as details:
            module.main()

        self.assertEqual(scrape.call_args.args[0], "")
        self.assertIsNone(scrape.call_args.kwargs["position_codes"])
        self.assertIsNone(scrape.call_args.kwargs["max_positions"])
        details.assert_not_called()

    def test_cli_cooldown_exits_before_login_probe_or_cdp(self):
        module = load_module()
        future = datetime.now().astimezone() + timedelta(hours=1)
        state = {
            "schema_version": module.POSITION_CHECKPOINT_SCHEMA,
            "catalog": [{"position_code": "100101"}],
            "completed_position_codes": [],
            "site_restriction": {
                "position_code": "100101",
                "cooldown_until": future.isoformat(),
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            checkpoint = pathlib.Path(temp_dir) / "checkpoint.json"
            checkpoint.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                str(SCRIPT_PATH),
                "--all-positions",
                "--output", str(output),
                "--checkpoint", str(checkpoint),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(module, "require_runtime_dependencies", return_value=True), \
                    mock.patch.object(module, "check_login_state", side_effect=AssertionError("login probe ran")), \
                    mock.patch.object(module, "CDPSession", side_effect=AssertionError("CDP opened")):
                with self.assertRaises(SystemExit) as exit_context:
                    module.main()

        self.assertEqual(exit_context.exception.code, 1)

    def test_login_probe_restriction_bootstrap_recovers_without_empty_catalog(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )
        run_config = module.build_position_run_config(
            "", "100010000", 1, 1, {}, False, [],
        )
        restricted = module.LoginProbeResult(
            module.LoginProbeStatus.RESTRICTED,
            code=37,
            message="您的环境存在异常.",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            checkpoint_path, restriction = module.persist_position_login_probe_restriction(
                str(output), None, None, run_config, "全国", restricted,
                block_cooldown_seconds=60,
            )
            checkpoint = pathlib.Path(checkpoint_path)
            coverage = pathlib.Path(str(output.with_suffix("")) + "_coverage.json")
            state = json.loads(checkpoint.read_text(encoding="utf-8"))

            self.assertEqual(state["checkpoint_status"], "awaiting_catalog")
            self.assertNotIn("catalog", state)
            self.assertEqual(restriction["scope"], "login_probe")
            self.assertFalse(output.exists())
            self.assertFalse(coverage.exists())
            self.assertIsNotNone(
                module.load_active_position_checkpoint_cooldown(str(output))
            )

            state["site_restriction"]["cooldown_until"] = (
                datetime.now().astimezone() - timedelta(seconds=1)
            ).isoformat()
            checkpoint.write_text(json.dumps(state), encoding="utf-8")

            collected = {
                "jobs": [],
                "records_fetched": 0,
                "filtered_out_count": 0,
                "pages_fetched": 1,
                "stop_reason": "empty",
            }
            with mock.patch.object(module, "CDPSession", return_value=mock.Mock()), \
                    mock.patch.object(module, "create_page_session", return_value=("target", "session")), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(module, "collect_position_jobs", return_value=collected), \
                    mock.patch.object(module.time, "sleep"):
                module.scrape_position_categories(
                    "", "全国", 1, {}, str(output),
                    per_position_limit=1,
                    catalog=catalog,
                )

            recovered = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertNotIn("checkpoint_status", recovered)
            self.assertEqual(len(recovered["catalog"]), 2)
            self.assertEqual(recovered["completed_position_codes"], ["100101", "100109"])

    def test_position_mode_login_restriction_updates_existing_checkpoint(self):
        module = load_module()
        catalog = module.parse_position_catalog(
            position_tree_fixture(),
            "https://example.test/catalog",
        )
        run_config = module.build_position_run_config(
            "", "100010000", 1, 1, {}, False, [],
        )
        restricted = module.LoginProbeResult(
            module.LoginProbeStatus.RESTRICTED,
            code=32,
            message="您的账户存在异常行为，已暂时被禁止使用.",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            checkpoint = pathlib.Path(str(output.with_suffix("")) + "_checkpoint.json")
            checkpoint.write_text(json.dumps({
                "schema_version": module.POSITION_CHECKPOINT_SCHEMA,
                "run_config": run_config,
                "city_name": "全国",
                "catalog": catalog,
                "completed_position_codes": ["100101"],
                "failed_positions": {},
                "deferred_positions": {},
                "position_stats": {},
            }, ensure_ascii=False), encoding="utf-8")
            argv = [
                str(SCRIPT_PATH),
                "--all-positions",
                "--city", "全国",
                "--pages", "1",
                "--per-position-limit", "1",
                "--output", str(output),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(module, "require_runtime_dependencies", return_value=True), \
                    mock.patch.object(module, "resolve_city", return_value=("全国", "100010000")), \
                    mock.patch.object(module, "check_login_state", return_value=restricted):
                with self.assertRaises(SystemExit) as exit_context:
                    module.main()

            updated = json.loads(checkpoint.read_text(encoding="utf-8"))

        self.assertEqual(exit_context.exception.code, 1)
        self.assertEqual(updated["completed_position_codes"], ["100101"])
        self.assertEqual(updated["catalog"], catalog)
        self.assertEqual(updated["site_restriction"]["scope"], "login_probe")
        self.assertEqual(updated["site_restriction"]["code"], 32)
        self.assertEqual(
            updated["site_restriction"]["message"],
            "您的账户存在异常行为，已暂时被禁止使用.",
        )

    def test_check_mode_never_writes_position_checkpoint(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = pathlib.Path(temp_dir) / "boss_jobs.json"
            argv = [
                str(SCRIPT_PATH),
                "--check",
                "--all-positions",
                "--output", str(output),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(module, "run_check", return_value=1), \
                    mock.patch.object(
                        module,
                        "persist_position_login_probe_restriction",
                        side_effect=AssertionError("--check wrote a checkpoint"),
                    ):
                with self.assertRaises(SystemExit) as exit_context:
                    module.main()

            checkpoint = pathlib.Path(str(output.with_suffix("")) + "_checkpoint.json")
            self.assertFalse(checkpoint.exists())
            self.assertEqual(exit_context.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
