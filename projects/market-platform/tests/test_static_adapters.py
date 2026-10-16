from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from drv_crawler.core import BlockedResponseError, FetchedPage, ProjectConfig
from drv_crawler.epwk_browser import (
    EpwkBrowserLimitReached,
    EpwkBrowserRenderError,
)
from drv_crawler.models import SiteResult
from drv_crawler.publication import PublicationFilter
from drv_crawler.sites import (
    EpwkAdapter,
    ProginnAdapter,
    ShixianAdapter,
    ZbjAdapter,
    extract_price_strings,
)


FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def get(self, url: str, **_: object) -> FetchedPage:
        name = self.mapping[url]
        content = (FIXTURES / name).read_bytes()
        return FetchedPage(url, url, 200, content, "utf-8")

    def load_storage_state(self, *_: object, **__: object) -> int:
        return 0


class InlineClient:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []
        self.storage_state_calls: list[tuple[str, set[str]]] = []

    def get(self, url: str, **_: object) -> FetchedPage:
        self.calls.append(url)
        return FetchedPage(url, url, 200, self.mapping[url].encode(), "utf-8")

    def load_storage_state(
        self, path: str, *, allowed_domains: set[str]
    ) -> int:
        self.storage_state_calls.append((path, allowed_domains))
        return 1


