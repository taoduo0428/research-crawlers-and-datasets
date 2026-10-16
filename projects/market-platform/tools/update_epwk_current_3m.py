from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from drv_crawler.core import BlockedResponseError, ProjectConfig, ResilientHttpClient, atomic_write_json
from drv_crawler.exporters import write_csv, write_jsonl, write_xlsx
from drv_crawler.models import CrawlFailure, CrawlRecord, PriceEvidence, SiteResult, clean_text, utc_now_iso
from drv_crawler.sites import EpwkAdapter, text_of

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLD = ROOT / "outputs/外包--完整数据/一品威客/epwk/records.jsonl"
DEFAULT_OUTPUT = ROOT / "outputs/epwk-current-3m-20260820"
AS_OF = date(2026, 8, 20)
CUTOFF = date(2026, 5, 20)
OLD_SNAPSHOT_AS_OF = date(2026, 7, 21)
MAIN_MODES = {"招标", "雇佣"}


@dataclass(slots=True)
class Candidate:
    task_id: str
    detail_url: str
    window_date: str
    date_basis: str
    sitemap_url: str | None = None
    old_snapshot_present: bool = False
    old_published_at_raw: str | None = None
    title_hint: str | None = None


def canonical_url(value: str) -> str:
    return value.strip().rstrip("/") + "/"


