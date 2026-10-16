from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

from drv_crawler.exporters import CSV_FIELDS, write_csv, write_jsonl, write_xlsx
from drv_crawler.models import CrawlRecord, PriceEvidence
from tools.enrich_original_categories import (
    CategoryEnrichmentError,
    _load_checkpoint,
    _load_site_category_checkpoints,
    _replace_site_exports,
)


def make_record(category: str | None) -> CrawlRecord:
    record = CrawlRecord(
        site_name="demo",
        content_category="outsourcing_task",
        title_raw="具体任务标题",
        list_url="https://example.test/list",
        detail_url="https://example.test/detail/1",
        page_no=1,
        list_position=1,
        category_level_1_raw=category,
        category_path_raw=category,
        category_source="detail_breadcrumb" if category else None,
        category_source_url="https://example.test/category" if category else None,
        category_identity_verified=bool(category),
    )
    record.add_price(
        PriceEvidence(
            raw_text="1000元",
            price_type="fixed_amount",
            price_source="detail_summary",
            evidence_url=record.detail_url or record.list_url,
        )
    )
    return record


class CategoryExportReplacementTests(unittest.TestCase):
    def test_empty_checkpoint_is_recoverable(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "epwk-part-0-of-1.jsonl"
            path.write_text("", encoding="utf-8")

            self.assertEqual({}, _load_checkpoint(path))

    def test_checkpoint_loader_ignores_failure_log(self) -> None:
        with TemporaryDirectory() as temporary:
            state_dir = Path(temporary)
            checkpoint = {
                "record_id": "epwk-1",
                "detail_url": "https://task.epwk.com/1/",
                "title_raw": "具体任务标题",
                "category_level_1_raw": "开发",
                "category_level_2_raw": "软件开发",
                "category_level_3_raw": "其他软件开发",
                "category_path_raw": "开发 > 软件开发 > 其他软件开发",
                "category_source": "detail_breadcrumb",
                "category_source_url": "https://task.epwk.com/1/",
                "category_identity_verified": True,
            }
            (state_dir / "epwk-part-0-of-1.jsonl").write_text(
                json.dumps(checkpoint, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            (state_dir / "epwk-part-0-of-1-failures.jsonl").write_text(
                json.dumps(
                    {
                        "record_id": "epwk-failed",
                        "error_type": "CategoryParseError",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            loaded = _load_site_category_checkpoints(state_dir, "epwk")

            self.assertEqual({"epwk-1"}, set(loaded))

    def test_replaces_all_three_exports_with_category_columns(self) -> None:
        with TemporaryDirectory() as temporary:
            site_dir = Path(temporary)
            original = [make_record(None)]
            write_jsonl(site_dir / "records.jsonl", original)
            write_csv(site_dir / "records.csv", original)
            write_xlsx(site_dir / "records.xlsx", original)

            _replace_site_exports(site_dir, [make_record("软件开发")])

            row = json.loads(
                (site_dir / "records.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertEqual("软件开发", row["category_level_1_raw"])
            self.assertTrue(row["category_identity_verified"])
            workbook = load_workbook(
                site_dir / "records.xlsx", read_only=True, data_only=True
            )
            headers = [cell.value for cell in workbook.active[1]]
            workbook.close()
            self.assertEqual(CSV_FIELDS, headers)
            self.assertFalse(list(site_dir.glob(".category-*")))

    def test_retains_backup_when_commit_and_rollback_both_fail(self) -> None:
        with TemporaryDirectory() as temporary:
            site_dir = Path(temporary)
            original = [make_record(None)]
            write_jsonl(site_dir / "records.jsonl", original)
            write_csv(site_dir / "records.csv", original)
            write_xlsx(site_dir / "records.xlsx", original)
            real_replace = os.replace

            def fail_commit_and_rollback(source: object, destination: object) -> None:
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    source_path.parent.name.startswith(".category-stage-")
                    and destination_path == site_dir / "records.csv"
                ):
                    raise OSError("injected commit failure")
                if (
                    source_path.parent.name.startswith(".category-backup-")
                    and destination_path == site_dir / "records.jsonl"
                ):
                    raise OSError("injected rollback failure")
                real_replace(source, destination)

            with (
                patch(
                    "tools.enrich_original_categories.os.replace",
                    side_effect=fail_commit_and_rollback,
                ),
                self.assertRaises(CategoryEnrichmentError),
            ):
                _replace_site_exports(site_dir, [make_record("软件开发")])

            backups = list(site_dir.glob(".category-backup-*"))
            self.assertEqual(1, len(backups))
            self.assertTrue((backups[0] / "records.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
