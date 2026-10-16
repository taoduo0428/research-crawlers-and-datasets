from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import ANY, MagicMock, call, patch

from curl_cffi import requests as curl_requests

from drv_crawler.job51.crawler import (
    BrowserBootstrapError,
    Job51Crawler,
    RateLimitedError,
    SearchPage,
    SessionRequiredError,
    TransportError,
    UpstreamSchemaError,
    _login_probe_is_authenticated,
    category_snapshot,
    flatten_category_tree,
    has_nonzero_numeric_salary,
    normalize_job,
    parse_search_payload,
    parse_salary_bounds,
    save_login_state,
)
from drv_crawler.job51.models import CrawlConfig, PACKAGE_DIR
from drv_crawler.job51.state import CrawlState, RunScopeMismatch


def fixture_tree() -> list[dict[str, object]]:
    return [
        {
            "id": "L1",
            "label": "Group",
            "children": [
                {
                    "id": "L2A",
                    "label": "Branch A",
                    "children": [
                        {"id": "LEAF", "label": "Shared role", "children": None}
                    ],
                },
                {
                    "id": "L2B",
                    "label": "Branch B",
                    "children": [
                        {"id": "LEAF", "label": "Shared role", "children": None},
                        {"id": "OTHER", "label": "Other role", "children": None},
                    ],
                },
            ],
        }
    ]


def fixture_job(job_id: str = "123") -> dict[str, object]:
    return {
        "jobId": job_id,
        "jobName": "Engineer",
        "companyName": "Example Co",
        "jobHref": f"https://jobs.51job.com/example/{job_id}.html",
        "provideSalaryString": "1-2\u4e07",
        "jobSalaryMin": "10000",
        "jobSalaryMax": 20000,
        "funcType1Code": "LEAF",
        "jobAreaString": "City District",
        "jobAreaLevelDetail": {
            "cityString": "City",
            "districtString": "District",
        },
        "issueDateString": "2026-07-22 12:00:00",
        "term": "0",
    }


class CategoryTests(unittest.TestCase):
    def test_duplicate_search_code_keeps_each_official_path(self) -> None:
        categories = flatten_category_tree(fixture_tree())
        self.assertEqual(3, len(categories))
        shared = [item for item in categories if item.search_code == "LEAF"]
        self.assertEqual(2, len(shared))
        self.assertEqual(2, len({item.path_key for item in shared}))

    def test_malformed_tree_fails_closed(self) -> None:
        with self.assertRaises(UpstreamSchemaError):
            flatten_category_tree([{"id": "L1", "label": "Group"}])


class PayloadTests(unittest.TestCase):
    def test_salary_text_fallback_preserves_period_scale(self) -> None:
        self.assertEqual((99000, 100000), parse_salary_bounds("9.9-10\u4e07/\u5e74"))
        self.assertEqual((150, 300), parse_salary_bounds("150-300\u5143/\u5929"))
        self.assertEqual((None, 8000), parse_salary_bounds("8\u5343\u4ee5\u4e0b"))
        self.assertEqual((10000, None), parse_salary_bounds("1\u4e07\u4ee5\u4e0a"))
        self.assertEqual((None, None), parse_salary_bounds("\u9762\u8bae"))

    def test_parse_and_normalize_preserve_numeric_salary(self) -> None:
        page = parse_search_payload(
            {
                "status": "1",
                "resultbody": {
                    "requestId": "request-1",
                    "job": {"items": [fixture_job()], "totalcount": 1},
                },
            }
        )
        record = normalize_job(page.items[0], "LEAF", "000000")
        self.assertEqual(1, page.total_count)
        self.assertEqual("Engineer", record["title_raw"])
        self.assertEqual("1-2\u4e07", record["price_evidence"][0]["raw_text"])
        self.assertEqual(10000, record["metadata"]["salary_min"])
        self.assertEqual(20000, record["metadata"]["salary_max"])
        self.assertEqual("month", record["metadata"]["salary_period"])
        self.assertTrue(record["category_identity_verified"])

    def test_schema_drift_is_not_silently_empty(self) -> None:
        with self.assertRaises(UpstreamSchemaError):
            parse_search_payload(
                {"status": "1", "resultbody": {"job": {"totalcount": 0}}}
            )

    def test_total_count_alias_is_accepted(self) -> None:
        page = parse_search_payload(
            {
                "status": "1",
                "resultbody": {
                    "requestId": "request-1",
                    "job": {"items": [], "totalCount": 17},
                },
            }
        )

        self.assertEqual(17, page.total_count)

    def test_search_evaluate_has_an_in_page_timeout(self) -> None:
        config = CrawlConfig(timeout_ms=5_000)
        crawler = Job51Crawler(config)
        page = MagicMock()
        response = MagicMock()
        response.url = (
            "https://we.51job.com/api/job/search-pc?"
            "function=LEAF&jobArea=000000&pageNum=1"
        )
        response.status = 200
        response.json.return_value = {
            "status": "1",
            "resultbody": {"job": {"items": [], "totalcount": 0}},
        }
        response_waiter = MagicMock()
        response_waiter.__enter__.return_value = MagicMock()
        response_waiter.__enter__.return_value.value = response
        page.expect_response.return_value = response_waiter

        result = crawler._search_once(page, "LEAF", 1)

        self.assertEqual(0, result.total_count)
        script, arguments = page.evaluate.call_args.args
        self.assertIn("setTimeout", script)
        self.assertEqual(4_000, arguments["requestTimeoutMs"])

    def test_waf_html_is_classified_as_rate_limit(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(timeout_ms=5_000, soft_block_cooldown_seconds=75)
        )
        context = MagicMock()
        crawler._runtime_session_context = context
        page = MagicMock()
        response = MagicMock()
        response.url = (
            "https://we.51job.com/api/job/search-pc?"
            "function=LEAF&jobArea=000000&pageNum=1"
        )
        response.status = 200
        response.headers = {"content-type": "text/html"}
        response.text.return_value = (
            '<textarea id="renderData">{"_waf_bd8ce2ce37":"fixture"}</textarea>'
        )
        response_waiter = MagicMock()
        response_waiter.__enter__.return_value = MagicMock()
        response_waiter.__enter__.return_value.value = response
        page.expect_response.return_value = response_waiter

        caught: RateLimitedError | None = None
        try:
            crawler._search_once(page, "LEAF", 1)
        except RateLimitedError as exc:
            caught = exc
        except Exception as exc:
            self.fail(f"WAF response had the wrong classification: {type(exc).__name__}")
        else:
            self.fail("WAF response was accepted")

        self.assertIsNotNone(caught)
        self.assertEqual(75, caught.retry_after)
        response.json.assert_not_called()
        context.storage_state.assert_not_called()

    def test_blocked_search_page_is_classified_before_vue_wait(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(timeout_ms=5_000, soft_block_cooldown_seconds=75)
        )
        page = MagicMock()
        response = MagicMock()
        response.status = 405
        response.headers = {}
        page.goto.return_value = response
        page.content.return_value = (
            "<html><body>\u8bbf\u95ee\u88ab\u963b\u65ad</body></html>"
        )

        with self.assertRaises(RateLimitedError) as caught:
            crawler._bootstrap_page(page)

        self.assertEqual(75, caught.exception.retry_after)
        page.wait_for_function.assert_not_called()

    def test_timeout_rebuilds_page_before_retry_then_succeeds(self) -> None:
        crawler = Job51Crawler(CrawlConfig(request_retries=1))
        page = MagicMock()
        success = parse_search_payload(
            {
                "status": "1",
                "resultbody": {"job": {"items": [], "totalcount": 0}},
            }
        )
        with (
            patch.object(
                crawler,
                "_search_once",
                side_effect=[
                    BrowserBootstrapError("request timed out"),
                    success,
                ],
            ) as search,
            patch.object(crawler, "_bootstrap_page") as bootstrap,
            patch("drv_crawler.job51.crawler.time.sleep"),
        ):
            result = crawler._search_with_retry(page, "LEAF", 1)

        self.assertIs(success, result)
        self.assertEqual(2, search.call_count)
        bootstrap.assert_called_once_with(page)

    def test_rate_limit_is_not_retried_in_the_same_browser_session(self) -> None:
        crawler = Job51Crawler(CrawlConfig(request_retries=2))
        page = MagicMock()
        error = RateLimitedError("WAF cooldown", retry_after=120)
        with (
            patch.object(crawler, "_search_once", side_effect=error) as search,
            patch("drv_crawler.job51.crawler.time.sleep") as sleep,
        ):
            with self.assertRaises(RateLimitedError):
                crawler._search_with_retry(page, "LEAF", 1)

        self.assertEqual(1, search.call_count)
        sleep.assert_not_called()


