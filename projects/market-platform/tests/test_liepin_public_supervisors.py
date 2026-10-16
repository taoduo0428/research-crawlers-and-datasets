import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from tools import run_liepin_camoufox_reverse_2_supervisor as reverse_2
from tools import run_liepin_camoufox_reverse_supervisor as reverse_1
from tools import run_liepin_edge_forward_supervisor as edge


class LiepinPublicSupervisorTests(unittest.TestCase):
    def assert_challenge_cools_then_resumes(self, module):
        results = [
            SimpleNamespace(returncode=module.EXIT_CHALLENGE),
            SimpleNamespace(returncode=0),
        ]
        deferred = (
            patch.object(module, "run_deferred", return_value=0)
            if hasattr(module, "run_deferred")
            else nullcontext()
        )
        with (
            patch.object(module.subprocess, "run", side_effect=results) as run_child,
            patch.object(module.time, "sleep") as sleep,
            deferred,
        ):
            code = module.run(
                max_restarts=2,
                restart_delay=0,
                challenge_cooldown=1,
                max_challenge_cooldown=4,
            )
        self.assertEqual(code, 0)
        self.assertEqual(run_child.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_edge_challenge_is_not_treated_as_login(self):
        self.assert_challenge_cools_then_resumes(edge)

    def test_reverse_1_challenge_is_not_treated_as_login(self):
        self.assert_challenge_cools_then_resumes(reverse_1)

    def test_reverse_2_challenge_is_not_treated_as_login(self):
        self.assert_challenge_cools_then_resumes(reverse_2)

    def test_edge_deferred_phase_requires_two_stable_empty_checks(self):
        results = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)]
        with (
            patch.object(edge.subprocess, "run", side_effect=results) as run_child,
            patch.object(edge.time, "sleep") as sleep,
        ):
            code = edge.run_deferred(max_runs=3, retry_cooldown=1)
        self.assertEqual(code, 0)
        self.assertEqual(run_child.call_count, 2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
