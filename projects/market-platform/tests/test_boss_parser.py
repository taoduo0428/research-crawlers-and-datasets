from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from drv_crawler.browser import BossAdapter, _repair_mojibake
from drv_crawler.core import ProjectConfig
from drv_crawler.models import CrawlRecord


class FakeClient:
    def ensure_allowed(self, _: str) -> None:
        return None


class FakeLocator:
    def inner_text(self, *, timeout: int) -> str:
        return ""

    def count(self) -> int:
        return 0


class FakePage:
    url = "https://www.zhipin.com/web/passport/zp/verify.html"

    def locator(self, _: str) -> FakeLocator:
        return FakeLocator()


class Code35Page(FakePage):
    url = "https://www.zhipin.com/web/passport/zp/verify.html?code=35"


class DomFallbackPage:
    def evaluate(self, _: str) -> list[dict[str, object]]:
        return [
            {
                "position": 1,
                "title": "Python Backend Engineer",
                "salary": "18-25K",
                "location": "Beijing",
                "company": "Example Company",
                "tags": ["Python"],
                "href": "https://www.zhipin.com/job_detail/example.html",
                "source": "salary_anchor_fallback",
            }
        ]


class CrawlPage:
    url = "https://www.zhipin.com/web/geek/jobs"

    def goto(self, *_: object, **__: object) -> None:
        return None

    def wait_for_timeout(self, _: int) -> None:
        return None

    def locator(self, _: str) -> FakeLocator:
        return FakeLocator()


class CrawlContext:
    def __init__(self, page: CrawlPage) -> None:
        self.page = page

    def on(self, *_: object) -> None:
        return None

    def new_page(self) -> CrawlPage:
        return self.page

    def close(self) -> None:
        return None


class CrawlBrowser:
    def close(self) -> None:
        return None


class PlaywrightManager:
    def __enter__(self) -> SimpleNamespace:
        return SimpleNamespace()

    def __exit__(self, *_: object) -> None:
        return None