def parse_date(value: str | None) -> date | None:
    raw = (value or "").strip().replace(".", "-")
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def load_old(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            url = str(payload.get("detail_url") or "").strip()
            if not url:
                continue
            rows[canonical_url(url)] = payload
    return rows


def prepare_manifest(*, old_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    old_rows = load_old(old_path)
    old_window: dict[str, dict[str, Any]] = {}
    for url, payload in old_rows.items():
        published = parse_date(str(payload.get("published_at_raw") or ""))
        if published is not None and CUTOFF <= published <= AS_OF:
            old_window[url] = payload

    config = ProjectConfig(ROOT / "crawler.config.yaml")
    site = config.site("epwk")
    state = ROOT / str(site.get("storage_state") or ".crawler-state/epwk-storage-state.json")
    result = SiteResult(site_name="epwk", requested_limit=1)
    with ResilientHttpClient(config, site_name="epwk") as client:
        if state.exists():
            client.load_storage_state(state, allowed_domains={"epwk.com", "task.epwk.com"})
        adapter = EpwkAdapter(config, client, lambda *_: None)
        inventory = adapter._load_publication_sitemap_inventory(result)

    candidates: dict[str, Candidate] = {}
    overlap_exact = 0
    overlap_mismatch = 0
    for entry in inventory.entries:
        sitemap_date = parse_date(entry.published_at)
        if sitemap_date is None or not (CUTOFF <= sitemap_date <= AS_OF):
            continue
        url = canonical_url(entry.detail_url)
        old = old_window.get(url)
        old_date = parse_date(str(old.get("published_at_raw") or "")) if old else None
        if old_date is not None:
            if old_date == sitemap_date:
                overlap_exact += 1
            else:
                overlap_mismatch += 1
        candidates[url] = Candidate(
            task_id=entry.task_key,
            detail_url=url,
            window_date=(old_date or sitemap_date).isoformat(),
            date_basis="old_verified_detail" if old_date is not None else "sitemap_lastmod_candidate",
            sitemap_url=entry.sitemap_url,
            old_snapshot_present=old is not None,
            old_published_at_raw=str(old.get("published_at_raw") or "") or None if old else None,
            title_hint=str(old.get("title_raw") or "") or None if old else None,
        )

    old_only = 0
    for url, old in old_window.items():
        if url in candidates:
            continue
        old_date = parse_date(str(old.get("published_at_raw") or ""))
        if old_date is None:
            continue
        task_id = EpwkAdapter._task_key(url)
        if not task_id:
            continue
        metadata = old.get("metadata") if isinstance(old.get("metadata"), dict) else {}
        candidates[url] = Candidate(
            task_id=task_id,
            detail_url=url,
            window_date=old_date.isoformat(),
            date_basis="old_verified_detail_old_only",
            sitemap_url=str(metadata.get("publication_sitemap_url") or "") or None,
            old_snapshot_present=True,
            old_published_at_raw=str(old.get("published_at_raw") or "") or None,
            title_hint=str(old.get("title_raw") or "") or None,
        )
        old_only += 1

    def priority(item: Candidate) -> tuple[int, str, int]:
        candidate_date = parse_date(item.window_date) or CUTOFF
        if not item.old_snapshot_present and candidate_date > OLD_SNAPSHOT_AS_OF:
            bucket = 0  # genuinely new since the 2026-07-21 snapshot
        elif item.old_snapshot_present:
            bucket = 1  # reusable current-window records from the historical snapshot
        else:
            bucket = 2  # historical misses; often the old 403/unresolved tail
        return bucket, item.window_date, int(item.task_id)

    ordered = sorted(candidates.values(), key=priority)
    manifest_path = output_dir / "candidates.jsonl"
    manifest_path.write_text(
        "".join(json.dumps(asdict(item), ensure_ascii=False) + "\n" for item in ordered),
        encoding="utf-8",
    )
    summary = {
        "prepared_at": utc_now_iso(),
        "as_of": AS_OF.isoformat(),
        "cutoff": CUTOFF.isoformat(),
        "old_snapshot_path": str(old_path),
        "old_snapshot_total": len(old_rows),
        "old_snapshot_in_current_window": len(old_window),
        "sitemap_inventory_complete": inventory.complete,
        "sitemap_window_candidates": sum(1 for item in ordered if item.sitemap_url is not None),
        "candidate_union_count": len(ordered),
        "candidate_not_in_old_snapshot": sum(not item.old_snapshot_present for item in ordered),
        "new_since_old_snapshot_count": sum(
            (not item.old_snapshot_present)
            and ((parse_date(item.window_date) or CUTOFF) > OLD_SNAPSHOT_AS_OF)
            for item in ordered
        ),
        "historical_missed_candidate_count": sum(
            (not item.old_snapshot_present)
            and ((parse_date(item.window_date) or CUTOFF) <= OLD_SNAPSHOT_AS_OF)
            for item in ordered
        ),
        "old_only_verified_count": old_only,
        "overlap_exact_date_matches": overlap_exact,
        "overlap_date_mismatches": overlap_mismatch,
        "runtime_notes": result.runtime_notes,
        "failures": [failure.to_dict() for failure in result.failures],
    }
    atomic_write_json(output_dir / "manifest-summary.json", summary)
    return summary


def load_candidates(path: Path) -> list[Candidate]:
    result: list[Candidate] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                result.append(Candidate(**json.loads(line)))
    return result


def title_from_soup(soup: Any, hint: str | None) -> str | None:
    title = text_of(soup.select_one("div.task_user_info h1"))
    if not title:
        breadcrumbs = soup.select(".cct-bread-crumb a")
        title = text_of(breadcrumbs[-1]) if breadcrumbs else None
    if not title:
        title = text_of(soup.select_one(".contxt .rctr .title"))
    return title or clean_text(hint)


def record_from_payload(payload: dict[str, Any]) -> CrawlRecord:
    value = dict(payload)
    value.pop("price_raw_joined", None)
    value.pop("core_complete", None)
    raw_evidence = value.pop("price_evidence", [])
    evidence = [PriceEvidence(**item) for item in raw_evidence if isinstance(item, dict)]
    return CrawlRecord(**value, price_evidence=evidence)


def _process_candidate_group(
    indexed_candidates: list[tuple[int, Candidate]],
    *,
    read_timeout: float,
) -> tuple[list[CrawlRecord], list[CrawlFailure]]:
    config = ProjectConfig(ROOT / "crawler.config.yaml")
    site = config.site("epwk")
    state = ROOT / str(site.get("storage_state") or ".crawler-state/epwk-storage-state.json")
    records: list[CrawlRecord] = []
    failures: list[CrawlFailure] = []
    site_result = SiteResult(site_name="epwk", requested_limit=len(indexed_candidates))
    site_result.adapter = "EpwkAdapter-current-3m-enrichment"
    with ResilientHttpClient(config, site_name="epwk") as client:
        client.timeout = (8.0, read_timeout)
        if state.exists():
            client.load_storage_state(state, allowed_domains={"epwk.com", "task.epwk.com"})
        adapter = EpwkAdapter(config, client, lambda *_: None)
        start_url = str(site["start_url"])
        for index, candidate in indexed_candidates:
            try:
                page = client.get(candidate.detail_url, referer=start_url)
                soup = page.soup()
                title = title_from_soup(soup, candidate.title_hint)
                if not title:
                    raise ValueError(f"detail title missing for task {candidate.task_id}")
                metadata: dict[str, Any] = {
                    "current_window_as_of": AS_OF.isoformat(),
                    "current_window_cutoff": CUTOFF.isoformat(),
                    "current_window_candidate_date": candidate.window_date,
                    "current_window_date_basis": candidate.date_basis,
                    "current_window_old_snapshot_present": candidate.old_snapshot_present,
                    "current_window_sitemap_url": candidate.sitemap_url,
                    "task_id": candidate.task_id,
                }
                published_at_raw = candidate.old_published_at_raw if candidate.old_snapshot_present else None
                if published_at_raw:
                    metadata["publication_date_source"] = "historical_verified_detail"
                record = CrawlRecord(
                    site_name="epwk",
                    content_category="outsourcing_task",
                    title_raw=title,
                    detail_title_raw=title,
                    list_url=start_url,
                    detail_url=candidate.detail_url,
                    page_no=0,
                    list_position=index + 1,
                    published_at_raw=published_at_raw,
                    metadata=metadata,
                )
                adapter._populate_detail(site_result, record, detail_soup=soup)
                records.append(record)
            except Exception as exc:
                failures.append(
                    CrawlFailure(
                        site_name="epwk",
                        stage="current_3m_enrichment",
                        error_type=type(exc).__name__,
                        message=str(exc)[:1000],
                        url=candidate.detail_url,
                        retryable=not isinstance(exc, BlockedResponseError),
                    )
                )
                if isinstance(exc, BlockedResponseError) and getattr(exc, "status_code", None) == 429:
                    break
    return records, failures


def process_batch(
    *,
    output_dir: Path,
    offset: int,
    limit: int,
    workers: int = 1,
    read_timeout: float = 20.0,
) -> dict[str, Any]:
    candidates = load_candidates(output_dir / "candidates.jsonl")
    selected = candidates[offset : offset + limit]
    if not selected:
        raise ValueError("selected batch is empty")
    batch_dir = output_dir / "batches" / f"batch-{offset:04d}-{offset + len(selected) - 1:04d}"
    if batch_dir.exists():
        raise FileExistsError(f"batch output already exists: {batch_dir}")
    batch_dir.mkdir(parents=True)

    records: list[CrawlRecord] = []
    failures: list[CrawlFailure] = []
    workers = max(1, min(int(workers), 4, len(selected)))
    indexed = list(enumerate(selected, start=offset))
    groups = [indexed[i::workers] for i in range(workers)]
    if workers == 1:
        records, failures = _process_candidate_group(groups[0], read_timeout=read_timeout)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_process_candidate_group, group, read_timeout=read_timeout)
                for group in groups
                if group
            ]
            for future in futures:
                worker_records, worker_failures = future.result()
                records.extend(worker_records)
                failures.extend(worker_failures)
    records.sort(key=lambda record: record.list_position)

    main_records = [record for record in records if record.job_type_raw in MAIN_MODES]
    write_jsonl(batch_dir / "all-records.jsonl", records)
    write_csv(batch_dir / "all-records.csv", records)
    write_xlsx(batch_dir / "all-records.xlsx", records)
    write_jsonl(batch_dir / "main-records.jsonl", main_records)
    write_csv(batch_dir / "main-records.csv", main_records)
    write_xlsx(batch_dir / "main-records.xlsx", main_records)
    summary = {
        "offset": offset,
        "requested": len(selected),
        "processed": len(records),
        "failures": len(failures),
        "workers": workers,
        "read_timeout_seconds": read_timeout,
        "main_records": len(main_records),
        "description_success": sum(bool(record.description_raw) for record in main_records),
        "trade_modes": {
            mode: sum(record.job_type_raw == mode for record in records)
            for mode in sorted({record.job_type_raw for record in records if record.job_type_raw})
        },
        "statuses": {
            status: sum(record.status_raw == status for record in records)
            for status in sorted({record.status_raw for record in records if record.status_raw})
        },
        "completed": sum(record.metadata.get("is_completed") is True for record in main_records),
        "failure_items": [failure.to_dict() for failure in failures],
        "finished_at": utc_now_iso(),
    }
    atomic_write_json(batch_dir / "summary.json", summary)
    return summary


