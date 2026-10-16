from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font

from .core import atomic_write_json, atomic_write_text
from .models import CrawlRecord, SiteResult


CSV_FIELDS = [
    "record_id",
    "site_name",
    "content_category",
    "category_level_1_raw",
    "category_level_2_raw",
    "category_level_3_raw",
    "category_path_raw",
    "category_source",
    "category_source_url",
    "category_identity_verified",
    "title_raw",
    "detail_title_raw",
    "price_raw_joined",
    "status_raw",
    "job_type_raw",
    "budget_raw",
    "work_period_raw",
    "publisher_name_raw",
    "location_raw",
    "published_at_raw",
    "task_id",
    "deadline_raw",
    "bid_count_raw",
    "publication_scope",
    "publication_boundary_interval",
    "publication_boundary_note",
    "description_raw",
    "description_length",
    "hard_filter_pass",
    "main_sample",
    "needs_ai_review",
    "quality_tier",
    "is_completed",
    "has_winner",
    "winner_count",
    "winner_names",
    "exclude_reasons",
    "description_price_mentions",
    "list_url",
    "detail_url",
    "page_no",
    "list_position",
    "capture_method",
    "source_state",
    "price_evidence_json",
    "metadata_json",
    "captured_at",
]

EXCEL_ILLEGAL_CHARACTER_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]"
)


def flatten_record(record: CrawlRecord) -> dict[str, Any]:
    data = record.to_dict()
    metadata = data["metadata"]
    publication = (
        metadata.get("publication_time") if isinstance(metadata, dict) else None
    )
    publication = publication if isinstance(publication, dict) else {}
    quality_fields = {
        key: metadata.get(key)
        for key in (
            "description_length", "hard_filter_pass", "main_sample",
            "needs_ai_review", "quality_tier", "is_completed", "has_winner",
            "winner_count",
        )
    }
    quality_fields["exclude_reasons"] = json.dumps(
        metadata.get("exclude_reasons", []), ensure_ascii=False, separators=(",", ":")
    )
    quality_fields["description_price_mentions"] = json.dumps(
        metadata.get("description_price_mentions", []), ensure_ascii=False, separators=(",", ":")
    )
    quality_fields["winner_names"] = json.dumps(
        metadata.get("winner_names"), ensure_ascii=False, separators=(",", ":")
    )
    epwk_fields = {
        "task_id": metadata.get("task_id"),
        "deadline_raw": metadata.get("deadline_raw"),
        "bid_count_raw": metadata.get("bid_count_raw"),
    }
    return {
        **{field: data.get(field) for field in CSV_FIELDS},
        **quality_fields,
        **epwk_fields,
        "publication_scope": publication.get("working_scope"),
        "publication_boundary_interval": publication.get("boundary_interval"),
        "publication_boundary_note": publication.get("boundary_note"),
        "price_evidence_json": json.dumps(
            data["price_evidence"], ensure_ascii=False, separators=(",", ":")
        ),
        "metadata_json": json.dumps(
            data["metadata"], ensure_ascii=False, separators=(",", ":")
        ),
    }


def write_jsonl(path: Path, records: list[CrawlRecord]) -> None:
    content = "".join(
        json.dumps(record.to_dict(), ensure_ascii=False) + "\n" for record in records
    )
    atomic_write_text(path, content)


