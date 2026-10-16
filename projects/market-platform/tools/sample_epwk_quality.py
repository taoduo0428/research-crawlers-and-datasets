from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable

from drv_crawler.core import (
    BlockedResponseError,
    ProjectConfig,
    ResilientHttpClient,
    atomic_write_json,
    safe_error_message,
)
from drv_crawler.exporters import export_run_report, export_site_result
from drv_crawler.models import CrawlFailure, SiteResult, utc_now_iso
from drv_crawler.sites import EpwkAdapter, EpwkSitemapEntry


DEFAULT_INPUT = Path("outputs/外包--完整数据/一品威客/epwk/records.jsonl")
TASK_URL_RE = re.compile(r"^https://task\.epwk\.com/(\d+)/?$")
MAX_INPUT_ROWS = 10_000
MAX_LINE_CHARS = 2_000_000
MAX_SAMPLE_SIZE = 200


@dataclass(frozen=True, slots=True)
class Candidate:
    task_id: str
    detail_url: str
    published_at: str
    sitemap_url: str


def load_candidates(path: Path) -> list[Candidate]:
    candidates: dict[str, Candidate] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line_number > MAX_INPUT_ROWS:
                raise ValueError(f"input exceeds {MAX_INPUT_ROWS} rows")
            if len(line) > MAX_LINE_CHARS:
                raise ValueError(f"input line {line_number} is too large")
            if not line.strip():
                continue
            payload = json.loads(line)
            detail_url = str(payload.get("detail_url") or "").strip()
            match = TASK_URL_RE.fullmatch(detail_url)
            if not match:
                continue
            metadata = payload.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            published_at = str(
                metadata.get("publication_sitemap_lastmod")
                or payload.get("published_at_raw")
                or ""
            ).strip()
            if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published_at):
                continue
            sitemap_url = str(
                metadata.get("publication_sitemap_url") or "historical_records"
            ).strip()
            task_id = match.group(1)
            candidates.setdefault(
                task_id,
                Candidate(task_id, detail_url, published_at, sitemap_url),
            )
    return sorted(candidates.values(), key=lambda item: (item.published_at, item.task_id))


def select_candidates(candidates: Iterable[Candidate], limit: int) -> list[Candidate]:
    if not 1 <= limit <= MAX_SAMPLE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_SAMPLE_SIZE}")
    return list(candidates)[:limit]


def run_sample(
    *,
    project_root: Path,
    input_path: Path,
    output_dir: Path,
    limit: int,
    max_consecutive_failures: int = 5,
) -> SiteResult:
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    selected = select_candidates(load_candidates(input_path), limit)
    if len(selected) < limit:
        raise ValueError(f"only {len(selected)} valid unique candidates are available")

    config = ProjectConfig(project_root / "crawler.config.yaml")
    site_config = config.site("epwk")
    storage_state = project_root / str(
        site_config.get("storage_state") or ".crawler-state/epwk-storage-state.json"
    )
    result = SiteResult(site_name="epwk", requested_limit=limit)
    result.adapter = "EpwkAdapter-quality-resample"
    result.difficulty = EpwkAdapter.difficulty
    result.runtime_notes.extend(
        [
            "Bounded quality resample from historical EPWK detail URLs.",
            "Candidates are ordered oldest-first; this is a quality-oriented sample, not a population estimate.",
            "Sitemap lastmod is retained only as metadata and never copied into published_at_raw.",
        ]
    )
    output_dir.mkdir(parents=True)
    consecutive_failures = 0

    with ResilientHttpClient(config, site_name="epwk") as client:
        if storage_state.exists():
            try:
                client.load_storage_state(
                    storage_state,
                    allowed_domains={"epwk.com", "task.epwk.com"},
                )
            except Exception as exc:
                result.failures.append(
                    CrawlFailure(
                        site_name="epwk",
                        stage="session_load",
                        error_type=type(exc).__name__,
                        message=safe_error_message(str(exc)),
                    )
                )
        adapter = EpwkAdapter(config, client, lambda *_: None)
        for position, candidate in enumerate(selected, start=1):
            try:
                entry = EpwkSitemapEntry(
                    task_key=candidate.task_id,
                    detail_url=candidate.detail_url,
                    published_at=candidate.published_at,
                    sitemap_url=candidate.sitemap_url,
                )
                record = adapter._sitemap_detail_record(result, entry, position=position)
                record.metadata["quality_sample_source"] = "historical_epwk_records"
                result.records.append(record)
                consecutive_failures = 0
            except Exception as exc:
                result.failures.append(
                    CrawlFailure(
                        site_name="epwk",
                        stage="quality_sample_detail",
                        error_type=type(exc).__name__,
                        message=safe_error_message(str(exc)),
                        url=candidate.detail_url,
                        retryable=not isinstance(exc, BlockedResponseError),
                    )
                )
                consecutive_failures += 1
                if isinstance(exc, BlockedResponseError) or consecutive_failures >= max_consecutive_failures:
                    result.stop_reason = "blocked_response" if isinstance(exc, BlockedResponseError) else "consecutive_failure_limit"
                    break
            if position % 25 == 0:
                atomic_write_json(
                    output_dir / "progress.json",
                    {
                        "processed": position,
                        "requested": limit,
                        "records": len(result.records),
                        "failures": len(result.failures),
                        "last_task_id": candidate.task_id,
                        "updated_at": utc_now_iso(),
                    },
                )

    if result.stop_reason is None:
        result.stop_reason = "sample_limit_reached"
    result.finish()
    export_site_result(output_dir, result)
    export_run_report(output_dir, [result])
    atomic_write_json(
        output_dir / "progress.json",
        {
            "processed": result.details_fetched + len(result.failures),
            "requested": limit,
            "records": len(result.records),
            "failures": len(result.failures),
            "finished_at": result.finished_at,
            "stop_reason": result.stop_reason,
        },
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded EPWK detail quality resample.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=150)
    args = parser.parse_args()

    root = args.project_root.resolve()
    input_path = args.input if args.input.is_absolute() else root / args.input
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    result = run_sample(
        project_root=root,
        input_path=input_path,
        output_dir=output_dir,
        limit=args.limit,
    )
    print(json.dumps(result.report_dict(), ensure_ascii=False, indent=2))
    return 0 if result.records else 1


if __name__ == "__main__":
    raise SystemExit(main())
