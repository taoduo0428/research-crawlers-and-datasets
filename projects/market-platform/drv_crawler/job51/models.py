from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class CategoryPath:
    """One official 51job work-function path."""

    level_1_code: str
    level_1_name: str
    level_2_code: str
    level_2_name: str
    level_3_code: str
    level_3_name: str
    ordinal: int = 0

    @property
    def search_code(self) -> str:
        return self.level_3_code

    @property
    def path_key(self) -> str:
        return "/".join(
            (self.level_1_code, self.level_2_code, self.level_3_code)
        )

    @property
    def path_name(self) -> str:
        return " > ".join(
            (self.level_1_name, self.level_2_name, self.level_3_name)
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["search_code"] = self.search_code
        value["path_key"] = self.path_key
        value["path_name"] = self.path_name
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CategoryPath":
        return cls(
            level_1_code=str(value["level_1_code"]),
            level_1_name=str(value["level_1_name"]),
            level_2_code=str(value["level_2_code"]),
            level_2_name=str(value["level_2_name"]),
            level_3_code=str(value["level_3_code"]),
            level_3_name=str(value["level_3_name"]),
            ordinal=int(value.get("ordinal", 0)),
        )


@dataclass(slots=True)
class CrawlConfig:
    """Bounded runtime configuration for one resumable crawl scope."""

    city_code: str = "000000"
    per_category_limit: int = 3
    max_pages_per_category: int = 2
    max_codes: int | None = None
    page_size: int = 20
    numeric_salary_only: bool = False
    request_retries: int = 2
    max_category_attempts: int = 3
    timeout_ms: int = 45_000
    delay_min_seconds: float = 3.0
    delay_max_seconds: float = 5.0
    batch_size: int = 20
    batch_cooldown_seconds: float = 60.0
    soft_block_cooldown_seconds: float = 900.0
    headless: bool = True
    proxy_policy: str = "auto"
    proxy_url: str | None = None
    browser_channel: str | None = None
    persistent_profile_dir: Path | None = None
    storage_state_path: Path = PACKAGE_DIR / ".state" / "storage_state.json"
    runtime_session_state_path: Path = (
        PACKAGE_DIR / ".state" / "runtime_session_state.json"
    )
    category_snapshot_path: Path = PACKAGE_DIR / ".state" / "categories.json"
    state_db_path: Path = PACKAGE_DIR / ".state" / "crawl.sqlite3"
    output_path: Path = PACKAGE_DIR / "output" / "jobs.jsonl"
    coverage_path: Path = PACKAGE_DIR / "output" / "coverage.json"

    def __post_init__(self) -> None:
        self.city_code = str(self.city_code).strip()
        if not self.city_code:
            raise ValueError("city_code must not be empty")
        if not 1 <= self.per_category_limit <= 100:
            raise ValueError("per_category_limit must be between 1 and 100")
        if not 1 <= self.max_pages_per_category <= 20:
            raise ValueError("max_pages_per_category must be between 1 and 20")
        if self.max_codes is not None and self.max_codes < 1:
            raise ValueError("max_codes must be positive when provided")
        if self.page_size != 20:
            raise ValueError("page_size must be 20 because the live client fixes it to 20")
        if not isinstance(self.numeric_salary_only, bool):
            raise ValueError("numeric_salary_only must be a boolean")
        required_pages = (
            self.per_category_limit + self.page_size - 1
        ) // self.page_size
        self.max_pages_per_category = (
            20
            if self.numeric_salary_only
            else max(self.max_pages_per_category, required_pages)
        )
        if not 0 <= self.request_retries <= 5:
            raise ValueError("request_retries must be between 0 and 5")
        if not 1 <= self.max_category_attempts <= 10:
            raise ValueError("max_category_attempts must be between 1 and 10")
        if not 5_000 <= self.timeout_ms <= 180_000:
            raise ValueError("timeout_ms must be between 5000 and 180000")
        if self.delay_min_seconds < 0:
            raise ValueError("delay_min_seconds must be non-negative")
        if self.delay_max_seconds < self.delay_min_seconds:
            raise ValueError("delay_max_seconds must be >= delay_min_seconds")
        if not 1 <= self.batch_size <= 200:
            raise ValueError("batch_size must be between 1 and 200")
        if not 0 <= self.batch_cooldown_seconds <= 3_600:
            raise ValueError("batch_cooldown_seconds must be between 0 and 3600")
        if not 30 <= self.soft_block_cooldown_seconds <= 3_600:
            raise ValueError("soft_block_cooldown_seconds must be between 30 and 3600")
        if self.proxy_policy not in {"auto", "off", "required"}:
            raise ValueError("proxy_policy must be auto, off, or required")
        if self.persistent_profile_dir is not None:
            self.persistent_profile_dir = Path(
                self.persistent_profile_dir
            ).expanduser().resolve()
        for name in (
            "storage_state_path",
            "runtime_session_state_path",
            "category_snapshot_path",
            "state_db_path",
            "output_path",
            "coverage_path",
        ):
            setattr(self, name, Path(getattr(self, name)).expanduser().resolve())
        if self.runtime_session_state_path == self.storage_state_path:
            raise ValueError(
                "runtime_session_state_path must differ from storage_state_path"
            )

    def run_scope(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "site": "51job",
            "city_code": self.city_code,
            "per_category_limit": self.per_category_limit,
            "numeric_salary_only": self.numeric_salary_only,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CrawlConfig":
        fields = cls.__dataclass_fields__
        unknown = sorted(set(value) - set(fields))
        if unknown:
            raise ValueError(f"unknown config keys: {', '.join(unknown)}")
        return cls(**dict(value))
