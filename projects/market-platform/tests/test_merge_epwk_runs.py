from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from drv_crawler.models import CrawlRecord, PriceEvidence
from drv_crawler.publication import PublicationFilter
from tools.merge_epwk_runs import MergeValidationError, merge_epwk_runs


class MergeEpwkRunsTests(unittest.TestCase):
    def _record(
        self,
        task_id: str,
        *,
        published_at: str = "2026.07.21",
        detail_url: str | None = None,
    ) -> CrawlRecord:
        record = CrawlRecord(
            site_name="epwk",
            content_category="outsourcing_task",
            title_raw=f"task {task_id}",
            list_url="https://task.epwk.com/?o=7",
            detail_url=detail_url or f"https://task.epwk.com/{task_id}/",
            page_no=1,
            list_position=1,
            published_at_raw=published_at,
        )
        record.add_price(
            PriceEvidence(
                raw_text="1000元",
                price_type="fixed_amount",
                price_source="list_page",
                evidence_url=record.list_url,
            )
        )
        return record

    def _write_run(
        self,
        root: Path,
        name: str,
        records: list[CrawlRecord],
        *,
        as_of: date = date(2026, 7, 21),
    ) -> Path:
        run_dir = root / name
        site_dir = run_dir / "epwk"
        site_dir.mkdir(parents=True)
        publication_filter = PublicationFilter(
            recent_months=3,
            as_of=as_of,
        ).describe()
        report = {
            "site_name": "epwk",
            "status": "partial",
            "adapter": "static_html",
            "difficulty": 2,
            "pages_fetched": 3,
            "details_fetched": len(records),
            "filtered_out_count": 4,
            "filter_counts": {"publication_too_old": 4},
            "publication_filter": publication_filter,
            "completion_mode": "publication_window",
            "window_complete": False,
            "stop_reason": "bounded_run",
            "source_coverage": {
                "tasks": {"complete": False, "stop_reason": "bounded_run"}
            },
            "quality": {
                "record_count": len(records),
                "requested_limit": 10000,
            },
            "runtime_notes": [],
            "failures": [],
        }
        (site_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (site_dir / "records.jsonl").write_text(
            "".join(
                json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        return run_dir

    def _write_recovery(
        self,
        root: Path,
        name: str,
        records: list[CrawlRecord],
        *,
        as_of: date = date(2026, 7, 21),
        unresolved_count: int = 0,
    ) -> Path:
        run_dir = root / name
        run_dir.mkdir(parents=True)
        report = {
            "site_name": "epwk",
            "publication_filter": PublicationFilter(
                recent_months=3,
                as_of=as_of,
            ).describe(),
            "target_count": len(records) + unresolved_count,
            "recovered_count": len(records),
            "unresolved_count": unresolved_count,
        }
        (run_dir / "recovery-report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (run_dir / "recovered-records.jsonl").write_text(
            "".join(
                json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        return run_dir

    def test_deduplicates_canonical_detail_urls_and_exports_all_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = self._write_run(
                root,
                "run-1",
                [
                    self._record(
                        "100",
                        detail_url="http://TASK.EPWK.COM/100/?utm_source=test#part",
                    )
                ],
            )
            second = self._write_run(
                root,
                "run-2",
                [self._record("100"), self._record("101")],
            )
            output_dir = root / "merged"

            result = merge_epwk_runs([first, second], output_dir)

            self.assertEqual(2, len(result.records))
            self.assertEqual(
                {"https://task.epwk.com/100/", "https://task.epwk.com/101/"},
                {record.detail_url for record in result.records},
            )
            self.assertFalse(result.window_complete)
            self.assertEqual("verified_union_not_global", result.stop_reason)
            self.assertEqual(2, len(result.source_coverage))
            self.assertIn("merge_input_records=3", result.runtime_notes)
            self.assertIn("merge_deduplicated_records=2", result.runtime_notes)
            self.assertIn("merge_duplicates_removed=1", result.runtime_notes)

            expected_files = {
                output_dir / "epwk" / "records.jsonl",
                output_dir / "epwk" / "records.csv",
                output_dir / "epwk" / "records.xlsx",
                output_dir / "epwk" / "report.json",
                output_dir / "epwk" / "report.md",
                output_dir / "run-report.json",
                output_dir / "run-report.md",
            }
            self.assertTrue(all(path.is_file() for path in expected_files))
            report = json.loads(
                (output_dir / "epwk" / "report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(report["window_complete"])
            self.assertEqual("verified_union_not_global", report["stop_reason"])

    def test_accepts_recovery_output_and_counts_unresolved_targets(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = self._write_run(root, "run-1", [self._record("100")])
            recovery = self._write_recovery(
                root,
                "recovery",
                [
                    self._record(
                        "100",
                        detail_url="https://task.epwk.com/100/?retry=1",
                    ),
                    self._record("101"),
                ],
                unresolved_count=2,
            )

            result = merge_epwk_runs([first, recovery], root / "merged")

            self.assertEqual(2, len(result.records))
            recovery_coverage = result.source_coverage["input_002"]
            self.assertEqual("recovery", recovery_coverage["input_kind"])
            self.assertEqual(2, recovery_coverage["failure_count"])
            self.assertEqual(
                {
                    "target_count": 4,
                    "recovered_count": 2,
                    "unresolved_count": 2,
                },
                recovery_coverage["report"]["source_coverage"],
            )
            self.assertTrue(
                any(
                    note.startswith("merge_input_failure_count=2;")
                    for note in result.runtime_notes
                )
            )

    def test_rejects_mismatched_windows_without_creating_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = self._write_run(root, "run-1", [self._record("100")])
            second = self._write_run(
                root,
                "run-2",
                [self._record("101", published_at="2026.07.20")],
                as_of=date(2026, 7, 20),
            )
            output_dir = root / "merged"

            with self.assertRaisesRegex(MergeValidationError, "does not match"):
                merge_epwk_runs([first, second], output_dir)

            self.assertFalse(output_dir.exists())

    def test_rejects_out_of_window_record_without_creating_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = self._write_run(root, "run-1", [self._record("100")])
            second = self._write_recovery(
                root,
                "recovery",
                [self._record("101", published_at="2026.04.20")],
            )
            output_dir = root / "merged"

            with self.assertRaisesRegex(MergeValidationError, "inclusive window"):
                merge_epwk_runs([first, second], output_dir)

            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
