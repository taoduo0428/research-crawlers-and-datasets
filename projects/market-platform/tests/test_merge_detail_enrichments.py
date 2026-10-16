from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.merge_detail_enrichments import atomic_write_jsonl, merge_rows


class MergeDetailEnrichmentsTests(unittest.TestCase):
    def _write(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_nonempty_detail_wins_without_duplicate_record(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.jsonl"
            shard = root / "shard.jsonl"
            self._write(base, [{"record_id": "one", "detail_url": "https://x/1", "description_raw": None}])
            self._write(shard, [{"record_id": "one", "detail_url": "https://x/1?a=1", "description_raw": "真实正文"}])

            rows = merge_rows([base, shard])

            self.assertEqual(1, len(rows))
            self.assertEqual("真实正文", rows[0]["description_raw"])

    def test_conflicting_url_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            self._write(first, [{"record_id": "one", "detail_url": "https://x/1"}])
            self._write(second, [{"record_id": "one", "detail_url": "https://x/2"}])
            with self.assertRaisesRegex(ValueError, "多个详情 URL"):
                merge_rows([first, second])

    def test_atomic_writer_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "details.jsonl"
            count = atomic_write_jsonl(output, [{"record_id": "one", "jd": "正文"}])
            self.assertEqual(1, count)
            self.assertEqual("one", json.loads(output.read_text(encoding="utf-8"))["record_id"])


if __name__ == "__main__":
    unittest.main()
