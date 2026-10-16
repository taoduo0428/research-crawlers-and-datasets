from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .core import CrawlerError, FetchedPage, ResponseTooLargeError
from .models import CrawlRecord, clean_text, dedupe_records, make_record_id
from .sites import add_price


class ArchiveHttpClient(Protocol):
    def get(self, url: str, **kwargs: object) -> FetchedPage: ...


@dataclass(slots=True)
class BossArchiveCollection:
    records: list[CrawlRecord]
    archive_index: str
    snapshots_fetched: int
    candidates_considered: int
    errors: list[str]


def _timestamp_iso(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return value
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _header_body(payload: bytes, *, label: str) -> bytes:
    for separator in (b"\r\n\r\n", b"\n\n"):
        position = payload.find(separator)
        if position >= 0:
            return payload[position + len(separator) :]
    raise CrawlerError(f"Malformed Common Crawl {label}: header terminator missing")


def extract_warc_http_body(
    compressed: bytes, *, max_uncompressed_bytes: int
) -> bytes:
    """Return the archived HTTP entity while bounding gzip expansion."""

    if max_uncompressed_bytes <= 0:
        raise ValueError("max_uncompressed_bytes must be positive")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        output = decoder.decompress(compressed, max_uncompressed_bytes + 1)
        if decoder.unconsumed_tail or len(output) > max_uncompressed_bytes:
            raise ResponseTooLargeError(
                f"Common Crawl WARC expands beyond {max_uncompressed_bytes} bytes"
            )
        remaining = max_uncompressed_bytes + 1 - len(output)
        if remaining > 0:
            output += decoder.flush(remaining)
    except zlib.error as exc:
        raise CrawlerError("Invalid Common Crawl gzip member") from exc
    if len(output) > max_uncompressed_bytes:
        raise ResponseTooLargeError(
            f"Common Crawl WARC expands beyond {max_uncompressed_bytes} bytes"
        )
    if not decoder.eof:
        raise CrawlerError("Truncated Common Crawl gzip member")
    warc_payload = _header_body(output, label="WARC record")
    return _header_body(warc_payload, label="HTTP response")


def _direct_text(anchor: Tag) -> str | None:
    values = [
        clean_text(str(node))
        for node in anchor.find_all(string=True, recursive=False)
    ]
    return clean_text(" ".join(value for value in values if value))


def parse_boss_seo_snapshot(
    content: bytes,
    *,
    source_url: str,
    archive_index: str,
    archive_timestamp: str,
) -> list[CrawlRecord]:
    """Parse public BOSS SEO job cards preserved in one Common Crawl snapshot."""

    soup = BeautifulSoup(content, "html.parser", from_encoding="utf-8")
    records: list[CrawlRecord] = []
    archive_captured_at = _timestamp_iso(archive_timestamp)
    for position, card in enumerate(soup.select(".job-primary"), start=1):
        primary = card.select_one(".info-primary")
        if not isinstance(primary, Tag):
            continue
        anchor = primary.select_one('h3.name a[href*="/job_detail/"]')
        salary_node = primary.select_one("h3.name .red")
        if not isinstance(anchor, Tag) or not isinstance(salary_node, Tag):
            continue
        title = _direct_text(anchor)
        salary = clean_text(salary_node.get_text(" ", strip=True))
        href = clean_text(str(anchor.get("href") or ""))
        if not title or not salary or not href:
            continue

        detail_url = urljoin(source_url, href)
        facts_node = primary.select_one("p:not(.job-demand)")
        facts = list(facts_node.stripped_strings) if isinstance(facts_node, Tag) else []
        company_node = card.select_one(".info-company h3.name a")
        description_node = primary.select_one(".job-demand")
        record = CrawlRecord(
            site_name="boss",
            content_category="full_time_job",
            title_raw=title,
            list_url=source_url,
            detail_url=detail_url,
            page_no=1,
            list_position=position,
            publisher_name_raw=(
                company_node.get_text(" ", strip=True)
                if isinstance(company_node, Tag)
                else None
            ),
            location_raw=facts[0] if facts else None,
            description_raw=(
                description_node.get_text(" ", strip=True)
                if isinstance(description_node, Tag)
                else None
            ),
            capture_method="common_crawl_archive",
            source_state="archive_snapshot",
            metadata={
                "archive_index": archive_index,
                "archive_timestamp": archive_timestamp,
                "archive_captured_at": archive_captured_at,
                "archive_source_url": source_url,
                "experience_raw": facts[1] if len(facts) > 1 else None,
                "degree_raw": facts[2] if len(facts) > 2 else None,
            },
            record_id=make_record_id("boss", detail_url),
        )
        add_price(
            record,
            salary,
            source="archived_list_page",
            url=source_url,
            location=".job-primary .info-primary h3.name .red",
            kind="salary_range",
        )
        records.append(record)
    return dedupe_records(records)


def _positive_int(settings: Mapping[str, Any], key: str, default: int, cap: int) -> int:
    value = int(settings.get(key, default))
    if value <= 0:
        raise CrawlerError(f"boss.archive_fallback.{key} must be positive")
    return min(value, cap)


class BossArchiveCollector:
    """Bounded, no-auth BOSS collector backed by public Common Crawl WARC records."""

    def __init__(
        self, client: ArchiveHttpClient, settings: Mapping[str, Any] | None = None
    ) -> None:
        self.client = client
        self.settings = dict(settings or {})
        access_policy = str(self.settings.get("access_policy", "robots"))
        if access_policy not in {"robots", "documented_public_api"}:
            raise CrawlerError(
                "boss.archive_fallback.access_policy must be robots or documented_public_api"
            )
        self.obey_robots = access_policy != "documented_public_api"
        self.collinfo_url = str(
            self.settings.get(
                "collinfo_url", "https://index.commoncrawl.org/collinfo.json"
            )
        )
        self.data_base_url = str(
            self.settings.get("data_base_url", "https://data.commoncrawl.org/")
        )
        self.url_pattern = str(
            self.settings.get("url_pattern", "www.zhipin.com/zhaopin/*")
        )
        self.max_candidates = _positive_int(
            self.settings, "max_candidates", 100, 1_000
        )
        self.max_snapshots = _positive_int(self.settings, "max_snapshots", 4, 10)
        self.max_compressed_bytes = _positive_int(
            self.settings, "max_compressed_bytes", 500_000, 2_000_000
        )
        self.max_uncompressed_bytes = _positive_int(
            self.settings, "max_uncompressed_bytes", 1_500_000, 10_000_000
        )

    def _latest_index(self) -> tuple[str, str]:
        page = self.client.get(self.collinfo_url, obey_robots=self.obey_robots)
        try:
            collections = json.loads(page.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CrawlerError("Common Crawl collection metadata is not valid JSON") from exc
        if not isinstance(collections, list):
            raise CrawlerError("Common Crawl collection metadata must be a list")
        for item in collections:
            if not isinstance(item, dict):
                continue
            index_id = clean_text(str(item.get("id") or ""))
            cdx_api = clean_text(str(item.get("cdx-api") or ""))
            if index_id and cdx_api and urlparse(cdx_api).scheme == "https":
                return index_id, cdx_api
        raise CrawlerError("Common Crawl metadata contains no usable HTTPS index")

    def _index_entries(self, cdx_api: str) -> list[dict[str, Any]]:
        query = urlencode(
            [
                ("url", self.url_pattern),
                ("output", "json"),
                ("filter", "status:200"),
                ("filter", "mime:text/html"),
                ("collapse", "urlkey"),
            ]
        )
        page = self.client.get(
            f"{cdx_api}?{query}", obey_robots=self.obey_robots
        )
        entries: list[dict[str, Any]] = []
        for line in page.content.decode("utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            try:
                length = int(item.get("length", 0))
                offset = int(item.get("offset", -1))
            except (TypeError, ValueError):
                continue
            source_url = str(item.get("url") or "")
            parsed_source = urlparse(source_url)
            filename = str(item.get("filename") or "")
            if (
                str(item.get("status")) != "200"
                or not str(item.get("mime", "")).startswith("text/html")
                or parsed_source.hostname not in {"www.zhipin.com", "m.zhipin.com"}
                or not parsed_source.path.startswith("/zhaopin/")
                or length <= 0
                or length > self.max_compressed_bytes
                or offset < 0
                or not filename.startswith("crawl-data/")
                or ".." in filename
                or "://" in filename
            ):
                continue
            item["length"] = length
            item["offset"] = offset
            entries.append(item)
        entries.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return entries[: self.max_candidates]

    def _data_url(self, filename: str) -> str:
        base = self.data_base_url.rstrip("/") + "/"
        target = urljoin(base, filename)
        if urlparse(target).netloc != urlparse(base).netloc:
            raise CrawlerError("Common Crawl WARC filename escaped the data host")
        return target

    def collect(self, limit: int) -> BossArchiveCollection:
        if limit <= 0:
            raise ValueError("limit must be positive")
        archive_index, cdx_api = self._latest_index()
        entries = self._index_entries(cdx_api)
        records: list[CrawlRecord] = []
        errors: list[str] = []
        snapshots_fetched = 0
        considered = 0
        for entry in entries:
            if len(records) >= limit or snapshots_fetched >= self.max_snapshots:
                break
            considered += 1
            start = int(entry["offset"])
            length = int(entry["length"])
            end = start + length - 1
            try:
                page = self.client.get(
                    self._data_url(str(entry["filename"])),
                    headers={"Range": f"bytes={start}-{end}"},
                    obey_robots=self.obey_robots,
                )
                if len(page.content) != length:
                    raise CrawlerError(
                        "Common Crawl range response length did not match the index"
                    )
                html = extract_warc_http_body(
                    page.content,
                    max_uncompressed_bytes=self.max_uncompressed_bytes,
                )
                snapshot_records = parse_boss_seo_snapshot(
                    html,
                    source_url=str(entry["url"]),
                    archive_index=archive_index,
                    archive_timestamp=str(entry.get("timestamp") or ""),
                )
                if not snapshot_records:
                    raise CrawlerError("Common Crawl BOSS snapshot contained no job cards")
                records = dedupe_records([*records, *snapshot_records])
                snapshots_fetched += 1
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)[:180]}")
        if not records:
            detail = errors[-1] if errors else "no eligible index entries"
            raise CrawlerError(f"Common Crawl BOSS fallback produced no records: {detail}")
        return BossArchiveCollection(
            records=records[:limit],
            archive_index=archive_index,
            snapshots_fetched=snapshots_fetched,
            candidates_considered=considered,
            errors=errors[:10],
        )
