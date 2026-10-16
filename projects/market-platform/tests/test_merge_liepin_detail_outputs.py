import json
import tempfile
import unittest
from pathlib import Path

from tools.merge_liepin_detail_outputs import atomic_write_jsonl, merge_rows


class MergeLiepinDetailOutputsTests(unittest.TestCase):
    def test_merge_deduplicates_and_prefers_longer_jd_in_source_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            source.write_text(
                "".join(
                    json.dumps({"record_id": key, "detail_url": f"https://example/{key}"}) + "\n"
                    for key in ("a", "b", "c")
                ),
                encoding="utf-8",
            )
            first.write_text(
                json.dumps({"record_id": "b", "jd": "short"}) + "\n"
                + json.dumps({"record_id": "a", "jd": "alpha"}) + "\n",
                encoding="utf-8",
            )
            second.write_text(
                json.dumps({"record_id": "b", "jd": "longer detail"}) + "\n",
                encoding="utf-8",
            )

            rows, stats = merge_rows(source, [first, second])

            self.assertEqual([row["record_id"] for row in rows], ["a", "b"])
            self.assertEqual(rows[1]["jd"], "longer detail")
            self.assertEqual(stats["duplicates_removed"], 1)

            output = root / "output.jsonl"
            atomic_write_jsonl(output, rows)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
