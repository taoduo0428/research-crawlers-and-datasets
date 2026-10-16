from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
import json
import logging
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drv_crawler.core import (
    BlockedResponseError,
    ProjectConfig,
    ResilientHttpClient,
    atomic_write_json,
)
from drv_crawler.exporters import write_jsonl
from drv_crawler.models import CrawlRecord, SiteResult, utc_now_iso
from drv_crawler.publication import PublicationFilter
from drv_crawler.sites import EpwkAdapter, EpwkSitemapEntry
from tools.merge_epwk_runs import MergeValidationError, canonical_detail_url


LOGGER = logging.getLogger("epwk_failure_recovery")
MAX_INPUT_RUNS = 100
MAX_REPORT_BYTES = 5 * 1024 * 1024
MAX_TARGETS = 10_000
MAX_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 30.0
_TASK_PATH_RE = re.compile(r"/(\d+)/?$")
_HTTP_STATUS_RE = re.compile(r"(?:Blocked response|HTTP)\s+(\d{3})")
_SECRET_RE = re.compile(
    r"(?i)(?:authorization|cookie|access[_-]?token|refresh[_-]?token|"
    r"php(?:sess)?id|session[_-]?id|password|secret)"
)


class RecoveryError(ValueError):
    """Raised for a safe, user-actionable recovery failure."""


@dataclass(slots=True)
class RecoveryTarget:
    task_id: str
    detail_url: str
    source_inputs: set[str] = field(default_factory=set)
    initial_error_types: set[str] = field(default_factory=set)
    initial_http_statuses: set[int] = field(default_factory=set)


@dataclass(slots=True)
class RecoveryPlan:
    targets: list[RecoveryTarget]
    cutoff: date
    as_of: date
    publication_filter: dict[str, Any]
    inputs: list[dict[str, str]]


@dataclass(slots=True)
class RecoveryResult:
    recovered: list[CrawlRecord]
    unresolved: list[dict[str, Any]]
    report: dict[str, Any]


def _safe_source_name(value: Path, input_number: int) -> str:
    name = value.name or f"input-{input_number}"
    if _SECRET_RE.search(name):
        return f"input-{input_number}"
    return name[:200]


def _input_report_path(value: Path) -> Path:
    if value.is_file():
        return value
    report = value / "epwk" / "report.json"
    if report.is_file():
        return report
    checkpoint = value / "checkpoints" / "epwk.json"
    if checkpoint.is_file():
        return checkpoint
    raise RecoveryError("an input has no EPWK report or checkpoint")


