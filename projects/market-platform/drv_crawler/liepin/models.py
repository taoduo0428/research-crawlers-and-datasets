from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import re
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.replace("\u00a0", " ").split())
    return cleaned or None


@dataclass(frozen=True, slots=True)
class CareerCategory:
    level_1: str
    level_2: str
    level_3: str
    url: str

    @property
    def path(self) -> str:
        return " > ".join((self.level_1, self.level_2, self.level_3))

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class LiepinJobRecord:
    category: CareerCategory
    job_id: str
    job_kind: str | None
    title: str
    salary_raw: str | None
    detail_url: str
    list_position: int
    page_no: int = 1
    location: str | None = None
    experience: str | None = None
    education: str | None = None
    company_name: str | None = None
    company_industry: str | None = None
    company_size: str | None = None
    labels: list[str] = field(default_factory=list)
    recruiter_name: str | None = None
    recruiter_title: str | None = None
    captured_at: str = field(default_factory=utc_now_iso)
    record_id: str = ""

    def __post_init__(self) -> None:
        self.title = clean_text(self.title) or ""
        if not self.title:
            raise ValueError("LiepinJobRecord.title must not be empty")
        for name in (
            "salary_raw",
            "location",
            "experience",
            "education",
            "company_name",
            "company_industry",
            "company_size",
            "recruiter_name",
            "recruiter_title",
            "job_kind",
        ):
            setattr(self, name, clean_text(getattr(self, name)))
        self.labels = [value for item in self.labels if (value := clean_text(item))]
        if not self.record_id:
            source = f"{self.job_id}|{self.category.url}"
            digest = sha256(source.encode("utf-8")).hexdigest()[:20]
            self.record_id = f"liepin-{digest}"

    @property
    def dedupe_key(self) -> str:
        return f"{self.category.url}|{self.job_id}"

    def to_unified_dict(self) -> dict[str, Any]:
        price_evidence: list[dict[str, Any]] = []
        if self.salary_raw:
            price_evidence.append(
                {
                    "raw_text": self.salary_raw,
                    "price_type": "salary_range",
                    "price_source": "list_page",
                    "evidence_url": self.detail_url,
                    "evidence_location": "career_page_job_card",
                    "package_name": None,
                    "currency_text": "CNY",
                    "period_text": "monthly_salary",
                    "negotiable": "面议" in self.salary_raw,
                    "estimated_by_platform": False,
                    "captured_at": self.captured_at,
                }
            )
        return {
            "site_name": "liepin",
            "content_category": "full_time_job",
            "title_raw": self.title,
            "list_url": self.category.url,
            "detail_url": self.detail_url,
            "page_no": self.page_no,
            "list_position": self.list_position,
            "price_evidence": price_evidence,
            "detail_title_raw": None,
            "category_level_1_raw": self.category.level_1,
            "category_level_2_raw": self.category.level_2,
            "category_level_3_raw": self.category.level_3,
            "category_path_raw": self.category.path,
            "category_source": "liepin_home_career_taxonomy",
            "category_source_url": self.category.url,
            "category_identity_verified": True,
            "status_raw": None,
            "job_type_raw": self.job_kind,
            "budget_raw": self.salary_raw,
            "work_period_raw": None,
            "description_raw": None,
            "publisher_name_raw": self.company_name,
            "location_raw": self.location,
            "published_at_raw": None,
            "capture_method": "static_html",
            "source_state": "active_unknown",
            "metadata": {
                "source_job_id": self.job_id,
                "quota_salary_eligible": self.has_nonzero_numeric_salary,
                "experience": self.experience,
                "education": self.education,
                "company_industry": self.company_industry,
                "company_size": self.company_size,
                "labels": self.labels,
                "recruiter_name": self.recruiter_name,
                "recruiter_title": self.recruiter_title,
            },
            "captured_at": self.captured_at,
            "record_id": self.record_id,
        }

    @property
    def has_nonzero_numeric_salary(self) -> bool:
        if not self.salary_raw or "面议" in self.salary_raw:
            return False
        salary_part = re.split(r"[·•]", self.salary_raw, maxsplit=1)[0]
        return any(
            float(value) > 0
            for value in re.findall(r"\d+(?:\.\d+)?", salary_part)
        )


@dataclass(slots=True)
class CrawlSummary:
    status: str
    stop_reason: str
    categories_total: int
    categories_attempted_this_run: int
    categories_with_records: int
    categories_empty: int
    categories_failed: int
    categories_pending: int
    records_total: int
    unique_source_jobs: int
    output_path: str
    coverage_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