class BossParserTests(unittest.TestCase):
    def test_captured_api_keeps_salary_raw(self) -> None:
        adapter = BossAdapter(
            ProjectConfig("crawler.config.yaml"), FakeClient(), lambda *_: None
        )
        adapter._captured_payloads = [
            {
                "code": 0,
                "zpData": {
                    "jobList": [
                        {
                            "jobName": "Python 后端开发工程师",
                            "salaryDesc": "18-20K",
                            "brandName": "示例公司",
                            "cityName": "北京",
                            "encryptJobId": "opaque-id",
                        }
                    ]
                },
            }
        ]
        records = adapter._records_from_api("https://www.zhipin.com/web/geek/jobs")
        self.assertEqual(1, len(records))
        self.assertEqual("18-20K", records[0].price_raw_joined)
        self.assertEqual("playwright_api", records[0].capture_method)

    def test_api_message_mojibake_is_repaired(self) -> None:
        original = "您的IP地址存在异常行为."
        mojibake = original.encode("utf-8").decode("gbk")
        self.assertEqual(original, _repair_mojibake(mojibake))

    def test_dom_salary_anchor_fallback_keeps_salary(self) -> None:
        records = BossAdapter._records_from_dom(
            DomFallbackPage(),  # type: ignore[arg-type]
            "https://www.zhipin.com/web/geek/jobs",
        )

        self.assertEqual(1, len(records))
        self.assertEqual("18-25K", records[0].price_raw_joined)
        self.assertEqual("playwright_dom_fallback", records[0].capture_method)

    def test_verify_redirect_is_classified_as_human_handoff(self) -> None:
        reason = BossAdapter._session_block_reason(FakePage())
        self.assertEqual(
            "BOSS security verification requires human handoff",
            reason,
        )

    def test_code35_redirect_records_network_identity_block(self) -> None:
        reason = BossAdapter._session_block_reason(Code35Page())
        self.assertEqual(
            "BOSS network identity blocked (code=35); human handoff required",
            reason,
        )

    def test_boss_does_not_load_missing_storage_state(self) -> None:
        adapter = BossAdapter(
            ProjectConfig("crawler.config.yaml"), FakeClient(), lambda *_: None
        )
        self.assertFalse(adapter.site_config["anonymous_only"])
        adapter.site_config["storage_state"] = ".crawler-state/definitely-missing-boss-state.json"
        self.assertIsNone(adapter._storage_state_for_run())

    def test_boss_reuses_persisted_storage_state_when_available(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "boss-storage-state.json"
            state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
            adapter = BossAdapter(
                ProjectConfig("crawler.config.yaml"), FakeClient(), lambda *_: None
            )
            adapter.site_config["anonymous_only"] = False
            adapter.site_config["storage_state"] = str(state_path)

            self.assertEqual(state_path, adapter._storage_state_for_run())

    def test_default_config_disables_archive_fallback(self) -> None:
        adapter = BossAdapter(
            ProjectConfig("crawler.config.yaml"), FakeClient(), lambda *_: None
        )
        result = adapter.result(1)

        with patch("drv_crawler.browser.BossArchiveCollector") as collector_class:
            adapter._append_archive_fallback(result, limit=1)

        collector_class.assert_not_called()
        self.assertEqual([], result.records)

    def test_live_disabled_without_archive_queries_no_fallback_source(self) -> None:
        adapter = BossAdapter(
            ProjectConfig("crawler.config.yaml"), FakeClient(), lambda *_: None
        )
        adapter.site_config["anonymous_live_enabled"] = False
        adapter.site_config["archive_fallback"] = {"enabled": False}

        with patch("drv_crawler.browser.BossArchiveCollector") as collector_class:
            result = adapter.crawl(1, with_details=False)

        collector_class.assert_not_called()
        self.assertEqual([], result.records)
        self.assertTrue(
            any("archive fallback is disabled" in note for note in result.runtime_notes)
        )

    def test_numeric_price_only_filters_deduped_api_records_before_limit(self) -> None:
        adapter = BossAdapter(
            ProjectConfig("crawler.config.yaml"), FakeClient(), lambda *_: None
        )
        adapter.site_config["auth_mode"] = "storage_state"
        adapter.site_config["anonymous_only"] = True
        adapter._captured_payloads = [
            {
                "code": 0,
                "zpData": {
                    "jobList": [
                        {
                            "jobName": "Negotiable Role",
                            "salaryDesc": "\u9762\u8bae",
                            "encryptJobId": "negotiable-job",
                        },
                        {
                            "jobName": "Numeric Salary Role",
                            "salaryDesc": "18-20K",
                            "encryptJobId": "numeric-job",
                        },
                    ]
                },
            }
        ]
        candidates = adapter._records_from_api(
            "https://www.zhipin.com/web/geek/jobs"
        )
        page = CrawlPage()
        context = CrawlContext(page)
        browser = CrawlBrowser()

        with (
            patch(
                "drv_crawler.browser.sync_playwright",
                return_value=PlaywrightManager(),
            ),
            patch("drv_crawler.browser._launch_browser", return_value=browser),
            patch("drv_crawler.browser._new_context", return_value=context),
            patch.object(adapter, "_replay_captured_api", return_value=None),
            patch.object(
                adapter,
                "_records_from_api",
                side_effect=[
                    [candidates[0], candidates[0]],
                    [candidates[0], candidates[1]],
                ],
            ) as records_from_api,
            patch.object(adapter, "_records_from_dom", return_value=[]),
        ):
            result = adapter.crawl(
                1,
                with_details=False,
                numeric_price_only=True,
            )

        self.assertEqual(
            ["Numeric Salary Role"],
            [item.title_raw for item in result.records],
        )
        self.assertEqual(1, result.filtered_out_count)
        self.assertEqual(2, records_from_api.call_count)

    def test_numeric_price_only_accepts_same_job_when_salary_becomes_numeric(self) -> None:
        adapter = BossAdapter(
            ProjectConfig("crawler.config.yaml"), FakeClient(), lambda *_: None
        )
        adapter.site_config["auth_mode"] = "storage_state"
        adapter.site_config["anonymous_only"] = True
        adapter._captured_payloads = [
            {
                "code": 0,
                "zpData": {
                    "jobList": [
                        {
                            "jobName": "Updated Salary Role",
                            "salaryDesc": "\u9762\u8bae",
                            "encryptJobId": "updated-salary-job",
                        }
                    ]
                },
            }
        ]
        rejected = adapter._records_from_api(
            "https://www.zhipin.com/web/geek/jobs"
        )[0]
        adapter._captured_payloads = [
            {
                "code": 0,
                "zpData": {
                    "jobList": [
                        {
                            "jobName": "Updated Salary Role",
                            "salaryDesc": "20-40K",
                            "encryptJobId": "updated-salary-job",
                        }
                    ]
                },
            }
        ]
        accepted = adapter._records_from_api(
            "https://www.zhipin.com/web/geek/jobs"
        )[0]
        self.assertEqual(rejected.record_id, accepted.record_id)
        calls = 0

        def api_batch(_: str) -> list[CrawlRecord]:
            nonlocal calls
            calls += 1
            return [rejected] if calls == 1 else [accepted]

        page = CrawlPage()
        context = CrawlContext(page)
        browser = CrawlBrowser()
        with (
            patch(
                "drv_crawler.browser.sync_playwright",
                return_value=PlaywrightManager(),
            ),
            patch("drv_crawler.browser._launch_browser", return_value=browser),
            patch("drv_crawler.browser._new_context", return_value=context),
            patch.object(adapter, "_replay_captured_api", return_value=None),
            patch.object(adapter, "_records_from_api", side_effect=api_batch),
            patch.object(adapter, "_records_from_dom", return_value=[]),
        ):
            result = adapter.crawl(
                1,
                with_details=False,
                numeric_price_only=True,
            )

        self.assertEqual(
            ["20-40K"],
            [item.price_raw_joined for item in result.records],
        )
        self.assertEqual(1, result.filtered_out_count)
        self.assertEqual(2, calls)

    def test_archive_fallback_is_merged_and_labeled(self) -> None:
        adapter = BossAdapter(
            ProjectConfig("crawler.config.yaml"), FakeClient(), lambda *_: None
        )
        adapter.site_config["archive_fallback"] = {"enabled": True}
        adapter._captured_payloads = [
            {
                "code": 0,
                "zpData": {
                    "jobList": [
                        {
                            "jobName": "Python 开发",
                            "salaryDesc": "15-20K",
                            "encryptJobId": "archive-job",
                        }
                    ]
                },
            }
        ]
        archive_record = adapter._records_from_api(
            "https://www.zhipin.com/zhaopin/sample/"
        )[0]
        archive_record.capture_method = "common_crawl_archive"
        archive_record.source_state = "archive_snapshot"
        collection = SimpleNamespace(
            records=[archive_record],
            archive_index="CC-MAIN-TEST",
            snapshots_fetched=1,
            candidates_considered=1,
            errors=[],
        )
        result = adapter.result(1)

        with patch("drv_crawler.browser.BossArchiveCollector") as collector_class:
            collector_class.return_value.collect.return_value = collection
            adapter._append_archive_fallback(result, limit=1)

        self.assertEqual([archive_record], result.records)
        self.assertEqual(1, result.pages_fetched)
        self.assertTrue(any("CC-MAIN-TEST" in note for note in result.runtime_notes))


if __name__ == "__main__":
    unittest.main()
