import tempfile
import unittest
from pathlib import Path

from tools.liepin_coordination import (
    DEFERRED_OWNER,
    FORWARD_OWNER,
    LEGACY_MIDDLE_OWNER,
    REVERSE_OWNER,
    REVERSE_SECONDARY_OWNER,
    LiepinCoordination,
)


class LiepinCoordinationTests(unittest.TestCase):
    def test_atomic_claim_prevents_cross_worker_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coord.sqlite3"
            with LiepinCoordination(path) as forward, LiepinCoordination(path) as reverse:
                self.assertTrue(forward.claim("job-1", FORWARD_OWNER, 10))
                self.assertFalse(reverse.claim("job-1", REVERSE_OWNER, 10))
                forward.finish("job-1", FORWARD_OWNER, "success")
                self.assertFalse(reverse.claim("job-1", REVERSE_OWNER, 10))
                self.assertEqual(forward.status()["duplicate_claims"], 0)

    def test_workers_stop_when_positions_meet(self):
        with tempfile.TemporaryDirectory() as directory:
            with LiepinCoordination(Path(directory) / "coord.sqlite3") as coordination:
                coordination.advance(FORWARD_OWNER, 100)
                coordination.advance(REVERSE_OWNER, 200)
                self.assertFalse(coordination.has_met(FORWARD_OWNER, 199))
                self.assertTrue(coordination.has_met(FORWARD_OWNER, 200))
                self.assertFalse(coordination.has_met(REVERSE_OWNER, 101))
                self.assertTrue(coordination.has_met(REVERSE_OWNER, 100))

    def test_forward_uses_deepest_of_two_reverse_frontiers(self):
        with tempfile.TemporaryDirectory() as directory:
            with LiepinCoordination(Path(directory) / "coord.sqlite3") as coordination:
                coordination.advance(FORWARD_OWNER, 100)
                coordination.advance(REVERSE_OWNER, 800)
                coordination.advance(REVERSE_SECONDARY_OWNER, 700)
                self.assertFalse(coordination.has_met(FORWARD_OWNER, 699))
                self.assertTrue(coordination.has_met(FORWARD_OWNER, 700))
                self.assertFalse(coordination.has_met(REVERSE_OWNER, 101))
                self.assertTrue(coordination.has_met(REVERSE_OWNER, 100))
                self.assertFalse(coordination.has_met(REVERSE_SECONDARY_OWNER, 101))
                self.assertTrue(coordination.has_met(REVERSE_SECONDARY_OWNER, 100))

    def test_same_worker_can_reclaim_nonterminal_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with LiepinCoordination(Path(directory) / "coord.sqlite3") as coordination:
                self.assertTrue(coordination.claim("job-2", FORWARD_OWNER, 20))
                coordination.finish("job-2", FORWARD_OWNER, "error")
                self.assertTrue(coordination.claim("job-2", FORWARD_OWNER, 20))
                coordination.finish("job-2", FORWARD_OWNER, "success")
                self.assertFalse(coordination.claim("job-2", FORWARD_OWNER, 20))

    def test_progress_is_monotonic_across_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            with LiepinCoordination(Path(directory) / "coord.sqlite3") as coordination:
                coordination.advance(FORWARD_OWNER, 100)
                coordination.advance(FORWARD_OWNER, 1)
                coordination.advance(REVERSE_OWNER, 900)
                coordination.advance(REVERSE_OWNER, 999)
                coordination.advance(REVERSE_SECONDARY_OWNER, 800)
                coordination.advance(REVERSE_SECONDARY_OWNER, 999)
                progress = coordination.status()["progress"]
                self.assertEqual(progress[FORWARD_OWNER], 100)
                self.assertEqual(progress[REVERSE_OWNER], 900)
                self.assertEqual(progress[REVERSE_SECONDARY_OWNER], 800)

    def test_legacy_middle_claims_migrate_to_second_reverse(self):
        with tempfile.TemporaryDirectory() as directory:
            with LiepinCoordination(Path(directory) / "coord.sqlite3") as coordination:
                self.assertTrue(coordination.claim("legacy-job", LEGACY_MIDDLE_OWNER, 500))
                coordination.finish("legacy-job", LEGACY_MIDDLE_OWNER, "error")
                coordination.connection.execute(
                    "INSERT INTO progress(owner, source_index, updated_at) VALUES (?, ?, 0)",
                    (LEGACY_MIDDLE_OWNER, 500),
                )

                self.assertEqual(coordination.migrate_legacy_middle_claims(), 1)
                state = coordination.status()
                self.assertNotIn(LEGACY_MIDDLE_OWNER, state["progress"])
                self.assertNotIn(LEGACY_MIDDLE_OWNER, state["claims"])
                self.assertEqual(state["claims"][REVERSE_SECONDARY_OWNER], 1)
                self.assertTrue(
                    coordination.claim("legacy-job", REVERSE_SECONDARY_OWNER, 500)
                )

    def test_deferred_claim_is_atomically_moved_to_edge_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            with LiepinCoordination(Path(directory) / "coord.sqlite3") as coordination:
                self.assertTrue(coordination.claim("deferred-job", REVERSE_OWNER, 700))
                coordination.finish(
                    "deferred-job",
                    REVERSE_OWNER,
                    "deferred_security_transit",
                )
                self.assertEqual(
                    coordination.deferred_claims(),
                    [("deferred-job", 700, "deferred_security_transit")],
                )
                self.assertTrue(coordination.claim_deferred("deferred-job"))
                state = coordination.status()
                self.assertEqual(state["claims"][DEFERRED_OWNER], 1)
                coordination.finish("deferred-job", DEFERRED_OWNER, "success")
                self.assertEqual(coordination.deferred_claims(), [])


if __name__ == "__main__":
    unittest.main()
