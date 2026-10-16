from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from drv_crawler.liepin.crawler import LiepinConfig, LiepinCrawler, iter_jsonl_records
from drv_crawler.liepin.http import HttpPage
from drv_crawler.liepin.tests.fixtures import (
    EMPTY_JOB_HTML,
    TAXONOMY_HTML,
    build_job_page,
)


class _FixtureClient:
    def __init__(
        self,
        responses: dict[str, str] | None = None,
        allow_requests: bool = True,
    ) -> None:
        self.responses = responses or {
            "https://www.liepin.com/": TAXONOMY_HTML,
            "https://www.liepin.com/career/java/pn0/": build_job_page(
                [(84299681, "18-29k·14薪")]
            ),
            "https://www.liepin.com/career/python/pn0/": EMPTY_JOB_HTML,
        }
        self.allow_requests = allow_requests
        self.calls: list[str] = []

    def get(self, url: str, *, referer: str | None = None) -> HttpPage:
        if not self.allow_requests:
            raise AssertionError(f"resume unexpectedly fetched {url}")
        self.calls.append(url)
        try:
            body = self.responses[url]
        except KeyError:
            raise AssertionError(url)
        return HttpPage(url, url, 200, body)


class CrawlerTests(unittest.TestCase):
    def test_end_to_end_checkpoint_export_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            config = LiepinConfig(
                output_dir=output_dir,
                per_category=1,
                min_taxonomy_categories=2,
                delay_min=0,
                delay_max=0,
                proxy_policy="direct",
            )
            client = _FixtureClient()
            with LiepinCrawler(config, client=client) as crawler:
                summary = crawler.run()
            self.assertEqual("success", summary.status)
            self.assertEqual(2, summary.categories_total)
            self.assertEqual(1, summary.categories_with_records)
            self.assertEqual(1, summary.categories_empty)
            self.assertEqual(1, summary.records_total)
            records = list(iter_jsonl_records(output_dir / "jobs.jsonl"))
            self.assertEqual(1, len(records))

            with LiepinCrawler(config, client=_FixtureClient(allow_requests=False)) as crawler:
                resumed = crawler.run()
            self.assertEqual("success", resumed.status)
            self.assertEqual(0, resumed.categories_attempted_this_run)
            self.assertEqual(1, resumed.records_total)

    def test_two_pages_fill_fifty_numeric_salary_jobs(self) -> None:
        first_jobs = [(job_id, "10-20k") for job_id in range(1, 36)]
        first_jobs.extend((job_id, "面议") for job_id in range(36, 41))
        second_jobs = [(job_id, "20-30k") for job_id in range(41, 61)]
        responses = {
            "https://www.liepin.com/": TAXONOMY_HTML,
            "https://www.liepin.com/career/java/pn0/": build_job_page(
                first_jobs, current_page=0, page_links=(0, 1)
            ),
            "https://www.liepin.com/career/java/pn1/": build_job_page(
                second_jobs, current_page=1, page_links=(0, 1)
            ),
            "https://www.liepin.com/career/python/pn0/": EMPTY_JOB_HTML,
        }
        with tempfile.TemporaryDirectory() as directory:
            config = LiepinConfig(
                output_dir=Path(directory),
                per_category=50,
                numeric_salary_only=True,
                max_pages_per_category=3,
                min_taxonomy_categories=2,
                delay_min=0,
                delay_max=0,
                proxy_policy="direct",
            )
            client = _FixtureClient(responses)
            with LiepinCrawler(config, client=client) as crawler:
                summary = crawler.run()
            self.assertEqual("success", summary.status)
            self.assertEqual(50, summary.records_total)
            self.assertIn("https://www.liepin.com/career/java/pn1/", client.calls)
            records = list(iter_jsonl_records(config.jobs_path))
            self.assertTrue(all(record["budget_raw"] != "面议" for record in records))
            self.assertEqual(2, max(record["page_no"] for record in records))
            self.assertEqual(50, max(record["list_position"] for record in records))
            coverage = __import__("json").loads(
                config.coverage_path.read_text(encoding="utf-8")
            )
            self.assertEqual(1, coverage["quota_outcomes"]["quota_met"])
            self.assertEqual(0.5, coverage["quota_met_rate"])
            java = next(
                item for item in coverage["categories"] if item["level_3"] == "Java"
            )
            self.assertEqual("quota_met", java["quota_outcome"])
            self.assertEqual(2, java["pages_fetched"])
            self.assertEqual(5, java["filtered_non_numeric"])

    def test_short_last_page_is_exhausted_below_quota(self) -> None:
        responses = {
            "https://www.liepin.com/": TAXONOMY_HTML,
            "https://www.liepin.com/career/java/pn0/": build_job_page(
                [(1, "10-20k"), (2, "20-30k")]
            ),
            "https://www.liepin.com/career/python/pn0/": EMPTY_JOB_HTML,
        }
        with tempfile.TemporaryDirectory() as directory:
            config = LiepinConfig(
                output_dir=Path(directory),
                per_category=50,
                numeric_salary_only=True,
                min_taxonomy_categories=2,
                delay_min=0,
                delay_max=0,
                proxy_policy="direct",
            )
            with LiepinCrawler(config, client=_FixtureClient(responses)) as crawler:
                crawler.run()
            coverage = __import__("json").loads(
                config.coverage_path.read_text(encoding="utf-8")
            )
            self.assertEqual(1, coverage["quota_outcomes"]["exhausted_below_quota"])
            self.assertEqual(1, coverage["quota_outcomes"]["empty"])

    def test_second_page_challenge_fails_without_partial_commit(self) -> None:
        first_jobs = [(job_id, "10-20k") for job_id in range(1, 41)]
        responses = {
            "https://www.liepin.com/": TAXONOMY_HTML,
            "https://www.liepin.com/career/java/pn0/": build_job_page(
                first_jobs, current_page=0, page_links=(0, 1)
            ),
            "https://www.liepin.com/career/java/pn1/": "<title>安全验证</title>",
            "https://www.liepin.com/career/python/pn0/": EMPTY_JOB_HTML,
        }
        with tempfile.TemporaryDirectory() as directory:
            config = LiepinConfig(
                output_dir=Path(directory),
                per_category=50,
                numeric_salary_only=True,
                min_taxonomy_categories=2,
                delay_min=0,
                delay_max=0,
                proxy_policy="direct",
            )
            with LiepinCrawler(config, client=_FixtureClient(responses)) as crawler:
                summary = crawler.run()
            self.assertEqual("failed", summary.status)
            self.assertEqual(0, summary.records_total)
            coverage = __import__("json").loads(
                config.coverage_path.read_text(encoding="utf-8")
            )
            self.assertEqual(1, coverage["quota_outcomes"]["failed"])

    def test_page_limit_below_quota_is_not_reported_as_failure(self) -> None:
        first_jobs = [(job_id, "10-20k") for job_id in range(1, 41)]
        responses = {
            "https://www.liepin.com/": TAXONOMY_HTML,
            "https://www.liepin.com/career/java/pn0/": build_job_page(
                first_jobs, current_page=0, page_links=(0, 1)
            ),
            "https://www.liepin.com/career/python/pn0/": EMPTY_JOB_HTML,
        }
        with tempfile.TemporaryDirectory() as directory:
            config = LiepinConfig(
                output_dir=Path(directory),
                per_category=50,
                numeric_salary_only=True,
                max_pages_per_category=1,
                min_taxonomy_categories=2,
                delay_min=0,
                delay_max=0,
                proxy_policy="direct",
            )
            with LiepinCrawler(config, client=_FixtureClient(responses)) as crawler:
                summary = crawler.run()
            self.assertEqual(40, summary.records_total)
            self.assertEqual("partial", summary.status)
            self.assertEqual("page_limit_below_quota", summary.stop_reason)
            coverage = __import__("json").loads(
                config.coverage_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                1, coverage["quota_outcomes"]["page_limit_below_quota"]
            )
            java = next(
                item for item in coverage["categories"] if item["level_3"] == "Java"
            )
            self.assertEqual("page_limit_below_quota", java["quota_outcome"])

    def test_repeated_second_page_is_failed(self) -> None:
        page = build_job_page(
            [(job_id, "10-20k") for job_id in range(1, 41)],
            current_page=0,
            page_links=(0, 1),
        )
        responses = {
            "https://www.liepin.com/": TAXONOMY_HTML,
            "https://www.liepin.com/career/java/pn0/": page,
            "https://www.liepin.com/career/java/pn1/": page.replace(
                'data-currentpage="0"', 'data-currentpage="1"'
            ),
            "https://www.liepin.com/career/python/pn0/": EMPTY_JOB_HTML,
        }
        with tempfile.TemporaryDirectory() as directory:
            config = LiepinConfig(
                output_dir=Path(directory),
                per_category=50,
                numeric_salary_only=True,
                min_taxonomy_categories=2,
                delay_min=0,
                delay_max=0,
                proxy_policy="direct",
            )
            with LiepinCrawler(config, client=_FixtureClient(responses)) as crawler:
                summary = crawler.run()
            self.assertEqual("failed", summary.status)
            self.assertEqual(0, summary.records_total)


if __name__ == "__main__":
    unittest.main()
