from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import random
import time
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from .errors import (
    AccessDeniedError,
    ChallengeRequiredError,
    ConfigurationError,
    LiepinCrawlerError,
    SchemaChangedError,
)
from .http import LiepinHttpClient
from .models import CareerCategory, CrawlSummary, LiepinJobRecord
from .parser import (
    career_page_index,
    count_job_cards,
    has_nonzero_numeric_salary,
    has_pagination_evidence,
    is_empty_career_page,
    looks_like_career_page,
    looks_like_challenge,
    parse_job_cards,
    parse_next_page_url,
    parse_taxonomy,
)
from .state import LiepinStateStore


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LiepinConfig:
    output_dir: Path = Path(".crawler-state/liepin")
    home_url: str = "https://www.liepin.com/"
    per_category: int = 50
    numeric_salary_only: bool = False
    max_pages_per_category: int = 20
    max_categories: int | None = None
    min_taxonomy_categories: int = 500
    delay_min: float = 0.8
    delay_max: float = 1.8
    connect_timeout: float = 5.0
    read_timeout: float = 20.0
    retries: int = 2
    max_attempts_per_category: int = 2
    max_consecutive_failures: int = 5
    proxy_policy: str = "auto"
    proxy_url: str | None = None
    cookie_file: Path | None = None
    cookie_header: str | None = None
    refresh_taxonomy: bool = False
    retry_failed: bool = True

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        if self.cookie_file is not None:
            self.cookie_file = Path(self.cookie_file)
        if not 1 <= self.per_category <= 50:
            raise ConfigurationError("per_category must be between 1 and 50")
        if not isinstance(self.numeric_salary_only, bool):
            raise ConfigurationError("numeric_salary_only must be a boolean")
        if not 1 <= self.max_pages_per_category <= 20:
            raise ConfigurationError("max_pages_per_category must be between 1 and 20")
        if self.max_categories is not None and self.max_categories < 1:
            raise ConfigurationError("max_categories must be positive when supplied")
        if self.min_taxonomy_categories < 1:
            raise ConfigurationError("min_taxonomy_categories must be positive")
        if self.delay_min < 0 or self.delay_max < self.delay_min:
            raise ConfigurationError("delay range must satisfy 0 <= min <= max")
        if not 1 <= self.max_attempts_per_category <= 5:
            raise ConfigurationError("max_attempts_per_category must be between 1 and 5")
        if not 1 <= self.max_consecutive_failures <= 20:
            raise ConfigurationError("max_consecutive_failures must be between 1 and 20")

    @property
    def state_path(self) -> Path:
        return self.output_dir / "checkpoint.sqlite3"

    @property
    def jobs_path(self) -> Path:
        return self.output_dir / "jobs.jsonl"

    @property
    def coverage_path(self) -> Path:
        return self.output_dir / "coverage.json"

    def run_scope(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "site": "liepin",
            "home_url": self.home_url,
            "per_category": self.per_category,
            "numeric_salary_only": self.numeric_salary_only,
        }


@dataclass(slots=True)
class _CategoryCrawlResult:
    records: list[LiepinJobRecord]
    completion_reason: str
    pages_fetched: int
    filtered_non_numeric: int


def _category_page_url(category_url: str, page_index: int) -> str:
    parsed = urlsplit(category_url)
    path = f"{parsed.path.rstrip('/')}/pn{page_index}/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def iter_jsonl_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaChangedError(
                    f"Malformed JSONL at {path}:{line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise SchemaChangedError(
                    f"Expected JSON object at {path}:{line_number}"
                )
            yield value