class FreshApiTests(unittest.TestCase):
    @staticmethod
    def _response(
        *,
        status_code: int = 200,
        items: list[dict[str, object]] | None = None,
        total: int = 0,
        request_id: str = "request-1",
        headers: dict[str, str] | None = None,
    ) -> MagicMock:
        response = MagicMock()
        response.status_code = status_code
        response.headers = headers or {"content-type": "application/json"}
        response.json.return_value = {
            "status": "1",
            "resultbody": {
                "requestId": request_id,
                "job": {"items": items or [], "totalcount": total},
            },
        }
        return response

    def test_fresh_api_uses_bounded_signed_get_and_request_id(self) -> None:
        session = MagicMock()
        session.get.return_value = self._response(total=17, request_id="request-2")
        crawler = Job51Crawler(
            CrawlConfig(proxy_policy="off", timeout_ms=5_000),
            http_session=session,
        )

        with patch("drv_crawler.job51.crawler.time.time", return_value=1_784_764_000):
            result = crawler._fresh_search_once("LEAF", 2, "request-1")

        self.assertEqual(17, result.total_count)
        url = session.get.call_args.args[0]
        kwargs = session.get.call_args.kwargs
        self.assertTrue(
            url.startswith(
                "https://cupid.51job.com/open/noauth/jobs/fresh-job-list?"
                "api_key=51job&timestamp=1784764000&function=LEAF&jobArea=000000"
                "&pageNum=2&pageSize=20&requestId=request-1"
            )
        )
        self.assertEqual((5.0, 5.0), kwargs["timeout"])
        self.assertRegex(kwargs["headers"]["sign"], r"^[0-9a-f]{64}$")
        self.assertEqual("", kwargs["headers"]["user-token"])

    def test_default_transport_uses_curl_cffi_chrome_impersonation(self) -> None:
        crawler = Job51Crawler(CrawlConfig(proxy_policy="off"))
        session = MagicMock()

        with patch(
            "drv_crawler.job51.crawler.curl_requests.Session",
            return_value=session,
        ) as session_factory:
            result = crawler._get_http_session()

        self.assertIs(session, result)
        session_factory.assert_called_once_with(impersonate="chrome")

    def test_fresh_api_429_preserves_retry_after(self) -> None:
        session = MagicMock()
        session.get.return_value = self._response(
            status_code=429,
            headers={"content-type": "application/json", "retry-after": "12"},
        )
        crawler = Job51Crawler(CrawlConfig(proxy_policy="off"), http_session=session)

        with self.assertRaises(RateLimitedError) as caught:
            crawler._fresh_search_once("LEAF", 1, "")

        self.assertEqual(12.0, caught.exception.retry_after)
        session.get.assert_called_once()

    def test_fresh_api_rejects_oversized_response(self) -> None:
        session = MagicMock()
        session.get.return_value = self._response(
            headers={
                "content-type": "application/json",
                "content-length": str(6 * 1024 * 1024),
            },
        )
        crawler = Job51Crawler(CrawlConfig(proxy_policy="off"), http_session=session)

        with self.assertRaisesRegex(UpstreamSchemaError, "5 MiB limit"):
            crawler._fresh_search_once("LEAF", 1, "")

    def test_fresh_api_timeout_is_retried_with_bound(self) -> None:
        session = MagicMock()
        success = self._response(total=0)
        session.get.side_effect = [
            curl_requests.RequestsError("fixture timeout"),
            success,
        ]
        crawler = Job51Crawler(
            CrawlConfig(proxy_policy="off", request_retries=1),
            http_session=session,
        )

        with patch("drv_crawler.job51.crawler.time.sleep") as sleep:
            result = crawler._search_with_retry(
                None, "LEAF", 1, request_id=""
            )

        self.assertEqual(0, result.total_count)
        self.assertEqual(2, session.get.call_count)
        sleep.assert_called_once()

    def test_inconsistent_empty_later_page_is_retried(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(proxy_policy="off", request_retries=1)
        )
        recovered = SearchPage(
            items=[fixture_job()], total_count=40, request_id="request-2"
        )
        with (
            patch.object(
                crawler,
                "_fresh_search_once",
                side_effect=[
                    SearchPage(items=[], total_count=0, request_id=None),
                    recovered,
                ],
            ) as search,
            patch("drv_crawler.job51.crawler.time.sleep") as sleep,
        ):
            result = crawler._search_with_retry(
                None,
                "LEAF",
                2,
                request_id="request-1",
                prior_total=40,
            )

        self.assertIs(recovered, result)
        self.assertEqual(2, search.call_count)
        sleep.assert_called_once()

    def test_inconsistent_total_count_is_retried(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(proxy_policy="off", request_retries=1)
        )
        recovered = SearchPage(
            items=[fixture_job()], total_count=1, request_id="request-2"
        )
        with (
            patch.object(
                crawler,
                "_fresh_search_once",
                side_effect=[
                    SearchPage(
                        items=[fixture_job()], total_count=0, request_id=None
                    ),
                    recovered,
                ],
            ) as search,
            patch("drv_crawler.job51.crawler.time.sleep") as sleep,
        ):
            result = crawler._search_with_retry(None, "LEAF", 1)

        self.assertIs(recovered, result)
        self.assertEqual(2, search.call_count)
        sleep.assert_called_once()

    def test_repeated_inconsistent_empty_page_fails_closed(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(proxy_policy="off", request_retries=1)
        )
        empty = SearchPage(items=[], total_count=0, request_id=None)
        with (
            patch.object(crawler, "_fresh_search_once", return_value=empty) as search,
            patch("drv_crawler.job51.crawler.time.sleep"),
            self.assertRaisesRegex(TransportError, "inconsistent empty page"),
        ):
            crawler._search_with_retry(
                None,
                "LEAF",
                2,
                prior_total=40,
            )

        self.assertEqual(2, search.call_count)


