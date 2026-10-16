from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .browser import BossAdapter
from .core import ProjectConfig, ResilientHttpClient, atomic_write_json
from .exporters import export_run_report, export_site_result
from .models import CrawlFailure, SiteResult
from .publication import PublicationFilter
from .sites import STATIC_ADAPTERS, BaseSiteAdapter


LOGGER = logging.getLogger("drv_crawler.runner")


ADAPTERS: dict[str, type[BaseSiteAdapter]] = {**STATIC_ADAPTERS, "boss": BossAdapter}


class CheckpointManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, result: SiteResult, stage: str) -> None:
        value: dict[str, Any] = {
            "site_name": result.site_name,
            "stage": stage,
            "requested_limit": result.requested_limit,
            "pages_fetched": result.pages_fetched,
            "details_fetched": result.details_fetched,
            "filtered_out_count": result.filtered_out_count,
            "filter_counts": result.filter_counts,
            "publication_filter": result.publication_filter,
            "completion_mode": result.completion_mode,
            "window_complete": result.window_complete,
            "stop_reason": result.stop_reason,
            "source_coverage": result.source_coverage,
            "record_ids": [record.record_id for record in result.records],
            "records": [
                record.to_dict()
                for record in (
                    result.records
                    if result.completion_mode == "publication_window"
                    else result.records[: result.requested_limit]
                )
            ],
            "failures": [failure.to_dict() for failure in result.failures],
        }
        atomic_write_json(self.root / f"{result.site_name}.json", value)


def make_run_dir(config: ProjectConfig, *, mode: str) -> Path:
    export = config.section("export")
    root = Path(os.getenv("CRAWLER_OUTPUT_DIR", str(export.get("directory", "outputs"))))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    run_dir = root / f"{mode}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def run_sites(
    config: ProjectConfig,
    *,
    site_names: list[str],
    limit: int,
    with_details: bool,
    mode: str,
    numeric_price_only: bool = False,
    publication_filter: PublicationFilter | None = None,
    complete_publication_window: bool = False,
    detail_limit: int | None = None,
) -> tuple[Path, list[SiteResult], dict[str, Any]]:
    run_dir = make_run_dir(config, mode=mode)
    checkpoint = CheckpointManager(run_dir / "checkpoints")
    results: list[SiteResult] = []
    window_mode = complete_publication_window and publication_filter is not None
    for site_name in site_names:
        LOGGER.info("Starting %s sample with limit=%s", site_name, limit)
        site_result: SiteResult
        adapter_class = ADAPTERS.get(site_name)
        if adapter_class is None:
            site_result = SiteResult(
                site_name=site_name,
                requested_limit=limit,
                completion_mode=("publication_window" if window_mode else "target_count"),
                window_complete=False if window_mode else None,
                stop_reason="unknown_adapter" if window_mode else None,
            )
            site_result.failures.append(
                CrawlFailure(
                    site_name=site_name,
                    stage="planning",
                    error_type="UnknownAdapter",
                    message=f"No adapter is registered for {site_name}",
                )
            )
            site_result.finish()
        else:
            try:
                with ResilientHttpClient(config, site_name=site_name) as client:
                    adapter = adapter_class(config, client, checkpoint.save)
                    adapter.detail_limit_override = detail_limit
                    adapter.publication_filter = publication_filter
                    adapter.complete_publication_window = window_mode
                    site_result = adapter.crawl(
                        limit,
                        with_details=with_details,
                        numeric_price_only=numeric_price_only,
                    )
            except Exception as exc:
                LOGGER.exception("Uncaught site failure: %s", site_name)
                site_result = SiteResult(
                    site_name=site_name,
                    requested_limit=limit,
                    adapter=adapter_class.adapter_name,
                    difficulty=adapter_class.difficulty,
                    publication_filter=(
                        publication_filter.describe() if publication_filter else None
                    ),
                    completion_mode=(
                        "publication_window" if window_mode else "target_count"
                    ),
                    window_complete=False if window_mode else None,
                    stop_reason="site_error" if window_mode else None,
                )
                site_result.failures.append(
                    CrawlFailure(
                        site_name=site_name,
                        stage="site_run",
                        error_type=type(exc).__name__,
                        message=str(exc)[:500],
                    )
                )
                site_result.finish()
        export_site_result(run_dir, site_result)
        checkpoint.save(site_result, "exported")
        results.append(site_result)
        LOGGER.info(
            "Finished %s status=%s records=%s",
            site_name,
            site_result.status,
            len(site_result.records),
        )
    summary = export_run_report(run_dir, results)
    return run_dir, results, summary
