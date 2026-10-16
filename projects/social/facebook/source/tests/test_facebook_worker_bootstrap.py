import json
import threading
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

import facebook_worker_bootstrap as bootstrap


def worker(
    worker_id: str,
    serial: str,
    avd_name: str,
    capture_port: int,
    native_port: int,
    *,
    enabled: bool = True,
) -> dict:
    return {
        "worker_id": worker_id,
        "serial": serial,
        "avd_name": avd_name,
        "capture_port": capture_port,
        "native_port": native_port,
        "enabled": enabled,
        "cooldown_seconds": 900,
        "frida_ssl_unpin": worker_id != "worker-01",
    }


class WorkerAllocationTests(unittest.TestCase):
    def test_allocate_uses_next_identity_and_skips_busy_ports(self):
        existing = [
            worker("worker-01", "emulator-5554", "fb-x86_64-api35", 8081, 18766),
            worker(
                "worker-02",
                "emulator-5556",
                "fb-x86_64-api35-worker-02",
                18082,
                18767,
            ),
        ]
        unavailable = {5558, 18083, 18768}

        selected = bootstrap.allocate_worker(
            existing,
            avd_home=Path("X:/avds"),
            occupied_serials={"emulator-5560"},
            port_available=lambda port: port not in unavailable,
            path_exists=lambda _path: False,
        )

        self.assertEqual(selected.worker_id, "worker-03")
        self.assertEqual(selected.avd_name, "fb-x86_64-api35-worker-03")
        self.assertEqual(selected.serial, "emulator-5562")
        self.assertEqual(selected.capture_port, 18084)
        self.assertEqual(selected.native_port, 18769)
        self.assertFalse(selected.enabled)
        self.assertTrue(selected.frida_ssl_unpin)

    def test_allocate_skips_orphaned_avd_name(self):
        existing = [worker("worker-01", "emulator-5554", "fb-x86_64-api35", 8081, 18766)]

        selected = bootstrap.allocate_worker(
            existing,
            avd_home=Path("X:/avds"),
            port_available=lambda _port: True,
            path_exists=lambda path: "worker-02" in str(path),
        )

        self.assertEqual(selected.worker_id, "worker-03")
        self.assertEqual(selected.avd_name, "fb-x86_64-api35-worker-03")

    def test_allocate_rejects_invalid_registry(self):
        duplicate = [
            worker("worker-01", "emulator-5554", "a", 8081, 18766),
            worker("worker-01", "emulator-5556", "b", 18082, 18767),
        ]
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.allocate_worker(duplicate, avd_home=Path("X:/avds"))


class WorkerReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "facebook_workers.json"
        self.state_dir = self.root / "worker-bootstrap"
        self.config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "workers": [
                        worker("worker-01", "emulator-5554", "fb-x86_64-api35", 8081, 18766)
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_reserve_appends_disabled_worker_and_writes_state_and_backup(self):
        result = bootstrap.reserve_worker(
            self.config,
            avd_home=self.root / "avds",
            state_dir=self.state_dir,
            port_available=lambda _port: True,
        )

        payload = json.loads(self.config.read_text(encoding="utf-8"))
        added = payload["workers"][-1]
        self.assertEqual(added["worker_id"], "worker-02")
        self.assertFalse(added["enabled"])
        self.assertTrue(added["frida_ssl_unpin"])
        state = json.loads(Path(result["state_path"]).read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "reserved")
        self.assertEqual(state["completed_stages"], ["reserved"])
        self.assertTrue(Path(result["backup_path"]).is_file())

    def test_two_concurrent_reservations_are_unique(self):
        results: list[dict] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def run() -> None:
            try:
                barrier.wait()
                results.append(
                    bootstrap.reserve_worker(
                        self.config,
                        avd_home=self.root / "avds",
                        state_dir=self.state_dir,
                        port_available=lambda _port: True,
                    )
                )
            except Exception as exc:  # pragma: no cover - assertion reports details
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual({item["worker"]["worker_id"] for item in results}, {"worker-02", "worker-03"})
        payload = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["workers"]), 3)

    def test_reservation_retries_transient_windows_replace_on_same_candidate(self):
        original = bootstrap.write_worker_config
        errors = OSError("fixture sharing violation")
        errors.winerror = 5
        calls = []
        def write(path, workers):
            calls.append([w.worker_id for w in workers])
            if len(calls) < 3: raise errors
            original(path, workers)
        with mock.patch.object(bootstrap, "_registry_replace_retryable", return_value=True), \
             mock.patch.object(bootstrap, "write_worker_config", side_effect=write), \
             mock.patch.object(bootstrap.time, "sleep") as sleep:
            result = bootstrap.reserve_worker(self.config, avd_home=self.root / "avds", state_dir=self.state_dir,
                                              port_available=lambda _: True)
        self.assertEqual(result["worker"]["worker_id"], "worker-02")
        self.assertEqual(calls, [["worker-01", "worker-02"]] * 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(len(list((self.state_dir / "backups").glob("*.json"))), 1)

    def test_registry_retry_is_bounded_and_permanent_failure_preserves_file(self):
        before = self.config.read_bytes()
        with mock.patch.object(bootstrap, "_registry_replace_retryable", return_value=True), \
             mock.patch.object(bootstrap, "write_worker_config", side_effect=PermissionError("fixture permanent")) as write, \
             mock.patch.object(bootstrap.time, "sleep") as sleep:
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.reserve_worker(self.config, avd_home=self.root / "avds", state_dir=self.state_dir,
                                         port_available=lambda _: True)
        self.assertEqual(write.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.state_dir / "worker-02.json").exists())

    def test_retry_classifier_and_nonretryable_error(self):
        error = OSError("fixture")
        error.winerror = 32
        with mock.patch.object(bootstrap.os, "name", "nt"):
            self.assertTrue(bootstrap._registry_replace_retryable(error))
            self.assertFalse(bootstrap._registry_replace_retryable(OSError("fixture disk full")))
            self.assertFalse(bootstrap._registry_replace_retryable(ValueError("fixture")))
        with mock.patch.object(bootstrap.os, "name", "posix"):
            self.assertFalse(bootstrap._registry_replace_retryable(error))
        with mock.patch.object(bootstrap, "write_worker_config", side_effect=ValueError("fixture invalid")) as write, \
             mock.patch.object(bootstrap.time, "sleep") as sleep:
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.reserve_worker(self.config, avd_home=self.root / "avds", state_dir=self.state_dir,
                                         port_available=lambda _: True)
        write.assert_called_once()
        sleep.assert_not_called()

    def test_successful_state_transition_clears_prior_failure(self):
        result = bootstrap.reserve_worker(
            self.config,
            avd_home=self.root / "avds",
            state_dir=self.state_dir,
            port_available=lambda _port: True,
        )
        worker_id = result["worker"]["worker_id"]
        bootstrap.update_state(
            self.state_dir,
            worker_id,
            status="failed",
            failed_stage="facebook-installed",
            error="test failure",
        )

        state = bootstrap.update_state(self.state_dir, worker_id, status="awaiting-login")

        self.assertIsNone(state["failed_stage"])
        self.assertIsNone(state["error"])


if __name__ == "__main__":
    unittest.main()
