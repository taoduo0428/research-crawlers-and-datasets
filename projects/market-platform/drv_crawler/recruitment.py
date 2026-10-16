from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any, BinaryIO, Iterator

from .core import (
    ProjectConfig,
    atomic_write_json,
    atomic_write_text,
    resolve_proxy,
    safe_error_message,
)
from .exporters import write_csv, write_xlsx
from .models import CrawlRecord, PriceEvidence


RECRUITMENT_SITES = ("boss", "job51", "liepin")
AUTO_RESUME_SITES = frozenset(("boss", "job51"))
NO_COOLDOWN_RESUME_REASONS = {
    "boss": frozenset(("process_completed", "process_timeout")),
    "job51": frozenset(("work_queue_exhausted", "max_codes_reached")),
}
DEFAULT_AUTO_RESUME_MAX_CYCLES = 8
DEFAULT_AUTO_RESUME_MAX_RUNTIME_SECONDS = 86_400
BOSS_POSITION_PAGE_SIZE = 30
BOSS_MAX_PAGES_PER_POSITION = 10
QUOTA_TERMINAL_STATUSES = (
    "quota_met",
    "exhausted_below_quota",
    "empty",
    "page_limit_below_quota",
    "failed",
)


class RecruitmentScopeBusy(RuntimeError):
    """Another process currently owns the same persistent crawl scope."""


def _try_lock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _scope_run_lock(
    scope_dir: Path, *, lock_name: str = ".run.lock"
) -> Iterator[Path]:
    """Hold a non-blocking kernel lock; stale files are harmless after a crash."""
    if Path(lock_name).name != lock_name:
        raise ValueError("lock_name must be a file name")
    scope_dir.mkdir(parents=True, exist_ok=True)
    lock_path = scope_dir / lock_name
    handle = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        _try_lock_file(handle)
    except OSError as exc:
        handle.close()
        raise RecruitmentScopeBusy(
            f"recruitment scope is already running: {scope_dir.resolve()}"
        ) from exc
    try:
        yield lock_path
    finally:
        try:
            _unlock_file(handle)
        finally:
            handle.close()


@dataclass(slots=True)
class RecruitmentSiteOutcome:
    site_name: str
    status: str
    records: int
    coverage_rate: float | None
    stop_reason: str
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _section(config: ProjectConfig) -> dict[str, Any]:
    value = config.section("recruitment")
    sites = value.get("sites")
    if not isinstance(sites, dict):
        raise ValueError("crawler.recruitment.sites must be a mapping")
    return value


def enabled_recruitment_sites(config: ProjectConfig) -> list[str]:
    sites = _section(config)["sites"]
    return [
        name
        for name in RECRUITMENT_SITES
        if isinstance(sites.get(name), dict) and sites[name].get("enabled", True)
    ]


def _site_config(config: ProjectConfig, site_name: str) -> dict[str, Any]:
    if site_name not in RECRUITMENT_SITES:
        raise ValueError(f"unsupported recruitment site: {site_name}")
    value = _section(config)["sites"].get(site_name)
    if not isinstance(value, dict) or not value.get("enabled", True):
        raise ValueError(f"recruitment site is disabled or missing: {site_name}")
    return value


def _numeric_salary_only(
    config: ProjectConfig,
    site_name: str,
    override: bool | None,
) -> bool:
    if override is not None:
        if not isinstance(override, bool):
            raise ValueError("numeric_salary_only override must be true or false")
        return override
    site = _site_config(config, site_name)
    key = "numeric_price_only" if site_name == "boss" else "numeric_salary_only"
    value = site.get(key, site_name == "boss")
    if not isinstance(value, bool):
        raise ValueError(f"{site_name}.{key} must be true or false")
    return value