def merge_batches(*, output_dir: Path) -> dict[str, Any]:
    manifest = load_candidates(output_dir / "candidates.jsonl")
    records_by_url: dict[str, CrawlRecord] = {}
    batch_summaries = []
    for batch_dir in sorted((output_dir / "batches").glob("batch-*")):
        summary_path = batch_dir / "summary.json"
        if summary_path.exists():
            batch_summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))
        path = batch_dir / "all-records.jsonl"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = record_from_payload(json.loads(line))
                records_by_url[canonical_url(record.detail_url or "")] = record
    all_records = list(records_by_url.values())
    all_records.sort(key=lambda record: (str(record.metadata.get("current_window_candidate_date") or ""), record.detail_url or ""))
    main_records = [record for record in all_records if record.job_type_raw in MAIN_MODES]
    final_dir = output_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(final_dir / "all-records.jsonl", all_records)
    write_csv(final_dir / "all-records.csv", all_records)
    write_xlsx(final_dir / "all-records.xlsx", all_records)
    write_jsonl(final_dir / "招标-雇佣-近三个月.jsonl", main_records)
    write_csv(final_dir / "招标-雇佣-近三个月.csv", main_records)
    write_xlsx(final_dir / "招标-雇佣-近三个月.xlsx", main_records)
    summary = {
        "as_of": AS_OF.isoformat(),
        "cutoff": CUTOFF.isoformat(),
        "candidate_count": len(manifest),
        "enriched_unique_count": len(all_records),
        "remaining_candidates": len(manifest) - len(all_records),
        "main_trade_mode_count": len(main_records),
        "description_success_count": sum(bool(record.description_raw) for record in main_records),
        "description_missing_count": sum(not bool(record.description_raw) for record in main_records),
        "completed_count": sum(record.metadata.get("is_completed") is True for record in main_records),
        "trade_modes": {
            mode: sum(record.job_type_raw == mode for record in all_records)
            for mode in sorted({record.job_type_raw for record in all_records if record.job_type_raw})
        },
        "batch_count": len(batch_summaries),
        "batch_failures": sum(int(item.get("failures", 0)) for item in batch_summaries),
        "updated_at": utc_now_iso(),
    }
    atomic_write_json(final_dir / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and enrich the current EPWK 3-month window without overwriting the historical snapshot.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--old", type=Path, default=DEFAULT_OLD)
    prepare.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    batch = sub.add_parser("batch")
    batch.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    batch.add_argument("--offset", type=int, required=True)
    batch.add_argument("--limit", type=int, default=200)
    batch.add_argument("--workers", type=int, default=1)
    batch.add_argument("--read-timeout", type=float, default=20.0)
    merge = sub.add_parser("merge")
    merge.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    if args.command == "prepare":
        old = args.old if args.old.is_absolute() else ROOT / args.old
        value = prepare_manifest(old_path=old, output_dir=output_dir)
    elif args.command == "batch":
        value = process_batch(
            output_dir=output_dir,
            offset=args.offset,
            limit=args.limit,
            workers=args.workers,
            read_timeout=args.read_timeout,
        )
    else:
        value = merge_batches(output_dir=output_dir)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
