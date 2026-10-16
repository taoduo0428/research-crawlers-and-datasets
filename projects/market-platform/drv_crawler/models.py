from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import re
from typing import Any


_NONZERO_PRICE_DIGIT_RE = re.compile(r"[1-9]")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.replace("\u00a0", " ").split())
    return cleaned or None


def make_record_id(site_name: str, source_key: str) -> str:
    digest = sha256(f"{site_name}|{source_key}".encode("utf-8")).hexdigest()
    return f"{site_name}-{digest[:20]}"


@dataclass(slots=True)
class PriceEvidence:
    """One verbatim price observation and its exact page location."""

    raw_text: str
    price_type: str
    price_source: str
    evidence_url: str
    evidence_location: str | None = None
    package_name: str | None = None
    currency_text: str | None = None
    period_text: str | None = None
    negotiable: bool = False
    estimated_by_platform: bool = False
    captured_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.raw_text = clean_text(self.raw_text) or ""
        self.package_name = clean_text(self.package_name)
        self.evidence_location = clean_text(self.evidence_location)
        if not self.raw_text:
            raise ValueError("PriceEvidence.raw_text must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CrawlRecord:
    site_name: str
    content_category: str
    title_raw: str
    list_url: str
    detail_url: str | None
    page_no: int
    list_position: int
    price_evidence: list[PriceEvidence] = field(default_factory=list)
    detail_title_raw: str | None = None
    category_level_1_raw: str | None = None
    category_level_2_raw: str | None = None
    category_level_3_raw: str | None = None
    category_path_raw: str | None = None
    category_source: str | None = None
    category_source_url: str | None = None
    category_identity_verified: bool = False
    status_raw: str | None = None
    job_type_raw: str | None = None
    budget_raw: str | None = None
    work_period_raw: str | None = None
    description_raw: str | None = None
    publisher_name_raw: str | None = None
    location_raw: str | None = None
    published_at_raw: str | None = None
    capture_method: str = "static_html"
    source_state: str = "active_unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
    captured_at: str = field(default_factory=utc_now_iso)
    record_id: str = ""

    def __post_init__(self) -> None:
        self.title_raw = clean_text(self.title_raw) or ""
        if not self.title_raw:
            raise ValueError("CrawlRecord.title_raw must not be empty")
        for name in (
            "detail_title_raw",
            "category_level_1_raw",
            "category_level_2_raw",
            "category_level_3_raw",
            "category_path_raw",
            "category_source",
            "category_source_url",
            "status_raw",
            "job_type_raw",
            "budget_raw",
            "work_period_raw",
            "description_raw",
            "publisher_name_raw",
            "location_raw",
            "published_at_raw",
        ):
            setattr(self, name, clean_text(getattr(self, name)))
        if not self.record_id:
            self.record_id = make_record_id(
                self.site_name, self.detail_url or f"{self.list_url}|{self.title_raw}"
            )

    @property
    def core_complete(self) -> bool:
        return bool(self.title_raw and self.price_evidence)

    @property
    def price_raw_joined(self) -> str:
        seen: set[str] = set()
        values: list[str] = []
        for evidence in self.price_evidence:
            if evidence.raw_text not in seen:
                seen.add(evidence.raw_text)
                values.append(evidence.raw_text)
        return " | ".join(values)

    @property
    def has_numeric_price(self) -> bool:
        return any(
            _NONZERO_PRICE_DIGIT_RE.search(evidence.raw_text)
            for evidence in self.price_evidence
        )

    def add_price(self, evidence: PriceEvidence) -> None:
        identity = (
            evidence.raw_text,
            evidence.price_source,
            evidence.package_name,
            evidence.evidence_location,
        )
        existing = {
            (item.raw_text, item.price_source, item.package_name, item.evidence_location)
            for item in self.price_evidence
        }
        if identity not in existing:
            self.price_evidence.append(evidence)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["price_evidence"] = [item.to_dict() for item in self.price_evidence]
        value["price_raw_joined"] = self.price_raw_joined
        value["core_complete"] = self.core_complete
        return value


@dataclass(slots=True)
class CrawlFailure:
    site_name: str
    stage: str
    error_type: str
    message: str
    url: str | None = None
    retryable: bool = False
    captured_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SiteResult:
    site_name: str
    requested_limit: int
    records: list[CrawlRecord] = field(default_factory=list)
    failures: list[CrawlFailure] = field(default_factory=list)
    pages_fetched: int = 0
    details_fetched: int = 0
    filtered_out_count: int = 0
    filter_counts: dict[str, int] = field(default_factory=dict)
    publication_filter: dict[str, Any] | None = None
    completion_mode: str = "target_count"
    window_complete: bool | None = None
    stop_reason: str | None = None
    source_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    adapter: str = "unknown"
    difficulty: int = 0
    status: str = "pending"
    runtime_notes: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None

    def finish(self) -> None:
        self.records = dedupe_records(self.records)
        if self.completion_mode != "publication_window":
            self.records = self.records[: self.requested_limit]
        self.finished_at = utc_now_iso()
        if self.completion_mode == "publication_window":
            if self.window_complete:
                self.status = "success"
            elif self.records:
                self.status = "partial"
            else:
                self.status = "failed"
            return
        if len(self.records) >= self.requested_limit and all(
            record.core_complete for record in self.records
        ):
            self.status = "success"
        elif self.records:
            self.status = "partial"
        else:
            self.status = "failed"

    def quality(self) -> dict[str, Any]:
        total = len(self.records)
        unique = len({record.record_id for record in self.records})
        complete = sum(record.core_complete for record in self.records)
        detail_links = sum(bool(record.detail_url) for record in self.records)
        structurally_complete = not total or complete / total >= 0.95
        if self.completion_mode == "publication_window":
            quality_passed = bool(
                self.window_complete and unique == total and structurally_complete
            )
        else:
            quality_passed = bool(
                total >= self.requested_limit
                and unique == total
                and structurally_complete
            )
        return {
            "record_count": total,
            "requested_limit": self.requested_limit,
            "unique_record_count": unique,
            "duplicate_rate": 0.0 if not total else round((total - unique) / total, 4),
            "core_complete_count": complete,
            "core_missing_rate": 0.0 if not total else round((total - complete) / total, 4),
            "detail_link_rate": 0.0 if not total else round(detail_links / total, 4),
            "quality_passed": quality_passed,
        }

    def report_dict(self) -> dict[str, Any]:
        quality_stats: dict[str, Any] | None = None
        if self.site_name == "epwk":
            description_lengths = [
                int(record.metadata.get("description_length", 0) or 0)
                for record in self.records
            ]
            trade_modes: dict[str, int] = {}
            statuses: dict[str, int] = {}
            exclude_reasons: dict[str, int] = {}
            for record in self.records:
                mode = record.job_type_raw or "未解析"
                trade_modes[mode] = trade_modes.get(mode, 0) + 1
                status = record.status_raw or "未解析"
                statuses[status] = statuses.get(status, 0) + 1
                for reason in record.metadata.get("exclude_reasons", []) or []:
                    exclude_reasons[reason] = exclude_reasons.get(reason, 0) + 1
            quality_stats = {
                "description_success_count": sum(
                    bool(record.description_raw) for record in self.records
                ),
                "description_length_distribution": {
                    "lt_40": sum(value < 40 for value in description_lengths),
                    "40_79": sum(40 <= value < 80 for value in description_lengths),
                    "80_149": sum(80 <= value < 150 for value in description_lengths),
                    "gte_150": sum(value >= 150 for value in description_lengths),
                },
                "trade_modes": dict(sorted(trade_modes.items())),
                "statuses": dict(sorted(statuses.items())),
                "completed_count": sum(
                    record.metadata.get("is_completed") is True
                    for record in self.records
                ),
                "winner_count": sum(
                    record.metadata.get("has_winner") is True
                    for record in self.records
                ),
                "explicit_price_count": sum(
                    record.has_numeric_price for record in self.records
                ),
                "main_sample_count": sum(
                    record.metadata.get("main_sample") is True
                    for record in self.records
                ),
                "exclude_reasons": dict(sorted(exclude_reasons.items())),
            }
        return {
            "site_name": self.site_name,
            "status": self.status,
            "adapter": self.adapter,
            "difficulty": self.difficulty,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pages_fetched": self.pages_fetched,
            "details_fetched": self.details_fetched,
            "filtered_out_count": self.filtered_out_count,
            "filter_counts": self.filter_counts,
            "publication_filter": self.publication_filter,
            "completion_mode": self.completion_mode,
            "window_complete": self.window_complete,
            "stop_reason": self.stop_reason,
            "source_coverage": self.source_coverage,
            "quality": self.quality(),
            "quality_stats": quality_stats,
            "runtime_notes": self.runtime_notes,
            "failures": [failure.to_dict() for failure in self.failures],
        }


def dedupe_records(records: list[CrawlRecord]) -> list[CrawlRecord]:
    output: list[CrawlRecord] = []
    seen: set[str] = set()
    for record in records:
        if record.record_id in seen:
            continue
        seen.add(record.record_id)
        output.append(record)
    return output
