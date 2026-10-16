import json
import tempfile
import time
import unittest
from pathlib import Path

from tools.liepin_coordination import (
    FORWARD_OWNER,
    REVERSE_OWNER,
    REVERSE_SECONDARY_OWNER,
    LiepinCoordination,
)
from tools.run_liepin_browser_supervisor import cooldown_remaining, workers_met


class LiepinBrowserSupervisorTests(unittest.TestCase):
    def test_reads_remaining_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cooldown.json"
            path.write_text(
                json.dumps({"cooldown_until": time.time() + 60}), encoding="utf-8"
            )
            self.assertGreater(cooldown_remaining(path), 50)

    def test_detects_meeting_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coord.sqlite3"
            with LiepinCoordination(path) as coordination:
                coordination.advance(FORWARD_OWNER, 200)
                coordination.advance(REVERSE_OWNER, 200)
            self.assertTrue(workers_met(path))

    def test_detects_meeting_against_deepest_reverse_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coord.sqlite3"
            with LiepinCoordination(path) as coordination:
                coordination.advance(FORWARD_OWNER, 180)
                coordination.advance(REVERSE_OWNER, 220)
                coordination.advance(REVERSE_SECONDARY_OWNER, 180)
            self.assertTrue(workers_met(path))


if __name__ == "__main__":
    unittest.main()
