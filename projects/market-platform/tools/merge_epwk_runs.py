from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence
from urllib.parse import urlsplit, urlunsplit


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drv_crawler.exporters import export_run_report, export_site_result
from drv_crawler.models import CrawlRecord, PriceEvidence, SiteResult, make_record_id
from drv_crawler.publication import PublicationFilter


MAX_INPUT_RUNS = 100
MAX_REPORT_BYTES = 5 * 1024 * 1024
MAX_JSONL_LINE_CHARS = 2 * 1024 * 1024
MAX_TOTAL_RECORDS = 1_000_000
_SECRET_WORD_RE = re.compile(
    r"(?i)(?:authorization|cookie|access[_-]?token|refresh[_-]?token|"
    r"php(?:sess)?id|session[_-]?id|password|secret)"
)


class MergeValidationError(ValueError):
    """Raised before any merged output is written."""


@dataclass(slots=True)
class InputRun:
    run_dir: Path
    report: dict[str, Any]
    records: list[CrawlRecord]
    input_kind: str = "standard"


def _resolve_input_paths(
    run_dir: Path, input_number: int
) -> tuple[Path, Path, str]:
    label = f"input #{input_number}"
    standard_report = run_dir / "epwk" / "report.json"
    standard_records = run_dir / "epwk" / "records.jsonl"
    recovery_report = run_dir / "recovery-report.json"
    recovery_records = run_dir / "recovered-records.jsonl"
    has_standard = standard_report.is_file() or standard_records.is_file()
    has_recovery = recovery_report.is_file() or recovery_records.is_file()
    if has_standard and has_recovery:
        raise MergeValidationError(
            f"{label} has both standard and recovery artifacts"
        )
    if has_standard:
        if not standard_report.is_file():
            raise MergeValidationError(f"{label} is missing epwk/report.json")
        if not standard_records.is_file():
            raise MergeValidationError(f"{label} is missing epwk/records.jsonl")
        return standard_report, standard_records, "standard"
    if has_recovery:
        if not recovery_report.is_file():
            raise MergeValidationError(f"{label} is missing recovery-report.json")
        if not recovery_records.is_file():
            raise MergeValidationError(
                f"{label} is missing recovered-records.jsonl"
            )
        return recovery_report, recovery_records, "recovery"
    raise MergeValidationError(f"{label} has no EPWK report/records artifacts")


def _redact_text(value: object) -> str:
    text = str(value)
    if _SECRET_WORD_RE.search(text):
        return "[redacted secret-bearing text]"
    return text


