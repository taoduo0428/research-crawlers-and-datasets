import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.liepin_coordination import (
    FORWARD_OWNER,
    REVERSE_OWNER,
    REVERSE_SECONDARY_OWNER,
)
from tools.run_liepin_edge_cdp_supervisor import (
    run as run_supervisor,
    run_deferred,
)
from tools.run_liepin_edge_cdp_worker import WORKER_CONFIGS, worker_rows


class LiepinEdgeCdpWorkerTests(unittest.TestCase):
    def test_three_owners_have_one_forward_and_two_reverse_configs(self):
        self.assertEqual(WORKER_CONFIGS[FORWARD_OWNER]["direction"], "forward")
        self.assertEqual(WORKER_CONFIGS[REVERSE_OWNER]["direction"], "reverse")
        self.assertEqual(
            WORKER_CONFIGS[REVERSE_SECONDARY_OWNER]["direction"],
            "reverse",
        )
        outputs = {str(config["output"]) for config in WORKER_CONFIGS.values()}
        self.assertEqual(len(outputs), 3)

    def test_forward_and_reverse_resume_from_their_frontier(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            source.write_text(
                "".join(json.dumps({"record_id": str(index)}) + "\n" for index in range(1, 6)),
                encoding="utf-8",
            )
            forward = list(worker_rows(source, 5, "forward", 3))
            reverse = list(worker_rows(source, 5, "reverse", 3))
            self.assertEqual([index for index, _, _ in forward], [3, 4, 5])
            self.assertEqual([index for index, _, _ in reverse], [3, 2, 1])

    def test_supervisor_recycles_only_the_failed_tab_worker(self):
        args = SimpleNamespace(
            owner=FORWARD_OWNER,
            cdp_endpoint="http://127.0.0.1:9223",
            max_restarts=2,
            restart_delay=0,
            challenge_cooldown=1,
            max_challenge_cooldown=4,
            delay_min=0,
            delay_max=0,
        )
        results = [SimpleNamespace(returncode=86), SimpleNamespace(returncode=0)]
        with (
            patch(
                "tools.run_liepin_edge_cdp_supervisor.subprocess.run",
                side_effect=results,
            ) as child,
            patch("tools.run_liepin_edge_cdp_supervisor.time.sleep") as sleep,
            patch(
                "tools.run_liepin_edge_cdp_supervisor.run_deferred",
                return_value=0,
            ),
            patch(
                "tools.run_liepin_edge_cdp_supervisor.ensure_edge_cdp",
                return_value={"ready": True, "launched": False},
            ) as ensure,
        ):
            self.assertEqual(run_supervisor(args), 0)
        self.assertEqual(child.call_count, 2)
        self.assertEqual(ensure.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_forward_deferred_phase_requires_two_empty_snapshots(self):
        args = SimpleNamespace(
            owner=FORWARD_OWNER,
            cdp_endpoint="http://127.0.0.1:9223",
            max_restarts=3,
            restart_delay=0,
            challenge_cooldown=1,
        )
        results = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)]
        with (
            patch(
                "tools.run_liepin_edge_cdp_supervisor.subprocess.run",
                side_effect=results,
            ) as child,
            patch("tools.run_liepin_edge_cdp_supervisor.time.sleep") as sleep,
            patch(
                "tools.run_liepin_edge_cdp_supervisor.ensure_edge_cdp",
                return_value={"ready": True, "launched": False},
            ) as ensure,
        ):
            self.assertEqual(run_deferred(args), 0)
        self.assertEqual(child.call_count, 2)
        self.assertEqual(ensure.call_count, 2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
