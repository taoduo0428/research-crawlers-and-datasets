from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.sample_epwk_quality import load_candidates, select_candidates


class EpwkQualitySampleToolTests(unittest.TestCase):
    def test_candidates_are_unique_valid_and_oldest_first(self) -> None:
        rows = [
            {
                "detail_url": "https://task.epwk.com/20/",
                "published_at_raw": "2026-06-02",
                "metadata": {},
            },
            {
                "detail_url": "https://task.epwk.com/10/",
                "published_at_raw": "2026-06-01",
                "metadata": {"publication_sitemap_lastmod": "2026-05-31"},
            },
            {
                "detail_url": "https://task.epwk.com/10/",
                "published_at_raw": "2026-06-01",
            },
            {"detail_url": "https://example.com/30/", "published_at_raw": "2026-05-01"},
            {"detail_url": "https://task.epwk.com/40/", "published_at_raw": None},
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            candidates = load_candidates(path)

        self.assertEqual(["10", "20"], [item.task_id for item in candidates])
        self.assertEqual("2026-05-31", candidates[0].published_at)

    def test_selection_limit_is_bounded(self) -> None:
        self.assertEqual([], select_candidates([], 1))
        with self.assertRaises(ValueError):
            select_candidates([], 0)
        with self.assertRaises(ValueError):
            select_candidates([], 201)


if __name__ == "__main__":
    unittest.main()
