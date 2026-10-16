import json
import tempfile
import unittest
from pathlib import Path

from tools.run_liepin_camoufox_reverse import (
    OUTPUT_DEFAULT,
    STATE_DEFAULT,
    iter_jsonl_reverse,
)


class LiepinCamoufoxReverseTests(unittest.TestCase):
    def test_reverse_worker_has_isolated_output_and_state(self):
        self.assertEqual(OUTPUT_DEFAULT.name, "worker-reverse.jsonl")
        self.assertIn("liepin-camoufox-reverse", str(STATE_DEFAULT))
        self.assertTrue(isinstance(OUTPUT_DEFAULT, Path))

    def test_reverse_jsonl_reader_preserves_reverse_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for value in ({"id": 1}, {"id": 2}, {"id": 3}):
                    handle.write(json.dumps(value) + "\n")
            self.assertEqual(
                [row["id"] for row in iter_jsonl_reverse(path, chunk_size=5)],
                [3, 2, 1],
            )


if __name__ == "__main__":
    unittest.main()
