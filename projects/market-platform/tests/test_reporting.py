from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from drv_crawler.exporters import export_run_report, site_report_markdown, write_xlsx
from drv_crawler.models import CrawlRecord, PriceEvidence, SiteResult
from drv_crawler.publication import PublicationFilter
from drv_crawler.runner import CheckpointManager


class ReportingTests(unittest.TestCase):
    def test_xlsx_export_removes_xml_illegal_control_characters(self) -> None:
        record = CrawlRecord(
            site_name="demo",
            content_category="full_time_job",
            title_raw="业务工程师\x00一员\x0b",
            list_url="https://example.test/jobs",
            detail_url=None,
            page_no=1,
            list_position=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.xlsx"
            write_xlsx(path, [record])
            workbook = load_workbook(path, read_only=True, data_only=True)
            headers = [cell.value for cell in workbook.active[1]]
            values = [cell.value for cell in workbook.active[2]]
            workbook.close()

        row = dict(zip(headers, values, strict=True))
        self.assertEqual("业务工程师一员", row["title_raw"])

    def test_publication_filter_metadata_and_counts_are_persisted(self) -> None:
        result = SiteResult(site_name="demo", requested_limit=2)
        try:
            result.publication_filter = PublicationFilter(
                recent_months=3,
                as_of=__import__("datetime").date(2026, 7, 21),
            ).describe()
            result.filter_counts = {
                "publication_too_old": 2,
                "publication_unknown": 1,
            }
        except AttributeError as exc:
            self.fail(f"SiteResult publication reporting fields are missing: {exc}")

        root = Path(tempfile.mkdtemp())
        CheckpointManager(root).save(result, "filtered")
        payload = json.loads((root / "demo.json").read_text(encoding="utf-8"))
        self.assertEqual("2026-04-21", payload["publication_filter"]["cutoff"])
        self.assertEqual(2, payload["filter_counts"]["publication_too_old"])
        self.assertIn("publication cutoff: `2026-04-21`", site_report_markdown(result))

    def test_checkpoint_persists_filtered_out_count(self) -> None:
        root = Path(tempfile.mkdtemp())
        result = SiteResult(
            site_name="demo",
            requested_limit=2,
            filtered_out_count=3,
        )

        CheckpointManager(root).save(result, "listing")

        payload = json.loads((root / "demo.json").read_text(encoding="utf-8"))
        self.assertEqual(3, payload["filtered_out_count"])

    def test_window_completion_is_persisted_without_record_truncation(self) -> None:
        root = Path(tempfile.mkdtemp())
        result = SiteResult(
            site_name="demo",
            requested_limit=1,
            completion_mode="publication_window",
            window_complete=True,
            stop_reason="cutoff_reached",
            source_coverage={
                "software": {"complete": True, "stop_reason": "cutoff_reached"}
            },
        )
        record = CrawlRecord(
            site_name="demo",
            content_category="outsourcing_task",
            title_raw="first",
            list_url="https://example.test/list",
            detail_url="https://example.test/first",
            page_no=1,
            list_position=1,
        )
        record.add_price(
            PriceEvidence(
                raw_text="100元",
                price_type="test",
                price_source="test",
                evidence_url=record.list_url,
            )
        )
        second = CrawlRecord(
            site_name="demo",
            content_category="outsourcing_task",
            title_raw="second",
            list_url="https://example.test/list",
            detail_url="https://example.test/second",
            page_no=1,
            list_position=2,
        )
        second.add_price(
            PriceEvidence(
                raw_text="200元",
                price_type="test",
                price_source="test",
                evidence_url=second.list_url,
            )
        )
        second.record_id = "demo-second"
        result.records = [record, second]

        CheckpointManager(root).save(result, "finished")

        payload = json.loads((root / "demo.json").read_text(encoding="utf-8"))
        self.assertEqual("publication_window", payload["completion_mode"])
        self.assertTrue(payload["window_complete"])
        self.assertEqual("cutoff_reached", payload["stop_reason"])
        self.assertTrue(payload["source_coverage"]["software"]["complete"])
        self.assertEqual(2, len(payload["records"]))
        self.assertIn("publication window complete: `True`", site_report_markdown(result))
        summary = export_run_report(root, [result])
        self.assertEqual(1, summary["totals"]["publication_windows"])
        self.assertEqual(1, summary["totals"]["publication_windows_complete"])

    def test_markdown_reports_filtered_out_count(self) -> None:
        result = SiteResult(
            site_name="demo",
            requested_limit=2,
            filtered_out_count=3,
        )

        site_report = site_report_markdown(result)
        self.assertIn("filtered out: `3`", site_report)

        run_dir = Path(tempfile.mkdtemp())
        summary = export_run_report(run_dir, [result])
        run_report = (run_dir / "run-report.md").read_text(encoding="utf-8")
        self.assertEqual(3, summary["totals"]["filtered_out"])
        self.assertIn("filtered=3", run_report)


if __name__ == "__main__":
    unittest.main()