def _read_report(value: Path, input_number: int) -> tuple[dict[str, Any], str]:
    path = _input_report_path(value)
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise RecoveryError(f"input #{input_number} report exceeds the size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except RecoveryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"input #{input_number} report is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("site_name") != "epwk":
        raise RecoveryError(f"input #{input_number} is not an EPWK report")
    return payload, _safe_source_name(value, input_number)


def _window_from_report(
    report: dict[str, Any], input_number: int
) -> tuple[date, date, dict[str, Any]]:
    raw_filter = report.get("publication_filter")
    if not isinstance(raw_filter, dict):
        raise RecoveryError(f"input #{input_number} has no publication_filter")
    try:
        cutoff = date.fromisoformat(str(raw_filter["cutoff"]))
        as_of = date.fromisoformat(str(raw_filter["as_of"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryError(
            f"input #{input_number} has invalid publication cutoff/as_of"
        ) from exc
    if cutoff > as_of or raw_filter.get("cutoff_inclusive") is not True:
        raise RecoveryError(f"input #{input_number} has a non-inclusive window")
    allowed = (
        "mode",
        "source",
        "recent_months",
        "timezone",
        "unknown_policy",
        "uncertain_policy",
    )
    normalized = {key: raw_filter.get(key) for key in allowed if key in raw_filter}
    normalized.update(
        {
            "as_of": as_of.isoformat(),
            "cutoff": cutoff.isoformat(),
            "cutoff_inclusive": True,
        }
    )
    return cutoff, as_of, normalized


def _failure_http_status(failure: dict[str, Any]) -> int | None:
    direct = failure.get("http_status")
    if isinstance(direct, int) and 100 <= direct <= 599:
        return direct
    match = _HTTP_STATUS_RE.search(str(failure.get("message") or ""))
    if not match:
        return None
    return int(match.group(1))


def _target_from_failure(
    failure: Any,
    *,
    source_name: str,
    allow_missing_stage: bool = False,
) -> RecoveryTarget | None:
    if not isinstance(failure, dict):
        return None
    stage = failure.get("stage")
    if stage != "publication_sitemap_detail" and not (
        allow_missing_stage and stage in {None, ""}
    ):
        return None
    raw_url = failure.get("url")
    if allow_missing_stage and not raw_url:
        raw_url = failure.get("detail_url")
    try:
        detail_url = canonical_detail_url(raw_url)
    except MergeValidationError:
        return None
    match = _TASK_PATH_RE.fullmatch(urlsplit(detail_url).path)
    if not match:
        return None
    target = RecoveryTarget(task_id=match.group(1), detail_url=detail_url)
    target.source_inputs.add(source_name)
    error_type = str(failure.get("error_type") or "unknown")
    target.initial_error_types.add(error_type[:200])
    status = _failure_http_status(failure)
    if status is not None:
        target.initial_http_statuses.add(status)
    return target


def load_recovery_plan(inputs: Sequence[Path | str]) -> RecoveryPlan:
    if not inputs:
        raise RecoveryError("at least one input run is required")
    if len(inputs) > MAX_INPUT_RUNS:
        raise RecoveryError(f"at most {MAX_INPUT_RUNS} input runs are supported")

    targets_by_url: dict[str, RecoveryTarget] = {}
    expected_window: tuple[date, date] | None = None
    output_filter: dict[str, Any] | None = None
    input_summaries: list[dict[str, str]] = []
    for input_number, raw_input in enumerate(inputs, start=1):
        input_path = Path(raw_input)
        report, source_name = _read_report(input_path, input_number)
        cutoff, as_of, normalized_filter = _window_from_report(report, input_number)
        if expected_window is None:
            expected_window = (cutoff, as_of)
            output_filter = normalized_filter
        elif expected_window != (cutoff, as_of):
            raise RecoveryError(
                f"input #{input_number} publication cutoff/as_of does not match input #1"
            )
        input_key = f"input_{input_number:03d}"
        input_summaries.append({"input": input_key, "source": source_name})
        failures = report.get("failures")
        allow_missing_stage = False
        if not isinstance(failures, list):
            failures = report.get("unresolved")
            allow_missing_stage = True
        if not isinstance(failures, list):
            raise RecoveryError(
                f"input #{input_number} has no failures or unresolved list"
            )
        for failure in failures:
            target = _target_from_failure(
                failure,
                source_name=input_key,
                allow_missing_stage=allow_missing_stage,
            )
            if target is None:
                continue
            existing = targets_by_url.get(target.detail_url)
            if existing is None:
                targets_by_url[target.detail_url] = target
            else:
                existing.source_inputs.update(target.source_inputs)
                existing.initial_error_types.update(target.initial_error_types)
                existing.initial_http_statuses.update(target.initial_http_statuses)
        if len(targets_by_url) > MAX_TARGETS:
            raise RecoveryError(f"recovery target count exceeds {MAX_TARGETS}")

    assert expected_window is not None and output_filter is not None
    return RecoveryPlan(
        targets=list(targets_by_url.values()),
        cutoff=expected_window[0],
        as_of=expected_window[1],
        publication_filter=output_filter,
        inputs=input_summaries,
    )


def _storage_state_path(config: ProjectConfig) -> Path:
    site_config = config.site("epwk")
    raw_path = site_config.get("storage_state")
    if not raw_path:
        raise RecoveryError("EPWK storage_state is not configured")
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = config.path.parent / path
    if not path.is_file():
        raise RecoveryError("EPWK storage_state is missing")
    site_config["storage_state"] = str(path)
    return path


def _error_fields(exc: Exception) -> tuple[str, int | None, str]:
    if isinstance(exc, BlockedResponseError):
        return "http_error", exc.status_code, type(exc).__name__
    if isinstance(exc, ValueError):
        return "parse_error", None, type(exc).__name__
    return "request_error", None, type(exc).__name__


def _close_adapter_renderer(adapter: EpwkAdapter) -> None:
    renderer = getattr(adapter, "_publication_renderer", None)
    if renderer is not None:
        try:
            renderer.close()
        finally:
            adapter._publication_renderer = None


def _bootstrap_recovery_session(
    adapter: EpwkAdapter,
    result: SiteResult,
) -> dict[str, Any]:
    inventory = adapter._load_publication_sitemap_inventory(result)
    if inventory.loaded_files <= 0:
        raise RecoveryError("EPWK sitemap bootstrap loaded no files")
    return {
        "mode": "formal_sitemap_inventory",
        "configured_files": inventory.configured_files,
        "loaded_files": inventory.loaded_files,
        "complete": inventory.complete,
    }


def _recover_target(
    target: RecoveryTarget,
    *,
    position: int,
    adapter: EpwkAdapter,
    result: SiteResult,
    publication_filter: PublicationFilter,
    max_attempts: int,
    delay_seconds: float,
    sleep: Callable[[float], None],
) -> tuple[CrawlRecord | None, dict[str, Any] | None]:
    last_status = "not_attempted"
    last_http_status: int | None = None
    last_error_type = "unknown"
    for attempt in range(1, max_attempts + 1):
        try:
            entry = EpwkSitemapEntry(
                task_key=target.task_id,
                detail_url=target.detail_url,
                published_at="",
                sitemap_url="",
            )
            record = adapter._sitemap_detail_record(result, entry, position=position)
            decision = publication_filter.decide(record.published_at_raw)
            if decision.included:
                for key in (
                    "publication_sitemap_primary",
                    "publication_sitemap_url",
                    "publication_sitemap_lastmod",
                ):
                    record.metadata.pop(key, None)
                record.metadata["publication_time"] = decision.to_dict()
                record.metadata["failure_recovery"] = {
                    "detail_date_authoritative": True,
                    "attempts": attempt,
                    "source_inputs": sorted(target.source_inputs),
                }
                return record, None
            last_status = f"publication_{decision.status}"
            last_http_status = None
            last_error_type = "PublicationWindowDecision"
            if decision.status in {"too_old", "future"}:
                break
        except Exception as exc:
            last_status, last_http_status, last_error_type = _error_fields(exc)
            LOGGER.warning(
                "task_id=%s attempt=%s/%s status=%s http_status=%s error_type=%s",
                target.task_id,
                attempt,
                max_attempts,
                last_status,
                last_http_status,
                last_error_type,
            )
        if attempt < max_attempts:
            sleep(min(MAX_BACKOFF_SECONDS, delay_seconds * (2**attempt)))

    return None, {
        "task_id": target.task_id,
        "detail_url": target.detail_url,
        "attempts": attempt,
        "status": last_status,
        "http_status": last_http_status,
        "error_type": last_error_type,
        "source_inputs": sorted(target.source_inputs),
        "initial_error_types": sorted(target.initial_error_types),
        "initial_http_statuses": sorted(target.initial_http_statuses),
    }


def recover_epwk_failures(
    inputs: Sequence[Path | str],
    *,
    output_dir: Path | str,
    config_path: Path | str = "crawler.config.yaml",
    delay_seconds: float = 2.0,
    max_attempts: int = MAX_ATTEMPTS,
    max_consecutive_failures: int = 10,
    target_limit: int | None = None,
    _sleep: Callable[[float], None] = time.sleep,
) -> RecoveryResult:
    if delay_seconds < 0:
        raise RecoveryError("delay_seconds must not be negative")
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise RecoveryError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}")
    if not 1 <= max_consecutive_failures <= 100:
        raise RecoveryError("max_consecutive_failures must be between 1 and 100")
    if target_limit is not None and not 1 <= target_limit <= MAX_TARGETS:
        raise RecoveryError(f"target_limit must be between 1 and {MAX_TARGETS}")

    plan = load_recovery_plan(inputs)
    source_target_count = len(plan.targets)
    if target_limit is not None:
        plan.targets = plan.targets[:target_limit]
    config = ProjectConfig(config_path)
    site_config = config.site("epwk")
    if str(site_config.get("http_transport", "")).lower() != "curl_cffi":
        raise RecoveryError("EPWK recovery requires curl_cffi transport")
    state_path = _storage_state_path(config)
    site_config["download_delay"] = delay_seconds
    site_config["randomize_download_delay"] = False
    config.section("retry")["retry_times"] = 0

    publication_filter = PublicationFilter(
        published_since=plan.cutoff,
        as_of=plan.as_of,
    )
    parse_result = SiteResult(site_name="epwk", requested_limit=len(plan.targets))
    recovered: list[CrawlRecord] = []
    unresolved: list[dict[str, Any]] = []
    consecutive_failures = 0
    circuit_opened = False
    destination = Path(output_dir)
    bootstrap_summary: dict[str, Any] | None = None

    def save_progress(processed: int, task_id: str, status: str) -> None:
        atomic_write_json(
            destination / "recovery-progress.json",
            {
                "processed": processed,
                "target_count": len(plan.targets),
                "recovered_count": len(recovered),
                "unresolved_count": len(unresolved),
                "last_task_id": task_id,
                "last_status": status,
            },
        )

    with ResilientHttpClient(config, site_name="epwk") as client:
        imported = client.load_storage_state(
            state_path,
            allowed_domains={"epwk.com", "task.epwk.com"},
        )
        if imported <= 0:
            raise RecoveryError("EPWK storage_state contains no usable domain cookies")
        adapter = EpwkAdapter(config, client, lambda *_: None)
        adapter.publication_filter = publication_filter
        try:
            bootstrap_summary = _bootstrap_recovery_session(adapter, parse_result)
            for position, target in enumerate(plan.targets, start=1):
                if consecutive_failures >= max_consecutive_failures:
                    circuit_opened = True
                    unresolved.append(
                        {
                            "task_id": target.task_id,
                            "detail_url": target.detail_url,
                            "attempts": 0,
                            "status": "circuit_breaker",
                            "http_status": None,
                            "error_type": "CircuitBreaker",
                            "source_inputs": sorted(target.source_inputs),
                            "initial_error_types": sorted(
                                target.initial_error_types
                            ),
                            "initial_http_statuses": sorted(
                                target.initial_http_statuses
                            ),
                        }
                    )
                    save_progress(position, target.task_id, "circuit_breaker")
                    continue
                record, failure = _recover_target(
                    target,
                    position=position,
                    adapter=adapter,
                    result=parse_result,
                    publication_filter=publication_filter,
                    max_attempts=max_attempts,
                    delay_seconds=delay_seconds,
                    sleep=_sleep,
                )
                if record is not None:
                    recovered.append(record)
                    consecutive_failures = 0
                    save_progress(position, target.task_id, "recovered")
                else:
                    assert failure is not None
                    unresolved.append(failure)
                    if failure["status"] in {
                        "publication_too_old",
                        "publication_future",
                    }:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                    save_progress(position, target.task_id, str(failure["status"]))
        finally:
            _close_adapter_renderer(adapter)

    generated_at = utc_now_iso()
    report = {
        "site_name": "epwk",
        "generated_at": generated_at,
        "publication_filter": plan.publication_filter,
        "inputs": plan.inputs,
        "source_target_count": source_target_count,
        "target_limit": target_limit,
        "target_count": len(plan.targets),
        "recovered_count": len(recovered),
        "unresolved_count": len(unresolved),
        "delay_seconds": delay_seconds,
        "max_attempts_per_task": max_attempts,
        "max_consecutive_failures": max_consecutive_failures,
        "circuit_opened": circuit_opened,
        "detail_date_authoritative": True,
        "session_bootstrap": bootstrap_summary,
    }
    manifest = {
        **report,
        "unresolved": unresolved,
    }
    write_jsonl(destination / "recovered-records.jsonl", recovered)
    atomic_write_json(destination / "unresolved-manifest.json", manifest)
    atomic_write_json(destination / "recovery-report.json", report)
    return RecoveryResult(recovered=recovered, unresolved=unresolved, report=report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Boundedly recover failed EPWK sitemap detail tasks."
    )
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=Path("crawler.config.yaml"))
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--max-consecutive-failures", type=int, default=10)
    parser.add_argument("--target-limit", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("drv_crawler").setLevel(logging.WARNING)
    try:
        result = recover_epwk_failures(
            args.run_dirs,
            output_dir=args.output_dir,
            config_path=args.config,
            delay_seconds=args.delay_seconds,
            max_attempts=args.max_attempts,
            max_consecutive_failures=args.max_consecutive_failures,
            target_limit=args.target_limit,
        )
    except RecoveryError as exc:
        LOGGER.error("recovery failed: %s", exc)
        return 1
    except Exception as exc:
        LOGGER.error("recovery failed with %s", type(exc).__name__)
        return 1
    print(
        f"targets={result.report['target_count']} "
        f"recovered={result.report['recovered_count']} "
        f"unresolved={result.report['unresolved_count']}"
    )
    return 0 if not result.unresolved else 2


if __name__ == "__main__":
    raise SystemExit(main())