def _positive_int(value: Any, *, name: str, maximum: int | None = None) -> int:
    parsed = int(value)
    if parsed < 1 or (maximum is not None and parsed > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be >= 1{suffix}")
    return parsed


def _bounded_nonnegative_float(value: Any, *, name: str, maximum: float) -> float:
    parsed = float(value)
    if parsed < 0 or parsed > maximum:
        raise ValueError(f"{name} must be >= 0 and <= {maximum:g}")
    return parsed


def _proxy_settings(
    config: ProjectConfig, site: dict[str, Any]
) -> tuple[str, str | None]:
    shared = config.section("proxy")
    policy = os.getenv(
        "CRAWLER_PROXY_MODE",
        str(site.get("proxy_policy", shared.get("mode", "auto"))),
    ).lower()
    url_env = str(shared.get("url_env", "CRAWLER_PROXY_URL"))
    proxy_url = site.get("proxy_url") or os.getenv(
        url_env, str(shared.get("default_url", "http://127.0.0.1:7897"))
    )
    return policy, str(proxy_url) if proxy_url else None


def _adapter_proxy_policy(policy: str, site_name: str) -> str:
    """Map the shared disabled-proxy spelling to each adapter's public API."""
    if policy in {"off", "direct"}:
        return "off" if site_name == "job51" else "direct"
    return policy


def _config_relative_path(config: ProjectConfig, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return config.path.resolve().parent / path


def _scope_dir(
    state_root: Path,
    site_name: str,
    *,
    per_category: int,
    scope: dict[str, Any],
) -> Path:
    payload = json.dumps(
        {"per_category": per_category, **scope},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()[:12]
    return state_root / site_name / f"scope-{digest}"


def _recruitment_scope_dir(
    config: ProjectConfig,
    state_root: Path,
    site_name: str,
    *,
    per_category: int,
    numeric_salary_only: bool | None = None,
) -> Path:
    site = _site_config(config, site_name)
    numeric_only = _numeric_salary_only(config, site_name, numeric_salary_only)
    if site_name == "boss":
        city = str(site.get("city", "全国"))
        configured_pages = _positive_int(
            site.get("pages_per_category", 1),
            name="boss.pages_per_category",
            maximum=BOSS_MAX_PAGES_PER_POSITION,
        )
        required_pages = math.ceil(per_category / BOSS_POSITION_PAGE_SIZE)
        pages = (
            BOSS_MAX_PAGES_PER_POSITION
            if required_pages > 1
            else max(configured_pages, required_pages)
        )
        scope = {
            "city": city,
            "pages": pages,
            "numeric": numeric_only,
        }
    elif site_name == "job51":
        scope = {"city_code": str(site.get("city_code", "000000"))}
        # Keep the legacy all-salary scope stable while separating numeric runs.
        if numeric_only:
            scope["numeric_salary_only"] = True
    else:
        scope = {
            "home_url": str(site.get("home_url", "https://www.liepin.com/"))
        }
        if numeric_only:
            scope["numeric_salary_only"] = True
    return _scope_dir(
        state_root,
        site_name,
        per_category=per_category,
        scope=scope,
    )


def _record_from_dict(value: dict[str, Any]) -> CrawlRecord:
    evidence_rows = value.get("price_evidence")
    evidence_rows = evidence_rows if isinstance(evidence_rows, list) else []
    evidence: list[PriceEvidence] = []
    for row in evidence_rows:
        if not isinstance(row, dict) or not row.get("raw_text"):
            continue
        evidence.append(
            PriceEvidence(
                raw_text=str(row["raw_text"]),
                price_type=str(row.get("price_type") or "unknown"),
                price_source=str(row.get("price_source") or "unknown"),
                evidence_url=str(
                    row.get("evidence_url")
                    or value.get("detail_url")
                    or value.get("list_url")
                    or ""
                ),
                evidence_location=row.get("evidence_location"),
                package_name=row.get("package_name"),
                currency_text=row.get("currency_text"),
                period_text=row.get("period_text"),
                negotiable=bool(row.get("negotiable", False)),
                estimated_by_platform=bool(row.get("estimated_by_platform", False)),
                captured_at=str(
                    row.get("captured_at")
                    or value.get("captured_at")
                    or datetime.now().astimezone().isoformat()
                ),
            )
        )
    title = str(value.get("title_raw") or value.get("title") or "").strip()
    list_url = str(
        value.get("list_url")
        or value.get("category_source_url")
        or value.get("detail_url")
        or ""
    )
    metadata = dict(value.get("metadata")) if isinstance(value.get("metadata"), dict) else {}
    for key in (
        "category_paths",
        "category_paths_raw",
        "category_matches",
        "position_code",
        "position_name",
        "category_level_1_code",
        "category_level_2_code",
        "category_level_3_code",
    ):
        if key in value and key not in metadata:
            metadata[key] = value[key]
    return CrawlRecord(
        site_name=str(value.get("site_name") or "unknown"),
        content_category=str(value.get("content_category") or "full_time_job"),
        title_raw=title,
        list_url=list_url,
        detail_url=value.get("detail_url") or value.get("job_link"),
        page_no=int(value.get("page_no") or 1),
        list_position=int(value.get("list_position") or 1),
        price_evidence=evidence,
        detail_title_raw=value.get("detail_title_raw"),
        category_level_1_raw=value.get("category_level_1_raw"),
        category_level_2_raw=value.get("category_level_2_raw"),
        category_level_3_raw=value.get("category_level_3_raw"),
        category_path_raw=value.get("category_path_raw"),
        category_source=value.get("category_source"),
        category_source_url=value.get("category_source_url"),
        category_identity_verified=bool(value.get("category_identity_verified", False)),
        status_raw=value.get("status_raw"),
        job_type_raw=value.get("job_type_raw"),
        budget_raw=value.get("budget_raw") or value.get("salary"),
        work_period_raw=value.get("work_period_raw"),
        description_raw=value.get("description_raw"),
        publisher_name_raw=value.get("publisher_name_raw") or value.get("company"),
        location_raw=value.get("location_raw") or value.get("location"),
        published_at_raw=value.get("published_at_raw"),
        capture_method=str(value.get("capture_method") or "unknown"),
        source_state=str(value.get("source_state") or "active_unknown"),
        metadata=metadata,
        captured_at=str(value.get("captured_at") or datetime.now().astimezone().isoformat()),
        record_id=str(value.get("record_id") or ""),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number} contains invalid JSON: "
                    f"{exc.msg} at column {exc.colno}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            records.append(value)
    return records


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _export_site_snapshot(
    run_dir: Path,
    site_name: str,
    records: list[dict[str, Any]],
    coverage: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    site_dir = run_dir / site_name
    content = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    atomic_write_text(site_dir / "records.jsonl", content)
    parsed_records: list[CrawlRecord] = []
    export_errors: list[dict[str, Any]] = []
    for index, record in enumerate(records, 1):
        try:
            parsed_records.append(_record_from_dict(record))
        except (TypeError, ValueError) as exc:
            export_errors.append(
                {"record_index": index, "error": safe_error_message(str(exc))[:300]}
            )
    write_csv(site_dir / "records.csv", parsed_records)
    write_xlsx(site_dir / "records.xlsx", parsed_records)
    atomic_write_json(site_dir / "coverage.json", coverage)
    atomic_write_json(
        site_dir / "summary.json",
        {**summary, "records_exported": len(parsed_records), "export_errors": export_errors},
    )


def _coverage_rate(coverage: dict[str, Any]) -> float | None:
    for key in (
        "coverage_rate",
        "position_code_coverage_rate",
        "category_coverage_rate",
    ):
        value = coverage.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    summary = coverage.get("summary")
    if isinstance(summary, dict):
        total = summary.get("categories_total")
        done = (summary.get("categories_done") or 0) + (summary.get("categories_empty") or 0)
        if isinstance(total, int) and total:
            return round(float(done) / total, 6)
    return None


def _normalized_quota_summary(coverage: dict[str, Any]) -> dict[str, Any] | None:
    source = coverage.get("quota_counts")
    source_name = "quota_counts"
    if not isinstance(source, dict):
        source = coverage.get("quota_summary")
        source_name = "quota_summary"
    if not isinstance(source, dict):
        source = coverage.get("quota_outcomes")
        source_name = "quota_outcomes"
    if not isinstance(source, dict):
        return None

    summary = coverage.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    run_config = coverage.get("run_config")
    run_config = run_config if isinstance(run_config, dict) else {}
    total = int(
        source.get("unique_search_codes_total")
        or coverage.get("position_codes_total")
        or summary.get("categories_total")
        or sum(int(source.get(key) or 0) for key in (*QUOTA_TERMINAL_STATUSES, "pending", "running"))
    )
    pending = int(source.get("pending") or 0) + int(source.get("running") or 0)
    if source_name == "quota_summary":
        pending = int(coverage.get("position_codes_pending") or 0) + int(
            coverage.get("position_codes_deferred") or 0
        )
    target = int(
        source.get("target_per_unique_search_code")
        or source.get("quota_target")
        or coverage.get("quota_target")
        or 0
    )
    quota_met = int(source.get("quota_met") or 0)
    return {
        "target_per_category": target,
        "categories_total": total,
        "numeric_salary_only": bool(
            source.get("numeric_salary_only")
            if "numeric_salary_only" in source
            else (
                coverage.get("numeric_salary_only")
                if "numeric_salary_only" in coverage
                else run_config.get(
                    "numeric_salary_only",
                    run_config.get("numeric_price_only", False),
                )
            )
        ),
        **{key: int(source.get(key) or 0) for key in QUOTA_TERMINAL_STATUSES},
        "pending": pending,
        "quota_met_rate": (
            float(coverage["quota_met_rate"])
            if isinstance(coverage.get("quota_met_rate"), (int, float))
            else (0.0 if total == 0 else round(quota_met / total, 6))
        ),
    }


def _recover_site_snapshot(
    run_dir: Path,
    site_name: str,
    output_path: Path,
    coverage_path: Path,
    error: Exception,
    *,
    records_before: int,
) -> RecruitmentSiteOutcome:
    """Expose valid checkpoint output even when the current adapter startup fails."""
    records = _read_jsonl(output_path)
    coverage = _read_json(coverage_path)
    summary = {
        "status": "partial" if records else "failed",
        "stop_reason": type(error).__name__,
        "error_type": type(error).__name__,
        "message": safe_error_message(str(error))[:1000],
        "records_recovered_from_checkpoint": len(records),
        "records_added_this_run": max(0, len(records) - records_before),
    }
    _export_site_snapshot(run_dir, site_name, records, coverage, summary)
    return RecruitmentSiteOutcome(
        site_name=site_name,
        status=str(summary["status"]),
        records=len(records),
        coverage_rate=_coverage_rate(coverage),
        stop_reason=type(error).__name__,
        summary=summary,
    )


def _status_from_coverage(
    coverage: dict[str, Any], *, records: int, command_ok: bool
) -> str:
    rate = _coverage_rate(coverage)
    quota = _normalized_quota_summary(coverage)
    page_limited = bool(quota and quota["page_limit_below_quota"])
    if command_ok and rate is not None and rate >= 1.0 and not page_limited:
        return "success"
    if records or command_ok:
        return "partial"
    return "failed"


def _cooldown_seconds_from_payload(value: object) -> float:
    if not isinstance(value, dict):
        return 0.0
    raw_until = str(value.get("cooldown_until") or "").strip()
    if raw_until:
        try:
            target = datetime.fromisoformat(raw_until.replace("Z", "+00:00"))
            remaining = target.timestamp() - time.time()
        except (OSError, OverflowError, ValueError):
            pass
        else:
            return max(0.0, remaining) if math.isfinite(remaining) else 0.0
    try:
        remaining = float(value.get("remaining_seconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return remaining if math.isfinite(remaining) and remaining > 0 else 0.0


def _site_cooldown_remaining(site_name: str, scope_dir: Path) -> float:
    if site_name == "job51":
        coverage = _read_json(scope_dir / "coverage.json")
        return _cooldown_seconds_from_payload(coverage.get("rate_limit_cooldown"))
    if site_name == "boss":
        checkpoint = _read_json(scope_dir / "checkpoint.json")
        return _cooldown_seconds_from_payload(checkpoint.get("site_restriction"))
    return 0.0


def _site_remaining_work(
    config: ProjectConfig, site_name: str, scope_dir: Path
) -> int | None:
    if site_name == "job51":
        coverage = _read_json(scope_dir / "coverage.json")
        categories = coverage.get("categories")
        if isinstance(categories, list) and categories:
            max_attempts = int(
                _site_config(config, "job51").get("max_category_attempts", 3)
            )
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in categories:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("search_code") or "").strip()
                if code:
                    grouped.setdefault(code, []).append(row)
            remaining = 0
            for rows in grouped.values():
                if any(
                    str(row.get("status") or "") in {"pending", "partial", "running"}
                    or (
                        str(row.get("status") or "") == "failed"
                        and int(row.get("attempts") or 0) < max_attempts
                    )
                    for row in rows
                ):
                    remaining += 1
            return remaining
        status_counts = coverage.get("status_counts")
        if isinstance(status_counts, dict):
            return sum(
                int(status_counts.get(status) or 0)
                for status in ("pending", "partial", "running")
            )
        return None
    if site_name == "boss":
        checkpoint = _read_json(scope_dir / "checkpoint.json")
        if not checkpoint:
            return None
        catalog = checkpoint.get("catalog")
        catalog_codes = {
            str(row.get("position_code") or "").strip()
            for row in catalog
            if isinstance(row, dict) and str(row.get("position_code") or "").strip()
        } if isinstance(catalog, list) else set()
        completed = {
            str(code).strip()
            for code in checkpoint.get("completed_position_codes", [])
            if str(code).strip()
        }
        failed = checkpoint.get("failed_positions")
        failed = failed if isinstance(failed, dict) else {}
        terminal_failed = {
            str(code)
            for code, detail in failed.items()
            if isinstance(detail, dict) and detail.get("terminal") is True
        }
        if catalog_codes:
            return len(catalog_codes - completed - terminal_failed)
        deferred = checkpoint.get("deferred_positions")
        deferred = deferred if isinstance(deferred, dict) else {}
        retryable_failed = set(map(str, failed)) - terminal_failed
        if deferred or retryable_failed:
            return len(set(map(str, deferred)) | retryable_failed)
        if any(
            key in checkpoint
            for key in ("completed_position_codes", "failed_positions", "deferred_positions")
        ):
            return 0
        return None
    return None


def _annotate_auto_resume(
    outcome: RecruitmentSiteOutcome,
    *,
    cycles: int,
    sleeps: int,
    waited_seconds: float,
    records_added: int,
    stop_reason: str | None = None,
    cooldown_remaining: float = 0.0,
    remaining_work: int | None = None,
) -> RecruitmentSiteOutcome:
    summary = dict(outcome.summary)
    summary.update(
        {
            "auto_resume_enabled": True,
            "auto_resume_cycles": cycles,
            "auto_resume_sleeps": sleeps,
            "auto_resume_wait_seconds": round(waited_seconds, 3),
            "records_added_this_run": records_added,
            "remaining_work": remaining_work,
        }
    )
    if stop_reason is None:
        return RecruitmentSiteOutcome(
            site_name=outcome.site_name,
            status=outcome.status,
            records=outcome.records,
            coverage_rate=outcome.coverage_rate,
            stop_reason=outcome.stop_reason,
            summary=summary,
        )
    summary.update(
        {
            "adapter_stop_reason": outcome.stop_reason,
            "stop_reason": stop_reason,
            "cooldown_remaining_seconds": round(cooldown_remaining, 3),
        }
    )
    status = "partial" if outcome.records or (outcome.coverage_rate or 0) > 0 else "failed"
    return RecruitmentSiteOutcome(
        site_name=outcome.site_name,
        status=status,
        records=outcome.records,
        coverage_rate=outcome.coverage_rate,
        stop_reason=stop_reason,
        summary=summary,
    )


def _run_with_auto_resume(
    runner: Any,
    config: ProjectConfig,
    run_dir: Path,
    state_root: Path,
    scope_dir: Path,
    *,
    site_name: str,
    per_category: int,
    numeric_salary_only: bool | None,
    max_cycles: int,
    max_runtime_seconds: int,
) -> RecruitmentSiteOutcome:
    started = time.monotonic()
    cycles = 0
    sleeps = 0
    waited_seconds = 0.0
    records_added = 0
    while True:
        outcome = runner(
            config,
            run_dir,
            state_root,
            per_category=per_category,
            max_categories=None,
            numeric_salary_only=numeric_salary_only,
        )
        cycles += 1
        records_added += int(outcome.summary.get("records_added_this_run") or 0)
        if outcome.status == "success" or (outcome.coverage_rate or 0) >= 1.0:
            return _annotate_auto_resume(
                outcome,
                cycles=cycles,
                sleeps=sleeps,
                waited_seconds=waited_seconds,
                records_added=records_added,
            )
        cooldown = _site_cooldown_remaining(site_name, scope_dir)
        remaining_work = _site_remaining_work(config, site_name, scope_dir)
        can_resume_without_cooldown = outcome.stop_reason in NO_COOLDOWN_RESUME_REASONS[
            site_name
        ]
        if cooldown <= 0 and (
            remaining_work is None
            or remaining_work <= 0
            or not can_resume_without_cooldown
        ):
            return _annotate_auto_resume(
                outcome,
                cycles=cycles,
                sleeps=sleeps,
                waited_seconds=waited_seconds,
                records_added=records_added,
                remaining_work=remaining_work,
            )
        if cycles >= max_cycles:
            return _annotate_auto_resume(
                outcome,
                cycles=cycles,
                sleeps=sleeps,
                waited_seconds=waited_seconds,
                records_added=records_added,
                stop_reason="full_probe_cycle_limit",
                cooldown_remaining=cooldown,
                remaining_work=remaining_work,
            )
        runtime_remaining = max_runtime_seconds - (time.monotonic() - started)
        if runtime_remaining <= 0 or cooldown >= runtime_remaining:
            return _annotate_auto_resume(
                outcome,
                cycles=cycles,
                sleeps=sleeps,
                waited_seconds=waited_seconds,
                records_added=records_added,
                stop_reason="full_probe_runtime_limit",
                cooldown_remaining=cooldown,
                remaining_work=remaining_work,
            )
        if cooldown > 0:
            time.sleep(cooldown)
            sleeps += 1
            waited_seconds += cooldown


def _run_boss(
    config: ProjectConfig,
    run_dir: Path,
    state_root: Path,
    *,
    per_category: int,
    max_categories: int | None,
    numeric_salary_only: bool | None = None,
) -> RecruitmentSiteOutcome:
    site = _site_config(config, "boss")
    city = str(site.get("city", "全国"))
    configured_pages = _positive_int(
        site.get("pages_per_category", 1),
        name="boss.pages_per_category",
        maximum=BOSS_MAX_PAGES_PER_POSITION,
    )
    required_pages = math.ceil(per_category / BOSS_POSITION_PAGE_SIZE)
    pages = (
        BOSS_MAX_PAGES_PER_POSITION
        if required_pages > 1
        else max(configured_pages, required_pages)
    )
    numeric_only = _numeric_salary_only(config, "boss", numeric_salary_only)
    cdp_port = _positive_int(site.get("cdp_port", 9222), name="boss.cdp_port", maximum=65535)
    category_interval_seconds = _bounded_nonnegative_float(
        site.get("category_interval_seconds", 12.0),
        name="boss.category_interval_seconds",
        maximum=300.0,
    )
    category_jitter_seconds = _bounded_nonnegative_float(
        site.get("category_jitter_seconds", 8.0),
        name="boss.category_jitter_seconds",
        maximum=300.0,
    )
    block_cooldown_seconds = _bounded_nonnegative_float(
        site.get("block_cooldown_seconds", 3600.0),
        name="boss.block_cooldown_seconds",
        maximum=14_400.0,
    )
    page_delay_min_seconds = _bounded_nonnegative_float(
        site.get("page_delay_min_seconds", 4.0),
        name="boss.page_delay_min_seconds",
        maximum=60.0,
    )
    page_delay_max_seconds = _bounded_nonnegative_float(
        site.get("page_delay_max_seconds", 8.0),
        name="boss.page_delay_max_seconds",
        maximum=60.0,
    )
    if page_delay_min_seconds > page_delay_max_seconds:
        raise ValueError("boss page delay min must be <= max")
    batch_size = _positive_int(
        site.get("batch_size", 50), name="boss.batch_size", maximum=1200,
    )
    batch_cooldown_seconds = _bounded_nonnegative_float(
        site.get("batch_cooldown_seconds", 90.0),
        name="boss.batch_cooldown_seconds",
        maximum=14_400.0,
    )
    max_consecutive_errors = _positive_int(
        site.get("max_consecutive_errors", 2),
        name="boss.max_consecutive_errors",
        maximum=20,
    )
    if category_interval_seconds + category_jitter_seconds > 300.0:
        raise ValueError("boss category interval plus jitter must be <= 300 seconds")
    scope_dir = _recruitment_scope_dir(
        config,
        state_root,
        "boss",
        per_category=per_category,
        numeric_salary_only=numeric_only,
    ).resolve()
    scope_dir.mkdir(parents=True, exist_ok=True)
    output_path = scope_dir / "jobs.json"
    checkpoint_path = scope_dir / "checkpoint.json"
    coverage_path = scope_dir / "coverage.json"
    existing_payload = _read_json(output_path)
    existing_jobs = (
        existing_payload.get("jobs")
        if isinstance(existing_payload.get("jobs"), list)
        else []
    )
    records_before = sum(isinstance(item, dict) for item in existing_jobs)
    script = Path(__file__).resolve().parent / "boss_cdp" / "scripts" / "boss_cdp_raw.py"
    cdp_profile_dir = _config_relative_path(
        config,
        site.get("cdp_profile_dir", ".crawler-state/boss-new-account-profile"),
    ).resolve()
    auto_start_cdp = site.get("auto_start_cdp", False)
    if not isinstance(auto_start_cdp, bool):
        raise ValueError("boss.auto_start_cdp must be true or false")
    command = [
        sys.executable,
        os.fspath(script),
        "--all-positions",
        "--city",
        city,
        "--pages",
        str(pages),
        "--per-position-limit",
        str(per_category),
        "--category-interval-seconds",
        str(category_interval_seconds),
        "--category-jitter-seconds",
        str(category_jitter_seconds),
        "--page-delay-min-seconds",
        str(page_delay_min_seconds),
        "--page-delay-max-seconds",
        str(page_delay_max_seconds),
        "--batch-size",
        str(batch_size),
        "--batch-cooldown-seconds",
        str(batch_cooldown_seconds),
        "--max-consecutive-errors",
        str(max_consecutive_errors),
        "--block-cooldown-seconds",
        str(block_cooldown_seconds),
        "--cdp-port",
        str(cdp_port),
        "--cdp-data-dir",
        os.fspath(cdp_profile_dir),
        "--output",
        os.fspath(output_path),
        "--checkpoint",
        os.fspath(checkpoint_path),
        "--coverage-output",
        os.fspath(coverage_path),
        "--format",
        "json",
    ]
    if numeric_only:
        command.append("--numeric-price-only")
    if max_categories is not None:
        command.extend(("--max-positions", str(max_categories)))
    run_timeout_seconds = _positive_int(
        site.get("run_timeout_seconds", 21600),
        name="boss.run_timeout_seconds",
        maximum=86_400,
    )
    environment = dict(os.environ)
    environment.setdefault("PYTHONUTF8", "1")
    if auto_start_cdp:
        setup_command = [
            sys.executable,
            os.fspath(script),
            "--setup-chrome",
            "--no-wait-login",
            "--cdp-port",
            str(cdp_port),
            "--cdp-data-dir",
            os.fspath(cdp_profile_dir),
        ]
        setup = subprocess.run(
            setup_command,
            cwd=config.path.resolve().parent,
            env=environment,
            check=False,
            timeout=60,
        )
        if setup.returncode != 0:
            raise RuntimeError(
                f"BOSS CDP startup failed for configured profile (exit {setup.returncode})"
            )
    try:
        completed = subprocess.run(
            command,
            cwd=config.path.resolve().parent,
            env=environment,
            check=False,
            timeout=run_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        payload = _read_json(output_path)
        jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
        records = [item for item in jobs if isinstance(item, dict)]
        coverage = _read_json(coverage_path)
        checkpoint = _read_json(checkpoint_path)
        summary = {
            "return_code": None,
            "timed_out": True,
            "run_timeout_seconds": run_timeout_seconds,
            "persistent_state_dir": os.fspath(scope_dir),
            "checkpoint_path": os.fspath(checkpoint_path),
            "checkpoint_present": checkpoint_path.is_file(),
            "records_recovered_from_checkpoint": len(records),
            "records_added_this_run": max(0, len(records) - records_before),
            "numeric_salary_only": numeric_only,
            "pages_per_category": pages,
        }
        _export_site_snapshot(run_dir, "boss", records, coverage, summary)
        if checkpoint_path.is_file():
            atomic_write_json(run_dir / "boss" / "checkpoint.json", checkpoint)
        return RecruitmentSiteOutcome(
            site_name="boss",
            status="partial" if records else "failed",
            records=len(records),
            coverage_rate=_coverage_rate(coverage),
            stop_reason="process_timeout",
            summary=summary,
        )
    if completed.returncode == 0:
        missing = [
            os.fspath(path)
            for path in (output_path, coverage_path)
            if not path.is_file()
        ]
        if missing:
            raise RuntimeError(
                "BOSS process exited successfully without required artifacts: "
                + ", ".join(missing)
            )
    payload = _read_json(output_path)
    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
    records = [item for item in jobs if isinstance(item, dict)]
    coverage = _read_json(coverage_path)
    summary = {
        "return_code": completed.returncode,
        "persistent_state_dir": os.fspath(scope_dir),
        "position_codes_total": payload.get("position_codes_total"),
        "positions_completed": payload.get("positions_completed"),
        "records_total": len(records),
        "records_added_this_run": max(0, len(records) - records_before),
        "numeric_salary_only": numeric_only,
        "pages_per_category": pages,
    }
    _export_site_snapshot(run_dir, "boss", records, coverage, summary)
    return RecruitmentSiteOutcome(
        site_name="boss",
        status=(
            _status_from_coverage(coverage, records=len(records), command_ok=True)
            if completed.returncode == 0
            else "failed"
        ),
        records=len(records),
        coverage_rate=_coverage_rate(coverage),
        stop_reason=(
            "process_completed" if completed.returncode == 0 else f"process_exit_{completed.returncode}"
        ),
        summary=summary,
    )


def _run_job51(
    config: ProjectConfig,
    run_dir: Path,
    state_root: Path,
    *,
    per_category: int,
    max_categories: int | None,
    numeric_salary_only: bool | None = None,
) -> RecruitmentSiteOutcome:
    from .job51 import CrawlConfig, run_job51

    site = _site_config(config, "job51")
    proxy_policy, proxy_url = _proxy_settings(config, site)
    proxy_policy = _adapter_proxy_policy(proxy_policy, "job51")
    city_code = str(site.get("city_code", "000000"))
    numeric_only = _numeric_salary_only(config, "job51", numeric_salary_only)
    scope_dir = _recruitment_scope_dir(
        config,
        state_root,
        "job51",
        per_category=per_category,
        numeric_salary_only=numeric_only,
    )
    scope_dir.mkdir(parents=True, exist_ok=True)
    shared_dir = state_root / "job51"
    shared_dir.mkdir(parents=True, exist_ok=True)
    crawler_config = CrawlConfig(
        city_code=city_code,
        per_category_limit=per_category,
        numeric_salary_only=numeric_only,
        max_pages_per_category=_positive_int(
            site.get("pages_per_category", 2),
            name="job51.pages_per_category",
            maximum=20,
        ),
        max_codes=max_categories,
        request_retries=int(site.get("request_retries", 2)),
        max_category_attempts=int(site.get("max_category_attempts", 3)),
        timeout_ms=int(site.get("timeout_ms", 45_000)),
        delay_min_seconds=float(site.get("delay_min_seconds", 3.0)),
        delay_max_seconds=float(site.get("delay_max_seconds", 5.0)),
        batch_size=_positive_int(
            site.get("batch_size", 20), name="job51.batch_size", maximum=200
        ),
        batch_cooldown_seconds=float(site.get("batch_cooldown_seconds", 60.0)),
        soft_block_cooldown_seconds=float(
            site.get("soft_block_cooldown_seconds", 900.0)
        ),
        headless=bool(site.get("headless", True)),
        proxy_policy=proxy_policy,
        proxy_url=proxy_url,
        browser_channel=site.get("browser_channel") or None,
        persistent_profile_dir=shared_dir / "browser_profile",
        storage_state_path=shared_dir / "storage_state.json",
        runtime_session_state_path=shared_dir / "runtime_session_state.json",
        category_snapshot_path=shared_dir / "categories.json",
        state_db_path=scope_dir / "checkpoint.sqlite3",
        output_path=scope_dir / "jobs.jsonl",
        coverage_path=scope_dir / "coverage.json",
    )
    records_before = len(_read_jsonl(crawler_config.output_path))
    try:
        crawl_summary = run_job51(crawler_config)
    except Exception as exc:
        return _recover_site_snapshot(
            run_dir,
            "job51",
            crawler_config.output_path,
            crawler_config.coverage_path,
            exc,
            records_before=records_before,
        )
    summary = crawl_summary.to_dict()
    records = _read_jsonl(crawler_config.output_path)
    summary["records_added_this_run"] = max(0, len(records) - records_before)
    coverage = _read_json(crawler_config.coverage_path)
    _export_site_snapshot(run_dir, "job51", records, coverage, summary)
    stop_reason = str(summary.get("stop_reason") or "unknown")
    command_ok = stop_reason in {"work_queue_exhausted", "max_codes_reached"}
    return RecruitmentSiteOutcome(
        site_name="job51",
        status=_status_from_coverage(
            coverage, records=len(records), command_ok=command_ok
        ),
        records=len(records),
        coverage_rate=_coverage_rate(coverage),
        stop_reason=stop_reason,
        summary=summary,
    )


def _run_liepin(
    config: ProjectConfig,
    run_dir: Path,
    state_root: Path,
    *,
    per_category: int,
    max_categories: int | None,
    numeric_salary_only: bool | None = None,
) -> RecruitmentSiteOutcome:
    from .liepin import LiepinConfig, LiepinCrawler

    site = _site_config(config, "liepin")
    proxy_policy, proxy_url = _proxy_settings(config, site)
    proxy_policy = _adapter_proxy_policy(proxy_policy, "liepin")
    numeric_only = _numeric_salary_only(config, "liepin", numeric_salary_only)
    scope_dir = _recruitment_scope_dir(
        config,
        state_root,
        "liepin",
        per_category=per_category,
        numeric_salary_only=numeric_only,
    )
    shared_dir = state_root / "liepin"
    shared_dir.mkdir(parents=True, exist_ok=True)
    storage_state_path = shared_dir / "storage_state.json"
    crawler_config = LiepinConfig(
        output_dir=scope_dir,
        home_url=str(site.get("home_url", "https://www.liepin.com/")),
        per_category=per_category,
        numeric_salary_only=numeric_only,
        max_pages_per_category=_positive_int(
            site.get("pages_per_category", 20),
            name="liepin.pages_per_category",
            maximum=20,
        ),
        max_categories=max_categories,
        min_taxonomy_categories=int(site.get("min_taxonomy_categories", 500)),
        delay_min=float(site.get("delay_min_seconds", 0.8)),
        delay_max=float(site.get("delay_max_seconds", 1.8)),
        connect_timeout=float(site.get("connect_timeout_seconds", 5.0)),
        read_timeout=float(site.get("read_timeout_seconds", 20.0)),
        retries=int(site.get("request_retries", 2)),
        max_attempts_per_category=int(site.get("max_category_attempts", 2)),
        max_consecutive_failures=int(site.get("max_consecutive_failures", 5)),
        proxy_policy=proxy_policy,
        proxy_url=proxy_url,
        cookie_file=storage_state_path if storage_state_path.exists() else None,
        refresh_taxonomy=bool(site.get("refresh_taxonomy", False)),
    )
    records_before = len(_read_jsonl(crawler_config.jobs_path))
    try:
        with LiepinCrawler(crawler_config) as crawler:
            crawl_summary = crawler.run()
    except Exception as exc:
        return _recover_site_snapshot(
            run_dir,
            "liepin",
            crawler_config.jobs_path,
            crawler_config.coverage_path,
            exc,
            records_before=records_before,
        )
    summary = crawl_summary.to_dict()
    records = _read_jsonl(crawler_config.jobs_path)
    summary["records_added_this_run"] = max(0, len(records) - records_before)
    coverage = _read_json(crawler_config.coverage_path)
    _export_site_snapshot(run_dir, "liepin", records, coverage, summary)
    return RecruitmentSiteOutcome(
        site_name="liepin",
        status=str(summary.get("status") or "partial"),
        records=len(records),
        coverage_rate=_coverage_rate(coverage),
        stop_reason=str(summary.get("stop_reason") or "unknown"),
        summary=summary,
    )


def run_recruitment(
    config: ProjectConfig,
    *,
    site_name: str = "all",
    per_category: int | None = None,
    max_categories: int | None = None,
    numeric_salary_only: bool | None = None,
    output_root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    section = _section(config)
    per_category = _positive_int(
        per_category if per_category is not None else section.get("per_category", 2),
        name="per_category",
        maximum=50,
    )
    if max_categories is not None:
        max_categories = _positive_int(max_categories, name="max_categories")
    auto_resume_full_probe = section.get("auto_resume_full_probe", True)
    if not isinstance(auto_resume_full_probe, bool):
        raise ValueError("recruitment.auto_resume_full_probe must be true or false")
    auto_resume_max_cycles = _positive_int(
        section.get(
            "full_probe_max_cycles",
            section.get("auto_resume_max_cycles", DEFAULT_AUTO_RESUME_MAX_CYCLES),
        ),
        name="recruitment.full_probe_max_cycles",
        maximum=50,
    )
    auto_resume_max_runtime_seconds = _positive_int(
        section.get(
            "full_probe_max_runtime_seconds",
            section.get(
                "auto_resume_max_runtime_seconds",
                DEFAULT_AUTO_RESUME_MAX_RUNTIME_SECONDS,
            ),
        ),
        name="recruitment.full_probe_max_runtime_seconds",
        maximum=86_400,
    )
    enabled = enabled_recruitment_sites(config)
    if site_name == "all":
        selected = enabled
    elif site_name in enabled:
        selected = [site_name]
    else:
        raise ValueError(f"unknown or disabled recruitment site: {site_name}")
    numeric_modes = {
        site: _numeric_salary_only(config, site, numeric_salary_only)
        for site in selected
    }
    if output_root is None:
        output_root = _config_relative_path(
            config,
            section.get("output_directory")
            or config.section("export").get("directory", "outputs"),
        )
    else:
        output_root = Path(output_root)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    run_dir = output_root / f"jobs-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    state_root = _config_relative_path(
        config, section.get("state_directory", ".crawler-state/recruitment")
    )
    runners = {
        "boss": _run_boss,
        "job51": _run_job51,
        "liepin": _run_liepin,
    }
    outcomes: list[RecruitmentSiteOutcome] = []
    for current_site in selected:
        current_numeric_only = numeric_modes[current_site]
        try:
            scope_dir = _recruitment_scope_dir(
                config,
                state_root,
                current_site,
                per_category=per_category,
                numeric_salary_only=current_numeric_only,
            )
            with (
                _scope_run_lock(scope_dir.parent, lock_name=".site.run.lock"),
                _scope_run_lock(scope_dir),
            ):
                if (
                    max_categories is None
                    and auto_resume_full_probe
                    and current_site in AUTO_RESUME_SITES
                ):
                    outcome = _run_with_auto_resume(
                        runners[current_site],
                        config,
                        run_dir,
                        state_root,
                        scope_dir,
                        site_name=current_site,
                        per_category=per_category,
                        numeric_salary_only=current_numeric_only,
                        max_cycles=auto_resume_max_cycles,
                        max_runtime_seconds=auto_resume_max_runtime_seconds,
                    )
                else:
                    outcome = runners[current_site](
                        config,
                        run_dir,
                        state_root,
                        per_category=per_category,
                        max_categories=max_categories,
                        numeric_salary_only=current_numeric_only,
                    )
        except Exception as exc:
            message = safe_error_message(str(exc))[:1000]
            failure_summary = {
                "site_name": current_site,
                "status": "failed",
                "error_type": type(exc).__name__,
                "message": message,
            }
            atomic_write_json(run_dir / current_site / "summary.json", failure_summary)
            outcome = RecruitmentSiteOutcome(
                site_name=current_site,
                status="failed",
                records=0,
                coverage_rate=None,
                stop_reason=type(exc).__name__,
                summary=failure_summary,
            )
        summary_path = run_dir / current_site / "summary.json"
        coverage = _read_json(run_dir / current_site / "coverage.json")
        quota = _normalized_quota_summary(coverage)
        outcome.summary = {**_read_json(summary_path), **outcome.summary}
        if quota is not None:
            outcome.summary["quota"] = quota
        atomic_write_json(summary_path, outcome.summary)
        outcomes.append(outcome)
    quota_rows = [
        item.summary["quota"]
        for item in outcomes
        if isinstance(item.summary.get("quota"), dict)
    ]
    report = {
        "run_dir": os.fspath(run_dir.resolve()),
        "per_category": per_category,
        "numeric_salary_only": numeric_modes,
        "max_categories_this_run": max_categories,
        "auto_resume": {
            "enabled": (
                auto_resume_full_probe
                and max_categories is None
                and any(site in AUTO_RESUME_SITES for site in selected)
            ),
            "max_cycles": auto_resume_max_cycles,
            "max_runtime_seconds": auto_resume_max_runtime_seconds,
        },
        "sites": [outcome.to_dict() for outcome in outcomes],
        "totals": {
            "sites": len(outcomes),
            "success": sum(item.status == "success" for item in outcomes),
            "partial": sum(item.status == "partial" for item in outcomes),
            "failed": sum(item.status == "failed" for item in outcomes),
            "records": sum(item.records for item in outcomes),
            "records_added_this_run": sum(
                int(item.summary.get("records_added_this_run") or 0)
                for item in outcomes
            ),
            "quota": {
                **{
                    key: sum(int(row.get(key) or 0) for row in quota_rows)
                    for key in (*QUOTA_TERMINAL_STATUSES, "pending")
                },
                "categories_total": sum(
                    int(row.get("categories_total") or 0) for row in quota_rows
                ),
                "quota_met_rate": (
                    0.0
                    if not sum(
                        int(row.get("categories_total") or 0) for row in quota_rows
                    )
                    else round(
                        sum(int(row.get("quota_met") or 0) for row in quota_rows)
                        / sum(
                            int(row.get("categories_total") or 0)
                            for row in quota_rows
                        ),
                        6,
                    )
                ),
            },
        },
    }
    atomic_write_json(run_dir / "run-report.json", report)
    markdown = ["# Recruitment category crawl", ""]
    for item in outcomes:
        line = (
            f"- **{item.site_name}**: {item.status}, records={item.records}, "
            f"added={item.summary.get('records_added_this_run', 0)}, "
            f"coverage={item.coverage_rate}, stop={item.stop_reason}"
        )
        quota = item.summary.get("quota")
        if isinstance(quota, dict):
            line += (
                f", quota_met_rate={quota.get('quota_met_rate')}, "
                f"quota_met={quota.get('quota_met')}, "
                f"exhausted={quota.get('exhausted_below_quota')}, "
                f"empty={quota.get('empty')}, "
                f"page_limited={quota.get('page_limit_below_quota')}, "
                f"failed={quota.get('failed')}, pending={quota.get('pending')}"
            )
        markdown.append(line)
    atomic_write_text(run_dir / "run-report.md", "\n".join(markdown) + "\n")
    return run_dir, report


def recruitment_doctor(config: ProjectConfig) -> dict[str, Any]:
    section = _section(config)
    state_root = _config_relative_path(
        config, section.get("state_directory", ".crawler-state/recruitment")
    )
    checks: dict[str, Any] = {
        "enabled_sites": enabled_recruitment_sites(config),
        "ok": True,
    }
    for site_name in checks["enabled_sites"]:
        site = _site_config(config, site_name)
        if site_name == "boss":
            port = int(site.get("cdp_port", 9222))
            script = (
                Path(__file__).resolve().parent
                / "boss_cdp"
                / "scripts"
                / "boss_cdp_raw.py"
            )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    cdp_ready = True
            except OSError:
                cdp_ready = False
            script_present = script.exists()
            login_ready = False
            check_return_code: int | None = None
            check_message: str | None = None
            if cdp_ready and script_present:
                try:
                    completed = subprocess.run(
                        [
                            sys.executable,
                            os.fspath(script),
                            "--check",
                            "--cdp-port",
                            str(port),
                        ],
                        cwd=config.path.resolve().parent,
                        env={
                            **os.environ,
                            "PYTHONUTF8": os.environ.get("PYTHONUTF8", "1"),
                        },
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                        check=False,
                    )
                    check_return_code = completed.returncode
                    login_ready = completed.returncode == 0
                    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
                    if lines:
                        decisive = next(
                            (
                                line
                                for line in reversed(lines)
                                if "❌" in line or "限制" in line or "登录" in line
                            ),
                            lines[-1],
                        )
                        check_message = safe_error_message(decisive)[:300]
                except subprocess.TimeoutExpired:
                    check_message = "BOSS session check timed out after 30 seconds"
            checks[site_name] = {
                "cdp_port": port,
                "cdp_ready": cdp_ready,
                "script_present": script_present,
                "login_ready": login_ready,
                "check_return_code": check_return_code,
                "check_message": check_message,
            }
            checks["ok"] = (
                checks["ok"] and cdp_ready and script_present and login_ready
            )
        else:
            checks[site_name] = {
                "storage_state_present": (
                    state_root / site_name / "storage_state.json"
                ).exists(),
            }
    return checks


def run_recruitment_auth(
    config: ProjectConfig, site_name: str, *, wait_seconds: int
) -> dict[str, Any]:
    section = _section(config)
    state_root = _config_relative_path(
        config, section.get("state_directory", ".crawler-state/recruitment")
    )
    site = _site_config(config, site_name)
    if site_name == "boss":
        script = Path(__file__).resolve().parent / "boss_cdp" / "scripts" / "boss_cdp_raw.py"
        command = [
            sys.executable,
            os.fspath(script),
            "--setup-chrome",
            "--cdp-port",
            str(site.get("cdp_port", 9222)),
            "--login-timeout",
            str(wait_seconds),
        ]
        completed = subprocess.run(
            command,
            cwd=config.path.resolve().parent,
            env={**os.environ, "PYTHONUTF8": os.environ.get("PYTHONUTF8", "1")},
            check=False,
        )
        return {"site_name": site_name, "return_code": completed.returncode}
    if site_name == "job51":
        from .job51 import CrawlConfig
        from .job51.crawler import save_login_state

        shared_dir = state_root / "job51"
        proxy_policy, proxy_url = _proxy_settings(config, site)
        proxy_policy = _adapter_proxy_policy(proxy_policy, "job51")
        crawl_config = CrawlConfig(
            headless=False,
            proxy_policy=proxy_policy,
            proxy_url=proxy_url,
            browser_channel=site.get("browser_channel") or None,
            persistent_profile_dir=shared_dir / "browser_profile",
            storage_state_path=shared_dir / "storage_state.json",
            runtime_session_state_path=shared_dir / "runtime_session_state.json",
            category_snapshot_path=shared_dir / "categories.json",
            state_db_path=shared_dir / "auth.sqlite3",
            output_path=shared_dir / "auth.jsonl",
            coverage_path=shared_dir / "auth-coverage.json",
        )
        count = save_login_state(crawl_config, wait_seconds)
        return {
            "site_name": site_name,
            "storage_state_path": os.fspath(crawl_config.storage_state_path),
            "cookie_count": count,
        }
    if site_name == "liepin":
        from .liepin.auth import capture_login_state

        return {
            "site_name": site_name,
            **capture_login_state(
                state_root / "liepin" / "storage_state.json",
                proxy_url=resolve_proxy(config),
                wait_seconds=wait_seconds,
            ),
        }
    raise ValueError(f"unsupported recruitment auth site: {site_name}")