def write_csv(path: Path, records: list[CrawlRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8-sig", newline="", delete=False, dir=path.parent
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flatten_record(record) for record in records)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def write_xlsx(path: Path, records: list[CrawlRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook(write_only=False)
    sheet = workbook.active
    sheet.title = "sample"
    sheet.append(CSV_FIELDS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for record in records:
        row = flatten_record(record)
        sheet.append(
            [
                EXCEL_ILLEGAL_CHARACTER_RE.sub("", value)
                if isinstance(value := row.get(field), str)
                else value
                for field in CSV_FIELDS
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = {
        "A": 26,
        "B": 12,
        "C": 22,
        "D": 22,
        "E": 22,
        "F": 22,
        "G": 52,
        "H": 24,
        "I": 48,
        "J": 16,
        "K": 34,
        "L": 34,
        "M": 28,
        "N": 14,
        "O": 14,
        "P": 20,
        "Q": 18,
        "R": 22,
        "S": 18,
        "T": 18,
        "U": 60,
        "V": 42,
        "W": 42,
        "AB": 60,
        "AC": 48,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    with NamedTemporaryFile(suffix=".xlsx", delete=False, dir=path.parent) as handle:
        temp_path = Path(handle.name)
    try:
        workbook.save(temp_path)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def site_report_markdown(result: SiteResult) -> str:
    quality = result.quality()
    lines = [
        f"# {result.site_name} sample report",
        "",
        f"- status: `{result.status}`",
        f"- adapter: `{result.adapter}`",
        f"- difficulty: `{result.difficulty}/5`",
        f"- records: `{quality['record_count']}/{quality['requested_limit']}`",
        f"- core missing rate: `{quality['core_missing_rate']:.2%}`",
        f"- duplicate rate: `{quality['duplicate_rate']:.2%}`",
        f"- details fetched: `{result.details_fetched}`",
        f"- filtered out: `{result.filtered_out_count}`",
        f"- quality passed: `{quality['quality_passed']}`",
        "",
        "## Runtime notes",
        "",
    ]
    if result.publication_filter:
        lines.extend(
            [
                f"- publication cutoff: `{result.publication_filter['cutoff']}`",
                f"- publication reference date: `{result.publication_filter['as_of']}`",
                "- publication policy: `strict; unknown and uncertain excluded`",
                f"- filter reasons: `{json.dumps(result.filter_counts, ensure_ascii=False, sort_keys=True)}`",
            ]
        )
    if result.completion_mode == "publication_window":
        lines.extend(
            [
                f"- completion mode: `{result.completion_mode}`",
                f"- publication window complete: `{result.window_complete}`",
                f"- stop reason: `{result.stop_reason or 'unknown'}`",
                f"- source coverage: `{json.dumps(result.source_coverage, ensure_ascii=False, sort_keys=True)}`",
            ]
        )
    lines.extend(f"- {note}" for note in result.runtime_notes)
    if result.failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(
            f"- `{failure.stage}` / `{failure.error_type}`: {failure.message}"
            for failure in result.failures
        )
    return "\n".join(lines) + "\n"


def export_site_result(run_dir: Path, result: SiteResult) -> None:
    site_dir = run_dir / result.site_name
    write_jsonl(site_dir / "records.jsonl", result.records)
    write_csv(site_dir / "records.csv", result.records)
    write_xlsx(site_dir / "records.xlsx", result.records)
    atomic_write_json(site_dir / "report.json", result.report_dict())
    atomic_write_text(site_dir / "report.md", site_report_markdown(result))


def export_run_report(run_dir: Path, results: list[SiteResult]) -> dict[str, Any]:
    summary = {
        "run_dir": str(run_dir),
        "sites": [result.report_dict() for result in results],
        "totals": {
            "sites": len(results),
            "success": sum(result.status == "success" for result in results),
            "partial": sum(result.status == "partial" for result in results),
            "failed": sum(result.status == "failed" for result in results),
            "records": sum(len(result.records) for result in results),
            "filtered_out": sum(result.filtered_out_count for result in results),
            "publication_windows": sum(
                result.completion_mode == "publication_window" for result in results
            ),
            "publication_windows_complete": sum(
                result.completion_mode == "publication_window"
                and result.window_complete is True
                for result in results
            ),
        },
    }
    atomic_write_json(run_dir / "run-report.json", summary)
    lines = ["# DRV crawler sample run", ""]
    for result in results:
        quality = result.quality()
        window_status = (
            f", window_complete={result.window_complete}"
            if result.completion_mode == "publication_window"
            else ""
        )
        lines.append(
            f"- **{result.site_name}**: {result.status}, "
            f"{quality['record_count']}/{quality['requested_limit']}, "
            f"filtered={result.filtered_out_count}{window_status}, "
            f"difficulty {result.difficulty}/5, quality={quality['quality_passed']}"
        )
    atomic_write_text(run_dir / "run-report.md", "\n".join(lines) + "\n")
    return summary
