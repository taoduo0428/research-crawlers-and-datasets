import unittest
from argparse import Namespace
import json
import tempfile
from pathlib import Path

from tools.run_liepin_camoufox_dual import resolve_start_index
from tools.liepin_coordination import REVERSE_OWNER, REVERSE_SECONDARY_OWNER
from tools.run_liepin_camoufox_reverse import iter_worker_rows


class FakeCoordination:
    def __init__(self, progress):
        self.progress = progress

    def status(self):
        return {"progress": self.progress}


class LiepinCamoufoxDualTests(unittest.TestCase):
    def test_first_reverse_resumes_its_own_progress(self):
        args = Namespace(
            direction="reverse",
            start_index=0,
            coord_owner=REVERSE_OWNER,
        )
        coordination = FakeCoordination({REVERSE_OWNER: 900})
        self.assertEqual(resolve_start_index(args, coordination, 1000), 900)

    def test_second_reverse_starts_at_tail_without_progress(self):
        args = Namespace(
            direction="reverse",
            start_index=0,
            coord_owner=REVERSE_SECONDARY_OWNER,
        )
        coordination = FakeCoordination({REVERSE_OWNER: 900})
        self.assertEqual(resolve_start_index(args, coordination, 1000), 1000)

    def test_second_reverse_resumes_its_own_progress(self):
        args = Namespace(
            direction="reverse",
            start_index=0,
            coord_owner=REVERSE_SECONDARY_OWNER,
        )
        coordination = FakeCoordination({REVERSE_SECONDARY_OWNER: 750})
        self.assertEqual(resolve_start_index(args, coordination, 1000), 750)

    def test_explicit_reverse_start_is_bounded_by_source(self):
        args = Namespace(
            direction="reverse",
            start_index=1200,
            coord_owner=REVERSE_OWNER,
        )
        self.assertEqual(resolve_start_index(args, FakeCoordination({}), 1000), 1000)

    def test_reverse_iterator_skips_rows_after_resume_frontier(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            source.write_text(
                "".join(json.dumps({"record_id": str(index)}) + "\n" for index in range(1, 6)),
                encoding="utf-8",
            )
            rows = list(iter_worker_rows(source, 5, "reverse", 3))
            self.assertEqual([source_index for source_index, _, _ in rows], [3, 2, 1])


if __name__ == "__main__":
    unittest.main()
