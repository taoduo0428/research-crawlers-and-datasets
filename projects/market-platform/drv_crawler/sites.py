from __future__ import annotations

import re
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Tag

from .core import (
    BlockedResponseError,
    ProjectConfig,
    ResilientHttpClient,
    RobotsDeniedError,
    safe_error_message,
    safe_url,
)
from .epwk_browser import EpwkBrowserLimitReached, EpwkPublicationRenderer
from .categories import CategoryParseError, parse_epwk_detail_category
from .models import CrawlFailure, CrawlRecord, PriceEvidence, SiteResult, clean_text
from .publication import PublicationFilter


CheckpointCallback = Callable[[SiteResult, str], None]


PRICE_RE = re.compile(
    r"(?:待服务商报价|需人才报价|价格面议|薪资面议|面议|"
    r"\d+(?:\.\d+)?\s*/\s*(?:天|日|月|年)|"
    r"(?:[￥¥元]? *)?\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?"
    r"\s*(?:万元|元|万|千|[Kk])(?:\s*[/.] *(?:天|日|月|年|8小时))?"
    r"(?:·\d+薪)?)"
)

# EPWK descriptions sometimes put a bare amount after a payment-context label
# (for example ``项目总价38000``).  The context is required to avoid treating
# task numbers, dates, or arbitrary counts as price evidence.
EPWK_DESCRIPTION_PRICE_RE = re.compile(
    r"(?:项目总价|总价|一期|二期|三期|首款|尾款|定金|每张|每件|每单|单价)"
    r"\s*[：:]?\s*(?:人民币\s*)?\d+(?:\.\d+)?\s*(?:万元|元|万|千|[Kk])?"
)


@dataclass(frozen=True, slots=True)
class EpwkSitemapEntry:
    task_key: str
    detail_url: str
    published_at: str
    sitemap_url: str


@dataclass(slots=True)
class EpwkSitemapInventory:
    entries: list[EpwkSitemapEntry]
    complete: bool
    configured_files: int
    loaded_files: int
    undated_task_entries: int = 0

    @property
    def dates_by_id(self) -> dict[str, str]:
        return {
            entry.task_key: entry.published_at
            for entry in self.entries
        }


def text_of(node: Tag | None) -> str | None:
    return clean_text(node.get_text(" ", strip=True)) if node else None


def absolute(base_url: str, value: str | None) -> str | None:
    return urljoin(base_url, value) if value else None


def price_type(raw: str, *, platform_estimate: bool = False) -> str:
    normalized = raw.lower()
    if "面议" in raw or "报价" in raw:
        return "negotiable"
    if "薪" in raw:
        return "salary_range"
    if "/天" in raw or "/日" in raw:
        return "daily_salary"
    if "/月" in raw or "月薪" in raw:
        return "monthly_salary"
    if "/年" in raw or "年薪" in raw:
        return "annual_salary"
    if "预估" in raw or platform_estimate:
        return "platform_estimate"
    if any(char in normalized for char in ("-", "~", "至")):
        return "budget_range"
    return "fixed_amount"


def extract_price_strings(text: str | None, *, maximum: int = 12) -> list[str]:
    if not text:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for match in PRICE_RE.finditer(text):
        value = clean_text(match.group(0))
        if value and value not in seen:
            values.append(value)
            seen.add(value)
        if len(values) >= maximum:
            break
    return values


def nearest_card(
    anchor: Tag, *, must_contain_price: bool = True, max_levels: int = 8
) -> Tag:
    candidate: Tag = anchor
    best: Tag = anchor
    for _ in range(max_levels):
        parent = candidate.parent
        if not isinstance(parent, Tag):
            break
        candidate = parent
        text = text_of(candidate) or ""
        if len(text) <= 3500:
            best = candidate
            if not must_contain_price or extract_price_strings(text):
                return candidate
    return best


def add_price(
    record: CrawlRecord,
    raw: str | None,
    *,
    source: str,
    url: str,
    location: str,
    kind: str | None = None,
    package_name: str | None = None,
    estimated: bool = False,
) -> None:
    raw = clean_text(raw)
    if not raw:
        return
    record.add_price(
        PriceEvidence(
            raw_text=raw,
            price_type=kind or price_type(raw, platform_estimate=estimated),
            price_source=source,
            evidence_url=url,
            evidence_location=location,
            package_name=package_name,
            negotiable=("面议" in raw or "报价" in raw),
            estimated_by_platform=estimated,
        )
    )


class BaseSiteAdapter:
    site_name: str
    difficulty: int = 1
    adapter_name: str = "static_html"

    def __init__(
        self,
        config: ProjectConfig,
        client: ResilientHttpClient,
        checkpoint: CheckpointCallback,
    ) -> None:
        self.config = config
        self.site_config = config.site(self.site_name)
        # Per-run bounded quality-sample override; config remains the default.
        self.detail_limit_override: int | None = None
        self.client = client
        self.checkpoint = checkpoint
        self.publication_filter: PublicationFilter | None = None
        self.complete_publication_window = False

    def result(self, limit: int) -> SiteResult:
        result = SiteResult(
            site_name=self.site_name,
            requested_limit=limit,
            adapter=self.adapter_name,
            difficulty=self.difficulty,
            completion_mode=(
                "publication_window"
                if self.complete_publication_window
                else "target_count"
            ),
        )
        if self.publication_filter is not None:
            result.publication_filter = self.publication_filter.describe()
        return result

    def page_is_before_publication_cutoff(
        self, records: list[CrawlRecord]
    ) -> bool:
        return bool(
            self.publication_filter
            and records
            and all(
                self.publication_filter.decide(record.published_at_raw).status
                == "too_old"
                for record in records
            )
        )

    def failure(
        self,
        result: SiteResult,
        *,
        stage: str,
        exc: Exception,
        url: str | None = None,
        retryable: bool = False,
    ) -> None:
        result.failures.append(
            CrawlFailure(
                site_name=self.site_name,
                stage=stage,
                error_type=type(exc).__name__,
                message=safe_error_message(str(exc))[:500],
                url=safe_url(url),
                retryable=retryable,
            )
        )

    def accept_record(
        self,
        result: SiteResult,
        record: CrawlRecord,
        *,
        numeric_price_only: bool,
    ) -> bool:
        if any(existing.record_id == record.record_id for existing in result.records):
            return False
        publication_reason: str | None = None
        if self.publication_filter is not None:
            decision = self.publication_filter.decide(record.published_at_raw)
            record.metadata["publication_time"] = decision.to_dict()
            if not decision.included:
                publication_reason = f"publication_{decision.status}"
        numeric_rejected = self._numeric_price_rejected(
            result, record, numeric_price_only=numeric_price_only
        )
        if publication_reason or numeric_rejected:
            reason = publication_reason or "numeric_price"
            result.filter_counts[reason] = result.filter_counts.get(reason, 0) + 1
            result.filtered_out_count += 1
            return False
        result.records.append(record)
        return True

    @staticmethod
    def _numeric_price_rejected(
        result: SiteResult,
        record: CrawlRecord,
        *,
        numeric_price_only: bool,
    ) -> bool:
        if not numeric_price_only or record.has_numeric_price:
            return False
        return True

    def crawl(
        self,
        limit: int,
        *,
        with_details: bool = True,
        numeric_price_only: bool = False,
    ) -> SiteResult:
        raise NotImplementedError