class RunControlTests(unittest.TestCase):
    def test_batch_boundary_applies_configured_cooldown(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(
                delay_min_seconds=0,
                delay_max_seconds=0,
                batch_size=2,
                batch_cooldown_seconds=45,
            )
        )
        sleep_after_code = getattr(crawler, "_sleep_after_code", None)
        self.assertTrue(
            callable(sleep_after_code), "Job51Crawler._sleep_after_code is missing"
        )
        if not callable(sleep_after_code):
            return

        with patch("drv_crawler.job51.crawler.time.sleep") as sleep:
            sleep_after_code(2, has_more=True)

        sleep.assert_called_once_with(45)

    def test_rate_limit_defers_current_code_and_stops_batch(self) -> None:
        crawler = Job51Crawler(CrawlConfig())
        state = MagicMock()
        record_error = getattr(crawler, "_record_code_error", None)
        self.assertTrue(
            callable(record_error), "Job51Crawler._record_code_error is missing"
        )
        if not callable(record_error):
            return

        with patch("drv_crawler.job51.crawler.LOGGER.error"):
            stop_reason = record_error(
                state,
                "LEAF",
                RateLimitedError("WAF cooldown", retry_after=90),
                1,
            )

        self.assertEqual("rate_limit_cooldown_required", stop_reason)
        state.defer_code.assert_called_once_with(
            "LEAF", "RateLimitedError", "WAF cooldown", 1
        )
        state.set_rate_limit_cooldown.assert_called_once_with(
            90, "RateLimitedError: WAF cooldown"
        )
        state.fail_code.assert_not_called()

    def test_active_persistent_cooldown_skips_playwright_launch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = CrawlConfig(
                state_db_path=root / "state.sqlite3",
                output_path=root / "jobs.jsonl",
                coverage_path=root / "coverage.json",
            )
            with CrawlState(config.state_db_path) as state:
                state.set_rate_limit_cooldown(120, "WAF")

            with patch(
                "playwright.sync_api.sync_playwright",
                side_effect=AssertionError("Playwright launched during cooldown"),
            ):
                summary = Job51Crawler(config).run()

            self.assertEqual("rate_limit_cooldown_active", summary.stop_reason)
            self.assertEqual(0, summary.codes_attempted)
            self.assertTrue(config.coverage_path.exists())

    def test_existing_category_snapshot_bypasses_blocked_browser_entry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path = root / "categories.json"
            snapshot_path.write_text(
                json.dumps(category_snapshot(flatten_category_tree(fixture_tree()))),
                encoding="utf-8",
            )
            config = CrawlConfig(
                per_category_limit=1,
                max_codes=1,
                request_retries=0,
                delay_min_seconds=0,
                delay_max_seconds=0,
                batch_cooldown_seconds=0,
                proxy_policy="off",
                category_snapshot_path=snapshot_path,
                state_db_path=root / "state.sqlite3",
                output_path=root / "jobs.jsonl",
                coverage_path=root / "coverage.json",
            )
            session = MagicMock()
            response = FreshApiTests._response(
                items=[fixture_job()], total=1, request_id="request-1"
            )
            session.get.return_value = response
            crawler = Job51Crawler(config, http_session=session)

            with (
                patch(
                    "playwright.sync_api.sync_playwright",
                    side_effect=AssertionError("Playwright must not launch"),
                ),
                patch.object(
                    crawler,
                    "_bootstrap_page",
                    side_effect=RateLimitedError("HTTP 405"),
                ) as bootstrap,
            ):
                summary = crawler.run()

            self.assertEqual(1, summary.records_total)
            self.assertEqual(1, summary.codes_attempted)
            bootstrap.assert_not_called()
            session.get.assert_called_once()

    def test_bootstrap_rate_limit_is_persisted_before_category_work(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = CrawlConfig(
                state_db_path=root / "state.sqlite3",
                output_path=root / "jobs.jsonl",
                coverage_path=root / "coverage.json",
                category_snapshot_path=root / "missing-categories.json",
                soft_block_cooldown_seconds=75,
            )
            crawler = Job51Crawler(config)
            manager = MagicMock()
            manager.__enter__.return_value = MagicMock()
            browser = MagicMock()
            context = MagicMock()
            page = MagicMock()

            with (
                patch(
                    "playwright.sync_api.sync_playwright",
                    return_value=manager,
                ),
                patch.object(
                    crawler, "_launch", return_value=(browser, context, page)
                ),
                patch.object(
                    crawler,
                    "_bootstrap_page",
                    side_effect=RateLimitedError("blocked", retry_after=75),
                ),
                self.assertRaises(RateLimitedError),
            ):
                crawler.run()

            with CrawlState(config.state_db_path) as state:
                cooldown = state.active_rate_limit_cooldown()

            self.assertIsNotNone(cooldown)
            self.assertEqual(75.0, cooldown["cooldown_seconds"])
            coverage = json.loads(config.coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(
                75.0, coverage["rate_limit_cooldown"]["cooldown_seconds"]
            )
            context.close.assert_called_once_with()
            browser.close.assert_called_once_with()

    def test_final_timeout_still_rebuilds_page_for_next_category(self) -> None:
        crawler = Job51Crawler(CrawlConfig(request_retries=0))
        page = MagicMock()
        with (
            patch.object(
                crawler,
                "_search_once",
                side_effect=BrowserBootstrapError("request timed out"),
            ),
            patch.object(crawler, "_bootstrap_page") as bootstrap,
        ):
            with self.assertRaises(BrowserBootstrapError):
                crawler._search_with_retry(page, "LEAF", 1)

        bootstrap.assert_called_once_with(page)


class CollectionQuotaTests(unittest.TestCase):
    @staticmethod
    def _page(start: int, count: int, *, total: int) -> SearchPage:
        return SearchPage(
            items=[fixture_job(str(index)) for index in range(start, start + count)],
            total_count=total,
            request_id=f"request-{start}",
        )

    @staticmethod
    def _negotiable(job_id: str) -> dict[str, object]:
        item = fixture_job(job_id)
        item["provideSalaryString"] = "面议"
        item["jobSalaryMin"] = 0
        item["jobSalaryMax"] = 0
        return item

    def test_numeric_quota_reaches_fifty_across_three_low_frequency_pages(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(
                per_category_limit=50,
                max_pages_per_category=2,
                numeric_salary_only=True,
                delay_min_seconds=3,
                delay_max_seconds=5,
            )
        )
        state = MagicMock()
        state.code_record_ids.return_value = set()
        page = MagicMock()
        pages = [
            self._page(1, 20, total=1000),
            self._page(21, 20, total=1000),
            self._page(41, 20, total=1000),
        ]
        with (
            patch.object(crawler, "_search_with_retry", side_effect=pages) as search,
            patch("drv_crawler.job51.crawler.random.uniform", return_value=4),
            patch("drv_crawler.job51.crawler.time.sleep") as sleep,
        ):
            result = crawler._collect_code(page, state, "LEAF", 1)

        self.assertEqual(20, crawler.config.max_pages_per_category)
        self.assertEqual(50, len(result.records))
        self.assertEqual("completed", result.status)
        self.assertTrue(all(has_nonzero_numeric_salary(row) for row in result.records))
        self.assertEqual(
            [call(page, "LEAF", 1), call(page, "LEAF", 2), call(page, "LEAF", 3)],
            search.call_args_list,
        )
        self.assertEqual([call(4), call(4)], sleep.call_args_list)

    def test_fresh_api_pagination_carries_request_id_within_run(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(
                per_category_limit=30,
                max_pages_per_category=2,
                delay_min_seconds=0,
                delay_max_seconds=0,
            )
        )
        state = MagicMock()
        state.code_record_ids.return_value = set()
        pages = [
            self._page(1, 20, total=40),
            self._page(21, 20, total=40),
        ]
        with patch.object(
            crawler, "_search_with_retry", side_effect=pages
        ) as search:
            result = crawler._collect_code(None, state, "LEAF", 1)

        self.assertEqual(30, len(result.records))
        self.assertEqual("completed", result.status)
        self.assertEqual(
            [
                call(None, "LEAF", 1, request_id="", prior_total=None),
                call(
                    None,
                    "LEAF",
                    2,
                    request_id="request-1",
                    prior_total=40,
                ),
            ],
            search.call_args_list,
        )

    def test_fresh_api_natural_shortage_is_completed_below_quota(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(
                per_category_limit=50,
                max_pages_per_category=3,
                delay_min_seconds=0,
                delay_max_seconds=0,
            )
        )
        state = MagicMock()
        state.code_record_ids.return_value = set()
        with patch.object(
            crawler,
            "_search_with_retry",
            return_value=self._page(1, 7, total=7),
        ):
            result = crawler._collect_code(None, state, "LEAF", 1)

        self.assertEqual(7, len(result.records))
        self.assertEqual("completed", result.status)
        self.assertEqual(7, result.total_available)

    def test_fresh_api_all_wrong_function_codes_fail_closed(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(
                per_category_limit=1,
                delay_min_seconds=0,
                delay_max_seconds=0,
            )
        )
        state = MagicMock()
        state.code_record_ids.return_value = set()
        wrong = fixture_job("wrong")
        wrong["funcType1Code"] = "OTHER"
        with patch.object(
            crawler,
            "_search_with_retry",
            return_value=SearchPage(
                items=[wrong], total_count=1, request_id="request-1"
            ),
        ):
            with self.assertRaises(UpstreamSchemaError):
                crawler._collect_code(None, state, "LEAF", 1)

    def test_numeric_quota_exhausts_below_target_without_negotiable_jobs(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(
                per_category_limit=50,
                numeric_salary_only=True,
                delay_min_seconds=0,
                delay_max_seconds=0,
            )
        )
        state = MagicMock()
        state.code_record_ids.return_value = set()
        items = [fixture_job(str(index)) for index in range(1, 11)]
        items.extend(self._negotiable(str(index)) for index in range(11, 21))
        with patch.object(
            crawler,
            "_search_with_retry",
            return_value=SearchPage(items=items, total_count=20, request_id="one"),
        ):
            result = crawler._collect_code(MagicMock(), state, "LEAF", 1)

        self.assertEqual("completed", result.status)
        self.assertEqual(10, len(result.records))
        self.assertTrue(all(row["price_raw_joined"] == "1-2万" for row in result.records))
        self.assertTrue(all(has_nonzero_numeric_salary(row) for row in result.records))

    def test_default_single_record_quota_keeps_legacy_non_numeric_behavior(self) -> None:
        crawler = Job51Crawler(CrawlConfig(per_category_limit=1))
        state = MagicMock()
        state.code_record_ids.return_value = set()
        with patch.object(
            crawler,
            "_search_with_retry",
            return_value=SearchPage(
                items=[self._negotiable("1")], total_count=1, request_id="one"
            ),
        ):
            result = crawler._collect_code(MagicMock(), state, "LEAF", 1)

        self.assertFalse(crawler.config.numeric_salary_only)
        self.assertEqual(1, len(result.records))
        self.assertEqual("面议", result.records[0]["price_raw_joined"])
        self.assertFalse(result.records[0]["metadata"]["quota_salary_eligible"])

    def test_page_budget_resumes_at_next_unrequested_page(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(
                per_category_limit=10,
                max_pages_per_category=1,
                delay_min_seconds=0,
                delay_max_seconds=0,
            )
        )
        state = MagicMock()
        state.code_record_ids.return_value = set()
        with patch.object(
            crawler,
            "_search_with_retry",
            return_value=self._page(1, 5, total=100),
        ):
            result = crawler._collect_code(MagicMock(), state, "LEAF", 1)

        self.assertEqual("partial", result.status)
        self.assertEqual(2, result.next_page)

    def test_resume_does_not_count_existing_duplicate_ids_toward_quota(self) -> None:
        crawler = Job51Crawler(
            CrawlConfig(
                per_category_limit=50,
                numeric_salary_only=True,
                delay_min_seconds=3,
                delay_max_seconds=5,
            )
        )
        state = MagicMock()
        state.code_record_ids.return_value = {
            f"51job-{index}" for index in range(1, 41)
        }
        duplicate_page = self._page(21, 20, total=1000)
        new_page = self._page(41, 20, total=1000)
        with (
            patch.object(
                crawler,
                "_search_with_retry",
                side_effect=[duplicate_page, new_page],
            ) as search,
            patch("drv_crawler.job51.crawler.random.uniform", return_value=4),
            patch("drv_crawler.job51.crawler.time.sleep"),
        ):
            result = crawler._collect_code(MagicMock(), state, "LEAF", 3)

        self.assertEqual(10, len(result.records))
        self.assertEqual("completed", result.status)
        self.assertEqual(
            [
                call(ANY, "LEAF", 3),
                call(ANY, "LEAF", 4),
            ],
            search.call_args_list,
        )


class RuntimeSessionStateTests(unittest.TestCase):
    def _playwright_mocks(self) -> tuple[MagicMock, MagicMock, MagicMock]:
        playwright = MagicMock()
        browser = MagicMock()
        context = MagicMock()
        context.new_page.return_value = MagicMock()
        playwright.chromium.launch.return_value = browser
        browser.new_context.return_value = context
        return playwright, browser, context

    def test_runtime_session_is_loaded_before_strict_login_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_state = root / "runtime.json"
            login_state = root / "login.json"
            runtime_state.write_text("{}", encoding="utf-8")
            login_state.write_text("{}", encoding="utf-8")
            crawler = Job51Crawler(
                CrawlConfig(
                    proxy_policy="off",
                    storage_state_path=login_state,
                    runtime_session_state_path=runtime_state,
                )
            )
            playwright, browser, _context = self._playwright_mocks()

            crawler._launch(playwright)

            browser.new_context.assert_called_once_with(
                locale="zh-CN", storage_state=str(runtime_state.resolve())
            )

    def test_broken_runtime_session_falls_back_to_strict_login_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_state = root / "runtime.json"
            login_state = root / "login.json"
            runtime_state.write_text("{}", encoding="utf-8")
            login_state.write_text("{}", encoding="utf-8")
            crawler = Job51Crawler(
                CrawlConfig(
                    proxy_policy="off",
                    storage_state_path=login_state,
                    runtime_session_state_path=runtime_state,
                )
            )
            playwright, browser, context = self._playwright_mocks()
            browser.new_context.side_effect = [ValueError("broken runtime"), context]

            with patch("drv_crawler.job51.crawler.LOGGER.warning") as warning:
                crawler._launch(playwright)

            self.assertEqual(2, browser.new_context.call_count)
            self.assertEqual(
                str(login_state.resolve()),
                browser.new_context.call_args.kwargs["storage_state"],
            )
            warning.assert_called_once()
            browser.close.assert_not_called()

    def test_login_launch_ignores_anonymous_runtime_session(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_state = root / "runtime.json"
            login_state = root / "login.json"
            runtime_state.write_text("{}", encoding="utf-8")
            login_state.write_text("{}", encoding="utf-8")
            crawler = Job51Crawler(
                CrawlConfig(
                    proxy_policy="off",
                    storage_state_path=login_state,
                    runtime_session_state_path=runtime_state,
                )
            )
            playwright, browser, _context = self._playwright_mocks()

            crawler._launch(playwright, prefer_runtime_session=False)

            browser.new_context.assert_called_once_with(
                locale="zh-CN", storage_state=str(login_state.resolve())
            )

    def test_valid_search_json_atomically_saves_runtime_session(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_state = root / "runtime.json"
            crawler = Job51Crawler(
                CrawlConfig(
                    timeout_ms=5_000,
                    runtime_session_state_path=runtime_state,
                    storage_state_path=root / "login.json",
                )
            )
            context = MagicMock()
            saved_state = {
                "cookies": [{"name": "session", "value": "fixture"}],
                "origins": [],
            }
            context.storage_state.return_value = saved_state
            crawler._runtime_session_context = context
            page = MagicMock()
            response = MagicMock()
            response.url = (
                "https://we.51job.com/api/job/search-pc?"
                "function=LEAF&jobArea=000000&pageNum=1"
            )
            response.status = 200
            response.headers = {"content-type": "application/json"}
            response.json.return_value = {
                "status": "1",
                "resultbody": {"job": {"items": [], "totalcount": 0}},
            }
            waiter = MagicMock()
            waiter.__enter__.return_value = MagicMock(value=response)
            page.expect_response.return_value = waiter

            crawler._search_once(page, "LEAF", 1)

            self.assertEqual(
                saved_state,
                json.loads(runtime_state.read_text(encoding="utf-8")),
            )
            context.storage_state.assert_called_once_with()
            self.assertFalse(runtime_state.with_name(f".{runtime_state.name}.tmp").exists())

    def test_runtime_checkpoint_failure_keeps_previous_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_state = root / "runtime.json"
            old_state = {"cookies": [], "origins": [], "marker": "old"}
            runtime_state.write_text(json.dumps(old_state), encoding="utf-8")
            crawler = Job51Crawler(
                CrawlConfig(
                    runtime_session_state_path=runtime_state,
                    storage_state_path=root / "login.json",
                )
            )
            context = MagicMock()
            context.storage_state.return_value = {
                "cookies": [],
                "origins": [],
                "marker": "new",
            }
            crawler._runtime_session_context = context

            with (
                patch(
                    "drv_crawler.job51.crawler.atomic_write_json",
                    side_effect=OSError("disk full"),
                ),
                patch("drv_crawler.job51.crawler.LOGGER.warning") as warning,
            ):
                saved = crawler._persist_runtime_session_state()

            self.assertFalse(saved)
            self.assertEqual(
                old_state, json.loads(runtime_state.read_text(encoding="utf-8"))
            )
            warning.assert_called_once()

    def test_invalid_search_json_does_not_save_runtime_session(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_state = root / "runtime.json"
            crawler = Job51Crawler(
                CrawlConfig(
                    timeout_ms=5_000,
                    runtime_session_state_path=runtime_state,
                    storage_state_path=root / "login.json",
                )
            )
            context = MagicMock()
            crawler._runtime_session_context = context
            page = MagicMock()
            response = MagicMock()
            response.url = (
                "https://we.51job.com/api/job/search-pc?"
                "function=LEAF&jobArea=000000&pageNum=1"
            )
            response.status = 200
            response.headers = {"content-type": "application/json"}
            response.json.return_value = {
                "status": "1",
                "resultbody": {"job": {"totalcount": 0}},
            }
            waiter = MagicMock()
            waiter.__enter__.return_value = MagicMock(value=response)
            page.expect_response.return_value = waiter

            with self.assertRaises(UpstreamSchemaError):
                crawler._search_once(page, "LEAF", 1)

            self.assertFalse(runtime_state.exists())
            context.storage_state.assert_not_called()

    def test_login_and_runtime_paths_must_be_distinct(self) -> None:
        with TemporaryDirectory() as directory:
            shared = Path(directory) / "shared.json"
            with self.assertRaises(ValueError):
                CrawlConfig(
                    storage_state_path=shared,
                    runtime_session_state_path=shared,
                )


class PersistentProfileTests(unittest.TestCase):
    @staticmethod
    def _persistent_context() -> tuple[MagicMock, MagicMock, MagicMock]:
        playwright = MagicMock()
        context = MagicMock()
        page = MagicMock()
        context.pages = []
        context.new_page.return_value = page
        playwright.chromium.launch_persistent_context.return_value = context
        return playwright, context, page

    def test_profile_path_is_normalized_and_selects_persistent_context(self) -> None:
        with TemporaryDirectory() as directory:
            profile = Path(directory) / "browser-profile"
            crawler = Job51Crawler(
                CrawlConfig(
                    headless=False,
                    proxy_policy="off",
                    persistent_profile_dir=profile,
                )
            )
            playwright, context, page = self._persistent_context()

            browser, launched_context, launched_page = crawler._launch(playwright)

            self.assertEqual(profile.resolve(), crawler.config.persistent_profile_dir)
            self.assertIsNone(browser)
            self.assertIs(context, launched_context)
            self.assertIs(page, launched_page)
            profile_arg = (
                playwright.chromium.launch_persistent_context.call_args.args[0]
            )
            self.assertEqual(str(profile.resolve()), profile_arg)
            self.assertFalse(
                playwright.chromium.launch_persistent_context.call_args.kwargs[
                    "headless"
                ]
            )
            playwright.chromium.launch.assert_not_called()

    def test_run_closes_persistent_context_when_browser_is_none(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            crawler = Job51Crawler(
                CrawlConfig(
                    proxy_policy="off",
                    persistent_profile_dir=root / "browser-profile",
                    category_snapshot_path=root / "missing-categories.json",
                    state_db_path=root / "state.sqlite3",
                    output_path=root / "jobs.jsonl",
                    coverage_path=root / "coverage.json",
                    soft_block_cooldown_seconds=75,
                )
            )
            manager = MagicMock()
            manager.__enter__.return_value = MagicMock()
            context = MagicMock()
            page = MagicMock()

            with (
                patch(
                    "playwright.sync_api.sync_playwright",
                    return_value=manager,
                ),
                patch.object(
                    crawler, "_launch", return_value=(None, context, page)
                ),
                patch.object(
                    crawler,
                    "_bootstrap_page",
                    side_effect=RateLimitedError("blocked", retry_after=75),
                ),
                self.assertRaises(RateLimitedError),
            ):
                crawler.run()

            context.close.assert_called_once_with()

    def test_persistent_launch_failure_does_not_fall_back_to_ephemeral_browser(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            crawler = Job51Crawler(
                CrawlConfig(
                    proxy_policy="off",
                    persistent_profile_dir=Path(directory) / "browser-profile",
                )
            )
            playwright = MagicMock()
            playwright.chromium.launch_persistent_context.side_effect = RuntimeError(
                "profile is locked"
            )

            with self.assertRaises(BrowserBootstrapError):
                crawler._launch(playwright)

            playwright.chromium.launch_persistent_context.assert_called_once()
            playwright.chromium.launch.assert_not_called()

    def test_existing_profile_is_seeded_from_runtime_storage_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "browser-profile"
            profile.mkdir()
            (profile / "Interrupted Browser Startup").write_text(
                "fixture", encoding="utf-8"
            )
            runtime_state = root / "runtime.json"
            runtime_state.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "session",
                                "value": "fixture-cookie",
                                "domain": ".51job.com",
                                "path": "/",
                            }
                        ],
                        "origins": [
                            {
                                "origin": "https://we.51job.com",
                                "localStorage": [
                                    {"name": "fixture-key", "value": "fixture-value"}
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            crawler = Job51Crawler(
                CrawlConfig(
                    proxy_policy="off",
                    persistent_profile_dir=profile,
                    runtime_session_state_path=runtime_state,
                    storage_state_path=root / "login.json",
                )
            )
            playwright, context, _page = self._persistent_context()

            crawler._launch(playwright)

            context.add_cookies.assert_called_once_with(
                [
                    {
                        "name": "session",
                        "value": "fixture-cookie",
                        "domain": ".51job.com",
                        "path": "/",
                    }
                ]
            )
            context.add_init_script.assert_called_once()
            init_script = context.add_init_script.call_args.args[0]
            self.assertIn("fixture-key", init_script)
            self.assertIn("fixture-value", init_script)
            self.assertIn("https://we.51job.com", init_script)
            playwright.chromium.launch.assert_not_called()


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.categories = flatten_category_tree(fixture_tree())[:2]
        self.scope = {
            "schema_version": 1,
            "site": "51job",
            "city_code": "000000",
            "per_category_limit": 3,
        }

    def test_alias_paths_share_one_record_without_losing_coverage(self) -> None:
        with CrawlState(Path(":memory:")) as state:
            state.prepare_run(self.scope, self.categories)
            page, attempts = state.begin_code("LEAF")
            self.assertEqual((1, 1), (page, attempts))
            record = normalize_job(fixture_job(), "LEAF", "000000")
            state.finish_code(
                "LEAF",
                [record, record],
                status="completed",
                next_page=1,
                mismatch_count=0,
                total_available=1,
            )
            coverage = state.coverage()
            self.assertEqual(2, coverage["official_category_paths"])
            self.assertEqual(1, coverage["unique_search_codes"])
            self.assertEqual(1, coverage["record_count"])
            self.assertEqual(2, coverage["covered_paths"])
            self.assertEqual(0.0, coverage["quota_met_rate"])
            self.assertEqual(1, coverage["quota_counts"]["exhausted_below_quota"])
            self.assertEqual(0, coverage["quota_counts"]["quota_met"])
            self.assertTrue(
                all(
                    row["quota_outcome"] == "exhausted_below_quota"
                    for row in coverage["categories"]
                )
            )

            output = PACKAGE_DIR / ".state" / "tests" / "jobs.jsonl"
            report = PACKAGE_DIR / ".state" / "tests" / "coverage.json"
            state.export(output, report)
            exported = json.loads(output.read_text(encoding="utf-8").strip())
            self.assertEqual(2, len(exported["category_paths"]))
            self.assertEqual("Group", exported["category_level_1_raw"])
            self.assertEqual("Shared role", exported["category_level_3_raw"])
            self.assertEqual(2, len(exported["metadata"]["category_paths"]))

    def test_running_category_is_resumable_after_restart(self) -> None:
        state = CrawlState(Path(":memory:"))
        state.prepare_run(self.scope, self.categories)
        state.begin_code("LEAF")
        state.prepare_run(self.scope, self.categories)
        self.assertEqual(["LEAF"], state.pending_search_codes(3))
        state.close()

    def test_partial_category_is_reported_as_page_limit_below_quota(self) -> None:
        with CrawlState(Path(":memory:")) as state:
            state.prepare_run(self.scope, self.categories)
            state.begin_code("LEAF")
            state.finish_code(
                "LEAF",
                [normalize_job(fixture_job(), "LEAF", "000000")],
                status="partial",
                next_page=2,
                mismatch_count=0,
                total_available=100,
            )

            coverage = state.coverage()

            self.assertEqual(1, coverage["quota_counts"]["page_limit_below_quota"])
            self.assertEqual(0, coverage["quota_counts"]["quota_met"])
            self.assertEqual(0, coverage["covered_paths"])

    def test_quota_counts_use_unique_codes_and_keep_empty_separate(self) -> None:
        categories = flatten_category_tree(fixture_tree())
        scope = dict(self.scope, per_category_limit=1)
        with CrawlState(Path(":memory:")) as state:
            state.prepare_run(scope, categories)
            state.begin_code("LEAF")
            state.finish_code(
                "LEAF",
                [normalize_job(fixture_job(), "LEAF", "000000")],
                status="completed",
                next_page=1,
                mismatch_count=0,
                total_available=1,
            )
            state.begin_code("OTHER")
            state.finish_code(
                "OTHER",
                [],
                status="empty",
                next_page=1,
                mismatch_count=0,
                total_available=0,
            )

            coverage = state.coverage()

            self.assertEqual(2, coverage["quota_counts"]["unique_search_codes_total"])
            self.assertEqual(1, coverage["quota_counts"]["quota_met"])
            self.assertEqual(1, coverage["quota_counts"]["empty"])
            self.assertEqual(0.5, coverage["quota_met_rate"])

    def test_legacy_scope_is_upgraded_to_explicit_non_numeric_mode(self) -> None:
        with CrawlState(Path(":memory:")) as state:
            state.prepare_run(self.scope, self.categories)
            explicit_scope = CrawlConfig(per_category_limit=3).run_scope()
            state.prepare_run(explicit_scope, self.categories)

            self.assertFalse(state.coverage()["run_scope"]["numeric_salary_only"])

    def test_numeric_salary_mode_is_a_distinct_run_scope(self) -> None:
        with CrawlState(Path(":memory:")) as state:
            state.prepare_run(self.scope, self.categories)
            changed = CrawlConfig(
                per_category_limit=3, numeric_salary_only=True
            ).run_scope()
            with self.assertRaises(RunScopeMismatch):
                state.prepare_run(changed, self.categories)

    def test_rate_limit_defers_code_without_consuming_category_attempt(self) -> None:
        with CrawlState(Path(":memory:")) as state:
            categories = flatten_category_tree(fixture_tree())
            state.prepare_run(self.scope, categories)
            first_code = "LEAF"
            second_code = "OTHER"
            state.begin_code(first_code)
            defer_code = getattr(state, "defer_code", None)
            self.assertTrue(callable(defer_code), "CrawlState.defer_code is missing")
            if not callable(defer_code):
                return

            defer_code(first_code, "RateLimitedError", "WAF cooldown", 1)

            rows = [
                row
                for row in state.coverage()["categories"]
                if row["search_code"] == first_code
            ]
            self.assertTrue(rows)
            self.assertTrue(all(row["status"] == "pending" for row in rows))
            self.assertTrue(all(row["attempts"] == 0 for row in rows))
            self.assertTrue(
                all(row["last_error_type"] == "RateLimitedError" for row in rows)
            )
            pending = state.pending_search_codes(1)
            self.assertEqual(second_code, pending[0])
            self.assertEqual(first_code, pending[-1])

    def test_rate_limit_cooldown_is_persisted_and_expires(self) -> None:
        now = datetime(2026, 7, 23, tzinfo=UTC)
        with CrawlState(Path(":memory:")) as state:
            saved = state.set_rate_limit_cooldown(120, "WAF", now=now)
            active = state.active_rate_limit_cooldown(
                now=now + timedelta(seconds=30)
            )

            self.assertEqual(120.0, saved["cooldown_seconds"])
            self.assertIsNotNone(active)
            self.assertEqual(90.0, active["remaining_seconds"])
            self.assertIsNone(
                state.active_rate_limit_cooldown(
                    now=now + timedelta(seconds=121)
                )
            )

    def test_scope_mismatch_is_rejected(self) -> None:
        with CrawlState(Path(":memory:")) as state:
            state.prepare_run(self.scope, self.categories)
            changed = dict(self.scope, city_code="010000")
            with self.assertRaises(RunScopeMismatch):
                state.prepare_run(changed, self.categories)


class ConfigTests(unittest.TestCase):
    def test_unbounded_or_incompatible_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CrawlConfig(per_category_limit=101)
        with self.assertRaises(ValueError):
            CrawlConfig(page_size=50)
        with self.assertRaises(ValueError):
            CrawlConfig(delay_min_seconds=2, delay_max_seconds=1)
        with self.assertRaises(ValueError):
            CrawlConfig(batch_size=0)
        with self.assertRaises(ValueError):
            CrawlConfig(batch_cooldown_seconds=-1)
        with self.assertRaises(ValueError):
            CrawlConfig(soft_block_cooldown_seconds=29)
        with self.assertRaises(ValueError):
            CrawlConfig(numeric_salary_only="yes")  # type: ignore[arg-type]

    def test_fifty_record_quota_expands_page_budget_without_changing_default_mode(self) -> None:
        legacy = CrawlConfig(per_category_limit=1, max_pages_per_category=2)
        numeric = CrawlConfig(
            per_category_limit=50,
            max_pages_per_category=2,
            numeric_salary_only=True,
        )
        all_salaries = CrawlConfig(
            per_category_limit=50,
            max_pages_per_category=2,
            numeric_salary_only=False,
        )

        self.assertFalse(legacy.numeric_salary_only)
        self.assertEqual(2, legacy.max_pages_per_category)
        self.assertEqual(20, numeric.max_pages_per_category)
        self.assertEqual(3, all_salaries.max_pages_per_category)
        self.assertTrue(numeric.run_scope()["numeric_salary_only"])

    def test_rate_controls_are_configurable(self) -> None:
        self.assertEqual(900, CrawlConfig().soft_block_cooldown_seconds)
        try:
            config = CrawlConfig.from_mapping(
                {
                    "batch_size": 20,
                    "batch_cooldown_seconds": 45,
                    "soft_block_cooldown_seconds": 90,
                }
            )
        except Exception as exc:
            self.fail(f"rate controls were rejected: {type(exc).__name__}: {exc}")

        self.assertEqual(20, config.batch_size)
        self.assertEqual(45, config.batch_cooldown_seconds)
        self.assertEqual(90, config.soft_block_cooldown_seconds)


class LoginStateTests(unittest.TestCase):
    def test_login_probe_requires_is_login_and_account_id(self) -> None:
        self.assertFalse(_login_probe_is_authenticated(None))
        self.assertFalse(
            _login_probe_is_authenticated({"isLogin": True, "accountId": ""})
        )
        self.assertFalse(
            _login_probe_is_authenticated({"isLogin": False, "accountId": "123"})
        )
        self.assertTrue(
            _login_probe_is_authenticated({"isLogin": True, "accountId": "123"})
        )

    def _browser_mocks(self, probe: dict[str, object]) -> tuple[MagicMock, ...]:
        manager = MagicMock()
        manager.__enter__.return_value = object()
        page = MagicMock()
        page.evaluate.return_value = probe
        context = MagicMock()
        context.cookies.return_value = [{"name": "session"}, {"name": "account"}]
        browser = MagicMock()
        replay_page = MagicMock()
        replay_page.evaluate.return_value = probe
        replay_context = MagicMock()
        replay_context.new_page.return_value = replay_page
        browser.new_context.return_value = replay_context
        return manager, browser, context, page

    def test_anonymous_cookies_are_not_saved(self) -> None:
        config = CrawlConfig(
            storage_state_path=PACKAGE_DIR / ".state" / "tests" / "anonymous.json"
        )
        manager, browser, context, page = self._browser_mocks(
            {"isLogin": False, "accountId": ""}
        )
        with (
            patch("playwright.sync_api.sync_playwright", return_value=manager),
            patch.object(
                Job51Crawler,
                "_launch",
                return_value=(browser, context, page),
            ),
            patch.object(Job51Crawler, "_bootstrap_page"),
            patch.object(Job51Crawler, "_search_once"),
            patch("drv_crawler.job51.crawler.time.sleep"),
            patch("drv_crawler.job51.crawler.os.replace") as replace,
        ):
            with self.assertRaises(SessionRequiredError):
                save_login_state(config, wait_seconds=1)
        context.storage_state.assert_not_called()
        replace.assert_not_called()

    def test_verified_login_keeps_cookie_count_return_contract(self) -> None:
        config = CrawlConfig(
            storage_state_path=PACKAGE_DIR / ".state" / "tests" / "verified.json"
        )
        manager, browser, context, page = self._browser_mocks(
            {"isLogin": True, "accountId": "278526708"}
        )
        with (
            patch("playwright.sync_api.sync_playwright", return_value=manager),
            patch.object(
                Job51Crawler,
                "_launch",
                return_value=(browser, context, page),
            ) as launch,
            patch.object(Job51Crawler, "_bootstrap_page"),
            patch.object(Job51Crawler, "_search_once") as search,
            patch("drv_crawler.job51.crawler.time.sleep"),
            patch("drv_crawler.job51.crawler.os.replace") as replace,
        ):
            cookie_count = save_login_state(config, wait_seconds=1)
        self.assertEqual(2, cookie_count)
        self.assertEqual(2, search.call_count)
        launch.assert_called_once_with(
            ANY,
            load_storage_state=True,
            prefer_runtime_session=False,
            use_persistent_profile=False,
        )
        context.storage_state.assert_called_once()
        browser.new_context.assert_called_once()
        replace.assert_called_once()

    def test_logged_in_but_waf_blocked_state_is_not_saved(self) -> None:
        config = CrawlConfig(
            storage_state_path=PACKAGE_DIR / ".state" / "tests" / "waf.json"
        )
        manager, browser, context, page = self._browser_mocks(
            {"isLogin": True, "accountId": "278526708"}
        )
        with (
            patch("playwright.sync_api.sync_playwright", return_value=manager),
            patch.object(
                Job51Crawler,
                "_launch",
                return_value=(browser, context, page),
            ),
            patch.object(Job51Crawler, "_bootstrap_page"),
            patch.object(
                Job51Crawler,
                "_search_once",
                side_effect=RateLimitedError("Aliyun WAF", retry_after=120),
            ),
            patch("drv_crawler.job51.crawler.time.sleep"),
            patch("drv_crawler.job51.crawler.os.replace") as replace,
        ):
            with self.assertRaises(RateLimitedError):
                save_login_state(config, wait_seconds=1)

        context.storage_state.assert_not_called()
        replace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
