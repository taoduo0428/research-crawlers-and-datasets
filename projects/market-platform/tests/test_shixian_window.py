from __future__ import annotations

import unittest
from datetime import date

from drv_crawler.core import FetchedPage, ProjectConfig
from drv_crawler.publication import PublicationFilter
from drv_crawler.sites import ShixianAdapter


class InlineClient:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> FetchedPage:
        self.calls.append(url)
        return FetchedPage(
            url,
            url,
            200,
            self.mapping[url].encode("utf-8"),
            "utf-8",
        )


class AdapterConfig:
    def __init__(
        self,
        site_config: dict[str, object],
        *,
        publication_detail_cap: int = 5000,
    ) -> None:
        self.site_config = site_config
        self.publication_detail_cap = publication_detail_cap

    def site(self, name: str) -> dict[str, object]:
        if name != "shixian":
            raise KeyError(name)
        return self.site_config

    def section(self, name: str) -> dict[str, object]:
        if name == "limits":
            return {
                "max_pages_per_site": 10,
                "max_publication_records_per_site": 10000,
                "max_publication_detail_requests_per_site": (
                    self.publication_detail_cap
                ),
            }
        return {}


def job_list(*items: tuple[int, str, str]) -> str:
    cards = []
    for item_id, title, published in items:
        cards.append(
            f"""
            <div class="job">
              <div class="info"><a href="/jobs/{item_id}">
                <h5 class="title">{title}<span class="type">远程兼职</span></h5>
              </a></div>
              <div class="about"><h4 class="price">1000元</h4><dl>预算：1000元</dl></div>
              <span class="publish-at">{published}</span>
            </div>
            """
        )
    return '<div class="job-list">' + "".join(cards) + "</div>"


EMPTY_PLACEHOLDER = """
<div class="job-list">
  <div class="job text-center">抱歉，暂时还没有符合条件的兼职需求~</div>
</div>
"""


def detail_page(title: str) -> str:
    return f"""
    <article class="job-show">
      <h1 class="title">{title}</h1>
      <strong class="price">预估 1000 元</strong>
      <section class="description clearfix"><div class="content">项目预算 1000 元</div></section>
    </article>
    """