class LiepinCrawler:
    def __init__(
        self,
        config: LiepinConfig,
        *,
        client: LiepinHttpClient | None = None,
        state: LiepinStateStore | None = None,
    ) -> None:
        self.config = config
        config.output_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or LiepinHttpClient(
            proxy_policy=config.proxy_policy,
            proxy_url=config.proxy_url,
            cookie_file=config.cookie_file,
            cookie_header=config.cookie_header,
            connect_timeout=config.connect_timeout,
            read_timeout=config.read_timeout,
            retries=config.retries,
        )
        self.state = state or LiepinStateStore(config.state_path)
        self._owns_state = state is None
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_state:
            self.state.close()
        if self._owns_client:
            self.client.session.close()

    def __enter__(self) -> "LiepinCrawler":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def discover_categories(self, *, force: bool | None = None) -> list[CareerCategory]:
        should_refresh = self.config.refresh_taxonomy if force is None else force
        cached = self.state.active_categories()
        if cached and not should_refresh:
            LOGGER.info("Using %d cached Liepin career categories", len(cached))
            return cached

        page = self.client.get(self.config.home_url)
        if looks_like_challenge(page.text, page.final_url):
            raise ChallengeRequiredError(
                "Liepin homepage requires human verification; refresh the browser session"
            )
        categories = parse_taxonomy(page.text, self.config.home_url)
        if len(categories) < self.config.min_taxonomy_categories:
            raise SchemaChangedError(
                "Liepin taxonomy extraction returned "
                f"{len(categories)} categories; expected at least "
                f"{self.config.min_taxonomy_categories}"
            )
        snapshot = json.dumps(
            [category.to_dict() for category in categories],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot_hash = sha256(snapshot.encode("utf-8")).hexdigest()
        self.state.upsert_taxonomy(categories, snapshot_hash=snapshot_hash)
        LOGGER.info(
            "Refreshed Liepin taxonomy: %d level-1, %d level-2, %d level-3",
            len({category.level_1 for category in categories}),
            len({(category.level_1, category.level_2) for category in categories}),
            len(categories),
        )
        return categories

    def _sleep_between_pages(self) -> None:
        if self.config.delay_max > 0:
            time.sleep(random.uniform(self.config.delay_min, self.config.delay_max))

    def _crawl_category(self, category: CareerCategory) -> _CategoryCrawlResult:
        records = []
        seen_job_ids: set[str] = set()
        visited_urls: set[str] = set()
        filtered_non_numeric = 0
        page_index = 0
        referer = self.config.home_url

        while True:
            page_url = _category_page_url(category.url, page_index)
            if page_url in visited_urls:
                raise SchemaChangedError("career pagination loop detected")
            visited_urls.add(page_url)
            page = self.client.get(page_url, referer=referer)
            if looks_like_challenge(page.text, page.final_url):
                raise ChallengeRequiredError(
                    "Liepin human verification detected on career page"
                )
            final_page_index = career_page_index(
                page.final_url, category_url=category.url
            )
            if final_page_index != page_index:
                raise SchemaChangedError("career page redirected to an unexpected page")
            if not looks_like_career_page(page.text):
                raise SchemaChangedError(
                    "Career page has neither job cards nor a recognized empty state"
                )

            pages_fetched = len(visited_urls)
            raw_card_count = count_job_cards(page.text)
            explicit_empty = is_empty_career_page(page.text)
            if explicit_empty:
                if raw_card_count:
                    raise SchemaChangedError(
                        "Career page contains both job cards and an empty-state marker"
                    )
                return _CategoryCrawlResult(
                    records=records,
                    completion_reason="empty"
                    if page_index == 0 and not records
                    else "exhausted_below_quota",
                    pages_fetched=pages_fetched,
                    filtered_non_numeric=filtered_non_numeric,
                )
            if raw_card_count == 0:
                raise SchemaChangedError(
                    "Career page container is present but contains no job cards or empty state"
                )

            page_records = parse_job_cards(
                page.text,
                category,
                per_category=raw_card_count,
                page_no=page_index + 1,
            )
            if not page_records:
                raise SchemaChangedError("Career job cards could not be parsed")
            page_job_ids = {record.job_id for record in page_records}
            if page_job_ids <= seen_job_ids:
                raise SchemaChangedError("career pagination repeated a previously seen page")

            for record in page_records:
                if record.job_id in seen_job_ids:
                    continue
                seen_job_ids.add(record.job_id)
                if (
                    self.config.numeric_salary_only
                    and not has_nonzero_numeric_salary(record)
                ):
                    filtered_non_numeric += 1
                    continue
                record.list_position = len(records) + 1
                records.append(record)
                if len(records) >= self.config.per_category:
                    return _CategoryCrawlResult(
                        records=records,
                        completion_reason="quota_met",
                        pages_fetched=pages_fetched,
                        filtered_non_numeric=filtered_non_numeric,
                    )

            next_url = parse_next_page_url(
                page.text,
                current_url=page.final_url,
                category_url=category.url,
            )
            if next_url is None:
                if (
                    raw_card_count >= 40
                    and not has_pagination_evidence(page.text)
                ):
                    raise SchemaChangedError(
                        "Full career page has no trustworthy pagination evidence"
                    )
                return _CategoryCrawlResult(
                    records=records,
                    completion_reason="exhausted_below_quota",
                    pages_fetched=pages_fetched,
                    filtered_non_numeric=filtered_non_numeric,
                )
            if next_url in visited_urls:
                raise SchemaChangedError("career pagination loop detected")
            if pages_fetched >= self.config.max_pages_per_category:
                return _CategoryCrawlResult(
                    records=records,
                    completion_reason="page_limit_below_quota",
                    pages_fetched=pages_fetched,
                    filtered_non_numeric=filtered_non_numeric,
                )
            next_page_index = career_page_index(next_url, category_url=category.url)
            if next_page_index != page_index + 1:
                raise SchemaChangedError("career pagination is not contiguous")
            referer = page.final_url
            page_index = next_page_index
            self._sleep_between_pages()

    def run(self) -> CrawlSummary:
        self.discover_categories()
        reopened = self.state.prepare_run(
            quota_target=self.config.per_category,
            numeric_salary_only=self.config.numeric_salary_only,
        )
        if reopened:
            LOGGER.info("Reopened %d categories below the requested quota", reopened)
        interrupted = self.state.reset_interrupted()
        if interrupted:
            LOGGER.warning("Reset %d interrupted categories to pending", interrupted)
        candidates = self.state.list_candidates(
            limit=self.config.max_categories,
            retry_failed=self.config.retry_failed,
            max_attempts=self.config.max_attempts_per_category,
        )
        attempted = 0
        consecutive_failures = 0
        stop_reason = "nothing_to_do"
        abort_run = False

        for index, category in enumerate(candidates, start=1):
            if not self.state.claim_category(
                category, max_attempts=self.config.max_attempts_per_category
            ):
                continue
            attempted += 1
            LOGGER.info(
                "Crawling Liepin category %d/%d: %s",
                index,
                len(candidates),
                category.path,
            )
            try:
                result = self._crawl_category(category)
                stored = self.state.complete_category(
                    category,
                    result.records,
                    completion_reason=result.completion_reason,
                    pages_fetched=result.pages_fetched,
                    filtered_non_numeric=result.filtered_non_numeric,
                    quota_target=self.config.per_category,
                )
                LOGGER.info("Stored %d record(s) for %s", stored, category.path)
                consecutive_failures = 0
                stop_reason = "bounded_run_complete"
            except (ChallengeRequiredError, AccessDeniedError) as exc:
                self.state.mark_failed(category, f"{type(exc).__name__}: {exc}")
                LOGGER.error("Stopping after session/challenge failure: %s", exc)
                stop_reason = type(exc).__name__
                abort_run = True
            except LiepinCrawlerError as exc:
                self.state.mark_failed(category, f"{type(exc).__name__}: {exc}")
                consecutive_failures += 1
                LOGGER.error("Category failed: %s: %s", category.path, exc)
                if consecutive_failures >= self.config.max_consecutive_failures:
                    stop_reason = "consecutive_failure_budget_exhausted"
                    abort_run = True
            except Exception as exc:
                self.state.mark_failed(category, f"{type(exc).__name__}: {exc}")
                consecutive_failures += 1
                LOGGER.exception("Unexpected category failure: %s", category.path)
                if consecutive_failures >= self.config.max_consecutive_failures:
                    stop_reason = "consecutive_failure_budget_exhausted"
                    abort_run = True

            if abort_run:
                break
            if index < len(candidates) and self.config.delay_max > 0:
                time.sleep(random.uniform(self.config.delay_min, self.config.delay_max))

        self.state.export_jobs(self.config.jobs_path)
        coverage = self.state.export_coverage(self.config.coverage_path)
        counts = coverage["summary"]
        quota_outcomes = coverage.get("quota_outcomes", {})
        page_limited = int(quota_outcomes.get("page_limit_below_quota", 0))
        unfinished = (
            counts["categories_pending"]
            + counts["categories_failed"]
            + counts["categories_running"]
        )
        if unfinished == 0 and page_limited:
            status = "partial"
            stop_reason = "page_limit_below_quota"
        elif unfinished == 0:
            status = "success"
            stop_reason = "all_categories_completed"
        elif not counts["records_total"] and counts["categories_failed"]:
            status = "failed"
        elif attempted and not abort_run:
            status = "partial"
            if self.config.max_categories is not None:
                stop_reason = "category_limit_reached"
        elif counts["records_total"]:
            status = "partial"
        else:
            status = "failed"

        return CrawlSummary(
            status=status,
            stop_reason=stop_reason,
            categories_total=counts["categories_total"],
            categories_attempted_this_run=attempted,
            categories_with_records=counts["categories_done"],
            categories_empty=counts["categories_empty"],
            categories_failed=counts["categories_failed"],
            categories_pending=counts["categories_pending"],
            records_total=counts["records_total"],
            unique_source_jobs=counts["unique_source_jobs"],
            output_path=os.fspath(self.config.jobs_path.resolve()),
            coverage_path=os.fspath(self.config.coverage_path.resolve()),
        )