class EpwkAdapter(BaseSiteAdapter):
    site_name = "epwk"
    difficulty = 2

    MAX_SITEMAP_FILES = 16
    MAX_SITEMAP_RESPONSE_BYTES = 6_000_000
    MAX_SITEMAP_ENTRIES_PER_FILE = 50_000
    MAX_SITEMAP_VALIDATION_SAMPLES = 50
    MAX_SITEMAP_CHECKPOINT_INTERVAL = 500
    MAX_SITEMAP_CANDIDATE_LOOKBACK_DAYS = 90

    def __init__(
        self,
        config: ProjectConfig,
        client: ResilientHttpClient,
        checkpoint: CheckpointCallback,
    ) -> None:
        super().__init__(config, client, checkpoint)
        self._publication_renderer: EpwkPublicationRenderer | None = None
        self._publication_renderer_disabled = False
        self._publication_renderer_limit_noted = False

    @staticmethod
    def _task_key(url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() != "task.epwk.com":
            return None
        match = re.fullmatch(r"/(\d+)/?", parsed.path)
        return match.group(1) if match else None

    @staticmethod
    def _sitemap_date(raw: str | None) -> str | None:
        match = re.match(r"^\s*(20\d{2}-\d{2}-\d{2})(?:[T\s]|$)", raw or "")
        if not match:
            return None
        value = match.group(1)
        try:
            date.fromisoformat(value)
        except ValueError:
            return None
        return value

    @staticmethod
    def _is_valid_non_20xx_sitemap_date(raw: str | None) -> bool:
        value = clean_text(raw)
        match = re.match(
            r"^([0-9]{4}-[0-9]{2}-[0-9]{2})(?:[T ]|$)",
            value or "",
        )
        if not match or match.group(1).startswith("20"):
            return False
        try:
            date.fromisoformat(match.group(1))
        except ValueError:
            return False
        return True

    @staticmethod
    def _xml_local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _load_publication_sitemap_inventory(
        self, result: SiteResult
    ) -> EpwkSitemapInventory:
        sitemap_config = self.site_config.get("publication_sitemap")
        if sitemap_config is None:
            return EpwkSitemapInventory([], False, 0, 0)
        if not isinstance(sitemap_config, dict):
            self.failure(
                result,
                stage="publication_sitemap",
                exc=ValueError("EPWK publication_sitemap must be a mapping"),
            )
            return EpwkSitemapInventory([], False, 0, 0)
        raw_urls = sitemap_config.get("urls", [])
        if not isinstance(raw_urls, list):
            self.failure(
                result,
                stage="publication_sitemap",
                exc=ValueError("EPWK publication_sitemap.urls must be a list"),
            )
            return EpwkSitemapInventory([], False, 0, 0)
        try:
            max_files = min(
                max(int(sitemap_config.get("max_files", 12)), 0),
                self.MAX_SITEMAP_FILES,
            )
            max_response_bytes = min(
                max(int(sitemap_config.get("max_response_bytes", 5_000_000)), 1),
                self.MAX_SITEMAP_RESPONSE_BYTES,
            )
            max_entries = min(
                max(
                    int(sitemap_config.get("max_entries_per_file", 30_000)),
                    1,
                ),
                self.MAX_SITEMAP_ENTRIES_PER_FILE,
            )
        except (TypeError, ValueError) as exc:
            self.failure(
                result,
                stage="publication_sitemap",
                exc=ValueError("EPWK publication sitemap limits must be integers"),
            )
            return EpwkSitemapInventory([], False, 0, 0)

        configured_urls = [
            str(value).strip()
            for value in raw_urls
            if str(value).strip()
        ]
        urls = configured_urls[:max_files]
        load_complete = bool(urls) and len(urls) == len(configured_urls)
        if len(configured_urls) > max_files:
            result.runtime_notes.append(
                f"EPWK publication sitemap file limit applied: {max_files}."
            )
        entries_by_id: dict[str, EpwkSitemapEntry] = {}
        loaded_files = 0
        invalid_entries = 0
        undated_task_entries = 0
        conflicting_entries = 0
        for url in urls:
            try:
                page = self.client.get(url)
                if len(page.content) > max_response_bytes:
                    raise ValueError(
                        "EPWK publication sitemap response exceeds configured limit"
                    )
                root = ElementTree.fromstring(page.content)
                if self._xml_local_name(root.tag) != "urlset":
                    raise ValueError("EPWK publication sitemap root is not urlset")
                file_entries: dict[str, EpwkSitemapEntry] = {}
                entry_count = 0
                for node in root:
                    if self._xml_local_name(node.tag) != "url":
                        continue
                    entry_count += 1
                    if entry_count > max_entries:
                        raise ValueError(
                            "EPWK publication sitemap entry limit exceeded"
                        )
                    loc: str | None = None
                    lastmod: str | None = None
                    for child in node:
                        name = self._xml_local_name(child.tag)
                        if name == "loc":
                            loc = clean_text(child.text)
                        elif name == "lastmod":
                            lastmod = clean_text(child.text)
                    task_key = self._task_key(loc)
                    published_at = self._sitemap_date(lastmod)
                    if (
                        task_key
                        and loc
                        and not published_at
                        and self._is_valid_non_20xx_sitemap_date(lastmod)
                    ):
                        undated_task_entries += 1
                        continue
                    if not task_key or not published_at or not loc:
                        invalid_entries += 1
                        load_complete = False
                        continue
                    candidate = EpwkSitemapEntry(
                        task_key=task_key,
                        detail_url=loc,
                        published_at=published_at,
                        sitemap_url=url,
                    )
                    existing = file_entries.get(task_key)
                    if existing is not None:
                        if existing.published_at != candidate.published_at:
                            conflicting_entries += 1
                            load_complete = False
                        continue
                    file_entries[task_key] = candidate
                for task_key, candidate in file_entries.items():
                    existing = entries_by_id.get(task_key)
                    if existing is not None:
                        if existing.published_at != candidate.published_at:
                            conflicting_entries += 1
                            load_complete = False
                        continue
                    entries_by_id[task_key] = candidate
                loaded_files += 1
            except (BlockedResponseError, RobotsDeniedError) as exc:
                load_complete = False
                self.failure(
                    result,
                    stage="publication_sitemap",
                    exc=exc,
                    url=url,
                )
                result.runtime_notes.append(
                    "EPWK publication sitemap queue stopped after a blocked response."
                )
                break
            except Exception as exc:
                load_complete = False
                self.failure(
                    result,
                    stage="publication_sitemap",
                    exc=exc,
                    url=url,
                )
        if loaded_files != len(urls):
            load_complete = False
        if invalid_entries:
            self.failure(
                result,
                stage="publication_sitemap",
                exc=ValueError(
                    "EPWK publication sitemap has "
                    f"{invalid_entries} invalid task entries"
                ),
            )
        if conflicting_entries:
            self.failure(
                result,
                stage="publication_sitemap",
                exc=ValueError(
                    "EPWK publication sitemap has "
                    f"{conflicting_entries} conflicting task dates"
                ),
            )
        result.runtime_notes.append(
            "EPWK publication sitemap loaded "
            f"files={loaded_files}/{len(configured_urls)}, "
            f"dated_tasks={len(entries_by_id)}, "
            f"undated_task_entries={undated_task_entries}, "
            f"complete={load_complete}."
        )
        return EpwkSitemapInventory(
            entries=list(entries_by_id.values()),
            complete=load_complete,
            configured_files=len(configured_urls),
            loaded_files=loaded_files,
            undated_task_entries=undated_task_entries,
        )

    def _sitemap_validation_sample_size(
        self, result: SiteResult
    ) -> int | None:
        sitemap_config = self.site_config.get('publication_sitemap')
        if not isinstance(sitemap_config, dict):
            return None
        value = sitemap_config.get('validation_sample_size', 10)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= self.MAX_SITEMAP_VALIDATION_SAMPLES
        ):
            self.failure(
                result,
                stage='publication_sitemap_validation',
                exc=ValueError(
                    'EPWK sitemap validation_sample_size must be an integer '
                    f'between 1 and {self.MAX_SITEMAP_VALIDATION_SAMPLES}'
                ),
            )
            return None
        return value

    def _render_publication_date(
        self,
        result: SiteResult,
        record: CrawlRecord,
    ) -> None:
        if (
            self.publication_filter is None
            or record.metadata.get("publication_date_source") != "unavailable"
            or self._publication_renderer_disabled
            or not record.detail_url
        ):
            return
        try:
            if self._publication_renderer is None:
                self._publication_renderer = EpwkPublicationRenderer(
                    self.config,
                    self.client,
                    self.site_config,
                )
            rendered_html = self._publication_renderer.render(
                record.detail_url,
                referer=record.list_url,
            )
            self._populate_detail(
                result,
                record,
                detail_soup=BeautifulSoup(rendered_html, "html.parser"),
            )
            if record.metadata.get("publication_date_source") != "unavailable":
                record.metadata["publication_browser_rendered"] = True
        except EpwkBrowserLimitReached as exc:
            self._publication_renderer_disabled = True
            if not self._publication_renderer_limit_noted:
                self.failure(
                    result,
                    stage="detail_browser_limit",
                    exc=exc,
                    url=record.detail_url,
                )
                self._publication_renderer_limit_noted = True
        except Exception as exc:
            self._publication_renderer_disabled = True
            self.failure(
                result,
                stage="detail_browser",
                exc=exc,
                url=record.detail_url,
            )

    def _epwk_quality_config(self) -> dict[str, Any]:
        value = self.site_config.get("quality", {})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _clean_epwk_description_text(source: str | None) -> str | None:
        if not source:
            return None
        copy = BeautifulSoup(source, "html.parser")
        platform_text = re.compile(
            r"登录查看(?:完整|详细)需求(?:描述)?|登录后查看完整需求(?:描述)?|"
            r"返回顶部|收藏任务|举报任务"
        )
        navigation_text = re.compile(r"^首页\s*[>›/]\s*任务$")
        for node in copy.select(
            "script,style,noscript,iframe, .task_user_info, .task-user-info, "
            ".task-progress-item, .task-user-info-action, .cct-bread-crumb, "
            ".catList, .nummoney, .span_state, .status, .price, h1, .title, "
            ".tips, .nodesc"
        ):
            node.decompose()
        lines: list[str] = []
        for line in copy.get_text("\n", strip=True).splitlines():
            value = clean_text(line)
            if not value or navigation_text.fullmatch(value):
                continue
            value = clean_text(platform_text.sub(" ", value))
            if value:
                lines.append(value)
        return clean_text(" ".join(lines))

    @staticmethod
    def _clean_epwk_description(soup: BeautifulSoup) -> str | None:
        candidates: list[Tag] = []
        for selector in (
            ".task-detail-content", ".task-detail", ".task-description",
            ".task_content", ".contxt .rctr .content", ".contxt .rctr",
        ):
            candidates.extend(soup.select(selector))
        if not candidates:
            node = soup.select_one(".contxt")
            if node:
                candidates.append(node)
        best: str | None = None
        for source in candidates:
            text = EpwkAdapter._clean_epwk_description_text(str(source))
            if text and (best is None or len(text) > len(best)):
                best = text
        return best

    @staticmethod
    def _nuxt_task_data(soup: BeautifulSoup) -> dict[str, Any]:
        """Extract bounded taskData fields from Nuxt SSR without executing JS."""
        script_texts = [node.get_text() for node in soup.select("script")]
        scripts = "\n".join(script_texts)
        if "__NUXT__" not in scripts or "taskData" not in scripts:
            return {}
        full_scripts = next(
            (text for text in script_texts if "window.__NUXT__" in text and "taskData" in text),
            scripts,
        )
        task_match = re.search(r"taskData\s*:\s*\{", scripts)
        if not task_match:
            return {}
        start = task_match.end() - 1
        depth = 0
        quote: str | None = None
        escaped = False
        end = len(scripts)
        for index in range(start, len(scripts)):
            char = scripts[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        scripts = scripts[start:end]

        def decode_string(raw: str) -> str:
            try:
                return json.loads('"' + raw + '"')
            except json.JSONDecodeError:
                return raw

        def split_js_args(source: str) -> list[str]:
            """Split a Nuxt call's arguments without evaluating JavaScript."""
            values: list[str] = []
            start = 0
            depth = 0
            quote: str | None = None
            escaped = False
            for index, char in enumerate(source):
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = None
                    continue
                if char in "\"'":
                    quote = char
                elif char in "([{":
                    depth += 1
                elif char in ")]}":
                    depth = max(0, depth - 1)
                elif char == "," and depth == 0:
                    values.append(source[start:index].strip())
                    start = index + 1
            tail = source[start:].strip()
            if tail or source.strip():
                values.append(tail)
            return values

        def parse_js_literal(raw: str) -> str | int | float | bool | None:
            raw = raw.strip()
            if raw in {"null", "undefined", "void 0"}:
                return None
            if raw == "true":
                return True
            if raw == "false":
                return False
            number = re.fullmatch(r"-?\d+(?:\.\d+)?", raw)
            if number:
                return float(raw) if "." in raw else int(raw)
            if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
                return decode_string(raw[1:-1])
            return None

        def nuxt_alias_values(source: str) -> dict[str, str | int | float | bool | None]:
            """Resolve only positional Nuxt function arguments that are literals."""
            function = re.search(r"window\.__NUXT__\s*=\s*\(function\(", source)
            if not function:
                return {}
            params_start = function.end()
            params_end = source.find(")", params_start)
            if params_end < 0:
                return {}
            params = [item.strip() for item in source[params_start:params_end].split(",") if item.strip()]
            # Nuxt emits the invocation at the end of the script. Greedy matching
            # is bounded to this script and the final call, never executed.
            invocation = re.search(r"\}\)\((?P<args>.*)\)\s*;?\s*$", source, re.DOTALL)
            if not invocation:
                return {}
            args = split_js_args(invocation.group("args"))
            return {
                name: parse_js_literal(args[index]) if index < len(args) else None
                for index, name in enumerate(params)
                if re.fullmatch(r"[A-Za-z_$][\w$]*", name)
            }

        aliases = nuxt_alias_values(full_scripts)

        def value_for(key: str, source: str | None = None) -> str | int | float | None:
            source = scripts if source is None else source
            match = re.search(rf"(?:^|[{{,])\s*{re.escape(key)}\s*:", source)
            if not match:
                return None
            pos = match.end()
            while pos < len(source) and source[pos].isspace():
                pos += 1
            if pos >= len(source):
                return None
            if source[pos] in "\"'":
                quote = source[pos]
                start = pos + 1
                pos = start
                escaped = False
                chars: list[str] = []
                while pos < len(source):
                    char = source[pos]
                    if escaped:
                        chars.append("\\" + char)
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        raw = "".join(chars)
                        return decode_string(raw)
                    else:
                        chars.append(char)
                    pos += 1
                return None
            number = re.match(r"-?\d+(?:\.\d+)?", source[pos:])
            if number:
                raw = number.group(0)
                return float(raw) if "." in raw else int(raw)
            alias = re.match(r"[A-Za-z_$][\w$]*", source[pos:])
            if alias:
                return aliases.get(alias.group(0))
            # Complex expressions remain unavailable by design.
            return None

        def balanced_value(key: str, opening: str, closing: str) -> str | None:
            match = re.search(
                rf"(?:^|[{{,])\s*{re.escape(key)}\s*:\s*{re.escape(opening)}",
                scripts,
            )
            if not match:
                return None
            start = match.end() - 1
            depth = 0
            quote: str | None = None
            escaped = False
            # taskData is already bounded; this additional cap prevents a
            # malformed page from turning nested extraction into an unbounded scan.
            scan_end = min(len(scripts), start + 200_000)
            for index in range(start, scan_end):
                char = scripts[index]
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = None
                    continue
                if char in "\"'":
                    quote = char
                elif char == opening:
                    depth += 1
                elif char == closing:
                    depth -= 1
                    if depth == 0:
                        return scripts[start:index + 1]
            return None

        def object_items(array_source: str) -> list[str]:
            items: list[str] = []
            index = 1
            while index < len(array_source) - 1 and len(items) < 100:
                if array_source[index] != "{":
                    index += 1
                    continue
                start = index
                depth = 0
                quote: str | None = None
                escaped = False
                while index < len(array_source):
                    char = array_source[index]
                    if quote:
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == quote:
                            quote = None
                    elif char in "\"'":
                        quote = char
                    elif char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            items.append(array_source[start:index + 1])
                            break
                    index += 1
                index += 1
            return items

        keys = (
            "task_id", "task_title", "task_desc", "model_name", "cashStr",
            "signup_num", "publish_time", "create_time", "start_time",
            "end_time", "username", "province", "city", "bounty_dist_desc",
        )
        data = {key: value_for(key) for key in keys}
        # process_desc and category names are stable SSR keys; each is read only
        # from the bounded script text and remains None when not exposed.
        for key in ("desc", "extDesc"):
            data[key] = value_for(key)
        for key in ("indusG", "indusP", "indusC"):
            category_object = balanced_value(key, "{", "}")
            parsed_name = value_for("indus_name", category_object) if category_object else None
            data[key] = parsed_name if isinstance(parsed_name, str) else None
        ext_descriptions: list[str] = []
        ext_array = balanced_value("ext_desc", "[", "]")
        if ext_array:
            for item in object_items(ext_array):
                value = value_for("ext_desc", item)
                cleaned = clean_text(str(value)) if value is not None else None
                if cleaned and cleaned not in ext_descriptions:
                    ext_descriptions.append(cleaned)
        data["ext_descriptions"] = ext_descriptions

        winner_names: list[str] = []
        winner_count = 0
        bid_user = balanced_value("bid_user", "{", "}")
        if bid_user:
            bid_match = re.search(r"(?:^|[{{,])\s*bid\s*:\s*\[", bid_user)
            if bid_match:
                bid_start = bid_match.end() - 1
                # Reuse the same string-aware scanner on the bounded bid_user object.
                depth = 0
                quote = None
                escaped = False
                bid_array = None
                for index in range(bid_start, len(bid_user)):
                    char = bid_user[index]
                    if quote:
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == quote:
                            quote = None
                        continue
                    if char in "\"'":
                        quote = char
                    elif char == "[":
                        depth += 1
                    elif char == "]":
                        depth -= 1
                        if depth == 0:
                            bid_array = bid_user[bid_start:index + 1]
                            break
                if bid_array:
                    for item in object_items(bid_array):
                        status = clean_text(str(value_for("work_status_name", item) or ""))
                        if status != "中标":
                            continue
                        winner_count += 1
                        name = value_for("shop_name", item) or value_for("username", item)
                        cleaned = clean_text(str(name)) if name is not None else None
                        if cleaned and cleaned not in winner_names:
                            winner_names.append(cleaned)
        data["winner_names"] = winner_names
        data["winner_count"] = winner_count or None
        return data

    def _apply_epwk_detail_fields(
        self, record: CrawlRecord, soup: BeautifulSoup
    ) -> None:
        nuxt = self._nuxt_task_data(soup)
        task_id = self._task_key(record.detail_url)
        if nuxt.get("task_id") is not None:
            record.metadata["task_id"] = str(nuxt["task_id"])
        elif task_id:
            record.metadata["task_id"] = task_id
        # Nuxt's title is a detail-page observation.  Keep the list title as
        # the canonical record title, but retain the richer detail value when
        # the page exposes one.
        if nuxt.get("task_title"):
            record.detail_title_raw = clean_text(str(nuxt["task_title"]))
        description = self._clean_epwk_description_text(str(nuxt["task_desc"])) if nuxt.get("task_desc") else self._clean_epwk_description(soup)
        supplements: list[str] = []
        for raw_supplement in nuxt.get("ext_descriptions", []) or []:
            supplement = self._clean_epwk_description_text(str(raw_supplement))
            if (
                supplement
                and supplement not in supplements
                and (not description or supplement not in description)
            ):
                supplements.append(supplement)
        if supplements:
            description = clean_text(" ".join([description or "", *supplements]))
        record.metadata["description_supplements"] = supplements
        if description in {"登录查看详细需求描述", "登录查看完整需求描述"}:
            description = None
        if description:
            record.description_raw = description
            record.metadata["description_length"] = len(description)
        elif record.description_raw:
            record.metadata["description_length"] = len(record.description_raw)
        else:
            record.metadata["description_length"] = 0

        page_text = clean_text(soup.get_text(" ", strip=True)) or ""
        trade_modes = (
            "直接雇佣", "多人悬赏", "单人悬赏", "招标", "雇佣", "计件"
        )
        for mode in trade_modes:
            if mode in page_text:
                record.job_type_raw = mode
                break
        if nuxt.get("model_name"):
            record.job_type_raw = clean_text(str(nuxt["model_name"]))
        statuses = (
            "圆满完成", "已到期关闭", "待验收", "工作中", "选标中", "投标中",
            "投稿中", "选稿中", "公示中",
        )
        # The status badge is authoritative.  Restricting this lookup avoids
        # treating workflow help text elsewhere on the page as the task state.
        status_badge = text_of(soup.select_one(".rbottom .dead"))
        if status_badge:
            record.status_raw = next(
                (status for status in statuses if status in status_badge),
                status_badge,
            )
        else:
            for status in statuses:
                if status in page_text:
                    record.status_raw = status
                    break
        for status_key in ("desc", "extDesc"):
            status_value = clean_text(str(nuxt.get(status_key) or ""))
            if status_value and any(item in status_value for item in statuses):
                record.status_raw = next(item for item in statuses if item in status_value)
                break

        if nuxt.get("cashStr"):
            record.budget_raw = clean_text(str(nuxt["cashStr"]))
            add_price(
                record,
                record.budget_raw,
                source="detail_summary_nuxt",
                url=record.detail_url or "",
                location="taskData.cashStr",
            )
        if nuxt.get("signup_num") is not None:
            record.metadata["bid_count_raw"] = str(nuxt["signup_num"])
        if nuxt.get("username"):
            record.publisher_name_raw = clean_text(str(nuxt["username"]))
        if nuxt.get("province") or nuxt.get("city"):
            record.location_raw = clean_text(" ".join(str(nuxt.get(k)) for k in ("province", "city") if nuxt.get(k)))
        if nuxt.get("end_time"):
            record.metadata["deadline_raw"] = clean_text(str(nuxt["end_time"]))
        for nuxt_key, metadata_key in (
            ("create_time", "create_time_raw"),
            ("start_time", "start_time_raw"),
            ("bounty_dist_desc", "budget_distribution_raw"),
        ):
            value = clean_text(str(nuxt.get(nuxt_key))) if nuxt.get(nuxt_key) is not None else None
            if value:
                record.metadata[metadata_key] = value
        # A Nuxt publication value is accepted only when it is an explicit
        # date, and never replaces a stronger DOM progress/header observation.
        nuxt_publish = clean_text(str(nuxt.get("publish_time"))) if nuxt.get("publish_time") is not None else None
        if (
            nuxt_publish
            and re.fullmatch(r"20\d{2}([./-])\d{1,2}\1\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", nuxt_publish)
            and not record.published_at_raw
        ):
            record.published_at_raw = nuxt_publish
            record.metadata["publication_date_source"] = "detail_nuxt"
        category_values = []
        for key in ("indusG", "indusP", "indusC"):
            raw_value = nuxt.get(key)
            value = clean_text(str(raw_value)) if raw_value is not None else None
            # Missing SSR category fields must remain null; never persist the
            # string representation of Python None as source data.
            if value in {"None", "null", "undefined"}:
                value = None
            category_values.append(value)
        if any(category_values):
            record.category_level_1_raw, record.category_level_2_raw, record.category_level_3_raw = category_values
            record.category_path_raw = " / ".join(value for value in category_values if value)
            record.category_source = "epwk_nuxt_taskData"

        patterns = {
            "deadline_raw": r"(?:投标截止|截止时间|截稿时间|交稿时间)\s*[：:]?\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}(?:\s*\d{1,2}:\d{2})?)",
            "bid_count_raw": r"(?:投标人数|参与人数|稿件数|投稿数)\s*[：:]?\s*([0-9]+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, page_text)
            if match:
                record.metadata[key] = clean_text(match.group(1))

        # These values are only retained when the page exposes an explicit
        # label or matching element; no status/winner is inferred from a date.
        publisher = (
            text_of(soup.select_one(
                ".task-user-info .username, .task_user_info .username, "
                ".task-user-info .user-name, .task_user_info .user-name, "
                ".publisher-name, [class*=publisher]"
            ))
        )
        if publisher:
            record.publisher_name_raw = publisher
        location = text_of(soup.select_one(
            ".task-user-info .location, .task_user_info .location, "
            ".publisher-location, [class*=location]"
        ))
        if location:
            record.location_raw = location

        winner_names = list(nuxt.get("winner_names", []) or [])
        for item in soup.select(".statcnt .finished .zhongbiao .item"):
            winner_mark = text_of(item.select_one(".leff .zb"))
            value = text_of(item.select_one(".rightt h4"))
            if winner_mark == "中标" and value and value not in winner_names:
                winner_names.append(value)
        nuxt_winner_count = nuxt.get("winner_count")
        winner_count = max(len(winner_names), int(nuxt_winner_count or 0))
        record.metadata["winner_names"] = winner_names or None
        record.metadata["winner_count"] = winner_count or None
        record.metadata["has_winner"] = winner_count > 0
        record.metadata["is_completed"] = record.status_raw == "圆满完成"

        if record.description_raw:
            mentions = extract_price_strings(record.description_raw)
            for match in EPWK_DESCRIPTION_PRICE_RE.finditer(record.description_raw):
                value = clean_text(match.group(0))
                if value and value not in mentions:
                    mentions.append(value)
            record.metadata["description_price_mentions"] = mentions
            for mention in mentions:
                add_price(
                    record, mention, source="description_text", url=record.detail_url or "",
                    location="description_raw",
                )
        else:
            record.metadata["description_price_mentions"] = []

        try:
            category = parse_epwk_detail_category(
                soup, detail_url=record.detail_url or "", expected_titles=(record.title_raw, record.detail_title_raw or record.title_raw)
            )
            for key, value in category.record_fields().items():
                setattr(record, key, value)
        except (CategoryParseError, ValueError):
            pass
        cfg = self._epwk_quality_config()
        main_modes = set(str(x) for x in cfg.get("main_trade_modes", ["招标", "雇佣"]))
        secondary_modes = set(str(x) for x in cfg.get("secondary_trade_modes", ["单人悬赏"]))
        minimum = int(cfg.get("description_min_length", 80))
        gray_min = int(cfg.get("description_gray_min_length", 40))
        high_min = int(cfg.get("high_information_length", 150))
        length = int(record.metadata.get("description_length", 0))
        reasons: list[str] = []
        if record.job_type_raw not in main_modes:
            reasons.append("trade_mode_not_main")
        # Current primary sample scope is intentionally simple: the outer
        # collection run enforces the recent-three-month publication window,
        # while this detail layer only requires the observed trade mode to be
        # 招标 or 雇佣. Description, price, completion and winner fields are
        # enrichments for later analysis and must not remove an otherwise valid
        # recent 招标/雇佣 task from the main sample.
        tier = "high_information" if length >= high_min else "standard" if length >= minimum else "gray" if length >= gray_min else "low_information"
        record.metadata.update({
            "hard_filter_pass": not reasons,
            "main_sample": not reasons and record.job_type_raw in main_modes,
            "needs_ai_review": False,
            "exclude_reasons": reasons,
            "quality_tier": tier,
            "trade_mode_candidate": record.job_type_raw in secondary_modes,
        })

    def _populate_detail(
        self,
        result: SiteResult,
        record: CrawlRecord,
        *,
        detail_soup: BeautifulSoup | None = None,
    ) -> None:
        if not record.detail_url:
            return
        record.metadata.setdefault("publication_date_source", "unavailable")
        if detail_soup is None:
            page = self.client.get(record.detail_url, referer=record.list_url)
            soup = page.soup()
            record.detail_title_raw = text_of(soup.select_one(".contxt .rctr .title"))
        else:
            soup = detail_soup
        progress_date: str | None = None
        for step in soup.select(
            ".task-progress-item span.step_on, "
            ".task-progress-item span.step_off"
        ):
            if text_of(step.select_one("b")) == "发布需求":
                progress_date = text_of(step.select_one("font"))
                if not progress_date:
                    continue
                record.published_at_raw = progress_date
                record.metadata["publication_date_source"] = "detail_progress"
                break

        action = soup.select_one(".task-user-info-action")
        task_key = self._task_key(record.detail_url)
        header_date = text_of(
            soup.select_one(
                ".task-user-info-action > span:first-child > .dib_vm"
            )
        )
        header_matches_task = bool(
            action
            and task_key
            and any(
                text_of(node) == f"任务编号：{task_key}"
                for node in action.select(".dib_vm")
            )
        )
        header_date_is_exact = bool(
            header_date
            and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", header_date)
            and self._sitemap_date(header_date) is not None
        )
        if header_matches_task and header_date_is_exact:
            assert header_date is not None
            if progress_date and progress_date.replace(".", "-") != header_date:
                record.published_at_raw = None
                record.metadata["publication_date_source"] = "detail_conflict"
                record.metadata["publication_date_candidates"] = {
                    "detail_progress": progress_date,
                    "detail_header": header_date,
                }
            elif not progress_date:
                record.published_at_raw = header_date
                record.metadata["publication_date_source"] = "detail_header"
        if detail_soup is None:
            self._render_publication_date(result, record)
        price_location = ".task_user_info .nummoney > span"
        price_node = soup.select_one(price_location)
        if price_node is None:
            price_location = ".contxt .catList .money"
            price_node = soup.select_one(price_location)
        detail_price = text_of(price_node)
        if detail_price and not record.budget_raw:
            record.budget_raw = detail_price
        add_price(
            record,
            detail_price,
            source="detail_summary",
            url=record.detail_url,
            location=price_location,
        )
        self._apply_epwk_detail_fields(record, soup)
        result.details_fetched += 1

    def _sitemap_detail_record(
        self,
        result: SiteResult,
        entry: EpwkSitemapEntry,
        *,
        position: int,
    ) -> CrawlRecord:
        start_url = str(self.site_config["start_url"])
        page = self.client.get(entry.detail_url, referer=start_url)
        soup = page.soup()
        title = text_of(soup.select_one("div.task_user_info h1"))
        title_selector = "div.task_user_info h1"
        if not title:
            breadcrumbs = soup.select(".cct-bread-crumb a")
            title = text_of(breadcrumbs[-1]) if breadcrumbs else None
            title_selector = ".cct-bread-crumb a:last"
        if not title:
            title = text_of(soup.select_one(".contxt .rctr .title"))
            title_selector = ".contxt .rctr .title"
        if not title:
            raise ValueError(
                f"EPWK sitemap detail title missing for task {entry.task_key}"
            )
        record = CrawlRecord(
            site_name=self.site_name,
            content_category="outsourcing_task",
            title_raw=title,
            detail_title_raw=title,
            list_url=start_url,
            detail_url=entry.detail_url,
            page_no=0,
            list_position=position,
            metadata={
                "publication_sitemap_primary": True,
                "publication_sitemap_url": entry.sitemap_url,
                "publication_sitemap_lastmod": entry.published_at,
                "publication_sitemap_task_key": entry.task_key,
                "detail_title_selector": title_selector,
            },
        )
        self._populate_detail(result, record, detail_soup=soup)
        self._render_publication_date(result, record)
        if not record.price_evidence:
            raise ValueError(
                f"EPWK sitemap detail price missing for task {entry.task_key}"
            )
        return record

    @staticmethod
    def _normalized_detail_publication_date(record: CrawlRecord) -> str | None:
        return EpwkAdapter._sitemap_date(
            (record.published_at_raw or "").replace(".", "-")
        )

    def _finish_primary_sitemap(
        self,
        result: SiteResult,
        coverage: dict[str, Any],
        *,
        stop_reason: str,
        source_complete: bool,
    ) -> SiteResult:
        coverage["complete"] = source_complete
        coverage["stop_reason"] = (
            "source_exhausted" if source_complete else stop_reason
        )
        result.window_complete = False
        result.stop_reason = stop_reason
        result.runtime_notes.append(
            "EPWK sitemap is a bounded source inventory, not a complete "
            "site-wide task inventory; global publication window remains incomplete."
        )
        result.finish()
        self.checkpoint(result, "finished")
        return result

    def _crawl_primary_sitemap(
        self,
        result: SiteResult,
        inventory: EpwkSitemapInventory,
        *,
        numeric_price_only: bool,
        detail_cap: int,
        window_item_cap: int,
    ) -> SiteResult:
        sitemap_config = self.site_config.get("publication_sitemap")
        assert isinstance(sitemap_config, dict)
        coverage: dict[str, Any] = {
            "complete": False,
            "stop_reason": None,
            "configured_files": inventory.configured_files,
            "loaded_files": inventory.loaded_files,
            "dated_tasks": len(inventory.entries),
            "undated_task_entries": inventory.undated_task_entries,
            "candidate_scope": "strict_window",
            "candidate_lookback_days": 0,
            "candidate_cutoff": None,
            "global_strict_window_candidates": 0,
            "global_boundary_candidates": 0,
            "global_window_candidates": 0,
            "partition_count": 1,
            "partition_index": 0,
            "window_candidates": 0,
            "validated_samples": 0,
            "processed_candidates": 0,
            "failed_candidates": 0,
            "date_mismatch_candidates": 0,
        }
        result.source_coverage["sitemap"] = coverage
        raw_partition_count = sitemap_config.get("partition_count", 1)
        raw_partition_index = sitemap_config.get("partition_index", 0)
        partition_valid = bool(
            not isinstance(raw_partition_count, bool)
            and isinstance(raw_partition_count, int)
            and 1 <= raw_partition_count <= 3
            and not isinstance(raw_partition_index, bool)
            and isinstance(raw_partition_index, int)
            and 0 <= raw_partition_index < raw_partition_count
        )
        if not partition_valid:
            self.failure(
                result,
                stage="publication_sitemap",
                exc=ValueError(
                    "EPWK sitemap partition_count must be an integer from 1 to 3 "
                    "and partition_index must be an integer from 0 to count - 1"
                ),
            )
            return self._finish_primary_sitemap(
                result,
                coverage,
                stop_reason="sitemap_config_invalid",
                source_complete=False,
            )
        partition_count = raw_partition_count
        partition_index = raw_partition_index
        coverage["partition_count"] = partition_count
        coverage["partition_index"] = partition_index
        if not inventory.complete:
            return self._finish_primary_sitemap(
                result,
                coverage,
                stop_reason="sitemap_load_incomplete",
                source_complete=False,
            )

        validation_required = self._sitemap_validation_sample_size(result)
        if validation_required is None:
            return self._finish_primary_sitemap(
                result,
                coverage,
                stop_reason="sitemap_validation_failed",
                source_complete=False,
            )
        try:
            checkpoint_interval = int(
                sitemap_config.get("checkpoint_interval", 50)
            )
        except (TypeError, ValueError):
            checkpoint_interval = 0
        if not 1 <= checkpoint_interval <= self.MAX_SITEMAP_CHECKPOINT_INTERVAL:
            self.failure(
                result,
                stage="publication_sitemap",
                exc=ValueError(
                    "EPWK sitemap checkpoint_interval must be an integer "
                    f"between 1 and {self.MAX_SITEMAP_CHECKPOINT_INTERVAL}"
                ),
            )
            return self._finish_primary_sitemap(
                result,
                coverage,
                stop_reason="sitemap_config_invalid",
                source_complete=False,
            )

        assert self.publication_filter is not None
        raw_lookback_days = sitemap_config.get("candidate_lookback_days", 0)
        raw_boundary_only = sitemap_config.get("boundary_only", False)
        lookback_config_valid = bool(
            not isinstance(raw_lookback_days, bool)
            and isinstance(raw_lookback_days, int)
            and 0 <= raw_lookback_days <= self.MAX_SITEMAP_CANDIDATE_LOOKBACK_DAYS
            and isinstance(raw_boundary_only, bool)
            and (not raw_boundary_only or raw_lookback_days > 0)
        )
        if not lookback_config_valid:
            self.failure(
                result,
                stage="publication_sitemap",
                exc=ValueError(
                    "EPWK sitemap candidate_lookback_days must be an integer "
                    f"between 0 and {self.MAX_SITEMAP_CANDIDATE_LOOKBACK_DAYS}, "
                    "and boundary_only=true requires a positive lookback"
                ),
            )
            return self._finish_primary_sitemap(
                result,
                coverage,
                stop_reason="sitemap_config_invalid",
                source_complete=False,
            )

        lookback_days = raw_lookback_days
        boundary_only = raw_boundary_only
        candidate_cutoff = self.publication_filter.cutoff - timedelta(
            days=lookback_days
        )
        strict_window_entries = [
            entry
            for entry in inventory.entries
            if self.publication_filter.decide(entry.published_at).included
        ]
        boundary_entries = [
            entry
            for entry in inventory.entries
            if candidate_cutoff
            <= date.fromisoformat(entry.published_at)
            < self.publication_filter.cutoff
        ]
        global_window_entries = (
            boundary_entries
            if boundary_only
            else strict_window_entries + boundary_entries
        )
        coverage["candidate_scope"] = (
            "boundary_only"
            if boundary_only
            else "strict_window_and_boundary"
            if lookback_days
            else "strict_window"
        )
        coverage["candidate_lookback_days"] = lookback_days
        coverage["candidate_cutoff"] = candidate_cutoff.isoformat()
        coverage["global_strict_window_candidates"] = len(
            strict_window_entries
        )
        coverage["global_boundary_candidates"] = len(boundary_entries)
        coverage["global_window_candidates"] = len(global_window_entries)
        window_entries = [
            entry
            for position, entry in enumerate(global_window_entries)
            if position % partition_count == partition_index
        ]
        coverage["window_candidates"] = len(window_entries)
        global_window_keys = {
            entry.task_key for entry in global_window_entries
        }
        old_entries = [
            entry
            for entry in inventory.entries
            if entry.task_key not in global_window_keys
        ]
        partition_old_entries = [
            entry
            for position, entry in enumerate(old_entries)
            if position % partition_count == partition_index
        ]
        validation_pool = window_entries + [
            entry for entry in partition_old_entries
        ]
        if len(validation_pool) < validation_required:
            self.failure(
                result,
                stage="publication_sitemap_validation",
                exc=ValueError(
                    "EPWK sitemap partition validation requires "
                    f"{validation_required} unique tasks, "
                    f"found {len(validation_pool)}"
                ),
            )
            return self._finish_primary_sitemap(
                result,
                coverage,
                stop_reason="sitemap_validation_failed",
                source_complete=False,
            )
        detail_cache: dict[str, CrawlRecord] = {}
        for position, entry in enumerate(
            validation_pool[:validation_required],
            start=1,
        ):
            if result.details_fetched >= detail_cap:
                return self._finish_primary_sitemap(
                    result,
                    coverage,
                    stop_reason="detail_cap_reached",
                    source_complete=False,
                )
            try:
                record = self._sitemap_detail_record(
                    result,
                    entry,
                    position=position,
                )
            except Exception as exc:
                self.failure(
                    result,
                    stage="publication_sitemap_validation",
                    exc=exc,
                    url=entry.detail_url,
                )
                return self._finish_primary_sitemap(
                    result,
                    coverage,
                    stop_reason="sitemap_validation_failed",
                    source_complete=False,
                )
            detail_date = self._normalized_detail_publication_date(record)
            if detail_date is None:
                record.metadata["publication_sitemap_validation"] = {
                    "status": "invalid_detail_date",
                    "sitemap_date": entry.published_at,
                    "detail_date": detail_date,
                }
                self.failure(
                    result,
                    stage="publication_sitemap_validation",
                    exc=ValueError(
                        "EPWK detail publication date missing or invalid "
                        f"for task {entry.task_key}"
                    ),
                    url=entry.detail_url,
                )
                return self._finish_primary_sitemap(
                    result,
                    coverage,
                    stop_reason="sitemap_validation_failed",
                    source_complete=False,
                )
            if detail_date != entry.published_at:
                record.metadata["publication_sitemap_validation"] = {
                    "status": "mismatch_detail_authoritative",
                    "sitemap_date": entry.published_at,
                    "detail_date": detail_date,
                }
            else:
                record.metadata["publication_sitemap_validation"] = {
                    "status": "sample_match",
                    "sitemap_date": entry.published_at,
                    "detail_date": detail_date,
                }
            detail_cache[entry.task_key] = record
            coverage["validated_samples"] = position

        result.runtime_notes.append(
            "EPWK sitemap primary validation passed: "
            f"{validation_required}/{validation_required} parseable detail dates."
        )
        self.checkpoint(result, "publication_sitemap_validation")
        if window_entries and window_item_cap == 0:
            return self._finish_primary_sitemap(
                result,
                coverage,
                stop_reason="item_cap_reached",
                source_complete=False,
            )

        failed_candidates = 0
        processed_candidates = 0
        for index, entry in enumerate(window_entries):
            record = detail_cache.get(entry.task_key)
            if record is None:
                if result.details_fetched >= detail_cap:
                    coverage["processed_candidates"] = processed_candidates
                    coverage["failed_candidates"] = failed_candidates
                    return self._finish_primary_sitemap(
                        result,
                        coverage,
                        stop_reason="detail_cap_reached",
                        source_complete=False,
                    )
                try:
                    record = self._sitemap_detail_record(
                        result,
                        entry,
                        position=index + 1,
                    )
                except Exception as exc:
                    failed_candidates += 1
                    processed_candidates += 1
                    self.failure(
                        result,
                        stage="publication_sitemap_detail",
                        exc=exc,
                        url=entry.detail_url,
                    )
                    coverage["processed_candidates"] = processed_candidates
                    coverage["failed_candidates"] = failed_candidates
                    if processed_candidates % checkpoint_interval == 0:
                        result.runtime_notes.append(
                            "EPWK sitemap primary progress: "
                            f"processed={processed_candidates}/"
                            f"{len(window_entries)}, "
                            f"accepted={len(result.records)}, "
                            f"failed={failed_candidates}."
                        )
                        self.checkpoint(result, "publication_sitemap_progress")
                    continue

            detail_date = self._normalized_detail_publication_date(record)
            if detail_date is None:
                failed_candidates += 1
                processed_candidates += 1
                record.metadata["publication_sitemap_validation"] = {
                    "status": "invalid_detail_date",
                    "sitemap_date": entry.published_at,
                    "detail_date": detail_date,
                }
                self.failure(
                    result,
                    stage="publication_sitemap_detail",
                    exc=ValueError(
                        "EPWK detail publication date missing or invalid "
                        f"for task {entry.task_key}"
                    ),
                    url=entry.detail_url,
                )
            else:
                validation_evidence = record.metadata.get(
                    "publication_sitemap_validation"
                )
                if detail_date != entry.published_at:
                    coverage["date_mismatch_candidates"] += 1
                    record.metadata["publication_sitemap_validation"] = {
                        "status": "mismatch_detail_authoritative",
                        "sitemap_date": entry.published_at,
                        "detail_date": detail_date,
                        "validation_sample": bool(validation_evidence),
                    }
                else:
                    record.metadata["publication_sitemap_validation"] = {
                        "status": "verified_match",
                        "sitemap_date": entry.published_at,
                        "detail_date": detail_date,
                        "validation_sample": bool(validation_evidence),
                    }
                self.accept_record(
                    result,
                    record,
                    numeric_price_only=numeric_price_only,
                )
                processed_candidates += 1

            coverage["processed_candidates"] = processed_candidates
            coverage["failed_candidates"] = failed_candidates
            if processed_candidates % checkpoint_interval == 0:
                result.runtime_notes.append(
                    "EPWK sitemap primary progress: "
                    f"processed={processed_candidates}/{len(window_entries)}, "
                    f"accepted={len(result.records)}, "
                    f"failed={failed_candidates}."
                )
                self.checkpoint(result, "publication_sitemap_progress")
            if (
                len(result.records) >= window_item_cap
                and index + 1 < len(window_entries)
            ):
                return self._finish_primary_sitemap(
                    result,
                    coverage,
                    stop_reason="item_cap_reached",
                    source_complete=False,
                )

        result.runtime_notes.append(
            "EPWK sitemap primary progress: "
            f"processed={processed_candidates}/{len(window_entries)}, "
            f"accepted={len(result.records)}, failed={failed_candidates}."
        )
        if failed_candidates:
            return self._finish_primary_sitemap(
                result,
                coverage,
                stop_reason="sitemap_detail_incomplete",
                source_complete=False,
            )
        return self._finish_primary_sitemap(
            result,
            coverage,
            stop_reason="sitemap_source_exhausted_not_global",
            source_complete=True,
        )

    def _crawl(
        self,
        limit: int,
        *,
        with_details: bool = True,
        numeric_price_only: bool = False,
    ) -> SiteResult:
        result = self.result(limit)
        start_url = str(self.site_config["start_url"])
        template = str(self.site_config["page_url_template"])
        state_path = self.site_config.get("storage_state")
        if state_path:
            try:
                self.client.load_storage_state(
                    str(state_path),
                    allowed_domains={"epwk.com", "task.epwk.com"},
                )
            except Exception as exc:
                self.failure(result, stage="session_load", exc=exc)
        filter_by_publication = self.publication_filter is not None
        complete_window = filter_by_publication and self.complete_publication_window
        limits = self.config.section("limits")
        window_item_cap = int(limits.get("max_publication_records_per_site", 10000))
        max_pages = int(
            self.site_config.get("publication_max_pages", 400)
            if complete_window
            else limits.get("max_pages_per_site", 10)
        )
        detail_attempts = 0
        detail_cap = int(
            self.detail_limit_override
            if self.detail_limit_override is not None
            else (
                self.site_config.get(
                    "publication_max_detail_requests",
                    limits.get("max_publication_detail_requests_per_site", 5000),
                )
                if complete_window
                else limits.get("max_detail_requests_per_site", 10)
            )
        )
        detail_cap_reached = False
        item_cap_reached = False
        seen_candidate_ids: set[str] = set()
        publication_dates_by_id: dict[str, str | None] = {}
        publication_sources_by_id: dict[str, str] = {}
        publication_evidence_by_id: dict[str, dict[str, Any]] = {}
        sitemap_validation_state = 'disabled'
        sitemap_validation_required: int | None = None
        sitemap_validation_attempts = 0
        sitemap_validation_matches = 0
        numeric_rejected_candidate_ids: set[str] = set()
        source_parse_complete = True
        publication_unknown_seen = False
        old_known_page_streak = 0
        unknown_cutoff_pages = (
            max(
                int(self.site_config.get("publication_unknown_cutoff_pages", 50)),
                1,
            )
            if complete_window
            else 50
        )
        if filter_by_publication and not with_details:
            raise ValueError(
                "EPWK publication filtering requires detail fallback to be enabled"
            )
        sitemap_inventory = (
            self._load_publication_sitemap_inventory(result)
            if complete_window
            else EpwkSitemapInventory([], False, 0, 0)
        )
        publication_sitemap_dates = sitemap_inventory.dates_by_id
        sitemap_config = self.site_config.get("publication_sitemap")
        if (
            complete_window
            and isinstance(sitemap_config, dict)
            and sitemap_config.get("primary_source") is True
        ):
            return self._crawl_primary_sitemap(
                result,
                sitemap_inventory,
                numeric_price_only=numeric_price_only,
                detail_cap=max(detail_cap, 0),
                window_item_cap=max(window_item_cap, 0),
            )
        if publication_sitemap_dates:
            sitemap_validation_required = (
                self._sitemap_validation_sample_size(result)
            )
            if sitemap_validation_required is not None:
                sitemap_validation_state = 'pending'
                result.runtime_notes.append(
                    'EPWK publication sitemap validation started: '
                    f'required={sitemap_validation_required}, detail_cap={detail_cap}.'
                )
        try:
            for page_no in range(1, max_pages + 1):
                url = start_url if page_no == 1 else template.format(page=page_no)
                page = self.client.get(url, referer=start_url if page_no > 1 else None)
                result.pages_fetched += 1
                soup = page.soup()
                cards = soup.select(".content-lists .itemblock")
                if not cards:
                    result.runtime_notes.append(f"page {page_no}: no task cards")
                    if complete_window:
                        result.window_complete = (
                            source_parse_complete and not publication_unknown_seen
                        )
                        if not source_parse_complete:
                            result.stop_reason = "parse_incomplete"
                        elif publication_unknown_seen:
                            result.stop_reason = "publication_unavailable"
                        else:
                            result.stop_reason = "source_exhausted"
                    break
                page_publication_records: list[CrawlRecord] = []
                page_parse_complete = True
                page_fully_processed = True
                for position, card in enumerate(cards, start=1):
                    link = card.select_one(".title a[href]")
                    title = text_of(link)
                    detail_url = absolute(page.final_url, link.get("href") if link else None)
                    if not title or not detail_url:
                        page_parse_complete = False
                        source_parse_complete = False
                        continue
                    record = CrawlRecord(
                        site_name=self.site_name,
                        content_category="outsourcing_task",
                        title_raw=title,
                        list_url=url,
                        detail_url=detail_url,
                        page_no=page_no,
                        list_position=position,
                        status_raw=text_of(card.select_one(".right .status")),
                    )
                    add_price(
                        record,
                        text_of(card.select_one(".right .price")),
                        source="list_page",
                        url=url,
                        location=".right .price",
                    )
                    task_key = self._task_key(record.detail_url)
                    candidate_id = (
                        f"task:{task_key}" if task_key else record.record_id
                    )
                    if filter_by_publication:
                        if candidate_id in seen_candidate_ids:
                            record.published_at_raw = publication_dates_by_id.get(
                                candidate_id
                            )
                            cached_source = publication_sources_by_id.get(candidate_id)
                            if cached_source:
                                record.metadata["publication_date_source"] = cached_source
                            cached_evidence = publication_evidence_by_id.get(
                                candidate_id
                            )
                            if cached_evidence:
                                record.metadata["publication_sitemap_validation"] = dict(
                                    cached_evidence
                                )
                            page_publication_records.append(record)
                            if (
                                self.publication_filter.decide(
                                    record.published_at_raw
                                ).status
                                == "unknown"
                            ):
                                publication_unknown_seen = True
                            continue
                        if not complete_window and self._numeric_price_rejected(
                            result,
                            record,
                            numeric_price_only=numeric_price_only,
                        ):
                            if candidate_id not in numeric_rejected_candidate_ids:
                                numeric_rejected_candidate_ids.add(candidate_id)
                                result.filter_counts["numeric_price"] = (
                                    result.filter_counts.get("numeric_price", 0) + 1
                                )
                                result.filtered_out_count += 1
                            continue
                        if candidate_id in numeric_rejected_candidate_ids:
                            numeric_rejected_candidate_ids.remove(candidate_id)
                            remaining = result.filter_counts.get("numeric_price", 0) - 1
                            if remaining > 0:
                                result.filter_counts["numeric_price"] = remaining
                            else:
                                result.filter_counts.pop("numeric_price", None)
                            result.filtered_out_count -= 1
                        seen_candidate_ids.add(candidate_id)
                        sitemap_date = (
                            publication_sitemap_dates.get(task_key)
                            if task_key is not None
                            else None
                        )
                        if (
                            sitemap_date is not None
                            and sitemap_validation_state == 'trusted'
                        ):
                            record.published_at_raw = sitemap_date
                            record.metadata["publication_date_source"] = (
                                "sitemap_lastmod"
                            )
                            record.metadata['publication_sitemap_validation'] = {
                                'status': 'trusted',
                                'required_matches': sitemap_validation_required,
                                'matched_samples': sitemap_validation_matches,
                            }
                        else:
                            if detail_attempts >= detail_cap:
                                detail_cap_reached = True
                                page_fully_processed = False
                                break
                            detail_attempts += 1
                            record.metadata["publication_date_source"] = "unavailable"
                            try:
                                self._populate_detail(result, record)
                            except Exception as exc:
                                self.failure(
                                    result,
                                    stage="detail",
                                    exc=exc,
                                    url=record.detail_url,
                                )
                            if sitemap_date is not None:
                                evidence: dict[str, Any] = {
                                    'status': (
                                        'disabled'
                                        if sitemap_validation_state == 'disabled'
                                        else 'sample_unavailable'
                                    ),
                                    'sitemap_lastmod': sitemap_date,
                                    'detail_progress': record.published_at_raw,
                                    'required_matches': sitemap_validation_required,
                                    'matched_samples': sitemap_validation_matches,
                                }
                                if sitemap_validation_state == 'pending':
                                    assert sitemap_validation_required is not None
                                    sitemap_validation_attempts += 1
                                    sitemap_decision = self.publication_filter.decide(
                                        sitemap_date
                                    )
                                    detail_decision = self.publication_filter.decide(
                                        record.published_at_raw
                                    )
                                    sitemap_normalized = (
                                        sitemap_decision.normalized_date
                                    )
                                    detail_normalized = detail_decision.normalized_date
                                    evidence['normalized_sitemap_date'] = (
                                        sitemap_normalized.isoformat()
                                        if sitemap_normalized
                                        else None
                                    )
                                    evidence['normalized_detail_date'] = (
                                        detail_normalized.isoformat()
                                        if detail_normalized
                                        else None
                                    )
                                    if (
                                        sitemap_normalized is not None
                                        and detail_normalized is not None
                                    ):
                                        if sitemap_normalized == detail_normalized:
                                            sitemap_validation_matches += 1
                                            evidence['status'] = 'sample_match'
                                            evidence['matched_samples'] = (
                                                sitemap_validation_matches
                                            )
                                            if (
                                                sitemap_validation_matches
                                                >= sitemap_validation_required
                                            ):
                                                sitemap_validation_state = 'trusted'
                                                result.runtime_notes.append(
                                                    'EPWK publication sitemap trusted '
                                                    'for this run: '
                                                    f'matched={sitemap_validation_matches}/'
                                                    f'{sitemap_validation_required}, '
                                                    f'attempts={sitemap_validation_attempts}.'
                                                )
                                        else:
                                            evidence['status'] = 'sample_mismatch'
                                            sitemap_validation_state = 'disabled'
                                            mismatch = ValueError(
                                                'EPWK sitemap/detail publication '
                                                f'date mismatch for task {task_key}: '
                                                f'{sitemap_normalized.isoformat()} != '
                                                f'{detail_normalized.isoformat()}'
                                            )
                                            self.failure(
                                                result,
                                                stage='publication_sitemap_validation',
                                                exc=mismatch,
                                                url=record.detail_url,
                                            )
                                            result.runtime_notes.append(
                                                'EPWK publication sitemap disabled '
                                                f'for this run: task={task_key}.'
                                            )
                                record.metadata[
                                    'publication_sitemap_validation'
                                ] = evidence
                        publication_dates_by_id[candidate_id] = (
                            record.published_at_raw
                        )
                        publication_sources_by_id[candidate_id] = str(
                            record.metadata["publication_date_source"]
                        )
                        validation_evidence = record.metadata.get(
                            'publication_sitemap_validation'
                        )
                        if isinstance(validation_evidence, dict):
                            publication_evidence_by_id[candidate_id] = dict(
                                validation_evidence
                            )
                        page_publication_records.append(record)
                        if (
                            self.publication_filter.decide(
                                record.published_at_raw
                            ).status
                            == "unknown"
                        ):
                            publication_unknown_seen = True
                    accepted = self.accept_record(
                        result,
                        record,
                        numeric_price_only=(
                            numeric_price_only
                            if not filter_by_publication or complete_window
                            else False
                        ),
                    )
                    if complete_window and accepted and len(result.records) >= window_item_cap:
                        item_cap_reached = True
                        page_fully_processed = False
                        break
                    if not complete_window and accepted and len(result.records) >= limit:
                        break
                    if (
                        filter_by_publication
                        and detail_attempts >= detail_cap
                        and not complete_window
                        and len(result.records) < limit
                    ):
                        detail_cap_reached = True
                        page_fully_processed = False
                        break
                self.checkpoint(result, "list_page")
                if item_cap_reached or detail_cap_reached:
                    break
                page_is_complete_and_sorted = bool(
                    complete_window
                    and page_parse_complete
                    and page_fully_processed
                    and self.site_config.get("publication_sorted_desc", False)
                )
                if page_is_complete_and_sorted:
                    if (
                        not publication_unknown_seen
                        and source_parse_complete
                        and self.page_is_before_publication_cutoff(
                            page_publication_records
                        )
                    ):
                        result.window_complete = True
                        result.stop_reason = "cutoff_reached"
                        break
                    decisions = [
                        self.publication_filter.decide(record.published_at_raw).status
                        for record in page_publication_records
                    ]
                    known_decisions = [
                        status for status in decisions if status != "unknown"
                    ]
                    if known_decisions and all(
                        status == "too_old" for status in known_decisions
                    ):
                        old_known_page_streak += 1
                    else:
                        old_known_page_streak = 0
                    if old_known_page_streak >= unknown_cutoff_pages:
                        result.window_complete = False
                        result.stop_reason = "cutoff_reached_with_unknowns"
                        break
                elif complete_window:
                    old_known_page_streak = 0
                if not complete_window and len(result.records) >= limit:
                    break
        except Exception as exc:
            self.failure(result, stage="list", exc=exc, url=start_url)
            if complete_window:
                result.stop_reason = "list_error"

        if sitemap_validation_state == 'pending':
            result.runtime_notes.append(
                'EPWK publication sitemap not trusted for this run: '
                f'matched={sitemap_validation_matches}/'
                f'{sitemap_validation_required}, '
                f'attempts={sitemap_validation_attempts}.'
            )
        if not filter_by_publication:
            configured_detail_limit = (
                self.detail_limit_override
                if self.detail_limit_override is not None
                else int(self.site_config.get("detail_sample_limit", 5))
            )
            detail_limit = min(
                len(result.records),
                configured_detail_limit if with_details else 0,
            )
            for record in result.records[:detail_limit]:
                try:
                    self._populate_detail(result, record)
                except Exception as exc:
                    self.failure(
                        result, stage="detail", exc=exc, url=record.detail_url
                    )
        elif detail_cap_reached:
            result.runtime_notes.append(
                f"EPWK publication detail request cap reached: {detail_cap}."
            )
        if complete_window and not result.window_complete:
            result.window_complete = False
            if item_cap_reached:
                result.stop_reason = "item_cap_reached"
            elif detail_cap_reached:
                result.stop_reason = "detail_cap_reached"
            elif result.stop_reason is None:
                result.stop_reason = "page_cap_reached"
            result.source_coverage["tasks"] = {
                "complete": False,
                "stop_reason": result.stop_reason,
            }
        elif complete_window:
            result.source_coverage["tasks"] = {
                "complete": True,
                "stop_reason": result.stop_reason,
            }
        result.runtime_notes.append(
            "Core title/budget is public; complete requirement body may require normal login."
        )
        result.finish()
        self.checkpoint(result, "finished")
        return result

    def crawl(
        self,
        limit: int,
        *,
        with_details: bool = True,
        numeric_price_only: bool = False,
    ) -> SiteResult:
        try:
            return self._crawl(
                limit,
                with_details=with_details,
                numeric_price_only=numeric_price_only,
            )
        finally:
            if self._publication_renderer is not None:
                self._publication_renderer.close()
                self._publication_renderer = None


class ShixianAdapter(BaseSiteAdapter):
    site_name = "shixian"
    difficulty = 1

    @staticmethod
    def _is_three_month_site_label(raw: str | None) -> bool:
        value = clean_text(raw)
        return bool(value and re.fullmatch(r"3\s*个月前(?:发布)?", value))

    @staticmethod
    def _is_empty_placeholder(card: Tag) -> bool:
        classes = card.get("class", [])
        return bool(
            "text-center" in classes
            and card.select_one('.info > a[href^="/jobs/"]') is None
            and "暂时还没有符合条件的" in (text_of(card) or "")
        )

    def accept_record(
        self,
        result: SiteResult,
        record: CrawlRecord,
        *,
        numeric_price_only: bool,
    ) -> bool:
        publication_filter = self.publication_filter
        if (
            publication_filter is not None
            and publication_filter.source == "recent_months"
            and publication_filter.recent_months == 3
            and self._is_three_month_site_label(record.published_at_raw)
        ):
            decision = publication_filter.decide(record.published_at_raw)
            if decision.status == "uncertain":
                if any(
                    existing.record_id == record.record_id
                    for existing in result.records
                ):
                    return False
                publication_metadata = decision.to_dict()
                publication_metadata["boundary_assumption"] = (
                    "site_three_month_label"
                )
                record.metadata["publication_time"] = publication_metadata
                if self._numeric_price_rejected(
                    result,
                    record,
                    numeric_price_only=numeric_price_only,
                ):
                    result.filter_counts["numeric_price"] = (
                        result.filter_counts.get("numeric_price", 0) + 1
                    )
                    result.filtered_out_count += 1
                    return False
                result.records.append(record)
                return True
        return super().accept_record(
            result,
            record,
            numeric_price_only=numeric_price_only,
        )

    def crawl(
        self,
        limit: int,
        *,
        with_details: bool = True,
        numeric_price_only: bool = False,
    ) -> SiteResult:
        result = self.result(limit)
        streams: list[dict[str, Any]] = list(self.site_config.get("streams", []))
        complete_window = (
            self.publication_filter is not None and self.complete_publication_window
        )
        limits = self.config.section("limits")
        window_item_cap = int(
            limits.get("max_publication_records_per_site", 10000)
        )
        quotas = [limit // len(streams)] * len(streams)
        for index in range(limit % max(1, len(streams))):
            quotas[index] += 1
        max_pages = int(self.config.section("limits").get("max_pages_per_site", 10))
        item_cap_reached = False
        unknown_publication_values: dict[str, int] = {}
        for stream_index, stream in enumerate(streams):
            stream_target = (
                limit - len(result.records)
                if stream_index == len(streams) - 1
                else quotas[stream_index]
            )
            if not complete_window and stream_target <= 0:
                break
            stream_count = 0
            stream_complete = False
            stream_reason: str | None = None
            stream_parse_complete = True
            stream_pages = int(
                stream.get("publication_max_pages", 100)
                if complete_window
                else max_pages
            )
            for page_no in range(1, stream_pages + 1):
                if not complete_window and stream_count >= stream_target:
                    break
                url = str(stream["page_url_template"]).format(page=page_no)
                try:
                    page = self.client.get(url)
                except Exception as exc:
                    self.failure(result, stage="list", exc=exc, url=url)
                    stream_reason = "list_error"
                    break
                result.pages_fetched += 1
                soup = page.soup()
                job_lists = soup.select(".job-list")
                if not job_lists:
                    stream_parse_complete = False
                    stream_reason = "parse_incomplete"
                    break
                cards = soup.select(".job-list .job")
                source_exhausted = not cards or all(
                    self._is_empty_placeholder(card) for card in cards
                )
                if source_exhausted:
                    if complete_window:
                        stream_complete = stream_parse_complete
                        stream_reason = (
                            "source_exhausted"
                            if stream_parse_complete
                            else "parse_incomplete"
                        )
                    break
                for position, card in enumerate(cards, start=1):
                    link = card.select_one('.info > a[href^="/jobs/"]')
                    title_node = card.select_one("h5.title")
                    type_node = title_node.select_one(".type") if title_node else None
                    title_parts: list[str] = []
                    if title_node:
                        for child in title_node.contents:
                            if isinstance(child, str):
                                value = clean_text(child)
                                if value:
                                    title_parts.append(value)
                    title = clean_text(" ".join(title_parts)) or text_of(link)
                    detail_url = absolute(page.final_url, link.get("href") if link else None)
                    if not title or not detail_url:
                        stream_parse_complete = False
                        continue
                    dl_texts = [text_of(node) or "" for node in card.select(".about dl")]
                    budget = next((x for x in dl_texts if "预算" in x), None)
                    work_period = next((x for x in dl_texts if "工期" in x), None)
                    record = CrawlRecord(
                        site_name=self.site_name,
                        content_category=(
                            "full_time_job"
                            if stream.get("kind") == "full_time"
                            else "outsourcing_task"
                        ),
                        title_raw=title,
                        list_url=url,
                        detail_url=detail_url,
                        page_no=page_no,
                        list_position=position,
                        job_type_raw=text_of(type_node),
                        budget_raw=budget,
                        work_period_raw=work_period,
                        description_raw=text_of(card.select_one("p.describe")),
                        published_at_raw=text_of(card.select_one(".publish-at")),
                    )
                    add_price(
                        record,
                        text_of(card.select_one(".about h4.price")),
                        source="platform_estimate",
                        url=url,
                        location=".about h4.price",
                        estimated=True,
                    )
                    add_price(
                        record,
                        budget,
                        source="list_page",
                        url=url,
                        location=".about dl",
                    )
                    if self.publication_filter is not None:
                        decision = self.publication_filter.decide(
                            record.published_at_raw
                        )
                        if decision.status == 'unknown':
                            raw_value = clean_text(record.published_at_raw) or '<missing>'
                            if raw_value in unknown_publication_values or len(
                                unknown_publication_values
                            ) < 20:
                                unknown_publication_values[raw_value] = (
                                    unknown_publication_values.get(raw_value, 0) + 1
                                )
                    accepted = self.accept_record(
                        result,
                        record,
                        numeric_price_only=numeric_price_only,
                    )
                    if accepted:
                        stream_count += 1
                    if complete_window and accepted and len(result.records) >= window_item_cap:
                        item_cap_reached = True
                        stream_reason = "item_cap_reached"
                        break
                    if not complete_window and accepted and stream_count >= stream_target:
                        break
                self.checkpoint(result, f"list_{stream.get('kind', stream_index)}")
                if item_cap_reached:
                    break
            if complete_window and not stream_complete and stream_reason is None:
                stream_reason = "page_cap_reached"
            if complete_window:
                key = str(stream.get("kind", stream_index))
                result.source_coverage[key] = {
                    "complete": stream_complete,
                    "stop_reason": stream_reason,
                }
            if item_cap_reached:
                break

        if complete_window:
            if unknown_publication_values:
                samples = '; '.join(
                    f'{raw!r}={count}'
                    for raw, count in sorted(
                        unknown_publication_values.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                )
                result.runtime_notes.append(
                    'Shixian unknown publication samples (max 20): ' + samples
                )
            sources_complete = bool(streams) and (
                len(result.source_coverage) == len(streams)
                and all(
                    bool(value.get("complete"))
                    for value in result.source_coverage.values()
                )
            )
            publication_unknown = (
                result.filter_counts.get("publication_unknown", 0) > 0
            )
            result.window_complete = sources_complete and not publication_unknown
            if result.window_complete:
                result.stop_reason = "source_exhausted"
            elif item_cap_reached:
                result.stop_reason = "item_cap_reached"
            elif sources_complete and publication_unknown:
                result.stop_reason = "publication_unavailable"
            else:
                result.stop_reason = "source_incomplete"

        detail_cap_reached = False
        detail_failed = False
        if not with_details:
            detail_limit = 0
        elif complete_window:
            detail_cap = max(
                0,
                int(limits.get("max_publication_detail_requests_per_site", 5000)),
            )
            detail_limit = min(len(result.records), detail_cap)
            detail_cap_reached = detail_limit < len(result.records)
        else:
            detail_limit = min(
                len(result.records),
                int(self.site_config.get("detail_sample_limit", 10)),
            )
        for record in result.records[:detail_limit]:
            if not record.detail_url:
                detail_failed = True
                self.failure(
                    result,
                    stage="detail",
                    exc=ValueError("Shixian selected record is missing a detail URL"),
                )
                continue
            try:
                page = self.client.get(record.detail_url, referer=record.list_url)
                soup = page.soup()
                record.detail_title_raw = text_of(soup.select_one("article.job-show h1.title"))
                add_price(
                    record,
                    text_of(soup.select_one("article.job-show strong.price")),
                    source="detail_summary",
                    url=record.detail_url,
                    location="article.job-show strong.price",
                    estimated=True,
                )
                detail_body = text_of(
                    soup.select_one("article.job-show section.description.clearfix .content")
                    or soup.select_one("article.job-show section.description .content")
                )
                if detail_body:
                    record.description_raw = detail_body[:5000]
                    for raw in extract_price_strings(detail_body, maximum=5):
                        add_price(
                            record,
                            raw,
                            source="publisher_text",
                            url=record.detail_url,
                            location="section.description .content",
                        )
                result.details_fetched += 1
            except Exception as exc:
                detail_failed = True
                self.failure(result, stage="detail", exc=exc, url=record.detail_url)
        if complete_window and with_details:
            if detail_cap_reached:
                result.runtime_notes.append(
                    "Shixian publication detail request cap reached: "
                    f"{detail_limit}."
                )
                if result.window_complete:
                    result.window_complete = False
                    result.stop_reason = "detail_cap_reached"
            elif detail_failed and result.window_complete:
                result.window_complete = False
                result.stop_reason = "detail_error"
        result.runtime_notes.append(
            "Platform estimate/budget and publisher-written price are stored separately."
        )
        result.finish()
        self.checkpoint(result, "finished")
        return result


class ProginnAdapter(BaseSiteAdapter):
    site_name = "proginn"
    difficulty = 2

    def crawl(
        self,
        limit: int,
        *,
        with_details: bool = True,
        numeric_price_only: bool = False,
    ) -> SiteResult:
        result = self.result(limit)
        category_urls = [str(value) for value in self.site_config["category_urls"]]
        for url in category_urls:
            if len(result.records) >= limit:
                break
            try:
                page = self.client.get(url)
            except Exception as exc:
                self.failure(result, stage="list", exc=exc, url=url)
                continue
            result.pages_fetched += 1
            cards = page.soup().select("#widget-user-list .item.J_user")
            for position, card in enumerate(cards, start=1):
                avatar = card.select_one(".user-avatar[userid]")
                link = card.select_one("a.info[userid][title]")
                user_id = (
                    (avatar.get("userid") if avatar else None)
                    or (link.get("userid") if link else None)
                )
                title = clean_text(link.get("title") if link else None) or text_of(link)
                if not user_id or not title:
                    continue
                detail_url = urljoin(page.final_url, f"/wo/{user_id}")
                locations = [
                    clean_text(node.get("title"))
                    for node in card.select(".grade .mate_ajax_link[title]")
                    if clean_text(node.get("title"))
                ]
                descriptions = [
                    value
                    for value in (text_of(node) for node in card.select("p.desc-item"))
                    if value
                ]
                record = CrawlRecord(
                    site_name=self.site_name,
                    content_category="talent_service_quote",
                    title_raw=title,
                    list_url=url,
                    detail_url=detail_url,
                    page_no=1,
                    list_position=position,
                    description_raw=" | ".join(descriptions)[:5000] or None,
                    location_raw=" | ".join(locations) or None,
                    metadata={
                        "user_id": str(user_id),
                        "rating": (
                            card.select_one(".J_rating[data-rating]").get("data-rating")
                            if card.select_one(".J_rating[data-rating]")
                            else None
                        ),
                        "work_time_raw": text_of(card.select_one(".work-time")),
                    },
                )
                add_price(
                    record,
                    text_of(card.select_one(".hire-info .work-price")),
                    source="list_page",
                    url=url,
                    location=".hire-info .work-price",
                    kind="monthly_salary",
                )
                hidden = card.select_one(".J_GlobalContrast[data-price]")
                if hidden and clean_text(hidden.get("data-price")):
                    add_price(
                        record,
                        clean_text(hidden.get("data-price")),
                        source="list_page_attribute",
                        url=url,
                        location=".J_GlobalContrast[data-price]",
                        kind="daily_quote_attribute",
                    )
                accepted = self.accept_record(
                    result,
                    record,
                    numeric_price_only=numeric_price_only,
                )
                if accepted and len(result.records) >= limit:
                    break
            self.checkpoint(result, "list_category")

        detail_limit = min(
            len(result.records),
            int(self.site_config.get("detail_sample_limit", 10)) if with_details else 0,
        )
        for record in result.records[:detail_limit]:
            if not record.detail_url:
                continue
            try:
                page = self.client.get(record.detail_url, referer=record.list_url)
                soup = page.soup()
                header = text_of(soup.select_one("#top_infos .header"))
                if header:
                    record.detail_title_raw = header
                detail_price = text_of(soup.select_one("#top_infos .work-price"))
                for raw in extract_price_strings(detail_price, maximum=4):
                    add_price(
                        record,
                        raw,
                        source="detail_summary",
                        url=record.detail_url,
                        location="#top_infos .work-price",
                    )
                details = text_of(soup.select_one(".personal_details"))
                if details:
                    record.description_raw = details[:5000]
                result.details_fetched += 1
            except Exception as exc:
                self.failure(result, stage="detail", exc=exc, url=record.detail_url)
        result.runtime_notes.append(
            "Quotes describe talent service day/month rates, not a concrete project budget."
        )
        result.finish()
        self.checkpoint(result, "finished")
        return result


class ZbjAdapter(BaseSiteAdapter):
    site_name = "zbj"
    difficulty = 4

    SERVICE_LINK_RE = re.compile(r"/fw/\d+\.html$")
    DEMAND_LINK_RE = re.compile(r"/xq/[A-Za-z0-9]{12,}\.html$")

    @staticmethod
    def _is_verify_url(url: str) -> bool:
        return urlparse(url).netloc.lower() == "verify.zbj.com"

    @staticmethod
    def _demand_identity(url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        return parsed.path.rstrip("/") or None

    @staticmethod
    def _decode_js_string(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return clean_text(json.loads(f'"{value}"'))
        except (json.JSONDecodeError, TypeError):
            return clean_text(value.replace("\\n", " ").replace("\\\"", '"'))

    @staticmethod
    def _split_js_arguments(value: str) -> list[str]:
        output: list[str] = []
        start = 0
        quote: str | None = None
        escaped = False
        depth = 0
        for index, character in enumerate(value):
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in {'"', "'"}:
                quote = character
            elif character in "([{":
                depth += 1
            elif character in ")]}":
                depth = max(0, depth - 1)
            elif character == "," and depth == 0:
                output.append(value[start:index].strip())
                start = index + 1
        output.append(value[start:].strip())
        return output

    def _nuxt_aliases(self, html: str) -> dict[str, str | None]:
        start = html.find("window.__NUXT__=(function(")
        if start < 0:
            return {}
        script_end = html.find("</script>", start)
        script = html[start : script_end if script_end >= 0 else len(html)]
        params_start = script.find("(function(") + len("(function(")
        params_end = script.find("){", params_start)
        invocation_start = script.rfind("}(")
        invocation_end = script.rfind("))")
        if min(params_start, params_end, invocation_start, invocation_end) < 0:
            return {}
        parameters = [x.strip() for x in script[params_start:params_end].split(",")]
        arguments = self._split_js_arguments(
            script[invocation_start + 2 : invocation_end]
        )
        aliases: dict[str, str | None] = {}
        for parameter, argument in zip(parameters, arguments, strict=False):
            argument = argument.strip()
            if argument.startswith('"') and argument.endswith('"'):
                aliases[parameter] = self._decode_js_string(argument[1:-1])
            elif argument in {"null", "void 0", "undefined"}:
                aliases[parameter] = None
        return aliases

    def _parse_embedded_demands(
        self, html: str, *, list_url: str, page_no: int
    ) -> list[CrawlRecord]:
        """Parse the SSR Nuxt demand array without evaluating page JavaScript."""
        object_pattern = re.compile(
            r'\{id:\d+,uuid:"(?P<uuid>(?:\\.|[^"\\])*)"(?P<body>.*?)\};',
            re.DOTALL,
        )
        aliases = self._nuxt_aliases(html)
        field_pattern = lambda name: re.compile(  # noqa: E731
            rf'{name}:(?P<value>"(?:\\.|[^"\\])*"|[A-Za-z_$][\w$]*|void 0|null)',
            re.DOTALL,
        )
        patterns = {
            name: field_pattern(name)
            for name in ("title", "priceDesc", "content", "statusDesc", "publishTime")
        }
        output: list[CrawlRecord] = []
        seen: set[str] = set()
        for match in object_pattern.finditer(html):
            uuid = self._decode_js_string(match.group("uuid"))
            body = match.group("body")
            if not uuid or uuid in seen:
                continue
            seen.add(uuid)

            def value(pattern: re.Pattern[str]) -> str | None:
                found = pattern.search(body)
                if not found:
                    return None
                token = found.group("value")
                if token.startswith('"') and token.endswith('"'):
                    return self._decode_js_string(token[1:-1])
                return aliases.get(token)

            title = value(patterns["title"])
            price = value(patterns["priceDesc"])
            if not title or not price:
                continue
            detail_url = f"https://www.zbj.com/xq/{uuid}.html"
            record = CrawlRecord(
                site_name=self.site_name,
                content_category="outsourcing_task",
                title_raw=title,
                list_url=list_url,
                detail_url=detail_url,
                page_no=page_no,
                list_position=len(output) + 1,
                status_raw=value(patterns["statusDesc"]),
                description_raw=value(patterns["content"]),
                published_at_raw=value(patterns["publishTime"]),
                capture_method="ssr_embedded_state",
                metadata={"source_uuid": uuid},
            )
            add_price(
                record,
                price,
                source="list_page_embedded_state",
                url=list_url,
                location="SSR Nuxt demand array priceDesc",
            )
            output.append(record)
        return output

    def _parse_links(
        self,
        soup: BeautifulSoup,
        *,
        base_url: str,
        list_url: str,
        page_no: int,
        category: str,
        matcher: re.Pattern[str],
    ) -> list[CrawlRecord]:
        output: list[CrawlRecord] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = absolute(base_url, anchor.get("href"))
            if not href or not matcher.search(urlparse(href).path) or href in seen:
                continue
            seen.add(href)
            card = nearest_card(anchor)
            title = (
                clean_text(anchor.get("title"))
                or clean_text(anchor.get("data-title"))
                or text_of(anchor)
            )
            if not title or len(title) > 180:
                continue
            record = CrawlRecord(
                site_name=self.site_name,
                content_category=category,
                title_raw=title,
                list_url=list_url,
                detail_url=href,
                page_no=page_no,
                list_position=len(output) + 1,
                description_raw=(text_of(card) or "")[:3000] or None,
            )
            for raw in extract_price_strings(text_of(card), maximum=5):
                add_price(
                    record,
                    raw,
                    source="list_page",
                    url=list_url,
                    location="nearest listing card",
                )
            if record.price_evidence:
                output.append(record)
        return output

    def crawl(
        self,
        limit: int,
        *,
        with_details: bool = True,
        numeric_price_only: bool = False,
    ) -> SiteResult:
        result = self.result(limit)
        complete_window = (
            self.publication_filter is not None and self.complete_publication_window
        )
        window_item_cap = int(
            self.config.section("limits").get("max_publication_records_per_site", 10000)
        )
        state_path = self.site_config.get("storage_state")
        if state_path:
            try:
                self.client.load_storage_state(
                    str(state_path), allowed_domains={"zbj.com", "task.zbj.com"}
                )
            except Exception as exc:
                self.failure(result, stage="session_load", exc=exc)
        sources: list[dict[str, Any]] = list(self.site_config.get("sources", []))
        if self.publication_filter is not None:
            skipped_services = sum(
                source.get("kind") == "services" for source in sources
            )
            sources = [source for source in sources if source.get("kind") == "demands"]
            if skipped_services:
                result.runtime_notes.append(
                    "ZBJ service sources skipped because they do not expose demand publishTime."
                )
        domain_blocked = False
        item_cap_reached = False
        max_pages = int(self.config.section("limits").get("max_pages_per_site", 10))
        for source_index, source in enumerate(sources):
            if domain_blocked or item_cap_reached or (
                not complete_window and len(result.records) >= limit
            ):
                break
            pages = (
                int(source.get("publication_max_pages", 400))
                if complete_window
                else min(max_pages, int(source.get("max_pages", max_pages)))
            )
            source_complete = False
            source_reason: str | None = None
            for page_no in range(1, pages + 1):
                url = str(source["start_url"])
                if page_no > 1:
                    template = source.get("page_url_template")
                    if not template:
                        break
                    url = str(template).format(page=page_no)
                try:
                    page = self.client.get(url)
                    if self._is_verify_url(page.final_url):
                        raise BlockedResponseError(
                            "Redirected to verify.zbj.com; stop this domain run",
                            status_code=429,
                            url=url,
                        )
                    result.pages_fetched += 1
                    matcher = (
                        self.SERVICE_LINK_RE
                        if source.get("kind") == "services"
                        else self.DEMAND_LINK_RE
                    )
                    records = self._parse_links(
                        page.soup(),
                        base_url=page.final_url,
                        list_url=url,
                        page_no=page_no,
                        category=(
                            "service_listing"
                            if source.get("kind") == "services"
                            else "outsourcing_task"
                        ),
                        matcher=matcher,
                    )
                    if source.get("kind") == "demands":
                        embedded_records = self._parse_embedded_demands(
                            page.text, list_url=url, page_no=page_no
                        )
                        records_by_url = {
                            self._demand_identity(record.detail_url): record
                            for record in records
                            if self._demand_identity(record.detail_url)
                        }
                        for embedded in embedded_records:
                            identity = self._demand_identity(embedded.detail_url)
                            existing = records_by_url.get(identity)
                            if existing is None:
                                records.append(embedded)
                                if identity:
                                    records_by_url[identity] = embedded
                                continue
                            existing.detail_url = embedded.detail_url
                            existing.record_id = embedded.record_id
                            if embedded.published_at_raw:
                                existing.published_at_raw = embedded.published_at_raw
                            existing.status_raw = existing.status_raw or embedded.status_raw
                            existing.description_raw = (
                                existing.description_raw or embedded.description_raw
                            )
                            existing.metadata.update(embedded.metadata)
                            for evidence in embedded.price_evidence:
                                existing.add_price(evidence)
                    for record in records:
                        accepted = self.accept_record(
                            result,
                            record,
                            numeric_price_only=numeric_price_only,
                        )
                        if complete_window and accepted and len(result.records) >= window_item_cap:
                            item_cap_reached = True
                            source_reason = "item_cap_reached"
                            break
                        if not complete_window and accepted and len(result.records) >= limit:
                            break
                    self.checkpoint(result, f"list_{source.get('kind', 'unknown')}")
                    if not records:
                        if complete_window:
                            source_complete = True
                            source_reason = "source_exhausted"
                        break
                    if complete_window and source.get(
                        "publication_sorted_desc", False
                    ) and self.page_is_before_publication_cutoff(records):
                        source_complete = True
                        source_reason = "cutoff_reached"
                        break
                    if item_cap_reached:
                        break
                    if not complete_window and len(result.records) >= limit:
                        break
                except (BlockedResponseError, RobotsDeniedError) as exc:
                    self.failure(result, stage="list", exc=exc, url=url)
                    result.runtime_notes.append(
                        "ZBJ verification/rate-limit encountered; no automatic challenge bypass."
                    )
                    domain_blocked = True
                    source_reason = "blocked"
                    break
                except Exception as exc:
                    self.failure(result, stage="list", exc=exc, url=url)
                    source_reason = "list_error"
                    break
            if complete_window and not source_complete and source_reason is None:
                source_reason = "page_cap_reached"
            if complete_window:
                key = f"{source.get('kind', 'unknown')}:{source_index}"
                result.source_coverage[key] = {
                    "complete": source_complete,
                    "stop_reason": source_reason,
                    "start_url": str(source.get("start_url", "")),
                }

        if complete_window:
            result.window_complete = bool(sources) and (
                len(result.source_coverage) == len(sources)
                and all(
                    bool(value.get("complete"))
                    for value in result.source_coverage.values()
                )
            )
            if result.window_complete:
                result.stop_reason = "cutoff_reached"
            elif item_cap_reached:
                result.stop_reason = "item_cap_reached"
            elif domain_blocked:
                result.stop_reason = "blocked"
            else:
                result.stop_reason = "source_incomplete"

        detail_limit = min(
            len(result.records),
            int(self.site_config.get("detail_sample_limit", 5)) if with_details else 0,
        )
        if not domain_blocked:
            for record in result.records[:detail_limit]:
                if not record.detail_url:
                    continue
                try:
                    page = self.client.get(record.detail_url, referer=record.list_url)
                    if self._is_verify_url(page.final_url):
                        raise BlockedResponseError(
                            "Redirected to verify.zbj.com during detail sample",
                            status_code=429,
                            url=record.detail_url,
                        )
                    soup = page.soup()
                    record.detail_title_raw = (
                        text_of(soup.select_one("h1")) or text_of(soup.select_one("title"))
                    )
                    price_nodes = soup.select(
                        '[class*="price"], [class*="Price"], [class*="package"]'
                    )
                    for node in price_nodes[:40]:
                        for raw in extract_price_strings(text_of(node), maximum=4):
                            add_price(
                                record,
                                raw,
                                source=(
                                    "package_option"
                                    if record.content_category == "service_listing"
                                    else "detail_summary"
                                ),
                                url=record.detail_url,
                                location="price/package element",
                            )
                    result.details_fetched += 1
                except BlockedResponseError as exc:
                    self.failure(result, stage="detail", exc=exc, url=record.detail_url)
                    result.runtime_notes.append("Detail sampling stopped after verification wall.")
                    break
                except Exception as exc:
                    self.failure(result, stage="detail", exc=exc, url=record.detail_url)
        result.runtime_notes.append(
            "Service-package prices and outsourcing-demand budgets remain separate categories."
        )
        result.finish()
        self.checkpoint(result, "finished")
        return result


STATIC_ADAPTERS: dict[str, type[BaseSiteAdapter]] = {
    "epwk": EpwkAdapter,
    "shixian": ShixianAdapter,
    "proginn": ProginnAdapter,
    "zbj": ZbjAdapter,
}