class ShixianWindowTests(unittest.TestCase):
    @staticmethod
    def checkpoint(*_: object) -> None:
        return None

    def make_adapter(
        self,
        site_config: dict[str, object],
        client: InlineClient,
        *,
        publication_detail_cap: int = 5000,
    ) -> ShixianAdapter:
        adapter = ShixianAdapter(  # type: ignore[arg-type]
            AdapterConfig(
                site_config,
                publication_detail_cap=publication_detail_cap,
            ),
            client,
            self.checkpoint,
        )
        adapter.publication_filter = PublicationFilter(
            recent_months=3,
            as_of=date(2026, 7, 21),
        )
        adapter.complete_publication_window = True
        return adapter

    def test_complete_window_scans_both_streams_to_real_placeholder(self) -> None:
        full_template = "https://shixian.test/full?page={page}"
        part_template = "https://shixian.test/part?page={page}"
        mapping = {
            full_template.format(page=1): job_list((1, "三月边界", "3 个月前发布")),
            full_template.format(page=2): job_list((2, "全职旧任务", "4个月前")),
            full_template.format(page=3): EMPTY_PLACEHOLDER,
            part_template.format(page=1): job_list((3, "近期兼职", "1个月前")),
            part_template.format(page=2): job_list((4, "兼职旧任务", "4个月前")),
            part_template.format(page=3): EMPTY_PLACEHOLDER,
        }
        mapping[next(iter(mapping))] += job_list((5, "missing", ""))
        client = InlineClient(mapping)
        adapter = self.make_adapter(
            {
                "streams": [
                    {
                        "kind": "full_time",
                        "page_url_template": full_template,
                        "publication_sorted_desc": True,
                        "publication_max_pages": 5,
                    },
                    {
                        "kind": "part_time",
                        "page_url_template": part_template,
                        "publication_sorted_desc": True,
                        "publication_max_pages": 5,
                    },
                ],
                "detail_sample_limit": 10,
            },
            client,
        )

        result = adapter.crawl(1, with_details=False)

        self.assertEqual(list(mapping), client.calls)
        self.assertEqual(
            ["三月边界", "近期兼职"],
            [record.title_raw for record in result.records],
        )
        boundary = result.records[0].metadata["publication_time"]
        self.assertEqual("uncertain", boundary["status"])
        self.assertEqual("site_three_month_label", boundary["boundary_assumption"])
        self.assertEqual(3, result.filtered_out_count)
        self.assertNotIn("publication_uncertain", result.filter_counts)
        self.assertEqual(1, result.filter_counts["publication_unknown"])
        self.assertFalse(result.window_complete)
        self.assertEqual("publication_unavailable", result.stop_reason)
        self.assertTrue(any("<missing>" in note for note in result.runtime_notes))
        self.assertEqual("partial", result.status)
        self.assertEqual(
            {
                "full_time": {"complete": True, "stop_reason": "source_exhausted"},
                "part_time": {"complete": True, "stop_reason": "source_exhausted"},
            },
            result.source_coverage,
        )

    def test_only_exact_three_month_site_label_uses_boundary_assumption(self) -> None:
        template = "https://shixian.test/full?page={page}"
        client = InlineClient(
            {
                template.format(page=1): job_list(
                    (1, "精确标签", "3个月前"),
                    (2, "近似标签", "大约3个月前"),
                ),
                template.format(page=2): EMPTY_PLACEHOLDER,
            }
        )
        adapter = self.make_adapter(
            {
                "streams": [
                    {
                        "kind": "full_time",
                        "page_url_template": template,
                        "publication_max_pages": 3,
                    }
                ],
                "detail_sample_limit": 10,
            },
            client,
        )

        result = adapter.crawl(1, with_details=False)

        self.assertEqual(["精确标签"], [record.title_raw for record in result.records])
        publication = result.records[0].metadata["publication_time"]
        self.assertEqual("uncertain", publication["status"])
        self.assertEqual("site_three_month_label", publication["boundary_assumption"])
        self.assertEqual(1, result.filter_counts["publication_uncertain"])

    def test_complete_window_fetches_every_selected_detail(self) -> None:
        template = "https://shixian.test/part?page={page}"
        items = tuple(
            (item_id, f"近期任务{item_id}", "1个月前")
            for item_id in range(1, 13)
        )
        mapping = {
            template.format(page=1): job_list(*items),
            template.format(page=2): EMPTY_PLACEHOLDER,
        }
        for item_id, title, _ in items:
            mapping[f"https://shixian.test/jobs/{item_id}"] = detail_page(title)
        client = InlineClient(mapping)
        adapter = self.make_adapter(
            {
                "streams": [
                    {
                        "kind": "part_time",
                        "page_url_template": template,
                        "publication_max_pages": 3,
                    }
                ],
                "detail_sample_limit": 10,
            },
            client,
        )

        result = adapter.crawl(1, with_details=True)

        self.assertEqual(12, len(result.records))
        self.assertEqual(12, result.details_fetched)
        self.assertTrue(all(record.detail_title_raw for record in result.records))
        self.assertTrue(result.window_complete)
        capped_client = InlineClient(mapping)
        capped_adapter = self.make_adapter(
            adapter.site_config, capped_client, publication_detail_cap=2
        )
        capped_result = capped_adapter.crawl(1, with_details=True)
        self.assertEqual(2, capped_result.details_fetched)
        self.assertNotIn("https://shixian.test/jobs/3", capped_client.calls)
        self.assertFalse(capped_result.window_complete)
        self.assertEqual("detail_cap_reached", capped_result.stop_reason)
        self.assertEqual("partial", capped_result.status)
        broken_mapping = dict(list(mapping.items())[:-1])
        broken_client = InlineClient(broken_mapping)
        broken_adapter = self.make_adapter(adapter.site_config, broken_client)
        broken_result = broken_adapter.crawl(1, with_details=True)
        self.assertEqual(11, broken_result.details_fetched)
        self.assertEqual(["detail"], [item.stage for item in broken_result.failures])
        self.assertFalse(broken_result.window_complete)
        self.assertEqual("detail_error", broken_result.stop_reason)
        self.assertEqual("partial", broken_result.status)

    def test_configured_project_mode_cap_reaches_page_124(self) -> None:
        config = ProjectConfig("crawler.config.yaml")
        streams = config.site("shixian")["streams"]
        project = next(
            stream for stream in streams if stream.get("kind") == "part_time"
        )

        self.assertGreaterEqual(int(project["publication_max_pages"]), 124)


if __name__ == "__main__":
    unittest.main()
