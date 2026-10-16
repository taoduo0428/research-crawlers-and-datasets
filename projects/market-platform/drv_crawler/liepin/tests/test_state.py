from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from drv_crawler.liepin.models import CareerCategory, LiepinJobRecord
from drv_crawler.liepin.state import LiepinStateStore, atomic_replace, atomic_write_json


class StateTests(unittest.TestCase):
    @patch("drv_crawler.liepin.state.time.sleep")
    @patch("drv_crawler.liepin.state.os.replace")
    def test_atomic_replace_retries_transient_windows_lock(
        self,
        replace: MagicMock,
        sleep: MagicMock,
    ) -> None:
        replace.side_effect = [PermissionError("locked"), None]
        atomic_replace(Path("source.tmp"), Path("destination.json"))
        self.assertEqual(2, replace.call_count)

    def test_atomic_json_write_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "coverage.json"
            atomic_write_json(destination, {"status": "ok"})
            self.assertIn('"status": "ok"', destination.read_text(encoding="utf-8"))
            self.assertEqual([destination], list(Path(directory).iterdir()))

    def test_old_checkpoint_migrates_and_reopens_old_small_done_category(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE categories (
                    url TEXT PRIMARY KEY, level_1 TEXT NOT NULL, level_2 TEXT NOT NULL,
                    level_3 TEXT NOT NULL, path TEXT NOT NULL, active INTEGER NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL,
                    record_count INTEGER NOT NULL, last_error TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE jobs (
                    record_id TEXT PRIMARY KEY, source_job_id TEXT NOT NULL,
                    category_url TEXT NOT NULL, payload_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL, UNIQUE(category_url, source_job_id)
                );
                INSERT INTO categories VALUES(
                    'https://www.liepin.com/career/java/', 'IT', '开发', 'Java',
                    'IT > 开发 > Java', 1, 'done', 1, 1, NULL, '2026-07-22T00:00:00+00:00'
                );
                """
            )
            connection.commit()
            connection.close()

            with LiepinStateStore(path) as state:
                reopened = state.prepare_run(
                    quota_target=50, numeric_salary_only=False
                )
                columns = {
                    row["name"]
                    for row in state.connection.execute("PRAGMA table_info(categories)")
                }
                self.assertEqual(1, reopened)
                self.assertTrue(
                    {"completion_reason", "pages_fetched", "filtered_non_numeric"}
                    <= columns
                )
                category = state.list_candidates(
                    limit=None, retry_failed=True, max_attempts=2
                )[0]
                self.assertEqual("Java", category.level_3)

    def test_completion_is_atomic_and_reports_quota_outcome(self) -> None:
        category = CareerCategory(
            "IT", "开发", "Java", "https://www.liepin.com/career/java/"
        )
        with tempfile.TemporaryDirectory() as directory:
            with LiepinStateStore(Path(directory) / "checkpoint.sqlite3") as state:
                state.upsert_taxonomy([category], snapshot_hash="fixture")
                state.prepare_run(quota_target=50, numeric_salary_only=True)
                self.assertTrue(state.claim_category(category, max_attempts=2))
                record = LiepinJobRecord(
                    category=category,
                    job_id="1",
                    job_kind=None,
                    title="Java开发",
                    salary_raw="10-20k",
                    detail_url="https://www.liepin.com/job/1.shtml",
                    list_position=1,
                    page_no=1,
                )
                state.complete_category(
                    category,
                    [record],
                    completion_reason="exhausted_below_quota",
                    pages_fetched=1,
                    filtered_non_numeric=2,
                    quota_target=50,
                )
                coverage = state.coverage()
                self.assertEqual(1, coverage["quota_outcomes"]["exhausted_below_quota"])
                self.assertEqual(
                    "exhausted_below_quota",
                    coverage["categories"][0]["quota_outcome"],
                )


if __name__ == "__main__":
    unittest.main()