def _sanitize_report_value(value: Any, *, depth: int = 0) -> Any:
    """Keep source coverage useful without copying credentials or large blobs."""
    if depth >= 5:
        return "[truncated]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= 100:
                output["_truncated"] = True
                break
            key = str(raw_key)
            output[key] = (
                "[redacted]"
                if _SECRET_WORD_RE.search(key)
                else _sanitize_report_value(item, depth=depth + 1)
            )
        return output
    if isinstance(value, list):
        return [
            _sanitize_report_value(item, depth=depth + 1) for item in value[:100]
        ]
    if isinstance(value, str):
        return _redact_text(value)[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(value)[:2000]


def canonical_detail_url(value: str | None) -> str:
    """Return the query-free HTTPS identity for one EPWK detail page."""
    if not value:
        raise MergeValidationError("record has no detail_url")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, TypeError, ValueError) as exc:
        raise MergeValidationError("record has an invalid detail_url") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise MergeValidationError("record detail_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise MergeValidationError("record detail_url must not contain credentials")

    host = parsed.hostname.lower().rstrip(".")
    if host != "epwk.com" and not host.endswith(".epwk.com"):
        raise MergeValidationError("record detail_url is not an EPWK URL")
    netloc = host
    if port is not None and port not in {80, 443}:
        netloc = f"{host}:{port}"

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/") + "/"
    return urlunsplit(("https", netloc, path, "", ""))


def _read_report(path: Path, input_number: int) -> dict[str, Any]:
    label = f"input #{input_number}"
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise MergeValidationError(f"{label} report exceeds the size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except MergeValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MergeValidationError(f"{label} report is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise MergeValidationError(f"{label} report must be a JSON object")
    if value.get("site_name") != "epwk":
        raise MergeValidationError(f"{label} report site_name must be epwk")
    return value


def _publication_window(
    report: dict[str, Any], input_number: int
) -> tuple[dict[str, Any], date, date]:
    label = f"input #{input_number}"
    raw_filter = report.get("publication_filter")
    if not isinstance(raw_filter, dict):
        raise MergeValidationError(f"{label} has no publication_filter object")
    try:
        as_of = date.fromisoformat(str(raw_filter["as_of"]))
        cutoff = date.fromisoformat(str(raw_filter["cutoff"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MergeValidationError(
            f"{label} publication_filter has invalid as_of/cutoff"
        ) from exc
    if cutoff > as_of:
        raise MergeValidationError(f"{label} publication cutoff is after as_of")
    if raw_filter.get("cutoff_inclusive") is not True:
        raise MergeValidationError(f"{label} publication cutoff is not inclusive")

    allowed_keys = (
        "mode",
        "source",
        "recent_months",
        "as_of",
        "cutoff",
        "cutoff_inclusive",
        "timezone",
        "unknown_policy",
        "uncertain_policy",
    )
    normalized = {
        key: _sanitize_report_value(raw_filter[key])
        for key in allowed_keys
        if key in raw_filter
    }
    normalized["as_of"] = as_of.isoformat()
    normalized["cutoff"] = cutoff.isoformat()
    normalized["cutoff_inclusive"] = True
    return normalized, cutoff, as_of


def _record_from_payload(
    payload: Any,
    *,
    input_number: int,
    line_number: int,
    publication_filter: PublicationFilter,
) -> CrawlRecord:
    label = f"input #{input_number} record line {line_number}"
    if not isinstance(payload, dict):
        raise MergeValidationError(f"{label} must be a JSON object")
    value = dict(payload)
    value.pop("price_raw_joined", None)
    value.pop("core_complete", None)
    raw_evidence = value.pop("price_evidence", [])
    if not isinstance(raw_evidence, list):
        raise MergeValidationError(f"{label} price_evidence must be a list")
    try:
        evidence = [
            PriceEvidence(**item) if isinstance(item, dict) else None
            for item in raw_evidence
        ]
        if any(item is None for item in evidence):
            raise TypeError("non-object price evidence")
        record = CrawlRecord(
            **value,
            price_evidence=[item for item in evidence if item is not None],
        )
    except (TypeError, ValueError) as exc:
        raise MergeValidationError(f"{label} cannot be rebuilt") from exc

    if record.site_name != "epwk":
        raise MergeValidationError(f"{label} site_name must be epwk")
    if not record.core_complete:
        raise MergeValidationError(f"{label} is missing required core fields")
    try:
        canonical_url = canonical_detail_url(record.detail_url)
    except MergeValidationError as exc:
        raise MergeValidationError(f"{label}: {exc}") from exc

    decision = publication_filter.decide(record.published_at_raw)
    if not decision.included:
        raise MergeValidationError(
            f"{label} publication date is outside the inclusive window"
        )
    record.detail_url = canonical_url
    record.record_id = make_record_id("epwk", canonical_url)
    return record


def _read_records(
    path: Path,
    input_number: int,
    publication_filter: PublicationFilter,
    remaining_capacity: int,
) -> list[CrawlRecord]:
    label = f"input #{input_number}"
    records: list[CrawlRecord] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                if len(line) > MAX_JSONL_LINE_CHARS:
                    raise MergeValidationError(
                        f"{label} record line {line_number} exceeds the size limit"
                    )
                if len(records) >= remaining_capacity:
                    raise MergeValidationError(
                        f"merged inputs exceed {MAX_TOTAL_RECORDS} records"
                    )
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MergeValidationError(
                        f"{label} record line {line_number} is invalid JSON"
                    ) from exc
                records.append(
                    _record_from_payload(
                        payload,
                        input_number=input_number,
                        line_number=line_number,
                        publication_filter=publication_filter,
                    )
                )
    except MergeValidationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise MergeValidationError(f"{label} records file is unreadable") from exc
    return records


def _report_count(report: dict[str, Any], key: str) -> int:
    value = report.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _requested_limit(report: dict[str, Any]) -> int:
    quality = report.get("quality")
    value = (
        quality.get("requested_limit", 0)
        if isinstance(quality, dict)
        else report.get("target_count", 0)
    )
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _input_coverage(input_run: InputRun, input_number: int) -> dict[str, Any]:
    report = input_run.report
    failures = report.get("failures")
    failure_count = (
        len(failures)
        if isinstance(failures, list)
        else _report_count(report, "unresolved_count")
    )
    if input_run.input_kind == "recovery":
        stop_reason = (
            "recovery_unresolved" if failure_count else "recovery_targets_complete"
        )
        source_coverage = {
            "target_count": _report_count(report, "target_count"),
            "recovered_count": _report_count(report, "recovered_count"),
            "unresolved_count": failure_count,
        }
    else:
        stop_reason = _redact_text(report.get("stop_reason") or "unknown")[:500]
        source_coverage = _sanitize_report_value(
            report.get("source_coverage", {})
        )
    return {
        "complete": report.get("window_complete") is True,
        "stop_reason": stop_reason,
        "run_dir": _redact_text(input_run.run_dir.resolve())[:2000],
        "input_kind": input_run.input_kind,
        "record_count": len(input_run.records),
        "failure_count": failure_count,
        "report": {
            "site_name": "epwk",
            "status": _redact_text(
                report.get("status") or input_run.input_kind
            )[:100],
            "window_complete": report.get("window_complete"),
            "source_coverage": source_coverage,
        },
        "input_number": input_number,
    }


def merge_epwk_runs(run_dirs: Sequence[Path | str], output_dir: Path | str) -> SiteResult:
    """Validate and export a conservative union of two or more EPWK runs."""
    if len(run_dirs) < 2:
        raise MergeValidationError("at least two input run directories are required")
    if len(run_dirs) > MAX_INPUT_RUNS:
        raise MergeValidationError(f"at most {MAX_INPUT_RUNS} input runs are supported")

    inputs: list[InputRun] = []
    expected_window: tuple[date, date] | None = None
    output_filter: dict[str, Any] | None = None
    total_records = 0
    for input_number, raw_run_dir in enumerate(run_dirs, start=1):
        run_dir = Path(raw_run_dir)
        report_path, records_path, input_kind = _resolve_input_paths(
            run_dir, input_number
        )
        report = _read_report(report_path, input_number)
        normalized_filter, cutoff, as_of = _publication_window(report, input_number)
        current_window = (cutoff, as_of)
        if expected_window is None:
            expected_window = current_window
            output_filter = normalized_filter
        elif current_window != expected_window:
            raise MergeValidationError(
                f"input #{input_number} publication as_of/cutoff does not match input #1"
            )

        publication_filter = PublicationFilter(published_since=cutoff, as_of=as_of)
        records = _read_records(
            records_path,
            input_number,
            publication_filter,
            MAX_TOTAL_RECORDS - total_records,
        )
        total_records += len(records)
        inputs.append(
            InputRun(
                run_dir=run_dir,
                report=report,
                records=records,
                input_kind=input_kind,
            )
        )

    unique_by_url: dict[str, CrawlRecord] = {}
    for input_run in inputs:
        for record in input_run.records:
            assert record.detail_url is not None
            unique_by_url.setdefault(record.detail_url, record)
    records = list(unique_by_url.values())

    filter_counts: Counter[str] = Counter()
    for input_run in inputs:
        raw_counts = input_run.report.get("filter_counts")
        if isinstance(raw_counts, dict):
            for key, value in raw_counts.items():
                if (
                    isinstance(key, str)
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                ):
                    filter_counts[key] += value

    source_coverage = {
        f"input_{index:03d}": _input_coverage(input_run, index)
        for index, input_run in enumerate(inputs, start=1)
    }
    input_failure_count = sum(
        len(run.report["failures"])
        if isinstance(run.report.get("failures"), list)
        else _report_count(run.report, "unresolved_count")
        for run in inputs
    )
    input_dirs = [
        _redact_text(input_run.run_dir.resolve()) for input_run in inputs
    ]
    requested_limit = max(
        len(records), *(_requested_limit(run.report) for run in inputs)
    )
    result = SiteResult(
        site_name="epwk",
        requested_limit=requested_limit,
        records=records,
        pages_fetched=sum(
            _report_count(run.report, "pages_fetched") for run in inputs
        ),
        details_fetched=sum(
            _report_count(run.report, "details_fetched") for run in inputs
        ),
        filtered_out_count=sum(
            _report_count(run.report, "filtered_out_count") for run in inputs
        ),
        filter_counts=dict(filter_counts),
        publication_filter=output_filter,
        completion_mode="publication_window",
        window_complete=False,
        stop_reason="verified_union_not_global",
        source_coverage=source_coverage,
        adapter="verified_run_union",
        difficulty=max(
            (_report_count(run.report, "difficulty") for run in inputs),
            default=0,
        ),
        runtime_notes=[
            "merge_input_dirs=" + json.dumps(input_dirs, ensure_ascii=False),
            f"merge_input_count={len(inputs)}",
            f"merge_input_records={total_records}",
            f"merge_deduplicated_records={len(records)}",
            f"merge_duplicates_removed={total_records - len(records)}",
            (
                f"merge_input_failure_count={input_failure_count}; "
                "original failure messages omitted to avoid credential disclosure"
            ),
        ],
    )
    result.finish()

    destination = Path(output_dir)
    export_site_result(destination, result)
    export_run_report(destination, [result])
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge verified EPWK run directories into one conservative union."
    )
    parser.add_argument("run_dirs", nargs="+", type=Path, help="input run directories")
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if len(args.run_dirs) < 2:
        parser.error("at least two input run directories are required")
    try:
        result = merge_epwk_runs(args.run_dirs, args.output_dir)
    except MergeValidationError as exc:
        print(f"merge failed: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("merge failed while writing atomic output files", file=sys.stderr)
        return 1
    print(args.output_dir)
    print(
        f"records={len(result.records)} "
        f"window_complete={result.window_complete} stop_reason={result.stop_reason}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
