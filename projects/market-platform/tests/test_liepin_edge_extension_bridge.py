import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.liepin_coordination import (
    FORWARD_OWNER,
    REVERSE_OWNER,
    REVERSE_SECONDARY_OWNER,
    LiepinCoordination,
)
from tools.run_liepin_edge_extension_bridge import (
    LOOPBACK_HOST,
    MAX_BODY_BYTES,
    BridgeHTTPServer,
    LiepinExtensionBridge,
)


class LiepinEdgeExtensionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.jsonl"
        self.rows = [
            {
                "record_id": f"bridge-job-{index}",
                "detail_url": f"https://www.liepin.com/job/{index}.shtml",
                "job_title": f"Job {index}",
            }
            for index in range(1, 8)
        ]
        self.source.write_text(
            "".join(json.dumps(row) + "\n" for row in self.rows),
            encoding="utf-8",
        )
        self.coord_db = self.root / "coord.sqlite3"
        self.configs = {}
        for worker, direction in (
            (FORWARD_OWNER, "forward"),
            (REVERSE_OWNER, "reverse"),
            (REVERSE_SECONDARY_OWNER, "reverse"),
        ):
            self.configs[worker] = {
                "direction": direction,
                "state_dir": self.root / f"state-{worker}",
                "output": self.root / f"output-{worker}.jsonl",
            }
        completed_patch = patch(
            "tools.run_liepin_edge_extension_bridge.completed_ids",
            return_value=set(),
        )
        self.addCleanup(completed_patch.stop)
        completed_patch.start()

    def tearDown(self):
        self.temporary.cleanup()

    def bridge(self, *, limit=0):
        return LiepinExtensionBridge(
            source=self.source,
            coord_db=self.coord_db,
            workers=(FORWARD_OWNER, REVERSE_OWNER, REVERSE_SECONDARY_OWNER),
            limit=limit,
            worker_configs=self.configs,
        )

    def test_three_workers_claim_forward_and_reverse_without_duplicates(self):
        bridge = self.bridge()
        forward, _ = bridge.next_assignment(FORWARD_OWNER)
        reverse_one, _ = bridge.next_assignment(REVERSE_OWNER)
        reverse_two, _ = bridge.next_assignment(REVERSE_SECONDARY_OWNER)

        self.assertEqual(forward.source_index, 1)
        self.assertEqual(reverse_one.source_index, 7)
        self.assertEqual(reverse_two.source_index, 6)
        self.assertEqual(
            len({forward.record_id, reverse_one.record_id, reverse_two.record_id}),
            3,
        )
        with LiepinCoordination(self.coord_db) as coordination:
            self.assertEqual(coordination.status()["duplicate_claims"], 0)

    def test_success_writes_worker_output_and_duplicate_result_is_idempotent(self):
        bridge = self.bridge()
        assignment, _ = bridge.next_assignment(FORWARD_OWNER)
        payload = {
            "record_id": assignment.record_id,
            "source_index": assignment.source_index,
            "status": "success",
            "jd": "岗位职责和任职要求",
            "final_url": assignment.detail_url,
        }
        first = bridge.accept_result(payload)
        second = bridge.accept_result(payload)

        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        lines = self.configs[FORWARD_OWNER]["output"].read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        result = json.loads(lines[0])
        self.assertEqual(result["jd"], payload["jd"])
        self.assertEqual(result["description_raw"], payload["jd"])
        self.assertEqual(result["detail_final_url"], assignment.detail_url)

    def test_failure_becomes_deferred_and_is_not_immediately_reissued(self):
        bridge = self.bridge()
        first, _ = bridge.next_assignment(REVERSE_OWNER)
        result = bridge.accept_result(
            {
                "record_id": first.record_id,
                "source_index": first.source_index,
                "status": "security_transit",
                "jd": "",
                "final_url": "https://wow.liepin.com/",
            }
        )
        second, _ = bridge.next_assignment(REVERSE_OWNER)

        self.assertEqual(result["status"], "deferred_security_transit")
        self.assertNotEqual(first.record_id, second.record_id)
        with LiepinCoordination(self.coord_db) as coordination:
            deferred = coordination.deferred_claims()
        self.assertIn(
            (first.record_id, first.source_index, "deferred_security_transit"),
            deferred,
        )

    def test_limit_counts_new_assignments_but_replays_outstanding(self):
        bridge = self.bridge(limit=1)
        first, reason = bridge.next_assignment(FORWARD_OWNER)
        replay, replay_reason = bridge.next_assignment(FORWARD_OWNER)
        other, stop_reason = bridge.next_assignment(REVERSE_OWNER)

        self.assertEqual(reason, "claimed")
        self.assertEqual(replay, first)
        self.assertEqual(replay_reason, "outstanding")
        self.assertIsNone(other)
        self.assertEqual(stop_reason, "limit_reached")

    def test_workers_process_legacy_deferred_after_main_frontiers_meet(self):
        with LiepinCoordination(self.coord_db) as coordination:
            coordination.advance(FORWARD_OWNER, 4)
            coordination.advance(REVERSE_OWNER, 4)
            coordination.advance(REVERSE_SECONDARY_OWNER, 4)
            self.assertTrue(coordination.claim("bridge-job-2", FORWARD_OWNER, 2))
            coordination.finish("bridge-job-2", FORWARD_OWNER, "deferred_empty")

        bridge = self.bridge()
        assignment, reason = bridge.next_assignment(FORWARD_OWNER)

        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.record_id, "bridge-job-2")
        self.assertEqual(reason, "deferred_claimed")
        with LiepinCoordination(self.coord_db) as coordination:
            owner, status = coordination.connection.execute(
                "SELECT owner, status FROM claims WHERE record_id = ?",
                ("bridge-job-2",),
            ).fetchone()
        self.assertEqual(owner, FORWARD_OWNER)
        self.assertEqual(status, "claimed")

    def test_stale_database_success_without_jsonl_is_reclaimed(self):
        with LiepinCoordination(self.coord_db) as coordination:
            coordination.advance(FORWARD_OWNER, 4)
            coordination.advance(REVERSE_OWNER, 4)
            coordination.advance(REVERSE_SECONDARY_OWNER, 4)
            self.assertTrue(coordination.claim("bridge-job-3", FORWARD_OWNER, 3))
            coordination.finish("bridge-job-3", FORWARD_OWNER, "success")

        bridge = self.bridge()
        assignment, reason = bridge.next_assignment(FORWARD_OWNER)

        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.record_id, "bridge-job-3")
        self.assertEqual(reason, "deferred_claimed")

    def test_http_auth_cors_status_and_body_limit(self):
        bridge = self.bridge(limit=1)
        server = BridgeHTTPServer((LOOPBACK_HOST, 0), bridge, "test-runtime-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        connection = http.client.HTTPConnection(LOOPBACK_HOST, server.server_port, timeout=3)
        connection.request("GET", f"/next?worker={FORWARD_OWNER}")
        unauthorized = connection.getresponse()
        unauthorized.read()
        self.assertEqual(unauthorized.status, 401)

        connection.request(
            "OPTIONS",
            "/result",
            headers={"Origin": "chrome-extension://example"},
        )
        options = connection.getresponse()
        options.read()
        self.assertEqual(options.status, 204)
        self.assertEqual(options.getheader("Access-Control-Allow-Origin"), "*")

        connection.request(
            "GET",
            f"/next?worker={FORWARD_OWNER}",
            headers={"X-Bridge-Token": "test-runtime-token"},
        )
        response = connection.getresponse()
        task = json.loads(response.read())["task"]
        self.assertEqual(response.status, 200)
        self.assertEqual(task["source_index"], 1)

        connection.request(
            "POST",
            "/result",
            body=b"{}",
            headers={
                "X-Bridge-Token": "test-runtime-token",
                "Content-Length": str(MAX_BODY_BYTES + 1),
                "Content-Type": "application/json",
            },
        )
        too_large = connection.getresponse()
        too_large.read()
        self.assertEqual(too_large.status, 413)
        connection.close()


if __name__ == "__main__":
    unittest.main()
