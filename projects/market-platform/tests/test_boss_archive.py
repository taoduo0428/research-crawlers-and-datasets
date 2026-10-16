from __future__ import annotations

import gzip
import json
import unittest
from urllib.parse import urlparse

from drv_crawler.boss_archive import (
    BossArchiveCollector,
    extract_warc_http_body,
    parse_boss_seo_snapshot,
)
from drv_crawler.core import FetchedPage, ResponseTooLargeError


SEO_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>岗位招聘</title></head><body>
<ul class="job-list">
  <li><div class="job-primary">
    <div class="info-primary">
      <h3 class="name"><a href="/job_detail/job-one.html">
        Python 后端开发工程师 <span class="red">18-20K·15薪</span>
      </a></h3>
      <p><a href="/c101010100/">北京海淀区</a><em class="vline"></em>3-5年<em class="vline"></em>本科</p>
      <p class="job-demand">负责 Python 服务端开发。</p>
    </div>
    <div class="info-company"><h3 class="name"><a href="/gongsi/company-one.html">示例科技</a></h3></div>
  </div></li>
  <li><div class="job-primary">
    <div class="info-primary">
      <h3 class="name"><a href="/job_detail/job-two.html">
        数据分析师 <span class="red">12-18K</span>
      </a></h3>
      <p><a href="/c101020100/">上海浦东新区</a><em class="vline"></em>1-3年<em class="vline"></em>大专</p>
      <p class="job-demand">负责业务数据分析。</p>
    </div>
    <div class="info-company"><h3 class="name"><a href="/gongsi/company-two.html">样例数据</a></h3></div>
  </div></li>
</ul></body></html>"""


def make_warc(html: str) -> bytes:
    body = html.encode("utf-8")
    http = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )
    warc = (
        b"WARC/1.0\r\n"
        b"WARC-Type: response\r\n"
        + f"Content-Length: {len(http)}\r\n\r\n".encode("ascii")
        + http
        + b"\r\n\r\n"
    )
    return gzip.compress(warc)


class FakeArchiveClient:
    def __init__(self, warc: bytes) -> None:
        self.warc = warc
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> FetchedPage:
        self.calls.append((url, kwargs))
        host = urlparse(url).netloc
        if url == "https://index.test/collinfo.json":
            content = json.dumps(
                [{"id": "CC-MAIN-TEST", "cdx-api": "https://index.test/cdx"}]
            ).encode()
        elif host == "index.test" and url.startswith("https://index.test/cdx?"):
            content = (
                json.dumps(
                    {
                        "url": "https://www.zhipin.com/zhaopin/sample/",
                        "timestamp": "20260616092514",
                        "mime": "text/html",
                        "status": "200",
                        "length": str(len(self.warc)),
                        "offset": "10",
                        "filename": "crawl-data/test.warc.gz",
                    }
                )
                + "\n"
            ).encode()
        elif url == "https://data.test/crawl-data/test.warc.gz":
            content = self.warc
        else:
            raise AssertionError(f"Unexpected URL: {url}")
        return FetchedPage(url, url, 200, content, "utf-8")


class BossArchiveTests(unittest.TestCase):
    def test_snapshot_parser_preserves_salary_and_archive_provenance(self) -> None:
        records = parse_boss_seo_snapshot(
            SEO_HTML.encode("utf-8"),
            source_url="https://www.zhipin.com/zhaopin/sample/",
            archive_index="CC-MAIN-TEST",
            archive_timestamp="20260616092514",
        )

        self.assertEqual(2, len(records))
        self.assertEqual("Python 后端开发工程师", records[0].title_raw)
        self.assertEqual("18-20K·15薪", records[0].price_raw_joined)
        self.assertEqual("示例科技", records[0].publisher_name_raw)
        self.assertEqual("北京海淀区", records[0].location_raw)
        self.assertEqual("common_crawl_archive", records[0].capture_method)
        self.assertEqual("archive_snapshot", records[0].source_state)
        self.assertEqual("CC-MAIN-TEST", records[0].metadata["archive_index"])
        self.assertEqual("2026-06-16T09:25:14Z", records[0].metadata["archive_captured_at"])

    def test_collector_fetches_bounded_range_without_cookie_state(self) -> None:
        warc = make_warc(SEO_HTML)
        client = FakeArchiveClient(warc)
        collector = BossArchiveCollector(
            client,
            {
                "access_policy": "documented_public_api",
                "collinfo_url": "https://index.test/collinfo.json",
                "data_base_url": "https://data.test/",
                "url_pattern": "www.zhipin.com/zhaopin/*",
                "max_candidates": 10,
                "max_snapshots": 2,
                "max_compressed_bytes": 100_000,
                "max_uncompressed_bytes": 500_000,
            },
        )

        result = collector.collect(limit=2)

        self.assertEqual(2, len(result.records))
        self.assertEqual(1, result.snapshots_fetched)
        range_calls = [kwargs for url, kwargs in client.calls if "data.test" in url]
        self.assertEqual(
            [
                {
                    "headers": {"Range": f"bytes=10-{10 + len(warc) - 1}"},
                    "obey_robots": False,
                }
            ],
            range_calls,
        )
        self.assertTrue(
            all(kwargs.get("obey_robots") is False for _, kwargs in client.calls)
        )
        self.assertTrue(all("storage_state" not in kwargs for _, kwargs in client.calls))

    def test_warc_decompression_limit_is_enforced(self) -> None:
        compressed = make_warc("x" * 20_000)
        with self.assertRaises(ResponseTooLargeError):
            extract_warc_http_body(compressed, max_uncompressed_bytes=1_000)


if __name__ == "__main__":
    unittest.main()