class AdapterConfig:
    def __init__(self, site_name: str, site_config: dict[str, object], *, detail_cap: int) -> None:
        self.site_name = site_name
        self.site_config = site_config
        self.detail_cap = detail_cap

    def site(self, name: str) -> dict[str, object]:
        if name != self.site_name:
            raise KeyError(name)
        return self.site_config

    def section(self, name: str) -> dict[str, object]:
        if name == "limits":
            return {
                "max_pages_per_site": 10,
                "max_detail_requests_per_site": self.detail_cap,
            }
        return {}


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = ProjectConfig("crawler.config.yaml")

    @staticmethod
    def checkpoint(*_: object) -> None:
        return None

    def test_epwk(self) -> None:
        client = FakeClient({"https://task.epwk.com/?o=7": "epwk.html"})
        result = EpwkAdapter(self.config, client, self.checkpoint).crawl(
            1, with_details=False
        )
        self.assertEqual("success", result.status)
        self.assertEqual("30万-50万元", result.records[0].price_raw_joined)

    @staticmethod
    def _epwk_list_html(detail_url: str) -> str:
        return f"""
        <div class="content-lists">
          <div class="itemblock">
            <div class="title"><a href="{detail_url}">测试任务</a></div>
            <div class="right"><span class="price">5000元</span></div>
          </div>
        </div>
        """

    @staticmethod
    def _epwk_sitemap_xml(entries: list[tuple[str, str]]) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(
                f"<url><loc>{url}</loc><lastmod>{lastmod}</lastmod></url>"
                for url, lastmod in entries
            )
            + "</urlset>"
        )

    @staticmethod
    def _epwk_primary_detail_html(
        *,
        title: str,
        price: str,
        published: str,
    ) -> str:
        return f"""
        <div class="task_user_info"><h1>{title}</h1></div>
        <div class="contxt">
          <div class="catList"><span class="money">{price}</span></div>
          <div class="task-progress-item">
            <span class="step_on">
              <b>&#21457;&#24067;&#38656;&#27714;</b>
              <font>{published}</font>
            </span>
          </div>
        </div>
        """

    def _crawl_epwk_detail(self, detail_html: str) -> SiteResult:
        list_url = "https://task.epwk.com/"
        detail_url = "https://task.epwk.com/1234567/"
        client = InlineClient(
            {
                list_url: self._epwk_list_html(detail_url),
                detail_url: detail_html,
            }
        )
        adapter = EpwkAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "start_url": list_url,
            "page_url_template": "https://task.epwk.com/page{page}.html",
            "detail_sample_limit": 1,
        }
        return adapter.crawl(1, with_details=True)

    def _epwk_filtered_adapter(
        self, detail_html: str
    ) -> tuple[EpwkAdapter, InlineClient]:
        list_url = "https://task.epwk.com/"
        detail_url = "https://task.epwk.com/1234567/"
        client = InlineClient(
            {
                list_url: self._epwk_list_html(detail_url),
                detail_url: detail_html,
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html",
                },
                detail_cap=1,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3,
            as_of=date(2026, 7, 21),
        )
        return adapter, client

    def test_epwk_static_publication_date_does_not_start_renderer(self) -> None:
        adapter, _ = self._epwk_filtered_adapter(
            """
            <div class="task-progress-item">
              <span class="step_off">
                <b>&#21457;&#24067;&#38656;&#27714;</b>
                <font>2026.07.18</font>
              </span>
            </div>
            """
        )

        with patch("drv_crawler.sites.EpwkPublicationRenderer") as renderer_cls:
            result = adapter.crawl(1, with_details=True)

        renderer_cls.assert_not_called()
        self.assertEqual(1, len(result.records))
        self.assertEqual("2026.07.18", result.records[0].published_at_raw)
        self.assertEqual(
            "detail_progress",
            result.records[0].metadata["publication_date_source"],
        )

    def test_epwk_missing_static_date_uses_bounded_renderer_html(self) -> None:
        adapter, _ = self._epwk_filtered_adapter("<main>no publication date</main>")
        renderer = Mock()
        renderer.render.return_value = """
            <div class="task-progress-item">
              <span class="step_on">
                <b>&#21457;&#24067;&#38656;&#27714;</b>
                <font>2026.07.17</font>
              </span>
            </div>
        """

        with patch(
            "drv_crawler.sites.EpwkPublicationRenderer",
            return_value=renderer,
        ):
            result = adapter.crawl(1, with_details=True)

        self.assertEqual(1, len(result.records))
        self.assertEqual("2026.07.17", result.records[0].published_at_raw)
        self.assertEqual(
            "detail_progress",
            result.records[0].metadata["publication_date_source"],
        )
        self.assertTrue(
            result.records[0].metadata["publication_browser_rendered"]
        )
        renderer.render.assert_called_once_with(
            "https://task.epwk.com/1234567/",
            referer="https://task.epwk.com/",
        )
        renderer.close.assert_called_once_with()

    def test_epwk_renderer_limit_and_error_keep_publication_unknown(self) -> None:
        cases = (
            (
                EpwkBrowserLimitReached("detail limit reached"),
                "detail_browser_limit",
            ),
            (EpwkBrowserRenderError("render failed"), "detail_browser"),
        )
        for error, expected_stage in cases:
            with self.subTest(stage=expected_stage):
                adapter, _ = self._epwk_filtered_adapter(
                    "<main>no publication date</main>"
                )
                renderer = Mock()
                renderer.render.side_effect = error

                with patch(
                    "drv_crawler.sites.EpwkPublicationRenderer",
                    return_value=renderer,
                ):
                    result = adapter.crawl(1, with_details=True)

                self.assertEqual([], result.records)
                self.assertEqual(1, result.filter_counts["publication_unknown"])
                self.assertIn(
                    expected_stage,
                    [failure.stage for failure in result.failures],
                )
                renderer.close.assert_called_once_with()

    def test_epwk_crawl_closes_renderer_when_crawl_raises(self) -> None:
        adapter, _ = self._epwk_filtered_adapter("<main>unused</main>")
        renderer = Mock()
        adapter._publication_renderer = renderer

        with patch.object(adapter, "_crawl", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                adapter.crawl(1, with_details=True)

        renderer.close.assert_called_once_with()
        self.assertIsNone(adapter._publication_renderer)

    def test_epwk_detail_uses_publish_demand_progress_date(self) -> None:
        result = self._crawl_epwk_detail(
            """
            <div class="contxt">
              <div class="title">测试任务详情</div>
              <div class="catList"><span class="money">5000元</span></div>
              <div class="task-progress-item">
                <span class="step_off"><b>威客投标</b><font>2026.07.15</font></span>
              </div>
              <div class="task-progress-item">
                <span class="step_off"><b> 发布需求 </b><font>2026.07.14</font></span>
              </div>
              <div class="time">截稿时间：2026-08-08</div>
            </div>
            """
        )

        self.assertEqual("2026.07.14", result.records[0].published_at_raw)

    def test_epwk_detail_accepts_active_publish_demand_step(self) -> None:
        result = self._crawl_epwk_detail(
            """
            <div class="contxt">
              <div class="task-progress-item">
                <span class="step_on"><b>发布需求</b><font>2026.07.18</font></span>
              </div>
              <div class="task-progress-item">
                <span class="step_off"><b>威客投标</b><font>2026.07.19</font></span>
              </div>
            </div>
            """
        )

        self.assertEqual("2026.07.18", result.records[0].published_at_raw)

    def test_epwk_detail_uses_scoped_header_date_as_fallback(self) -> None:
        result = self._crawl_epwk_detail(
            """
            <div class="task-progress-item">
              <span class="step_on"><b>发布需求</b></span>
            </div>
            <div class="task-user-info-action">
              <span><i class="iconfont"></i><span class="dib_vm">2026-07-18</span></span>
              <span><span class="dib_vm">任务编号：1234567</span></span>
            </div>
            <div class="span_state">
              距投标截止
              <span id="TimeCountdown" starttime="2026/07/31 14:35:28"></span>
            </div>
            """
        )

        self.assertEqual("2026-07-18", result.records[0].published_at_raw)
        self.assertEqual(
            "detail_header",
            result.records[0].metadata["publication_date_source"],
        )

    def test_epwk_detail_rejects_conflicting_progress_and_header_dates(self) -> None:
        result = self._crawl_epwk_detail(
            """
            <div class="task-progress-item">
              <span class="step_on"><b>发布需求</b><font>2026.07.17</font></span>
            </div>
            <div class="task-user-info-action">
              <span><span class="dib_vm">2026-07-18</span></span>
              <span><span class="dib_vm">任务编号：1234567</span></span>
            </div>
            """
        )

        self.assertIsNone(result.records[0].published_at_raw)
        self.assertEqual(
            "detail_conflict",
            result.records[0].metadata["publication_date_source"],
        )
        self.assertEqual(
            {
                "detail_progress": "2026.07.17",
                "detail_header": "2026-07-18",
            },
            result.records[0].metadata["publication_date_candidates"],
        )

    def test_epwk_header_date_requires_current_task_number(self) -> None:
        result = self._crawl_epwk_detail(
            """
            <div class="task-user-info-action">
              <span><span class="dib_vm">2026-07-18</span></span>
              <span><span class="dib_vm">任务编号：7654321</span></span>
            </div>
            <span class="dib_vm">2026-08-08</span>
            """
        )

        self.assertIsNone(result.records[0].published_at_raw)
        self.assertEqual(
            "unavailable",
            result.records[0].metadata["publication_date_source"],
        )

    def test_epwk_loads_configured_storage_state_before_requests(self) -> None:
        list_url = "https://task.epwk.com/?o=7"
        detail_url = "https://task.epwk.com/1234567/"
        state_path = ".crawler-state/epwk-storage-state.json"
        client = InlineClient({list_url: self._epwk_list_html(detail_url)})
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "storage_state": state_path,
                },
                detail_cap=1,
            ),
            client,
            self.checkpoint,
        )

        adapter.crawl(1, with_details=False)

        self.assertEqual(
            [(state_path, {"epwk.com", "task.epwk.com"})],
            client.storage_state_calls,
        )
        self.assertEqual([list_url], client.calls)

    def test_epwk_storage_state_failure_is_recorded_and_crawl_continues(self) -> None:
        class FailingStateClient(InlineClient):
            def load_storage_state(
                self, path: str, *, allowed_domains: set[str]
            ) -> int:
                self.storage_state_calls.append((path, allowed_domains))
                raise OSError("simulated storage-state load failure")

        list_url = "https://task.epwk.com/?o=7"
        detail_url = "https://task.epwk.com/1234567/"
        state_path = ".crawler-state/epwk-storage-state.json"
        client = FailingStateClient(
            {list_url: self._epwk_list_html(detail_url)}
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "storage_state": state_path,
                },
                detail_cap=1,
            ),
            client,
            self.checkpoint,
        )

        result = adapter.crawl(1, with_details=False)

        self.assertEqual(["session_load"], [item.stage for item in result.failures])
        self.assertEqual([list_url], client.calls)
        self.assertEqual(1, len(result.records))

    def test_epwk_project_config_uses_publication_order(self) -> None:
        site = self.config.site("epwk")

        self.assertEqual(0.5, site["download_delay"])
        self.assertEqual("https://task.epwk.com/?o=7", site["start_url"])
        self.assertEqual(
            "https://task.epwk.com/page{page}.html?o=7",
            site["page_url_template"],
        )
        self.assertFalse(site["publication_sorted_desc"])
        self.assertEqual(
            ".crawler-state/epwk-storage-state.json", site["storage_state"]
        )
        sitemap = site["publication_sitemap"]
        self.assertTrue(sitemap["primary_source"])
        self.assertEqual(10, sitemap["validation_sample_size"])
        self.assertEqual(50, sitemap["checkpoint_interval"])
        self.assertEqual(30, sitemap["candidate_lookback_days"])
        self.assertFalse(sitemap["boundary_only"])
        self.assertEqual(12, len(sitemap["urls"]))
        self.assertEqual(12, sitemap["max_files"])
        self.assertEqual(5_000_000, sitemap["max_response_bytes"])
        self.assertEqual(30_000, sitemap["max_entries_per_file"])
        self.assertEqual(50, site["publication_unknown_cutoff_pages"])

    def test_epwk_primary_sitemap_validates_and_collects_real_details(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        first_url = "https://task.epwk.com/2001/"
        second_url = "https://task.epwk.com/2002/"
        checkpoints: list[str] = []

        def checkpoint(_: SiteResult, stage: str) -> None:
            checkpoints.append(stage)

        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (first_url, "2026-07-20"),
                        (second_url, "2026-04-21"),
                    ]
                ),
                first_url: self._epwk_primary_detail_html(
                    title="Primary task one",
                    price="5000 CNY",
                    published="2026.07.20",
                ),
                second_url: self._epwk_primary_detail_html(
                    title="Primary task two",
                    price="6000 CNY",
                    published="2026.04.21",
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 1,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual([sitemap_url, first_url, second_url], client.calls)
        self.assertEqual(
            ["Primary task one", "Primary task two"],
            [record.title_raw for record in result.records],
        )
        self.assertEqual(
            ["5000 CNY", "6000 CNY"],
            [record.price_raw_joined for record in result.records],
        )
        self.assertEqual(
            ["div.task_user_info h1", "div.task_user_info h1"],
            [
                record.metadata["detail_title_selector"]
                for record in result.records
            ],
        )
        self.assertEqual(2, result.details_fetched)
        self.assertFalse(result.window_complete)
        self.assertEqual("partial", result.status)
        self.assertEqual(
            "sitemap_source_exhausted_not_global", result.stop_reason
        )
        self.assertTrue(result.source_coverage["sitemap"]["complete"])
        self.assertEqual(
            "source_exhausted",
            result.source_coverage["sitemap"]["stop_reason"],
        )
        self.assertIn("publication_sitemap_validation", checkpoints)
        self.assertEqual("finished", checkpoints[-1])

    def test_epwk_primary_sitemap_ignores_1970_task_date_without_blocking(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        undated_url = "https://task.epwk.com/1900/"
        recent_url = "https://task.epwk.com/3000/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (undated_url, "1970-01-01"),
                        (recent_url, "2026-07-20"),
                    ]
                ),
                recent_url: self._epwk_primary_detail_html(
                    title="Recent dated task",
                    price="5000 CNY",
                    published="2026.07.20",
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual([sitemap_url, recent_url], client.calls)
        self.assertEqual(
            ["Recent dated task"],
            [record.title_raw for record in result.records],
        )
        self.assertEqual([], result.failures)
        coverage = result.source_coverage["sitemap"]
        self.assertEqual(1, coverage["undated_task_entries"])
        self.assertEqual(1, coverage["global_window_candidates"])
        self.assertTrue(coverage["complete"])
        self.assertTrue(
            any(
                "undated_task_entries=1" in note
                for note in result.runtime_notes
            )
        )

    def test_epwk_primary_sitemap_uses_current_detail_price_node(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        detail_url = "https://task.epwk.com/3001/"
        detail_html = """
        <div class="task_user_info">
          <h1>Current price task</h1>
          <div class="nummoney"><span>8800 CNY</span></div>
        </div>
        <div class="task-progress-item">
          <span class="step_on">
            <b>&#21457;&#24067;&#38656;&#27714;</b>
            <font>2026.07.20</font>
          </span>
        </div>
        """
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [(detail_url, "2026-07-20")]
                ),
                detail_url: detail_html,
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual(1, len(result.records))
        evidence = result.records[0].price_evidence
        self.assertEqual(1, len(evidence))
        self.assertEqual("8800 CNY", evidence[0].raw_text)
        self.assertEqual("detail_summary", evidence[0].price_source)
        self.assertEqual(
            ".task_user_info .nummoney > span",
            evidence[0].evidence_location,
        )

    def test_epwk_primary_sitemap_partitions_are_disjoint_and_complete(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        detail_urls = [
            f"https://task.epwk.com/{3000 + index}/"
            for index in range(6)
        ]
        sitemap_entries = [
            (detail_url, f"2026-07-{20 - index:02d}")
            for index, detail_url in enumerate(detail_urls)
        ]
        mapping = {
            sitemap_url: self._epwk_sitemap_xml(sitemap_entries),
            **{
                detail_url: self._epwk_primary_detail_html(
                    title=f"Partition task {index}",
                    price=f"{5000 + index} CNY",
                    published=published,
                )
                for index, (detail_url, published) in enumerate(
                    sitemap_entries
                )
            },
        }

        def crawl_partition(
            partition_count: int,
            partition_index: int,
        ) -> tuple[SiteResult, InlineClient]:
            client = InlineClient(dict(mapping))
            adapter = EpwkAdapter(
                AdapterConfig(
                    "epwk",
                    {
                        "start_url": list_url,
                        "page_url_template": (
                            "https://task.epwk.com/page{page}.html?o=7"
                        ),
                        "publication_max_detail_requests": 20,
                        "publication_sitemap": {
                            "primary_source": True,
                            "validation_sample_size": 1,
                            "checkpoint_interval": 50,
                            "partition_count": partition_count,
                            "partition_index": partition_index,
                            "urls": [sitemap_url],
                        },
                    },
                    detail_cap=20,
                ),
                client,
                self.checkpoint,
            )
            adapter.publication_filter = PublicationFilter(
                recent_months=3, as_of=date(2026, 7, 21)
            )
            adapter.complete_publication_window = True
            return adapter.crawl(100, with_details=True), client

        full_result, _ = crawl_partition(1, 0)
        partition_runs = [
            crawl_partition(3, partition_index)
            for partition_index in range(3)
        ]
        full_urls = {
            record.detail_url for record in full_result.records
        }
        partition_sets = [
            {record.detail_url for record in result.records}
            for result, _ in partition_runs
        ]

        self.assertEqual(full_urls, set().union(*partition_sets))
        for left_index, left in enumerate(partition_sets):
            for right in partition_sets[left_index + 1 :]:
                self.assertTrue(left.isdisjoint(right))
        for partition_index, (result, client) in enumerate(partition_runs):
            expected_urls = detail_urls[partition_index::3]
            self.assertEqual(
                expected_urls,
                [record.detail_url for record in result.records],
            )
            self.assertEqual([sitemap_url, *expected_urls], client.calls)
            coverage = result.source_coverage["sitemap"]
            self.assertEqual(6, coverage["global_window_candidates"])
            self.assertEqual(3, coverage["partition_count"])
            self.assertEqual(partition_index, coverage["partition_index"])
            self.assertEqual(2, coverage["window_candidates"])
            self.assertTrue(coverage["complete"])

    def test_epwk_primary_sitemap_rejects_invalid_partition_config(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        detail_url = "https://task.epwk.com/3000/"
        invalid_values = (
            (0, 0),
            (4, 0),
            (2, -1),
            (2, 2),
            (True, 0),
            (2, False),
            ("3", 0),
        )
        for partition_count, partition_index in invalid_values:
            with self.subTest(
                partition_count=partition_count,
                partition_index=partition_index,
            ):
                client = InlineClient(
                    {
                        sitemap_url: self._epwk_sitemap_xml(
                            [(detail_url, "2026-07-20")]
                        )
                    }
                )
                adapter = EpwkAdapter(
                    AdapterConfig(
                        "epwk",
                        {
                            "start_url": list_url,
                            "page_url_template": (
                                "https://task.epwk.com/page{page}.html?o=7"
                            ),
                            "publication_max_detail_requests": 10,
                            "publication_sitemap": {
                                "primary_source": True,
                                "validation_sample_size": 1,
                                "checkpoint_interval": 50,
                                "partition_count": partition_count,
                                "partition_index": partition_index,
                                "urls": [sitemap_url],
                            },
                        },
                        detail_cap=10,
                    ),
                    client,
                    self.checkpoint,
                )
                adapter.publication_filter = PublicationFilter(
                    recent_months=3, as_of=date(2026, 7, 21)
                )
                adapter.complete_publication_window = True

                result = adapter.crawl(100, with_details=True)

                self.assertEqual([sitemap_url], client.calls)
                self.assertEqual([], result.records)
                self.assertEqual(
                    "sitemap_config_invalid", result.stop_reason
                )
                self.assertFalse(
                    result.source_coverage["sitemap"]["complete"]
                )
                self.assertEqual(
                    ["publication_sitemap"],
                    [failure.stage for failure in result.failures],
                )

    def test_epwk_primary_sitemap_validation_uses_detail_date_on_mismatch(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        detail_url = "https://task.epwk.com/2001/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [(detail_url, "2026-07-20")]
                ),
                detail_url: self._epwk_primary_detail_html(
                    title="Mismatched task",
                    price="5000 CNY",
                    published="2026.07.19",
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual([sitemap_url, detail_url], client.calls)
        self.assertEqual(
            ["Mismatched task"],
            [item.title_raw for item in result.records],
        )
        self.assertEqual("2026.07.19", result.records[0].published_at_raw)
        self.assertEqual(
            "mismatch_detail_authoritative",
            result.records[0].metadata["publication_sitemap_validation"]["status"],
        )
        self.assertEqual(
            1,
            result.source_coverage["sitemap"]["date_mismatch_candidates"],
        )
        self.assertFalse(result.window_complete)
        self.assertEqual(
            "sitemap_source_exhausted_not_global",
            result.stop_reason,
        )
        self.assertTrue(result.source_coverage["sitemap"]["complete"])
        self.assertEqual([], result.failures)

    def test_epwk_primary_sitemap_validation_rejects_invalid_detail_date(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        detail_url = "https://task.epwk.com/2001/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [(detail_url, "2026-07-20")]
                ),
                detail_url: self._epwk_primary_detail_html(
                    title="Invalid publication date",
                    price="5000 CNY",
                    published="not-a-date",
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual([sitemap_url, detail_url], client.calls)
        self.assertEqual([], result.records)
        self.assertEqual("sitemap_validation_failed", result.stop_reason)
        self.assertFalse(result.source_coverage["sitemap"]["complete"])
        self.assertEqual(
            ["publication_sitemap_validation"],
            [failure.stage for failure in result.failures],
        )

    def test_epwk_primary_sitemap_detail_date_is_final_filter_authority(
        self,
    ) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        sample_url = "https://task.epwk.com/2001/"
        too_old_url = "https://task.epwk.com/2002/"
        cutoff_url = "https://task.epwk.com/2003/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (sample_url, "2026-07-20"),
                        (too_old_url, "2026-07-19"),
                        (cutoff_url, "2026-07-18"),
                    ]
                ),
                sample_url: self._epwk_primary_detail_html(
                    title="Validation sample",
                    price="5000 CNY",
                    published="2026.07.20",
                ),
                too_old_url: self._epwk_primary_detail_html(
                    title="Actually too old",
                    price="6000 CNY",
                    published="2026.04.20",
                ),
                cutoff_url: self._epwk_primary_detail_html(
                    title="Exactly on cutoff",
                    price="7000 CNY",
                    published="2026.04.21",
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual(
            [sitemap_url, sample_url, too_old_url, cutoff_url],
            client.calls,
        )
        self.assertEqual(
            ["Validation sample", "Exactly on cutoff"],
            [item.title_raw for item in result.records],
        )
        self.assertEqual(1, result.filtered_out_count)
        self.assertEqual(1, result.filter_counts["publication_too_old"])
        self.assertEqual(
            2,
            result.source_coverage["sitemap"]["date_mismatch_candidates"],
        )
        self.assertTrue(result.source_coverage["sitemap"]["complete"])
        self.assertEqual([], result.failures)

    def test_epwk_primary_sitemap_boundary_only_uses_lookback_candidates(
        self,
    ) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        strict_url = "https://task.epwk.com/2001/"
        rescued_url = "https://task.epwk.com/2002/"
        stale_url = "https://task.epwk.com/2003/"
        outside_url = "https://task.epwk.com/2004/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (strict_url, "2026-04-21"),
                        (rescued_url, "2026-04-10"),
                        (stale_url, "2026-04-05"),
                        (outside_url, "2026-03-21"),
                    ]
                ),
                rescued_url: self._epwk_primary_detail_html(
                    title="Boundary rescue",
                    price="5000 CNY",
                    published="2026.04.22",
                ),
                stale_url: self._epwk_primary_detail_html(
                    title="Boundary stale",
                    price="6000 CNY",
                    published="2026.04.05",
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "candidate_lookback_days": 30,
                        "boundary_only": True,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual(
            [sitemap_url, rescued_url, stale_url],
            client.calls,
        )
        self.assertEqual(
            ["Boundary rescue"],
            [item.title_raw for item in result.records],
        )
        coverage = result.source_coverage["sitemap"]
        self.assertEqual("boundary_only", coverage["candidate_scope"])
        self.assertEqual(30, coverage["candidate_lookback_days"])
        self.assertEqual("2026-03-22", coverage["candidate_cutoff"])
        self.assertEqual(1, coverage["global_strict_window_candidates"])
        self.assertEqual(2, coverage["global_boundary_candidates"])
        self.assertEqual(2, coverage["global_window_candidates"])
        self.assertEqual(2, coverage["window_candidates"])
        self.assertEqual(1, coverage["date_mismatch_candidates"])
        self.assertTrue(coverage["complete"])
        self.assertEqual(1, result.filter_counts["publication_too_old"])
        self.assertEqual([], result.failures)

    def test_epwk_primary_sitemap_detail_failure_is_partial(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        first_url = "https://task.epwk.com/2001/"
        missing_url = "https://task.epwk.com/2002/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (first_url, "2026-07-20"),
                        (missing_url, "2026-07-19"),
                    ]
                ),
                first_url: self._epwk_primary_detail_html(
                    title="Valid task",
                    price="5000 CNY",
                    published="2026.07.20",
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual([sitemap_url, first_url, missing_url], client.calls)
        self.assertEqual(["Valid task"], [record.title_raw for record in result.records])
        self.assertEqual("partial", result.status)
        self.assertEqual("sitemap_detail_incomplete", result.stop_reason)
        self.assertFalse(result.source_coverage["sitemap"]["complete"])
        self.assertEqual(
            ["publication_sitemap_detail"],
            [failure.stage for failure in result.failures],
        )

    def test_epwk_primary_sitemap_stops_at_detail_cap(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        first_url = "https://task.epwk.com/2001/"
        capped_url = "https://task.epwk.com/2002/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (first_url, "2026-07-20"),
                        (capped_url, "2026-07-19"),
                    ]
                ),
                first_url: self._epwk_primary_detail_html(
                    title="Validated task",
                    price="5000 CNY",
                    published="2026.07.20",
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 1,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual([sitemap_url, first_url], client.calls)
        self.assertEqual(["Validated task"], [record.title_raw for record in result.records])
        self.assertEqual(1, result.details_fetched)
        self.assertEqual("detail_cap_reached", result.stop_reason)
        self.assertFalse(result.source_coverage["sitemap"]["complete"])

    def test_epwk_primary_sitemap_stops_at_record_cap(self) -> None:
        class RecordLimitedConfig(AdapterConfig):
            def section(self, name: str) -> dict[str, object]:
                values = super().section(name)
                if name == "limits":
                    values["max_publication_records_per_site"] = 1
                return values

        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        first_url = "https://task.epwk.com/2001/"
        capped_url = "https://task.epwk.com/2002/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (first_url, "2026-07-20"),
                        (capped_url, "2026-07-19"),
                    ]
                ),
                first_url: self._epwk_primary_detail_html(
                    title="Record limited task",
                    price="5000 CNY",
                    published="2026.07.20",
                ),
            }
        )
        adapter = EpwkAdapter(
            RecordLimitedConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_max_detail_requests": 10,
                    "publication_sitemap": {
                        "primary_source": True,
                        "validation_sample_size": 1,
                        "checkpoint_interval": 50,
                        "urls": [sitemap_url],
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(100, with_details=True)

        self.assertEqual([sitemap_url, first_url], client.calls)
        self.assertEqual(
            ["Record limited task"],
            [record.title_raw for record in result.records],
        )
        self.assertEqual("item_cap_reached", result.stop_reason)
        self.assertFalse(result.source_coverage["sitemap"]["complete"])

    def test_epwk_unsorted_window_scans_past_old_page(self) -> None:
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        page3 = "https://task.epwk.com/page3.html?o=7"
        old_url = "https://task.epwk.com/900/"
        recent_url = "https://task.epwk.com/1001/"
        client = InlineClient(
            {
                list_url: self._epwk_list_html(old_url),
                old_url: (
                    '<div class="task-progress-item"><span class="step_off">'
                    "<b>发布需求</b><font>2026.04.20</font></span></div>"
                ),
                page2: self._epwk_list_html(recent_url),
                recent_url: (
                    '<div class="task-progress-item"><span class="step_on">'
                    "<b>发布需求</b><font>2026.04.21</font></span></div>"
                ),
                page3: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": (
                        "https://task.epwk.com/page{page}.html?o=7"
                    ),
                    "publication_sorted_desc": False,
                    "publication_max_pages": 3,
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual(
            [list_url, old_url, page2, recent_url, page3], client.calls
        )
        self.assertEqual(
            [recent_url], [record.detail_url for record in result.records]
        )
        self.assertEqual(1, result.filter_counts["publication_too_old"])
        self.assertEqual(3, result.pages_fetched)
        self.assertTrue(result.window_complete)
        self.assertEqual("source_exhausted", result.stop_reason)

    def test_epwk_sitemap_mismatch_uses_detail_and_disables_sitemap(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        detail_url = "https://task.epwk.com/1158208/"
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [(detail_url, "2026-07-20")]
                ),
                list_url: self._epwk_list_html(detail_url),
                detail_url: (
                    '<div class=task-progress-item><span class=step_on>'
                    '<b>&#21457;&#24067;&#38656;&#27714;</b>'
                    '<font>2026.07.19</font></span></div>'
                ),
                page2: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_sitemap": {
                        "validation_sample_size": 1,
                        "urls": [sitemap_url],
                        "max_files": 1,
                        "max_response_bytes": 100_000,
                        "max_entries_per_file": 100,
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual([sitemap_url, list_url, detail_url, page2], client.calls)
        self.assertEqual(1, result.details_fetched)
        self.assertEqual("2026.07.19", result.records[0].published_at_raw)
        self.assertEqual(
            "detail_progress",
            result.records[0].metadata["publication_date_source"],
        )
        self.assertEqual(
            "sample_mismatch",
            result.records[0].metadata["publication_sitemap_validation"]["status"],
        )
        self.assertEqual(
            ["publication_sitemap_validation"],
            [failure.stage for failure in result.failures],
        )
        self.assertTrue(any("disabled" in note for note in result.runtime_notes))
        self.assertTrue(result.window_complete)

    def test_epwk_sitemap_miss_falls_back_to_detail(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        detail_url = "https://task.epwk.com/1158262/"
        detail_html = (
            '<div class="task-progress-item"><span class="step_on">'
            "<b>发布需求</b><font>2026.07.21</font></span></div>"
        )
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml([]),
                list_url: self._epwk_list_html(detail_url),
                detail_url: detail_html,
                page2: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_sitemap": {
                        "urls": [sitemap_url],
                        "max_files": 1,
                        "max_response_bytes": 100_000,
                        "max_entries_per_file": 100,
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual(
            [sitemap_url, list_url, detail_url, page2], client.calls
        )
        self.assertEqual(1, result.details_fetched)
        self.assertEqual(
            "detail_progress",
            result.records[0].metadata["publication_date_source"],
        )
        self.assertTrue(result.window_complete)

    def test_epwk_old_sitemap_shard_resolves_pinned_task_for_cutoff(self) -> None:
        current_sitemap = "https://task.epwk.com/sitemap/3.xml"
        old_sitemap = "https://task.epwk.com/sitemap/4.xml"
        list_url = "https://task.epwk.com/?o=7"
        old_url = "https://task.epwk.com/909619/"
        client = InlineClient(
            {
                current_sitemap: self._epwk_sitemap_xml([]),
                old_sitemap: self._epwk_sitemap_xml(
                    [(old_url, "2021-01-01")]
                ),
                list_url: self._epwk_list_html(old_url),
                old_url: (
                    '<div class=task-progress-item><span class=step_on>'
                    '<b>&#21457;&#24067;&#38656;&#27714;</b>'
                    '<font>2021.01.01</font></span></div>'
                ),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_sitemap": {
                        "validation_sample_size": 2,
                        "urls": [current_sitemap, old_sitemap],
                        "max_files": 2,
                        "max_response_bytes": 100_000,
                        "max_entries_per_file": 100,
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual(
            [current_sitemap, old_sitemap, list_url, old_url],
            client.calls,
        )
        self.assertEqual(1, result.details_fetched)
        self.assertTrue(any("not trusted" in note for note in result.runtime_notes))
        self.assertTrue(result.window_complete)
        self.assertEqual("cutoff_reached", result.stop_reason)
        self.assertEqual(1, result.filter_counts["publication_too_old"])

    def test_epwk_sitemap_parse_failure_records_and_uses_detail(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        detail_url = "https://task.epwk.com/1158262/"
        detail_html = (
            '<div class="task-progress-item"><span class="step_on">'
            "<b>发布需求</b><font>2026.07.21</font></span></div>"
        )
        client = InlineClient(
            {
                sitemap_url: "<urlset><url>",
                list_url: self._epwk_list_html(detail_url),
                detail_url: detail_html,
                page2: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_sitemap": {
                        "urls": [sitemap_url],
                        "max_files": 1,
                        "max_response_bytes": 100_000,
                        "max_entries_per_file": 100,
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual(
            [sitemap_url, list_url, detail_url, page2], client.calls
        )
        self.assertEqual(
            ["publication_sitemap"],
            [failure.stage for failure in result.failures],
        )
        self.assertEqual(
            "detail_progress",
            result.records[0].metadata["publication_date_source"],
        )

    def test_epwk_sitemap_file_and_response_limits_fall_back(self) -> None:
        first_sitemap = "https://task.epwk.com/sitemap/3.xml"
        skipped_sitemap = "https://task.epwk.com/sitemap/4.xml"
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        detail_url = "https://task.epwk.com/1158262/"
        detail_html = (
            '<div class="task-progress-item"><span class="step_on">'
            "<b>发布需求</b><font>2026.07.21</font></span></div>"
        )
        client = InlineClient(
            {
                first_sitemap: self._epwk_sitemap_xml([]),
                list_url: self._epwk_list_html(detail_url),
                detail_url: detail_html,
                page2: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_sitemap": {
                        "urls": [first_sitemap, skipped_sitemap],
                        "max_files": 1,
                        "max_response_bytes": 32,
                        "max_entries_per_file": 100,
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertNotIn(skipped_sitemap, client.calls)
        self.assertEqual(
            [first_sitemap, list_url, detail_url, page2], client.calls
        )
        self.assertEqual(
            ["publication_sitemap"],
            [failure.stage for failure in result.failures],
        )
        self.assertEqual(1, result.details_fetched)

    def test_epwk_sitemap_block_stops_remaining_sitemap_queue(self) -> None:
        first_sitemap = "https://task.epwk.com/sitemap/3.xml"
        skipped_sitemap = "https://task.epwk.com/sitemap/4.xml"
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        detail_url = "https://task.epwk.com/1158262/"

        class BlockingSitemapClient(InlineClient):
            def get(self, url: str, **kwargs: object) -> FetchedPage:
                if url == first_sitemap:
                    self.calls.append(url)
                    raise BlockedResponseError(
                        "simulated sitemap block",
                        status_code=418,
                        url=url,
                    )
                return super().get(url, **kwargs)

        client = BlockingSitemapClient(
            {
                list_url: self._epwk_list_html(detail_url),
                detail_url: (
                    '<div class="task-progress-item"><span class="step_on">'
                    "<b>发布需求</b><font>2026.07.21</font></span></div>"
                ),
                page2: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_sitemap": {
                        "urls": [first_sitemap, skipped_sitemap],
                        "max_files": 2,
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual(
            [first_sitemap, list_url, detail_url, page2],
            client.calls,
        )
        self.assertNotIn(skipped_sitemap, client.calls)
        self.assertEqual(
            ["publication_sitemap"],
            [failure.stage for failure in result.failures],
        )
        self.assertTrue(result.window_complete)

    def test_epwk_detail_cap_allows_sitemap_hits_until_next_miss(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        detail_urls = [
            "https://task.epwk.com/1158261/",
            "https://task.epwk.com/1158262/",
            "https://task.epwk.com/1158263/",
        ]
        list_html = '<div class="content-lists">' + "".join(
            '<div class="itemblock">'
            f'<div class="title"><a href="{url}">任务 {position}</a></div>'
            '<div class="right"><span class="price">5000元</span></div>'
            "</div>"
            for position, url in enumerate(detail_urls, start=1)
        ) + "</div>"
        detail_html = (
            '<div class="task-progress-item"><span class="step_on">'
            "<b>发布需求</b><font>2026.07.21</font></span></div>"
        )
        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (detail_urls[0], "2026-07-21"),
                        (detail_urls[1], "2026-07-20"),
                    ]
                ),
                list_url: list_html,
                detail_urls[0]: detail_html,
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_max_detail_requests": 1,
                    "publication_sitemap": {
                        "validation_sample_size": 1,
                        "urls": [sitemap_url],
                        "max_files": 1,
                        "max_response_bytes": 100_000,
                        "max_entries_per_file": 100,
                    },
                },
                detail_cap=1,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual([sitemap_url, list_url, detail_urls[0]], client.calls)
        self.assertEqual(1, result.details_fetched)
        self.assertEqual(2, len(result.records))
        self.assertEqual(
            ["detail_progress", "sitemap_lastmod"],
            [
                record.metadata["publication_date_source"]
                for record in result.records
            ],
        )
        self.assertEqual(
            ["sample_match", "trusted"],
            [
                record.metadata["publication_sitemap_validation"]["status"]
                for record in result.records
            ],
        )
        self.assertFalse(result.window_complete)
        self.assertEqual("detail_cap_reached", result.stop_reason)

    def test_epwk_detail_cap_at_page_end_still_allows_exact_cutoff(self) -> None:
        list_url = "https://task.epwk.com/?o=7"
        detail_url = "https://task.epwk.com/900/"
        detail_html = (
            '<div class="task-progress-item"><span class="step_off">'
            "<b>发布需求</b><font>2026.04.20</font></span></div>"
        )
        client = InlineClient(
            {
                list_url: self._epwk_list_html(detail_url),
                detail_url: detail_html,
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_max_detail_requests": 1,
                },
                detail_cap=1,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual([list_url, detail_url], client.calls)
        self.assertTrue(result.window_complete)
        self.assertEqual("cutoff_reached", result.stop_reason)

    def test_epwk_detail_cap_at_page_end_allows_source_exhaustion(self) -> None:
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        detail_url = "https://task.epwk.com/1158262/"
        detail_html = (
            '<div class="task-progress-item"><span class="step_on">'
            "<b>发布需求</b><font>2026.07.21</font></span></div>"
        )
        client = InlineClient(
            {
                list_url: self._epwk_list_html(detail_url),
                detail_url: detail_html,
                page2: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 2,
                    "publication_max_detail_requests": 1,
                },
                detail_cap=1,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual([list_url, detail_url, page2], client.calls)
        self.assertTrue(result.window_complete)
        self.assertEqual("source_exhausted", result.stop_reason)

    def test_epwk_detail_missing_publish_date_stays_unknown(self) -> None:
        result = self._crawl_epwk_detail(
            """
            <div class="contxt">
              <div class="title">测试任务详情</div>
              <div class="catList"><span class="money">5000元</span></div>
              <div class="task-progress-item">
                <span class="step_off"><b>发布需求</b></span>
              </div>
              <div class="task-progress-item">
                <span class="step_off"><b>威客投标</b><font>2026.07.15</font></span>
              </div>
              <div class="time">截稿时间：2026-08-08</div>
            </div>
            """
        )

        self.assertIsNone(result.records[0].published_at_raw)
        self.assertEqual(
            "unavailable",
            result.records[0].metadata["publication_date_source"],
        )

    def test_epwk_recent_filter_fetches_details_until_limit_is_filled(self) -> None:
        list_url = "https://task.epwk.com/"
        old_url = "https://task.epwk.com/1234567/"
        boundary_url = "https://task.epwk.com/1234568/"
        list_html = f"""
        <div class="content-lists">
          <div class="itemblock"><div class="title"><a href="{old_url}">旧任务</a></div>
            <div class="right"><span class="price">5000元</span></div></div>
          <div class="itemblock"><div class="title"><a href="{boundary_url}">边界任务</a></div>
            <div class="right"><span class="price">6000元</span></div></div>
        </div>
        """
        detail = lambda title, value: f"""
        <div class="contxt"><div class="title">{title}</div>
          <div class="task-progress-item"><span class="step_off">
            <b>发布需求</b><font>{value}</font>
          </span></div>
        </div>
        """
        client = InlineClient(
            {
                list_url: list_html,
                old_url: detail("旧任务", "2026.04.20"),
                boundary_url: detail("边界任务", "2026.04.21"),
            }
        )
        adapter = EpwkAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "start_url": list_url,
            "page_url_template": "https://task.epwk.com/page{page}.html",
            "detail_sample_limit": 1,
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(1, with_details=True)

        self.assertEqual([list_url, old_url, boundary_url], client.calls)
        self.assertEqual(["边界任务"], [record.title_raw for record in result.records])
        self.assertEqual(2, result.details_fetched)
        self.assertEqual(1, result.filtered_out_count)
        self.assertEqual(1, result.filter_counts["publication_too_old"])

    def test_epwk_recent_filter_dedupes_before_spending_detail_budget(self) -> None:
        list_url = "https://task.epwk.com/"
        old_url = "https://task.epwk.com/1234567/"
        recent_url = "https://task.epwk.com/1234568/"
        list_html = f"""
        <div class="content-lists">
          <div class="itemblock"><div class="title"><a href="{old_url}">旧任务</a></div>
            <div class="right"><span class="price">5000元</span></div></div>
          <div class="itemblock"><div class="title"><a href="{old_url}">旧任务置顶</a></div>
            <div class="right"><span class="price">5000元</span></div></div>
          <div class="itemblock"><div class="title"><a href="{recent_url}">近期任务</a></div>
            <div class="right"><span class="price">6000元</span></div></div>
        </div>
        """
        detail = lambda value: f"""
        <div class="task-progress-item"><span class="step_off">
          <b>发布需求</b><font>{value}</font>
        </span></div>
        """
        client = InlineClient(
            {
                list_url: list_html,
                old_url: detail("2026.04.20"),
                recent_url: detail("2026.04.21"),
            }
        )
        site_config = {
            "start_url": list_url,
            "page_url_template": "https://task.epwk.com/page{page}.html",
            "detail_sample_limit": 1,
        }
        adapter = EpwkAdapter(  # type: ignore[arg-type]
            AdapterConfig("epwk", site_config, detail_cap=2),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(1, with_details=True)

        self.assertEqual([list_url, old_url, recent_url], client.calls)
        self.assertEqual(["近期任务"], [record.title_raw for record in result.records])

    def test_epwk_detail_cap_at_page_end_stops_before_next_list_page(self) -> None:
        list_url = "https://task.epwk.com/"
        page2 = "https://task.epwk.com/page2.html"
        old_url = "https://task.epwk.com/1234567/"
        recent_url = "https://task.epwk.com/1234568/"
        page_html = lambda detail_url, title: f"""
        <div class="content-lists"><div class="itemblock">
          <div class="title"><a href="{detail_url}">{title}</a></div>
          <div class="right"><span class="price">5000元</span></div>
        </div></div>
        """
        detail = lambda value: f"""
        <div class="task-progress-item"><span class="step_off">
          <b>发布需求</b><font>{value}</font>
        </span></div>
        """
        client = InlineClient(
            {
                list_url: page_html(old_url, "旧任务"),
                old_url: detail("2026.04.20"),
                page2: page_html(recent_url, "近期任务"),
                recent_url: detail("2026.04.21"),
            }
        )
        site_config = {
            "start_url": list_url,
            "page_url_template": "https://task.epwk.com/page{page}.html",
            "detail_sample_limit": 1,
        }
        adapter = EpwkAdapter(  # type: ignore[arg-type]
            AdapterConfig("epwk", site_config, detail_cap=1),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(1, with_details=True)

        self.assertEqual([list_url, old_url], client.calls)
        self.assertTrue(
            any("detail request cap reached: 1" in note for note in result.runtime_notes)
        )

    def test_epwk_detail_failure_is_counted_as_unknown_publication(self) -> None:
        list_url = "https://task.epwk.com/"
        detail_url = "https://task.epwk.com/1234567/"
        client = InlineClient({list_url: self._epwk_list_html(detail_url)})
        site_config = {
            "start_url": list_url,
            "page_url_template": "https://task.epwk.com/page{page}.html",
            "detail_sample_limit": 1,
        }
        adapter = EpwkAdapter(  # type: ignore[arg-type]
            AdapterConfig("epwk", site_config, detail_cap=1),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(1, with_details=True)

        self.assertEqual([list_url, detail_url], client.calls)
        self.assertEqual([], result.records)
        self.assertEqual(1, result.filtered_out_count)
        self.assertEqual(1, result.filter_counts["publication_unknown"])
        self.assertEqual(["detail"], [failure.stage for failure in result.failures])

    def test_epwk_numeric_filter_runs_before_publication_detail_budget(self) -> None:
        list_url = "https://task.epwk.com/"
        quote_url = "https://task.epwk.com/1234567/"
        numeric_url = "https://task.epwk.com/1234568/"
        list_html = f"""
        <div class="content-lists">
          <div class="itemblock"><div class="title"><a href="{quote_url}">面议任务</a></div>
            <div class="right"><span class="price">面议</span></div></div>
          <div class="itemblock"><div class="title"><a href="{numeric_url}">数字任务</a></div>
            <div class="right"><span class="price">6000元</span></div></div>
        </div>
        """
        detail_html = """
        <div class="task-progress-item"><span class="step_off">
          <b>发布需求</b><font>2026.04.21</font>
        </span></div>
        """
        client = InlineClient(
            {
                list_url: list_html,
                quote_url: detail_html,
                numeric_url: detail_html,
            }
        )
        site_config = {
            "start_url": list_url,
            "page_url_template": "https://task.epwk.com/page{page}.html",
            "detail_sample_limit": 1,
        }
        adapter = EpwkAdapter(  # type: ignore[arg-type]
            AdapterConfig("epwk", site_config, detail_cap=1),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(
            1, with_details=True, numeric_price_only=True
        )

        self.assertEqual([list_url, numeric_url], client.calls)
        self.assertEqual(["数字任务"], [record.title_raw for record in result.records])
        self.assertEqual(1, result.filter_counts["numeric_price"])
        self.assertEqual(1, result.filtered_out_count)

    def test_epwk_reconsiders_task_when_price_becomes_numeric(self) -> None:
        list_url = "https://task.epwk.com/"
        page2 = "https://task.epwk.com/page2.html"
        task_url = "https://task.epwk.com/1234567/"
        list_html = lambda price: f"""
        <div class="content-lists"><div class="itemblock">
          <div class="title"><a href="{task_url}">价格更新任务</a></div>
          <div class="right"><span class="price">{price}</span></div>
        </div></div>
        """
        detail_html = """
        <div class="task-progress-item"><span class="step_off">
          <b>发布需求</b><font>2026.04.21</font>
        </span></div>
        """
        client = InlineClient(
            {
                list_url: list_html("面议"),
                page2: list_html("6000元"),
                task_url: detail_html,
            }
        )
        site_config = {
            "start_url": list_url,
            "page_url_template": "https://task.epwk.com/page{page}.html",
            "detail_sample_limit": 1,
        }
        adapter = EpwkAdapter(  # type: ignore[arg-type]
            AdapterConfig("epwk", site_config, detail_cap=1),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(
            1, with_details=True, numeric_price_only=True
        )

        self.assertEqual([list_url, page2, task_url], client.calls)
        self.assertEqual(["价格更新任务"], [record.title_raw for record in result.records])
        self.assertEqual(0, result.filtered_out_count)
        self.assertNotIn("numeric_price", result.filter_counts)

    def test_epwk_complete_window_ignores_old_limit_and_stops_at_old_page(self) -> None:
        list_url = "https://task.epwk.com/"
        page2 = "https://task.epwk.com/page2.html"
        recent_urls = ["https://task.epwk.com/1001/", "https://task.epwk.com/1002/"]
        old_url = "https://task.epwk.com/900/"

        def listing(items: list[tuple[str, str]]) -> str:
            return '<div class="content-lists">' + "".join(
                f'<div class="itemblock"><div class="title"><a href="{url}">{title}</a></div>'
                '<div class="right"><span class="price">5000元</span></div></div>'
                for url, title in items
            ) + "</div>"

        def detail(value: str) -> str:
            return '<div class="task-progress-item"><span class="step_off">' \
                f'<b>发布需求</b><font>{value}</font></span></div>'

        client = InlineClient(
            {
                list_url: listing([(recent_urls[0], "近期一"), (recent_urls[1], "近期二")]),
                page2: listing([(old_url, "旧任务")]),
                recent_urls[0]: detail("2026.07.20"),
                recent_urls[1]: detail("2026.07.19"),
                old_url: detail("2026.04.20"),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig("epwk", {
                "start_url": list_url,
                "page_url_template": "https://task.epwk.com/page{page}.html",
                "publication_sorted_desc": True,
                "publication_max_pages": 3,
            }, detail_cap=10),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertEqual(2, len(result.records))
        self.assertTrue(result.window_complete)
        self.assertEqual("cutoff_reached", result.stop_reason)
        self.assertEqual([list_url, recent_urls[0], recent_urls[1], page2, old_url], client.calls)

    def test_epwk_duplicate_date_participates_in_full_page_cutoff(self) -> None:
        sitemap_url = "https://task.epwk.com/sitemap/3.xml"
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        page3 = "https://task.epwk.com/page3.html?o=7"
        recent_url = "https://task.epwk.com/1001/"
        recent_variant = "https://task.epwk.com/1001?from=pin#top"
        old_url = "https://task.epwk.com/900/"
        older_url = "https://task.epwk.com/800/"

        def listing(items: list[tuple[str, str]]) -> str:
            return '<div class="content-lists">' + "".join(
                f'<div class="itemblock"><div class="title"><a href="{url}">{title}</a></div>'
                '<div class="right"><span class="price">5000元</span></div></div>'
                for url, title in items
            ) + "</div>"

        def detail(value: str) -> str:
            return '<div class="task-progress-item"><span class="step_off">' \
                f'<b>发布需求</b><font>{value}</font></span></div>'

        client = InlineClient(
            {
                sitemap_url: self._epwk_sitemap_xml(
                    [
                        (recent_url, "2026-07-20"),
                        (old_url, "2026-04-20"),
                        (older_url, "2026-04-19"),
                    ]
                ),
                list_url: listing([(recent_url, "近期任务")]),
                page2: listing(
                    [(recent_variant, "近期任务置顶"), (old_url, "旧任务")]
                ),
                page3: listing([(older_url, "更旧任务")]),
                recent_url: detail("2026.07.20"),
                old_url: detail("2026.04.20"),
                older_url: detail("2026.04.19"),
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 3,
                    "publication_sitemap": {
                        "validation_sample_size": 2,
                        "urls": [sitemap_url],
                        "max_files": 1,
                        "max_response_bytes": 100_000,
                        "max_entries_per_file": 100,
                    },
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertTrue(result.window_complete)
        self.assertEqual("cutoff_reached", result.stop_reason)
        self.assertEqual(
            [sitemap_url, list_url, recent_url, page2, old_url, page3],
            client.calls,
        )
        self.assertEqual(2, result.details_fetched)

    def test_epwk_partial_page_at_detail_cap_is_not_a_cutoff(self) -> None:
        list_url = "https://task.epwk.com/"
        old_url = "https://task.epwk.com/900/"
        recent_url = "https://task.epwk.com/1001/"
        list_html = f"""
        <div class="content-lists">
          <div class="itemblock"><div class="title"><a href="{old_url}">旧任务</a></div>
            <div class="right"><span class="price">5000元</span></div></div>
          <div class="itemblock"><div class="title"><a href="{recent_url}">近期任务</a></div>
            <div class="right"><span class="price">5000元</span></div></div>
        </div>
        """
        old_detail = '<div class="task-progress-item"><span class="step_off"><b>发布需求</b><font>2026.04.20</font></span></div>'
        client = InlineClient({list_url: list_html, old_url: old_detail})
        adapter = EpwkAdapter(
            AdapterConfig("epwk", {
                "start_url": list_url,
                "page_url_template": "https://task.epwk.com/page{page}.html",
                "publication_sorted_desc": True,
                "publication_max_pages": 3,
                "publication_max_detail_requests": 1,
            }, detail_cap=1),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertFalse(result.window_complete)
        self.assertEqual("detail_cap_reached", result.stop_reason)
        self.assertEqual([list_url, old_url], client.calls)

    def test_epwk_unknown_date_never_claims_complete_window(self) -> None:
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        detail_url = "https://task.epwk.com/1001/"
        client = InlineClient(
            {
                list_url: self._epwk_list_html(detail_url),
                detail_url: '<div class="task-progress-item"><span class="step_on">'
                "<b>发布需求</b></span></div>",
                page2: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 3,
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertFalse(result.window_complete)
        self.assertNotEqual("source_exhausted", result.stop_reason)

    def test_epwk_unknown_pages_do_not_stop_before_later_pages(self) -> None:
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        page3 = "https://task.epwk.com/page3.html?o=7"
        page4 = "https://task.epwk.com/page4.html?o=7"
        unknown_urls = [
            "https://task.epwk.com/1001/",
            "https://task.epwk.com/1002/",
        ]
        old_url = "https://task.epwk.com/900/"
        unknown_detail = (
            '<div class="task-progress-item"><span class="step_on">'
            "<b>发布需求</b></span></div>"
        )
        old_detail = (
            '<div class="task-progress-item"><span class="step_off">'
            "<b>发布需求</b><font>2026.04.20</font></span></div>"
        )
        client = InlineClient(
            {
                list_url: self._epwk_list_html(unknown_urls[0]),
                unknown_urls[0]: unknown_detail,
                page2: self._epwk_list_html(unknown_urls[1]),
                unknown_urls[1]: unknown_detail,
                page3: self._epwk_list_html(old_url),
                old_url: old_detail,
                page4: '<div class="content-lists"></div>',
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 4,
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertFalse(result.window_complete)
        self.assertEqual("publication_unavailable", result.stop_reason)
        self.assertEqual(
            [
                list_url,
                unknown_urls[0],
                page2,
                unknown_urls[1],
                page3,
                old_url,
                page4,
            ],
            client.calls,
        )

    def test_epwk_two_old_known_pages_with_unknowns_stop_partial(self) -> None:
        list_url = "https://task.epwk.com/?o=7"
        page2 = "https://task.epwk.com/page2.html?o=7"
        unknown_urls = [
            "https://task.epwk.com/1001/",
            "https://task.epwk.com/1002/",
        ]
        old_urls = [
            "https://task.epwk.com/901/",
            "https://task.epwk.com/902/",
        ]

        def listing(unknown_url: str, old_url: str) -> str:
            return (
                '<div class="content-lists">'
                f'<div class="itemblock"><div class="title"><a href="{unknown_url}">未知置顶</a></div>'
                '<div class="right"><span class="price">5000元</span></div></div>'
                f'<div class="itemblock"><div class="title"><a href="{old_url}">旧任务</a></div>'
                '<div class="right"><span class="price">5000元</span></div></div>'
                "</div>"
            )

        unknown_detail = (
            '<div class="task-progress-item"><span class="step_on">'
            "<b>发布需求</b></span></div>"
        )
        old_detail = (
            '<div class="task-progress-item"><span class="step_off">'
            "<b>发布需求</b><font>2026.04.20</font></span></div>"
        )
        client = InlineClient(
            {
                list_url: listing(unknown_urls[0], old_urls[0]),
                page2: listing(unknown_urls[1], old_urls[1]),
                unknown_urls[0]: unknown_detail,
                unknown_urls[1]: unknown_detail,
                old_urls[0]: old_detail,
                old_urls[1]: old_detail,
            }
        )
        adapter = EpwkAdapter(
            AdapterConfig(
                "epwk",
                {
                    "start_url": list_url,
                    "page_url_template": "https://task.epwk.com/page{page}.html?o=7",
                    "publication_sorted_desc": True,
                    "publication_max_pages": 400,
                    "publication_unknown_cutoff_pages": 2,
                },
                detail_cap=10,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=True)

        self.assertFalse(result.window_complete)
        self.assertEqual("cutoff_reached_with_unknowns", result.stop_reason)
        self.assertEqual(2, result.pages_fetched)
        self.assertEqual(2, result.filter_counts["publication_unknown"])
        self.assertEqual(2, result.filter_counts["publication_too_old"])
        self.assertEqual(
            [
                list_url,
                unknown_urls[0],
                old_urls[0],
                page2,
                unknown_urls[1],
                old_urls[1],
            ],
            client.calls,
        )

    def test_shixian_balances_full_and_part_time(self) -> None:
        full = "https://shixian.com/job/all?page=1&pay_type=daily_wage_mode&sort_arrow=down"
        part = "https://shixian.com/job/all?page=1&pay_type=project_mode&sort_arrow=down"
        client = FakeClient({full: "shixian-full.html", part: "shixian-part.html"})
        result = ShixianAdapter(self.config, client, self.checkpoint).crawl(
            2, with_details=False
        )
        self.assertEqual("success", result.status)
        self.assertEqual({"远程全职", "远程兼职"}, {x.job_type_raw for x in result.records})

    @staticmethod
    def _shixian_html(*, item_id: int, title: str, published: str) -> str:
        return f"""
        <div class="job-list"><div class="job">
          <div class="info"><a href="/jobs/{item_id}"><h5 class="title">{title}
            <span class="type">远程全职</span></h5></a></div>
          <div class="about"><h4 class="price">1000元</h4><dl>预算：1000元</dl></div>
          <span class="publish-at">{published}</span>
        </div></div>
        """

    def test_shixian_recent_filter_continues_after_an_old_page(self) -> None:
        template = "https://shixian.com/job/all?page={page}&pay_type=daily_wage_mode"
        page1 = template.format(page=1)
        page2 = template.format(page=2)
        client = InlineClient(
            {
                page1: self._shixian_html(
                    item_id=1, title="旧任务", published="2026-04-20"
                ),
                page2: self._shixian_html(
                    item_id=2, title="边界任务", published="2026-04-21"
                ),
            }
        )
        adapter = ShixianAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "streams": [{"kind": "full_time", "page_url_template": template}],
            "detail_sample_limit": 0,
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(1, with_details=False)

        self.assertEqual([page1, page2], client.calls)
        self.assertEqual(["边界任务"], [record.title_raw for record in result.records])
        self.assertEqual(1, result.filter_counts["publication_too_old"])

    def test_shixian_complete_window_collects_to_source_exhaustion(self) -> None:
        template = "https://shixian.com/full?page={page}"
        page1 = template.format(page=1)
        page2 = template.format(page=2)
        page3 = template.format(page=3)
        recent = self._shixian_html(item_id=1, title="近期一", published="1个月前")
        recent += self._shixian_html(item_id=2, title="近期二", published="2个月前")
        client = InlineClient({
            page1: recent,
            page2: self._shixian_html(item_id=3, title="旧任务", published="4个月前"),
            page3: '<div class="job-list"><div class="job text-center">抱歉，暂时还没有符合条件的兼职需求~</div></div>',
        })
        adapter = ShixianAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "streams": [{
                "kind": "full_time",
                "page_url_template": template,
                "publication_sorted_desc": True,
                "publication_max_pages": 3,
            }],
            "detail_sample_limit": 0,
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=False)

        self.assertEqual(["近期一", "近期二"], [record.title_raw for record in result.records])
        self.assertEqual([page1, page2, page3], client.calls)
        self.assertTrue(result.window_complete)
        self.assertEqual("source_exhausted", result.stop_reason)

    def test_shixian_parse_gap_prevents_complete_window_claim(self) -> None:
        template = "https://shixian.com/full?page={page}"
        page1 = template.format(page=1)
        page2 = template.format(page=2)
        malformed = '<div class="job-list"><div class="job"><span class="publish-at">1个月前</span></div></div>'
        client = InlineClient({page1: malformed, page2: '<div class="job-list"></div>'})
        adapter = ShixianAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "streams": [{
                "kind": "full_time",
                "page_url_template": template,
                "publication_sorted_desc": True,
                "publication_max_pages": 3,
            }],
            "detail_sample_limit": 0,
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=False)

        self.assertFalse(result.window_complete)
        self.assertEqual("source_incomplete", result.stop_reason)
        self.assertEqual("parse_incomplete", result.source_coverage["full_time"]["stop_reason"])

    def test_shixian_last_stream_fills_filtered_first_stream_shortfall(self) -> None:
        full_template = "https://shixian.com/full?page={page}"
        part_template = "https://shixian.com/part?page={page}"
        part_html = """
        <div class="job-list">
          <div class="job"><div class="info"><a href="/jobs/11"><h5 class="title">
            近期任务一 <span class="type">远程兼职</span></h5></a></div>
            <div class="about"><h4 class="price">1000元</h4></div>
            <span class="publish-at">1个月前</span></div>
          <div class="job"><div class="info"><a href="/jobs/12"><h5 class="title">
            近期任务二 <span class="type">远程兼职</span></h5></a></div>
            <div class="about"><h4 class="price">2000元</h4></div>
            <span class="publish-at">2个月前</span></div>
        </div>
        """
        client = InlineClient(
            {
                full_template.format(page=1): '<div class="job-list"></div>',
                part_template.format(page=1): part_html,
            }
        )
        adapter = ShixianAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "streams": [
                {"kind": "full_time", "page_url_template": full_template},
                {"kind": "part_time", "page_url_template": part_template},
            ],
            "detail_sample_limit": 0,
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(2, with_details=False)

        self.assertEqual(
            ["近期任务一", "近期任务二"],
            [record.title_raw for record in result.records],
        )

    def test_shixian_duplicate_does_not_consume_stream_quota(self) -> None:
        full_template = "https://shixian.com/full?page={page}"
        part_template = "https://shixian.com/part?page={page}"
        full_html = """
        <div class="job-list">
          <div class="job"><div class="info"><a href="/jobs/1"><h5 class="title">
            全职任务 <span class="type">远程全职</span></h5></a></div>
            <div class="about"><h4 class="price">1000元</h4></div>
            <span class="publish-at">1个月前</span></div>
          <div class="job"><div class="info"><a href="/jobs/1"><h5 class="title">
            全职任务置顶 <span class="type">远程全职</span></h5></a></div>
            <div class="about"><h4 class="price">1000元</h4></div>
            <span class="publish-at">1个月前</span></div>
        </div>
        """
        part_html = "<div class='job-list'>" + "".join(
            f"""<div class="job"><div class="info"><a href="/jobs/{item_id}">
            <h5 class="title">兼职任务{item_id}<span class="type">远程兼职</span></h5></a></div>
            <div class="about"><h4 class="price">{item_id}000元</h4></div>
            <span class="publish-at">2个月前</span></div>"""
            for item_id in (2, 3, 4)
        ) + "</div>"
        client = InlineClient(
            {
                full_template.format(page=1): full_html,
                full_template.format(page=2): '<div class="job-list"></div>',
                part_template.format(page=1): part_html,
            }
        )
        adapter = ShixianAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "streams": [
                {"kind": "full_time", "page_url_template": full_template},
                {"kind": "part_time", "page_url_template": part_template},
            ],
            "detail_sample_limit": 0,
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(4, with_details=False)

        self.assertEqual(4, len(result.records))
        self.assertEqual(4, len({record.record_id for record in result.records}))

    def test_combined_filters_report_one_final_reason_per_record(self) -> None:
        template = "https://shixian.com/full?page={page}"
        client = InlineClient(
            {
                template.format(page=1): self._shixian_html(
                    item_id=1, title="面议任务", published="1个月前"
                ).replace("1000元", "面议")
            }
        )
        adapter = ShixianAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "streams": [{"kind": "full_time", "page_url_template": template}],
            "detail_sample_limit": 0,
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(
            1, with_details=False, numeric_price_only=True
        )

        self.assertEqual(1, result.filtered_out_count)
        self.assertEqual({"numeric_price": 1}, result.filter_counts)

    def test_proginn(self) -> None:
        urls = {
            "https://www.proginn.com/cat/python": "proginn.html",
            "https://www.proginn.com/cat/java": "proginn.html",
        }
        result = ProginnAdapter(self.config, FakeClient(urls), self.checkpoint).crawl(
            1, with_details=False
        )
        self.assertEqual("success", result.status)
        self.assertIn("6525元/月", result.records[0].price_raw_joined)

    def test_proginn_detail_day_month_prices_keep_units(self) -> None:
        self.assertEqual(
            ["300/日", "6525/月"],
            extract_price_strings("全职 · 300/日 · 6525/月"),
        )

    def test_zbj_service(self) -> None:
        url = "https://www.zbj.com/fw/"
        result = ZbjAdapter(
            self.config, FakeClient({url: "zbj.html"}), self.checkpoint
        ).crawl(1, with_details=False)
        self.assertEqual("success", result.status)
        self.assertEqual("service_listing", result.records[0].content_category)

    def test_zbj_embedded_nuxt_aliases(self) -> None:
        html = """<script>window.__NUXT__=(function(a,b,c,d,e){x[0]={id:1,uuid:\"AbCdEf123456\",title:a,priceDesc:b,content:c,statusDesc:d,publishTime:e};return {}}(\"开发小程序\",\"16800元\",\"需求描述\",\"进行中\",\"2026-07-16发布\"));</script>"""
        adapter = ZbjAdapter(self.config, FakeClient({}), self.checkpoint)
        records = adapter._parse_embedded_demands(
            html, list_url="https://www.zbj.com/xq/rjkf/", page_no=1
        )
        self.assertEqual(1, len(records))
        self.assertEqual("16800元", records[0].price_raw_joined)
        self.assertEqual("进行中", records[0].status_raw)

    def test_zbj_recent_filter_enriches_link_record_from_nuxt_time(self) -> None:
        url = "https://www.zbj.com/xq/rjkf/"
        detail_url = "https://www.zbj.com/xq/RecentDemand01.html"
        html = f"""
        <div class="card"><a href="{detail_url}" title="近期项目">近期项目</a>
          <span>5000元</span></div>
        <script>window.__NUXT__=(function(){{x={{id:1,uuid:"RecentDemand01",
        title:"近期项目",priceDesc:"5000元",content:"需求",statusDesc:"进行中",
        publishTime:"2026-04-21发布"}};return {{}}}}());</script>
        """
        client = InlineClient({url: html})
        adapter = ZbjAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "detail_sample_limit": 0,
            "sources": [{"kind": "demands", "start_url": url, "max_pages": 1}],
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(1, with_details=False)

        self.assertEqual(1, len(result.records))
        self.assertEqual("2026-04-21发布", result.records[0].published_at_raw)

    def test_zbj_publication_filter_skips_service_sources_without_publish_time(self) -> None:
        service_url = "https://www.zbj.com/fw/"
        demand_url = "https://www.zbj.com/xq/rjkf/r4/"
        client = InlineClient({demand_url: self._zbj_demand_html("16800元")})
        adapter = ZbjAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "detail_sample_limit": 0,
            "sources": [
                {"kind": "services", "start_url": service_url, "max_pages": 1},
                {"kind": "demands", "start_url": demand_url, "max_pages": 1},
            ],
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(1, with_details=False)

        self.assertEqual([demand_url], client.calls)
        self.assertEqual(1, len(result.records))
        self.assertTrue(
            any("service sources skipped" in note for note in result.runtime_notes)
        )

    def test_zbj_nuxt_merge_ignores_link_query_and_fragment(self) -> None:
        url = "https://www.zbj.com/xq/rjkf/"
        canonical = "https://www.zbj.com/xq/RecentDemand01.html"
        html = f"""
        <div class="card"><a href="{canonical}?from=list#top" title="近期项目">近期项目</a>
          <span>5000元</span></div>
        <script>window.__NUXT__=(function(){{x={{id:1,uuid:"RecentDemand01",
        title:"近期项目",priceDesc:"5000元",content:"需求",statusDesc:"进行中",
        publishTime:"2026-04-21发布"}};return {{}}}}());</script>
        """
        adapter = ZbjAdapter(
            self.config, InlineClient({url: html}), self.checkpoint
        )
        adapter.site_config = {
            "detail_sample_limit": 0,
            "sources": [{"kind": "demands", "start_url": url, "max_pages": 1}],
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )

        result = adapter.crawl(2, with_details=False)

        self.assertEqual(1, len(result.records))
        self.assertEqual(canonical, result.records[0].detail_url)
        self.assertEqual(0, result.filtered_out_count)
        self.assertNotIn("publication_unknown", result.filter_counts)

    @staticmethod
    def _zbj_demand_html(*prices: str) -> str:
        objects = []
        for index, price in enumerate(prices, 1):
            objects.append(
                "{id:%d,uuid:\"DemandUuid%03d\",title:\"项目%d\","
                "priceDesc:\"%s\",content:\"需求\",statusDesc:\"进行中\","
                "publishTime:\"2026-07-20\"};"
                % (index, index, index, price)
            )
        return "<script>window.__NUXT__=(function(){%sreturn {}}());</script>" % "".join(objects)

    def test_zbj_numeric_price_filter_continues_to_next_page(self) -> None:
        page1 = "https://www.zbj.com/xq/rjkf/r4/"
        page2 = "https://www.zbj.com/xq/rjkf/r4p2/"
        client = InlineClient(
            {
                page1: self._zbj_demand_html("待服务商报价"),
                page2: self._zbj_demand_html("16800元"),
            }
        )
        adapter = ZbjAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "detail_sample_limit": 0,
            "sources": [
                {
                    "kind": "demands",
                    "start_url": page1,
                    "page_url_template": "https://www.zbj.com/xq/rjkf/r4p{page}/",
                    "max_pages": 2,
                }
            ],
        }

        result = adapter.crawl(
            1, with_details=False, numeric_price_only=True
        )

        self.assertEqual([page1, page2], client.calls)
        self.assertEqual("success", result.status)
        self.assertEqual(["16800元"], [record.price_raw_joined for record in result.records])
        self.assertEqual(1, result.filtered_out_count)

    def test_zbj_complete_window_collects_past_limit_until_cutoff(self) -> None:
        page1 = "https://www.zbj.com/xq/rjkf/r4/"
        page2 = "https://www.zbj.com/xq/rjkf/r4p2/"

        def demand(uuid: str, title: str, published: str) -> str:
            return (
                f'{{id:1,uuid:"{uuid}",title:"{title}",priceDesc:"5000元",'
                f'content:"需求",statusDesc:"进行中",publishTime:"{published}"}};'
            )

        wrap = lambda body: f"<script>window.__NUXT__=(function(){{{body}return {{}}}}());</script>"
        client = InlineClient({
            page1: wrap(demand("Recent001", "近期一", "2026-07-20") + demand("Recent002", "近期二", "2026-07-19")),
            page2: wrap(demand("Old001", "旧任务", "2026-04-20")),
        })
        adapter = ZbjAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "detail_sample_limit": 0,
            "sources": [{
                "kind": "demands",
                "start_url": page1,
                "page_url_template": "https://www.zbj.com/xq/rjkf/r4p{page}/",
                "publication_sorted_desc": True,
                "publication_max_pages": 3,
            }],
        }
        adapter.publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        adapter.complete_publication_window = True

        result = adapter.crawl(1, with_details=False)

        self.assertEqual(["近期一", "近期二"], [record.title_raw for record in result.records])
        self.assertEqual([page1, page2], client.calls)
        self.assertTrue(result.window_complete)

    def test_zbj_numeric_price_filter_reports_partial_at_page_cap(self) -> None:
        page1 = "https://www.zbj.com/xq/rjkf/"
        client = InlineClient(
            {page1: self._zbj_demand_html("待服务商报价", "16800元")}
        )
        adapter = ZbjAdapter(self.config, client, self.checkpoint)
        adapter.site_config = {
            "detail_sample_limit": 0,
            "sources": [
                {"kind": "demands", "start_url": page1, "max_pages": 1}
            ],
        }

        result = adapter.crawl(
            2, with_details=False, numeric_price_only=True
        )

        self.assertEqual("partial", result.status)
        self.assertEqual(1, len(result.records))
        self.assertEqual(1, result.filtered_out_count)


if __name__ == "__main__":
    unittest.main()
