import csv
import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import facebook_capture_api as api


class WorkerConfigTests(unittest.TestCase):
    def _write_config(self, path: Path, workers: list[dict]):
        path.write_text(json.dumps({"version": 1, "workers": workers}), encoding="utf-8")

    def test_default_workers_preserve_primary_and_disable_secondary(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "facebook_workers.json"
            self._write_config(path, [
                {
                    "worker_id": "worker-01",
                    "serial": "emulator-5554",
                    "avd_name": "fb-x86_64-api35",
                    "capture_port": 8081,
                    "native_port": 18766,
                    "enabled": True,
                },
                {
                    "worker_id": "worker-02",
                    "serial": "emulator-5556",
                    "avd_name": "fb-x86_64-api35-worker-02",
                    "capture_port": 18082,
                    "native_port": 18767,
                    "enabled": False,
                },
            ])
            workers = api.load_worker_config(path)
        self.assertEqual(workers[0].serial, "emulator-5554")
        self.assertEqual(workers[0].avd_name, "fb-x86_64-api35")
        self.assertTrue(workers[0].enabled)
        self.assertEqual(workers[1].serial, "emulator-5556")
        self.assertFalse(workers[1].enabled)
        self.assertFalse(workers[1].frida_ssl_unpin)

    def test_worker_ports_and_identity_must_be_unique(self):
        duplicate_ports = [
            api.WorkerConfig("worker-01", "emulator-5554", "first", 8081, 18766, True),
            api.WorkerConfig("worker-02", "emulator-5556", "second", 8081, 18767, True),
        ]
        with self.assertRaises(api.ApiError):
            api.validate_worker_config(duplicate_ports)

    def test_worker_config_requires_an_enabled_worker(self):
        workers = [api.WorkerConfig("worker-02", "emulator-5556", "second", 18082, 18767, False)]
        with self.assertRaises(api.ApiError):
            api.validate_worker_config(workers)


class AdbSnapshotTests(unittest.TestCase):
    @mock.patch("facebook_capture_api._run_quiet")
    @mock.patch("facebook_capture_api.shutil.which", return_value="adb")
    def test_proxy_timeout_keeps_confirmed_device_health_and_marks_proxy_unknown(self, _which, run_quiet):
        run_quiet.side_effect = [
            mock.Mock(returncode=0, stdout="device\n"),
            mock.Mock(returncode=0, stdout="1\n"),
            api.subprocess.TimeoutExpired(["adb"], 1),
        ]

        snapshot = api.adb_snapshot("emulator-5554", timeout_seconds=1.0)

        self.assertEqual(snapshot["state"], "device")
        self.assertTrue(snapshot["boot_completed"])
        self.assertEqual(snapshot["proxy"], "")
        self.assertEqual(snapshot["proxy_error"], "TimeoutExpired")

    @mock.patch("facebook_capture_api._run_quiet")
    @mock.patch("facebook_capture_api.shutil.which", return_value="adb")
    def test_proxy_success_preserves_existing_string_field(self, _which, run_quiet):
        run_quiet.side_effect = [
            mock.Mock(returncode=0, stdout="device\n"),
            mock.Mock(returncode=0, stdout="1\n"),
            mock.Mock(returncode=0, stdout="127.0.0.1:7897\n"),
        ]

        snapshot = api.adb_snapshot("emulator-5554", timeout_seconds=1.0)

        self.assertEqual(snapshot, {
            "state": "device",
            "boot_completed": True,
            "proxy": "127.0.0.1:7897",
        })


@mock.patch('facebook_capture_api.sys.platform', 'win32')
class WorkerCommandTests(unittest.TestCase):
    @mock.patch("facebook_capture_api.shutil.which")
    def test_capture_command_uses_worker_specific_ports(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        command = api.build_capture_command(
            request,
            serial="emulator-5556",
            proxy_port=18082,
            native_port=18767,
        )
        self.assertEqual(command[command.index("-Serial") + 1], "emulator-5556")
        self.assertEqual(command[command.index("-ProxyPort") + 1], "18082")
        self.assertEqual(command[command.index("-ReactionNativeScrollPort") + 1], "18767")

    @mock.patch("facebook_capture_api.shutil.which")
    def test_secondary_worker_can_enable_isolated_frida_unpin(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        command = api.build_capture_command(
            request,
            serial="emulator-5556",
            proxy_port=18082,
            native_port=18767,
            frida_ssl_unpin=True,
        )
        self.assertIn("-EnableFridaSslUnpin", command)

    @mock.patch("facebook_capture_api.time.sleep")
    @mock.patch("facebook_capture_api.subprocess.Popen")
    @mock.patch("facebook_capture_api.shutil.which", return_value="powershell")
    @mock.patch("facebook_capture_api.adb_snapshot")
    def test_emulator_bootstrap_receives_exact_worker_identity_and_ports(self, snapshot, _which, popen, _sleep):
        snapshot.side_effect = [
            {"state": "offline", "boot_completed": False, "proxy": ""},
            {"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
        ]
        worker = api.WorkerConfig(
            "worker-02", "emulator-5556", "fb-x86_64-api35-worker-02", 18082, 18767, False
        )
        api.ensure_emulator(worker=worker, timeout_seconds=2)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-Serial") + 1], "emulator-5556")
        self.assertEqual(command[command.index("-AvdName") + 1], "fb-x86_64-api35-worker-02")
        self.assertEqual(command[command.index("-CapturePort") + 1], "18082")
        self.assertEqual(command[command.index("-NativePort") + 1], "18767")

    @mock.patch("facebook_capture_api.time.sleep")
    @mock.patch("facebook_capture_api.subprocess.Popen")
    @mock.patch("facebook_capture_api.shutil.which", return_value="powershell")
    @mock.patch("facebook_capture_api.adb_snapshot")
    def test_frida_worker_restores_mitm_certificate_when_bootstrapped(self, snapshot, _which, popen, _sleep):
        snapshot.side_effect = [
            {"state": "offline", "boot_completed": False, "proxy": ""},
            {"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
        ]
        worker = api.WorkerConfig(
            "worker-03", "emulator-5558", "fb-x86_64-api35-worker-03", 18083, 18768, False,
            frida_ssl_unpin=True,
        )
        api.ensure_emulator(worker=worker, timeout_seconds=2)
        self.assertIn("-RestoreMitmCertificate", popen.call_args.args[0])


class MultiWorkerManagerTests(unittest.TestCase):
    _start_worker_impl = staticmethod(api.JobManager._start_worker)
    def setUp(self):
        self.workers = [
            api.WorkerConfig("worker-01", "emulator-5554", "first", 8081, 18766, True),
            api.WorkerConfig("worker-02", "emulator-5556", "second", 18082, 18767, False),
        ]

    def _manager(self, base: Path):
        base_patch = mock.patch.object(api, "BASE_DIR", base)
        base_patch.start()
        self.addCleanup(base_patch.stop)
        start_patch = mock.patch("facebook_capture_api.threading.Thread.start")
        started = start_patch.start()
        self.addCleanup(start_patch.stop)
        return api.JobManager(workers=self.workers), started

    def test_unstarted_scheduler_failure_rolls_back_registration_and_can_retry(self):
        for error in (RuntimeError("dummy thread resource limit"), OSError("dummy launch failure")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temp, \
                 mock.patch.object(api, "BASE_DIR", Path(temp)), \
                 mock.patch.object(api.JobManager, "_start_worker", return_value=None):
                manager = api.JobManager(workers=self.workers)
            # Invoke the saved real helper without starting capture or ADB.
                original_ids = set(manager._started_worker_ids)
                failed = mock.Mock()
                failed.ident = None
                failed.start.side_effect = error
                good = mock.Mock()
                good.ident = None
                with mock.patch.object(api.threading, "Thread", side_effect=[failed, good]):
                    # The construction patch above is still active; call the
                    # saved implementation captured before entering that scope.
                    with self.assertRaises(type(error)) as caught:
                        self._start_worker_impl(manager, "worker-01")
                    if isinstance(error, RuntimeError):
                        self.assertIsInstance(caught.exception, api.SchedulerStartError)
                        self.assertIs(caught.exception.__cause__, error)
                    else:
                        self.assertIs(caught.exception, error)
                    self.assertIsNone(manager._worker)
                    self.assertEqual(manager._workers, [])
                    self.assertEqual(manager._started_worker_ids, original_ids)
                    self.assertIs(self._start_worker_impl(manager, "worker-01"), good)
                    self.assertIs(manager._worker, good)
                    self.assertEqual(manager._workers, [good])
                    failed.start.assert_called_once()
                    good.start.assert_called_once()

    def test_started_scheduler_error_never_discards_handle_or_starts_duplicate(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(api, "BASE_DIR", Path(temp)), \
             mock.patch.object(api.JobManager, "_start_worker", return_value=None):
            manager = api.JobManager(workers=self.workers)
            started = mock.Mock()
            started.ident = 123
            started.start.side_effect = RuntimeError("dummy post-start error")
            with mock.patch.object(api.threading, "Thread", return_value=started) as factory:
                with self.assertRaises(RuntimeError):
                    self._start_worker_impl(manager, "worker-01")
                self.assertIs(manager._worker, started)
                self.assertEqual(manager._workers, [started])
                self.assertIs(self._start_worker_impl(manager, "worker-01"), started)
                factory.assert_called_once()
                started.start.assert_called_once()

    def test_native_scheduler_start_can_enter_manager_lock_without_deadlock(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(api, "BASE_DIR", Path(temp)), \
             mock.patch.object(api.JobManager, "_start_worker", return_value=None):
            manager = api.JobManager(workers=self.workers)
            entered = threading.Event()

            def fixture_worker():
                with manager._lock:
                    entered.set()

            with mock.patch.object(manager, "_worker_loop", side_effect=fixture_worker), \
                 mock.patch.object(api, "run_capture_job") as capture:
                thread = self._start_worker_impl(manager, "worker-01")
                try:
                    self.assertTrue(entered.wait(3))
                finally:
                    thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertIsNotNone(thread.ident)
                self.assertIs(manager._worker, thread)
                self.assertEqual(manager._workers, [thread])
                capture.assert_not_called()

    def test_scheduler_health_separates_http_readiness_and_liveness(self):
        for case in ("missing", "unstarted", "running", "stopped"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                manager, _started = self._manager(Path(temp))
                thread = None if case == "missing" else mock.Mock()
                if thread is not None:
                    thread.ident = None if case == "unstarted" else 123
                    thread.is_alive.return_value = case == "running"
                manager._worker = thread
                with mock.patch.object(api, "adb_snapshot", return_value={"state": "device", "boot_completed": True, "proxy": "fixture"}):
                    report = manager.health()
                expected = "running" if case == "running" else "stopped" if case == "stopped" else "not-started"
                self.assertEqual(report["scheduler"], {"state": expected, "alive": case == "running"})
                self.assertEqual(report["service"], "ready")
                self.assertEqual(report["worker"], "idle")
                self.assertEqual(report["mode"], "primary-standby-serial")
                self.assertEqual(report["capture_lease"]["state"], "available")
                self.assertIs(manager._worker, thread)

    def test_scheduler_probe_uncertainty_does_not_change_ownership_or_lease(self):
        for error in (OSError("PRIVATE_PROBE_DETAILS"), RuntimeError("PRIVATE_PROBE_DETAILS")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temp:
                manager, _started = self._manager(Path(temp))
                thread = mock.Mock()
                thread.is_alive.side_effect = error
                manager._worker = thread
                manager._capture_lease_uncertain = True
                manager._capture_lease_job_id = "fixture-owner"
                manager._capture_lease_reason = "fixture pending cleanup"
                with mock.patch.object(api, "adb_snapshot", return_value={"state": "device", "boot_completed": True, "proxy": "fixture"}):
                    report = manager.health()
                self.assertEqual(report["scheduler"], {"state": "unknown", "alive": None})
                self.assertEqual(report["worker"], "blocked")
                self.assertEqual(report["capture_lease"]["job_id"], "fixture-owner")
                self.assertIs(manager._worker, thread)
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertNotIn("PRIVATE_PROBE_DETAILS", json.dumps(report))

    def test_reload_retries_confirmed_unstarted_scheduler_without_config_change(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(api, "BASE_DIR", Path(temp)):
            disabled = [replace(self.workers[0], enabled=False), replace(self.workers[1], enabled=True)]
            with mock.patch.object(api.JobManager, "_start_worker", return_value=None):
                manager = api.JobManager(workers=disabled)
            enabled = [replace(disabled[0], enabled=True), replace(disabled[1], enabled=False)]
            api.write_worker_config(manager.worker_config_path, enabled)
            failed, good = mock.Mock(), mock.Mock()
            failed.ident = None
            failed.start.side_effect = RuntimeError("fixture thread failure")
            good.ident = 123
            with mock.patch.object(api.threading, "Thread", side_effect=[failed, good]) as factory:
                with self.assertRaises(api.SchedulerStartError): manager.reload_workers()
                self.assertTrue(manager.workers["worker-01"].enabled)
                self.assertIsNone(manager._worker)
                result = manager.reload_workers()
                self.assertEqual(result["updated"], [])
                self.assertIs(manager._worker, good)
                manager.reload_workers()
                self.assertEqual(factory.call_count, 2)
                good.start.assert_called_once()

    def test_reload_missing_scheduler_respects_lease_and_worker_faults(self):
        for blocked in ("lease", "failed", "orphan"):
            with self.subTest(blocked=blocked), tempfile.TemporaryDirectory() as temp, \
                 mock.patch.object(api, "BASE_DIR", Path(temp)), \
                 mock.patch.object(api.JobManager, "_start_worker", return_value=None):
                manager = api.JobManager(workers=self.workers)
                api.write_worker_config(manager.worker_config_path, self.workers)
                if blocked == "lease": manager._capture_lease_uncertain = True
                elif blocked == "failed": manager._failed_worker_ids.add("worker-01")
                else: manager._orphaned_worker_ids.add("worker-01")
                with mock.patch.object(manager, "_start_worker") as start:
                    manager.reload_workers()
                    start.assert_not_called()

    def test_disabled_worker_does_not_start_or_claim_work(self):
        with tempfile.TemporaryDirectory() as temp:
            manager, started = self._manager(Path(temp))
            self.assertEqual(started.call_count, 1)
            self.assertEqual(set(manager._started_worker_ids), {"worker-01"})

    def test_claim_persists_worker_ownership_and_rejects_cross_worker_reclaim(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            self.assertTrue(manager._assign_job_to_worker(job_id, self.workers[0]))
            self.assertFalse(manager._assign_job_to_worker(job_id, self.workers[1]))
            public = manager.public_job(job_id)
            self.assertEqual(public["worker_id"], "worker-01")
            self.assertEqual(public["serial"], "emulator-5554")
            manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["worker_id"], "worker-01")
            self.assertEqual(manifest["serial"], "emulator-5554")

    def test_two_enabled_workers_use_one_serial_active_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            first = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            second = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})
            self.assertTrue(manager._assign_job_to_worker(first["job_id"], self.workers[0]))
            self.assertFalse(manager._assign_job_to_worker(second["job_id"], self.workers[1]))
            self.assertEqual(manager.public_job(first["job_id"])["worker_id"], "worker-01")
            self.assertIsNone(manager.public_job(second["job_id"])["worker_id"])

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "offline", "boot_completed": False, "proxy": ""},
    )
    def test_two_enabled_workers_start_one_scheduler_and_keep_second_standby(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, started = self._manager(Path(temp))
            self.assertEqual(started.call_count, 1)
            self.assertEqual(manager._active_worker_id, "worker-01")
            self.assertEqual(manager._started_worker_ids, {"worker-01"})
            self.assertEqual(manager.worker_health("worker-02")["state"], "standby")

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "offline", "boot_completed": False, "proxy": ""},
    )
    def test_confirmed_primary_failure_requeues_from_checkpoint_to_standby(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(base)
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            self.assertTrue(manager._assign_job_to_worker(job_id, self.workers[0]))
            job = manager._jobs[job_id]
            attempt = Path(job["capture_run_dir"])
            analysis = attempt / "analysis"
            analysis.mkdir(parents=True)
            with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(api.REACTOR_USER_FIELDS))
                writer.writeheader()
                writer.writerow({"user_id": "u1", "user_name": "One", "avatar_url": "", "source_post_id": "p1", "reaction_id": "LIKE"})
            self.assertTrue(manager._recover_after_worker_failure(job_id, self.workers[0], api.ApiError("ADB disconnected")))
            self.assertEqual(manager._active_worker_id, "worker-02")
            self.assertEqual(manager._jobs[job_id]["status"], "queued")
            self.assertEqual(manager._jobs[job_id]["recovery_state"], "checkpointed-queued-for-standby")
            self.assertEqual(manager._jobs[job_id]["checkpoint"]["preserved_user_count"], 1)
            checkpoint = base / "runs" / f"api-{job_id}" / "checkpoint" / "facebook-reactor-users.csv"
            self.assertTrue(checkpoint.exists())
            with checkpoint.open(encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)
            self.assertFalse(manager._assign_job_to_worker(job_id, self.workers[1]))
            manager._active_job_id = None
            manager._worker_runtime["worker-01"]["current_job_id"] = None
            self.assertTrue(manager._assign_job_to_worker(job_id, self.workers[1]))
            self.assertTrue(manager._jobs[job_id]["capture_run_dir"].endswith("attempt-002"))

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
    )
    def test_online_capture_error_does_not_switch_to_standby(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            self.assertTrue(manager._assign_job_to_worker(submitted["job_id"], self.workers[0]))
            result = manager._recover_after_worker_failure(
                submitted["job_id"], self.workers[0], api.ApiError("capture command failed with exit 2; rate-limit")
            )
            self.assertIsNone(result)
            self.assertEqual(manager._active_worker_id, "worker-01")
            self.assertNotIn("worker-01", manager._failed_worker_ids)

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={
            "state": "device",
            "boot_completed": True,
            "proxy": "",
            "proxy_error": "TimeoutExpired",
        },
    )
    def test_proxy_probe_error_on_healthy_adb_does_not_switch_to_standby(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            self.assertTrue(manager._assign_job_to_worker(submitted["job_id"], self.workers[0]))

            result = manager._recover_after_worker_failure(
                submitted["job_id"], self.workers[0], api.ApiError("capture command failed after proxy probe timeout")
            )

            self.assertIsNone(result)
            self.assertEqual(manager._active_worker_id, "worker-01")
            self.assertNotIn("worker-01", manager._failed_worker_ids)

    def test_cancelling_worker_confirmed_failure_marks_primary_failed_without_requeue(self):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            manager._queue.get = mock.Mock(side_effect=[job_id, KeyboardInterrupt])
            manager._queue.put = mock.Mock()

            def fake_capture(*_args, **_kwargs):
                manager.cancel(job_id)
                raise api.ApiError("ADB disconnected after cancellation")

            with mock.patch(
                "facebook_capture_api.adb_snapshot",
                return_value={"state": "offline", "boot_completed": False, "proxy": ""},
            ) as snapshot, mock.patch("facebook_capture_api.time.sleep"), mock.patch(
                "facebook_capture_api.run_capture_job", side_effect=fake_capture
            ):
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()

            self.assertEqual(snapshot.call_count, 3)
            self.assertIn("worker-01", manager._failed_worker_ids)
            self.assertIn("ADB disconnected after cancellation", manager._worker_runtime["worker-01"]["last_failure"])
            self.assertEqual(manager._active_worker_id, "worker-02")
            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")
            manager._queue.put.assert_not_called()

    def test_cancelling_worker_with_healthy_adb_does_not_failover(self):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            manager._queue.get = mock.Mock(side_effect=[job_id, KeyboardInterrupt])
            manager._queue.put = mock.Mock()

            def fake_capture(*_args, **_kwargs):
                manager.cancel(job_id)
                raise api.ApiError("capture stopped after cancellation")

            with mock.patch(
                "facebook_capture_api.adb_snapshot",
                return_value={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
            ) as snapshot, mock.patch("facebook_capture_api.run_capture_job", side_effect=fake_capture):
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()

            self.assertEqual(snapshot.call_count, 1)
            self.assertNotIn("worker-01", manager._failed_worker_ids)
            self.assertEqual(manager._active_worker_id, "worker-01")
            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")
            manager._queue.put.assert_not_called()

    def test_cancellation_during_failure_diagnosis_marks_failed_without_requeue(self):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            manager._queue.get = mock.Mock(side_effect=[job_id, KeyboardInterrupt])
            manager._queue.put = mock.Mock()
            manager._queue.task_done = mock.Mock()

            def confirm_failure(_worker, _error):
                manager.cancel(job_id)
                return True

            with mock.patch.object(manager, "_worker_failure_confirmed", side_effect=confirm_failure) as confirmed, \
                 mock.patch("facebook_capture_api.run_capture_job", side_effect=api.ApiError("ADB disconnected")):
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()

            confirmed.assert_called_once()
            self.assertIn("worker-01", manager._failed_worker_ids)
            self.assertIn("ADB disconnected", manager._worker_runtime["worker-01"]["last_failure"])
            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")
            self.assertEqual(manager._active_worker_id, "worker-02")
            manager._queue.put.assert_not_called()

    def test_cancelling_worker_with_unverified_stop_does_not_take_over_on_standby(self):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            self.assertTrue(manager._assign_job_to_worker(job_id, self.workers[0]))
            process = mock.Mock()
            process.poll.return_value = None
            manager._running_processes[job_id] = process

            with mock.patch(
                "facebook_capture_api._terminate_tree", return_value=False
            ), mock.patch(
                "facebook_capture_api.adb_snapshot",
                return_value={"state": "offline", "boot_completed": False, "proxy": ""},
            ), mock.patch("facebook_capture_api.time.sleep"):
                manager.cancel(job_id)
                manager._recover_after_worker_failure(
                    job_id,
                    self.workers[0],
                    api.ApiError("ADB disconnected while cancellation was pending"),
                )

            self.assertEqual(manager.public_job(job_id)["status"], "cancelling")
            self.assertIn("worker-01", manager._failed_worker_ids)
            self.assertIsNone(manager._active_worker_id)
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertIs(manager._running_processes[job_id], process)

    def test_restored_cancellation_releases_lease_and_claims_next_job(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.workers[1] = replace(self.workers[1], enabled=True)
            job_id = "fb_restoredcancellation"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "cancelling",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": None,
                "cancel_requested_at": "2026-08-18T00:00:02Z",
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "worker_id": "worker-01",
                "serial": "emulator-5554",
                "process_pid": 4242,
                "process_creation_time": 123.0,
                "process_state": "stop-unverified",
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with mock.patch.object(api, "_process_identity_state", return_value="match"), \
                 mock.patch.object(api, "_terminate_known_pid", side_effect=[False, True]) as terminate:
                manager, _started = self._manager(base)
                next_job = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})
                retried = manager.cancel(job_id)

                self.assertEqual(retried["job"]["status"], "cancelled")
                self.assertFalse(manager._capture_lease_uncertain)
                self.assertEqual(manager._capture_lease_job_id, None)
                self.assertEqual(manager._active_worker_id, "worker-01")

                manager._queue.get = mock.Mock(side_effect=[next_job["job_id"], KeyboardInterrupt])
                manager._queue.task_done = mock.Mock()
                with mock.patch("facebook_capture_api.run_capture_job", return_value={"ok": True}), \
                     mock.patch(
                         "facebook_capture_api.build_api_result",
                         return_value={"ok": True, "completion_scope": "terminal-complete"},
                     ):
                    with self.assertRaises(KeyboardInterrupt):
                        manager._worker_loop()

            terminate.assert_has_calls([mock.call(4242, 123.0), mock.call(4242, 123.0)])
            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")
            self.assertEqual(manager.public_job(next_job["job_id"])["status"], "completed")
            self.assertIsNone(manager._worker_runtime["worker-01"]["current_job_id"])

    def test_restored_cancellation_keeps_other_uncertain_job_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.workers[1] = replace(self.workers[1], enabled=True)
            manifests = {
                "fb_restore_a": ("worker-01", "emulator-5554", 4242, 123.0),
                "fb_restore_b": ("worker-02", "emulator-5556", 4243, 124.0),
            }
            job_dir = base / "api-jobs"
            job_dir.mkdir(parents=True)
            for job_id, (worker_id, serial, pid, creation_time) in manifests.items():
                (job_dir / f"{job_id}.job.json").write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "job_id": job_id,
                            "status": "cancelling",
                            "created_at": "2026-08-18T00:00:00Z",
                            "started_at": "2026-08-18T00:00:01Z",
                            "finished_at": None,
                            "request": {"post_url": f"https://www.facebook.com/example/posts/{pid}"},
                            "worker_id": worker_id,
                            "serial": serial,
                            "process_pid": pid,
                            "process_creation_time": creation_time,
                            "process_state": "stop-unverified",
                        }
                    ),
                    encoding="utf-8",
                )

            with mock.patch.object(api, "_process_identity_state", return_value="match"), \
                 mock.patch.object(api, "_terminate_known_pid", side_effect=[False, False, True, True]):
                manager, _started = self._manager(base)
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertIsNone(manager._active_worker_id)

                first = manager.cancel("fb_restore_a")
                self.assertEqual(first["job"]["status"], "cancelled")
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertEqual(manager._capture_lease_job_id, "fb_restore_b")
                self.assertNotIn("worker-01", manager._orphaned_worker_ids)
                self.assertIn("worker-02", manager._orphaned_worker_ids)

                second = manager.cancel("fb_restore_b")
                self.assertEqual(second["job"]["status"], "cancelled")
                self.assertFalse(manager._capture_lease_uncertain)
                self.assertEqual(manager._active_worker_id, "worker-01")
                self.assertFalse(manager._orphaned_worker_ids)

    def test_restored_cleanup_preserves_other_stopped_job_transport_blocker(self):
        # Multiple old manifests may have stopped processes but unresolved
        # transport. Resolving the last-loaded record is not global cleanup.
        for same_worker, reverse_cleanup in ((False, False), (False, True), (True, False), (True, True)):
            for status in ("failed", "cancelled"):
                with self.subTest(same_worker=same_worker, reverse_cleanup=reverse_cleanup, status=status), tempfile.TemporaryDirectory() as temp:
                    base = Path(temp)
                    self.workers[1] = replace(self.workers[1], enabled=True)
                    job_dir = base / "api-jobs"
                    job_dir.mkdir()
                    for job_id, worker_id, serial in (
                        ("fb_cleanup_a", "worker-01", "emulator-5554"),
                        ("fb_cleanup_b", "worker-01" if same_worker else "worker-02",
                         "emulator-5554" if same_worker else "emulator-5556"),
                    ):
                        (job_dir / f"{job_id}.job.json").write_text(json.dumps({
                            "version": 1, "job_id": job_id, "status": status,
                            "created_at": "2026-10-02T00:00:00Z",
                            "request": {"post_url": "https://www.facebook.com/example/posts/1"},
                            "worker_id": worker_id, "serial": serial,
                            "process_pid": None, "process_creation_time": None,
                            "process_state": "stopped", "transport_cleanup_state": "failed",
                            "transport_cleanup_error": "dummy incomplete proxy restoration",
                        }), encoding="utf-8")
                    with mock.patch.object(api, "_cleanup_transport_after_capture", return_value={
                        "transport_cleanup_state": "restored", "error": None,
                    }) as cleanup, mock.patch.object(api.sys, "platform", "win32"):
                        manager, _started = self._manager(base)
                        self.assertEqual(manager._capture_lease_job_id, "fb_cleanup_b")
                        queued = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})
                        first = "fb_cleanup_a" if reverse_cleanup else "fb_cleanup_b"
                        remaining = "fb_cleanup_b" if reverse_cleanup else "fb_cleanup_a"
                        manager.cancel(first)
                        self.assertEqual(manager._jobs[first]["transport_cleanup_state"], "restored")
                        self.assertTrue(manager._capture_lease_uncertain)
                        self.assertEqual(manager._capture_lease_job_id, remaining)
                        self.assertEqual(manager._jobs[remaining]["transport_cleanup_state"], "failed")
                        blocked_worker = manager._jobs[remaining]["worker_id"]
                        self.assertIn(blocked_worker, manager._orphaned_worker_ids)
                        self.assertFalse(manager._assign_job_to_worker(queued["job_id"], self.workers[0]))
                        self.assertFalse(manager._assign_job_to_worker(queued["job_id"], self.workers[1]))
                        manager.cancel(remaining)
                        self.assertFalse(manager._capture_lease_uncertain)
                        self.assertIsNone(manager._capture_lease_job_id)
                        self.assertFalse(manager._orphaned_worker_ids)
                        active = manager.workers[manager._active_worker_id]
                        self.assertTrue(manager._assign_job_to_worker(queued["job_id"], active))
                        self.assertEqual(cleanup.call_count, 2)

    @mock.patch("facebook_capture_api._process_creation_time", return_value=999.0)
    def test_restart_with_unverified_capture_pid_blocks_all_workers(self, _creation_time):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_restartpid"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "running",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "error": None,
                "run_dir": str(base / "runs" / f"api-{job_id}"),
                "capture_run_dir": str(base / "runs" / f"api-{job_id}" / "attempt-001"),
                "attempt_dirs": [str(base / "runs" / f"api-{job_id}" / "attempt-001")],
                "worker_id": "worker-01",
                "serial": "emulator-5554",
                "process_pid": 424242,
                "process_creation_time": 123.0,
                "process_state": "running",
            }
            manifest_path = base / "api-jobs" / f"{job_id}.job.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            manager, started = self._manager(base)

            self.assertTrue(manager._capture_lease_uncertain)
            self.assertIsNone(manager._active_worker_id)
            self.assertEqual(started.call_count, 0)
            self.assertEqual(manager.public_job(job_id)["process_state"], "restart-unverified")
            self.assertFalse(manager._assign_job_to_worker(job_id, self.workers[1]))

    @mock.patch("facebook_capture_api.time.sleep")
    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        side_effect=[
            {"state": "offline", "boot_completed": False, "proxy": ""},
            {"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
        ],
    )
    def test_worker_failure_probe_retries_transient_adb_failure(self, snapshot, sleep):
        with tempfile.TemporaryDirectory() as temp:
            manager, _started = self._manager(Path(temp))

            self.assertFalse(manager._worker_failure_confirmed(self.workers[0], api.ApiError("capture failed")))
            self.assertEqual(snapshot.call_count, 2)
            sleep.assert_called_once_with(0.25)

    @mock.patch("facebook_capture_api.time.sleep")
    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "device", "boot_completed": False, "proxy": ""},
    )
    def test_worker_failure_probe_confirms_device_that_is_not_booted(self, snapshot, sleep):
        with tempfile.TemporaryDirectory() as temp:
            manager, _started = self._manager(Path(temp))

            self.assertTrue(manager._worker_failure_confirmed(self.workers[0], api.ApiError("capture failed")))
            self.assertEqual(snapshot.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_materialize_attempts_preserves_and_deduplicates_users(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "job"
            attempts = [root / "attempt-001", root / "attempt-002"]
            rows = [
                {"user_id": "u1", "user_name": "One", "avatar_url": "", "source_post_id": "p1", "reaction_id": "LIKE", "line": "7", "original_content_id": "content-1"},
                {"user_id": "u2", "user_name": "Two", "avatar_url": "", "source_post_id": "p1", "reaction_id": "LIKE", "source_field": "viewer_reaction", "original_content_id": "content-1"},
            ]
            for attempt in attempts:
                analysis = attempt / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=[*api.REACTOR_USER_FIELDS, "line", "source_field", "original_content_id"],
                    )
                    writer.writeheader()
                    for row in rows[:1] if attempt == attempts[0] else rows:
                        writer.writerow(row)
            api.materialize_capture_attempts(root, attempts)
            with (root / "analysis" / "facebook-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                merged = list(csv.DictReader(handle))
            self.assertEqual([row["user_id"] for row in merged], ["u1", "u2"])
            extra_fields = (set(merged[0]) | set(merged[1])) - set(api.REACTOR_USER_FIELDS)
            self.assertTrue({"line", "source_field", "original_content_id"} <= extra_fields)
            self.assertEqual(len(api._read_reactor_rows_file(root / "checkpoint" / "facebook-reactor-users.csv")), 2)

    @mock.patch("facebook_capture_api.time.time", return_value=100.0)
    def test_cooling_worker_does_not_claim_a_new_post(self, _time):
        with tempfile.TemporaryDirectory() as temp:
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            manager._worker_runtime["worker-01"]["cooldown_until"] = 101.0
            self.assertFalse(manager._assign_job_to_worker(submitted["job_id"], self.workers[0]))
            self.assertEqual(manager.public_job(submitted["job_id"])["status"], "queued")

    @mock.patch("facebook_capture_api.adb_snapshot", return_value={"state": "offline", "boot_completed": False, "proxy": ""})
    def test_health_lists_enabled_and_disabled_workers_without_account_identity(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            manager, _started = self._manager(Path(temp))
            health = manager.health()
            self.assertEqual([item["worker_id"] for item in health["workers"]], ["worker-01", "worker-02"])
            self.assertEqual(health["workers"][0]["state"], "offline")
            self.assertEqual(health["workers"][1]["state"], "disabled")
            self.assertNotIn("account", json.dumps(health).lower())

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        side_effect=[
            {"state": "offline", "boot_completed": False, "proxy": ""},
            {"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
        ],
    )
    def test_health_top_level_android_follows_active_worker(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            manager._active_worker_id = "worker-02"

            health = manager.health()

            self.assertEqual(health["active_worker_id"], "worker-02")
            self.assertEqual(health["serial"], "emulator-5556")
            self.assertEqual(health["android"]["state"], "device")

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
    )
    def test_reload_workers_adds_disabled_worker_then_allows_enable(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, started = self._manager(base)
            third = api.WorkerConfig(
                "worker-03", "emulator-5558", "third", 18083, 18768, False, frida_ssl_unpin=True
            )
            api.write_worker_config(base / "facebook_workers.json", [*self.workers, third])

            result = manager.reload_workers()

            self.assertEqual(result["added"], ["worker-03"])
            self.assertIn("worker-03", manager.workers)
            self.assertEqual(manager._worker_runtime["worker-03"]["current_job_id"], None)
            self.assertNotIn("worker-03", manager._started_worker_ids)
            manager.set_worker_enabled("worker-03", True)
            self.assertNotIn("worker-03", manager._started_worker_ids)
            self.assertEqual(started.call_count, 1)
            self.assertEqual(manager.worker_health("worker-03")["state"], "standby")

    def test_worker_enable_write_failure_keeps_live_and_persisted_state(self):
        for worker_id, enabled in (("worker-01", False), ("worker-02", True)):
            with self.subTest(worker_id=worker_id, enabled=enabled), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                self.workers[1] = replace(self.workers[1], enabled=(worker_id == "worker-01"))
                manager, _started = self._manager(base)
                api.write_worker_config(manager.worker_config_path, self.workers)
                original_disk = manager.worker_config_path.read_bytes()
                original_workers = dict(manager.workers)
                original_active = manager._active_worker_id
                original_started = set(manager._started_worker_ids)
                original_failed = set(manager._failed_worker_ids)
                manager._wake_event.clear()
                with mock.patch.object(api.os, "replace", side_effect=PermissionError("dummy locked config")), \
                     mock.patch.object(manager, "_start_worker") as start_worker:
                    with self.assertRaises(OSError):
                        manager.set_worker_enabled(worker_id, enabled)
                self.assertEqual(manager.workers, original_workers)
                self.assertEqual(manager._active_worker_id, original_active)
                self.assertEqual(manager._started_worker_ids, original_started)
                self.assertEqual(manager._failed_worker_ids, original_failed)
                self.assertFalse(manager._wake_event.is_set())
                self.assertEqual(manager.worker_config_path.read_bytes(), original_disk)
                start_worker.assert_not_called()

    def test_reload_cannot_disable_a_busy_active_worker(self):
        for cancelling in (False, True):
            with self.subTest(cancelling=cancelling), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                manager, _started = self._manager(base)
                submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
                self.assertTrue(manager._assign_job_to_worker(submitted["job_id"], self.workers[0]))
                if cancelling:
                    manager._jobs[submitted["job_id"]]["status"] = "cancelling"
                third = api.WorkerConfig("worker-03", "emulator-5558", "third", 18083, 18768, False)
                external = [replace(self.workers[0], enabled=False), replace(self.workers[1], enabled=True), third]
                api.write_worker_config(manager.worker_config_path, external)
                original_disk = manager.worker_config_path.read_bytes()
                original_workers = dict(manager.workers)
                original_order = list(manager._worker_order)
                original_runtime_ids = set(manager._worker_runtime)
                manager._wake_event.clear()
                with mock.patch.object(manager, "_start_worker") as start_worker:
                    with self.assertRaisesRegex(api.ApiError, "cannot disable the active worker"):
                        manager.reload_workers()
                self.assertEqual(manager.workers, original_workers)
                self.assertEqual(manager._worker_order, original_order)
                self.assertEqual(set(manager._worker_runtime), original_runtime_ids)
                self.assertEqual(manager._active_worker_id, "worker-01")
                self.assertEqual(manager._active_job_id, submitted["job_id"])
                self.assertTrue(manager.workers["worker-01"].enabled)
                self.assertFalse(manager._wake_event.is_set())
                self.assertEqual(manager.worker_config_path.read_bytes(), original_disk)
                start_worker.assert_not_called()

    @mock.patch("facebook_capture_api.adb_snapshot", return_value={"state": "offline", "boot_completed": False, "proxy": ""})
    def test_worker_enable_publishes_live_state_after_successful_write(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(base)
            api.write_worker_config(manager.worker_config_path, self.workers)
            original_workers = dict(manager.workers)
            original_write = api.write_worker_config

            def persist(path, candidate):
                self.assertEqual(manager.workers, original_workers)
                self.assertEqual(manager._active_worker_id, "worker-01")
                original_write(path, candidate)

            with mock.patch.object(api, "write_worker_config", side_effect=persist) as write:
                result = manager.set_worker_enabled("worker-01", False)
            self.assertFalse(result["enabled"])
            self.assertFalse(manager.workers["worker-01"].enabled)
            self.assertEqual(manager._active_worker_id, "worker-02")
            self.assertEqual(manager._started_worker_ids, {"worker-02"})
            persisted = api.load_worker_config(manager.worker_config_path)
            self.assertEqual(persisted, list(manager.workers.values()))
            write.assert_called_once()

    def test_reload_allows_idle_switch_and_busy_non_disabling_updates(self):
        for busy in (False, True):
            with self.subTest(busy=busy), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                manager, _started = self._manager(base)
                job_id = None
                if busy:
                    job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                    self.assertTrue(manager._assign_job_to_worker(job_id, self.workers[0]))
                third = api.WorkerConfig("worker-03", "emulator-5558", "third", 18083, 18768, False)
                external = ([replace(self.workers[0], cooldown_seconds=901), self.workers[1], third]
                            if busy else [replace(self.workers[0], enabled=False),
                                          replace(self.workers[1], enabled=True), third])
                api.write_worker_config(manager.worker_config_path, external)
                result = manager.reload_workers()
                self.assertEqual(result["added"], ["worker-03"])
                self.assertEqual(manager.workers, {worker.worker_id: worker for worker in external})
                self.assertEqual(manager._active_worker_id, "worker-01" if busy else "worker-02")
                self.assertEqual(manager._active_job_id, job_id)
                self.assertFalse(manager.workers["worker-03"].enabled)
                if busy:
                    self.assertEqual(manager._jobs[job_id]["worker_id"], "worker-01")
                    self.assertEqual(manager._jobs[job_id]["status"], "running")

    def test_reload_and_setter_keep_disk_and_memory_consistent(self):
        # Force read -> setter -> reload publish on the unlocked reader.
        # Under the corrected lock, setter blocks until read/publish completes.
        # A nonblocking lock probe chooses the ordering; no sleep/race lottery.
        real_start = threading.Thread.start
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                self.workers[1] = replace(self.workers[1], enabled=not enabled)
                manager, _started = self._manager(base)
                api.write_worker_config(manager.worker_config_path, self.workers)
                original_load = api.load_worker_config
                loaded = threading.Event()
                release_read = threading.Event()
                setter_attempted = threading.Event()
                setter_done = threading.Event()
                lock_probe = []
                errors = []

                def read_and_pause(*args, **kwargs):
                    snapshot = original_load(*args, **kwargs)
                    loaded.set()
                    if not release_read.wait(3):
                        raise RuntimeError("test did not release configuration reader")
                    return snapshot

                def reload():
                    try:
                        manager.reload_workers()
                    except BaseException as exc:
                        errors.append(exc)

                def enable():
                    acquired = manager._lock.acquire(blocking=False)
                    lock_probe.append(acquired)
                    setter_attempted.set()
                    try:
                        manager.set_worker_enabled("worker-02", enabled)
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        if acquired:
                            manager._lock.release()
                        setter_done.set()

                reload_thread = threading.Thread(target=reload, daemon=True)
                setter_thread = threading.Thread(target=enable, daemon=True)
                with mock.patch.object(api, "load_worker_config", side_effect=read_and_pause), \
                     mock.patch.object(api, "adb_snapshot", return_value={
                         "state": "offline", "boot_completed": False, "proxy": "",
                     }):
                    try:
                        real_start(reload_thread)
                        self.assertTrue(loaded.wait(3), "configuration was not read")
                        real_start(setter_thread)
                        self.assertTrue(setter_attempted.wait(3), "setter never attempted the lock")
                        if lock_probe[0]:
                            self.assertTrue(setter_done.wait(3), "unlocked setter did not commit")
                        release_read.set()
                        reload_thread.join(3)
                        setter_thread.join(3)
                        self.assertFalse(reload_thread.is_alive())
                        self.assertFalse(setter_thread.is_alive())
                    finally:
                        release_read.set()
                        if reload_thread.ident is not None:
                            reload_thread.join(3)
                        if setter_thread.ident is not None:
                            setter_thread.join(3)
                self.assertEqual(errors, [])
                persisted = original_load(manager.worker_config_path)
                self.assertEqual(persisted[1].enabled, enabled)
                self.assertEqual(list(manager.workers.values()), persisted)
                self.assertEqual(manager.workers["worker-02"].enabled, enabled)
                self.assertEqual(manager._active_worker_id, "worker-01")
                self.assertIsNone(manager._active_job_id)

    def test_submit_manifest_failure_does_not_publish_a_ghost_job(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            existing = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            original_jobs = dict(manager._jobs)
            original_disk = {p.name: p.read_bytes() for p in (base / "api-jobs").glob("*.job.json")}
            with mock.patch.object(api.os, "replace", side_effect=PermissionError("dummy disk failure")):
                with self.assertRaises(OSError):
                    manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})
            self.assertEqual(manager._jobs, original_jobs)
            self.assertEqual(manager._queue.qsize(), 1)
            self.assertEqual(manager._jobs[existing["job_id"]]["status"], "queued")
            self.assertEqual({p.name: p.read_bytes() for p in (base / "api-jobs").glob("*.job.json")}, original_disk)

    def test_batch_submission_failures_do_not_publish_or_restart_partial_work(self):
        for phase in ("outputs", "first-child", "second-child", "batch-commit"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                manager, _started = self._manager(base)
                original_persist = manager._persist_job
                calls = []

                def persist_child(job):
                    calls.append(job["job_id"])
                    if len(calls) == (1 if phase == "first-child" else 2):
                        raise OSError("dummy child manifest failure")
                    original_persist(job)

                if phase == "outputs":
                    patcher = mock.patch.object(api, "write_batch_outputs", side_effect=OSError("dummy output failure"))
                elif phase == "batch-commit":
                    patcher = mock.patch.object(manager, "_persist_batch", side_effect=OSError("dummy batch commit failure"))
                else:
                    patcher = mock.patch.object(manager, "_persist_job", side_effect=persist_child)
                with patcher, self.assertRaises(OSError):
                    manager.submit_batch({"post_urls": [
                        "https://www.facebook.com/example/posts/1", "https://www.facebook.com/example/posts/2",
                    ]})
                self.assertEqual(manager._jobs, {})
                self.assertEqual(manager._batches, {})
                self.assertEqual(manager._queue.qsize(), 0)
                restarted, _started = self._manager(base)
                self.assertEqual(restarted._queue.qsize(), 0)
                self.assertEqual(restarted._batches, {})
                self.assertTrue(all(job["status"] == "failed" for job in restarted._jobs.values()))

    def test_committed_batch_still_restores_all_queued_children(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            submitted = manager.submit_batch({"post_urls": [
                "https://www.facebook.com/example/posts/1", "https://www.facebook.com/example/posts/2",
            ]})
            job_ids = set(manager._jobs)
            restarted, _started = self._manager(base)
            self.assertEqual(set(restarted._jobs), job_ids)
            self.assertIn(submitted["batch_id"], restarted._batches)
            self.assertEqual(restarted._queue.qsize(), 2)
            self.assertTrue(all(job["status"] == "queued" for job in restarted._jobs.values()))

    def test_queued_batch_child_requires_matching_commit_membership(self):
        for parent_case in ("missing", "wrong-id", "nonmember", "wrong-version", "committed"):
            with self.subTest(parent_case=parent_case), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                jobs = base / "api-jobs"
                jobs.mkdir()
                job_id = "fb_commitfixture"
                batch_id = "batch_commitfixture"
                (jobs / f"{job_id}.job.json").write_text(json.dumps({
                    "version": 1, "job_id": job_id, "status": "queued", "batch_id": batch_id,
                    "request": {"post_url": "https://www.facebook.com/example/posts/1"},
                }), encoding="utf-8")
                if parent_case != "missing":
                    (base / "api-batches").mkdir()
                    (base / "api-batches" / f"{batch_id}.batch.json").write_text(json.dumps({
                        "version": 2 if parent_case == "wrong-version" else 1,
                        "batch_id": "batch_wrong" if parent_case == "wrong-id" else batch_id,
                        "job_ids": ["fb_other"] if parent_case == "nonmember" else [job_id],
                    }), encoding="utf-8")
                manager, _started = self._manager(base)
                self.assertEqual(manager._queue.qsize(), 1 if parent_case == "committed" else 0)
                self.assertEqual(manager._jobs[job_id]["status"], "queued" if parent_case == "committed" else "failed")

    def test_uncommitted_batch_keeps_uncertain_process_lease_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "api-jobs").mkdir()
            job_id = "fb_uncommittedprocess"
            (base / "api-jobs" / f"{job_id}.job.json").write_text(json.dumps({
                "version": 1, "job_id": job_id, "status": "queued", "batch_id": "batch_missing",
                "worker_id": "worker-01", "serial": "emulator-5554",
                "process_pid": 4242, "process_creation_time": 123.0, "process_state": "stop-unverified",
                "request": {"post_url": "https://www.facebook.com/example/posts/1"},
            }), encoding="utf-8")
            with mock.patch.object(api, "_process_identity_state", return_value="unknown"), \
                 mock.patch.object(api, "_terminate_known_pid") as terminate:
                manager, started = self._manager(base)
            self.assertEqual(manager._queue.qsize(), 0)
            self.assertEqual(manager._jobs[job_id]["status"], "failed")
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertIsNone(manager._active_worker_id)
            self.assertEqual(manager._jobs[job_id]["process_pid"], 4242)
            self.assertIn("worker-01", manager._orphaned_worker_ids)
            terminate.assert_not_called()
            started.assert_not_called()

    def test_assignment_write_failure_preserves_queued_job_and_worker(self):
        import copy
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            before = copy.deepcopy(manager._jobs[job_id])
            runtime = copy.deepcopy(manager._worker_runtime)
            disk = (base / "api-jobs" / f"{job_id}.job.json").read_bytes()
            with mock.patch.object(manager, "_persist_job", side_effect=OSError("dummy disk failure")):
                with self.assertRaises(OSError):
                    manager._assign_job_to_worker(job_id, self.workers[0])
            self.assertEqual(manager._jobs[job_id], before)
            self.assertEqual(manager._worker_runtime, runtime)
            self.assertIsNone(manager._active_job_id)
            self.assertFalse(manager._capture_lease_uncertain)
            self.assertEqual((base / "api-jobs" / f"{job_id}.job.json").read_bytes(), disk)

    def test_dispatch_write_failure_keeps_queue_without_device_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            original_get = manager._queue.get
            gets = []
            def get_once(*args, **kwargs):
                if gets:
                    raise KeyboardInterrupt
                gets.append(True)
                return original_get(*args, **kwargs)
            with mock.patch.object(manager._queue, "get", side_effect=get_once), \
                 mock.patch.object(manager, "_persist_job", side_effect=OSError("dummy disk failure")), \
                 mock.patch.object(manager, "_recover_after_worker_failure") as recover, \
                 mock.patch.object(manager, "_cleanup_job_process") as cleanup, \
                 mock.patch.object(manager, "_set_running_process") as final_callback, \
                 mock.patch.object(api, "run_capture_job") as capture, \
                 mock.patch.object(api.time, "sleep"), \
                 mock.patch.object(manager._wake_event, "wait"):
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()
            self.assertEqual(manager._jobs[job_id]["status"], "queued")
            self.assertEqual(manager._jobs[job_id]["attempts"], 0)
            self.assertIsNone(manager._active_job_id)
            self.assertIsNone(manager._worker_runtime["worker-01"]["current_job_id"])
            self.assertEqual(manager._queue.qsize(), 1)
            self.assertEqual(manager._queue.unfinished_tasks, 1)
            recover.assert_not_called()
            cleanup.assert_not_called()
            final_callback.assert_not_called()
            capture.assert_not_called()

    def test_dispatch_retries_storage_before_one_capture_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            original_persist = manager._persist_job
            original_get = manager._queue.get
            writes, gets = [], []
            def persist(job):
                if job["status"] == "running" and not writes:
                    writes.append("failed-before-capture")
                    raise OSError("dummy transient disk failure")
                writes.append(job["status"])
                original_persist(job)
            def get_twice(*args, **kwargs):
                if len(gets) == 2:
                    raise KeyboardInterrupt
                gets.append(True)
                return original_get(*args, **kwargs)
            with mock.patch.object(manager._queue, "get", side_effect=get_twice), \
                 mock.patch.object(manager, "_persist_job", side_effect=persist), \
                 mock.patch.object(manager._wake_event, "wait") as pause, \
                 mock.patch.object(api, "run_capture_job", return_value={"ok": True}) as capture, \
                 mock.patch.object(api, "materialize_capture_attempts"), \
                 mock.patch.object(api, "build_api_result", return_value={"ok": True, "completion_scope": "terminal-complete"}), \
                 mock.patch.object(manager, "_checkpoint_job_locked"), \
                 mock.patch.object(manager, "_recover_after_worker_failure") as recover:
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()
            pause.assert_called_once_with(0.5)
            capture.assert_called_once()
            recover.assert_not_called()
            self.assertEqual(manager._jobs[job_id]["status"], "completed")
            self.assertEqual(manager._jobs[job_id]["attempts"], 1)
            self.assertEqual(manager._queue.unfinished_tasks, 0)
            self.assertIsNone(manager._active_job_id)
            self.assertFalse(manager._capture_lease_uncertain)

    def _assert_finalization_storage_failure(self, *, fail_persist=False):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            next_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
            original_callback = manager._set_running_process
            original_persist = manager._persist_job
            in_final = []
            def persist(job):
                if fail_persist and in_final:
                    raise OSError("DUMMY_PRIVATE_MANIFEST_PATH")
                original_persist(job)
            def final_callback(*args, **kwargs):
                if kwargs.get("final"):
                    if not fail_persist:
                        raise OSError("DUMMY_PRIVATE_MANIFEST_PATH")
                    in_final.append(True)
                    try:
                        return original_callback(*args, **kwargs)
                    finally:
                        in_final.clear()
                return original_callback(*args, **kwargs)
            with mock.patch.object(manager, "_persist_job", side_effect=persist), \
                 mock.patch.object(manager, "_set_running_process", side_effect=final_callback), \
                 mock.patch.object(manager._wake_event, "wait", side_effect=KeyboardInterrupt), \
                 mock.patch.object(api, "run_capture_job", return_value={"ok": True}) as capture, \
                 mock.patch.object(api, "materialize_capture_attempts"), \
                 mock.patch.object(api, "build_api_result", return_value={"ok": True, "completion_scope": "terminal-complete"}), \
                 mock.patch.object(manager, "_checkpoint_job_locked"):
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()
            capture.assert_called_once()
            self.assertIsNone(manager._active_job_id)
            self.assertIsNone(manager._worker_runtime["worker-01"]["current_job_id"])
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertEqual(manager._capture_lease_job_id, job_id)
            self.assertIn("worker-01", manager._orphaned_worker_ids)
            self.assertEqual(manager._queue.unfinished_tasks, 1)
            self.assertEqual(manager._queue.qsize(), 1)
            self.assertEqual(manager._jobs[next_id]["status"], "queued")
            self.assertFalse(manager._assign_job_to_worker(next_id, self.workers[0]))
            self.assertIn("finalization", manager._capture_lease_reason)
            self.assertNotIn("DUMMY_PRIVATE_MANIFEST_PATH", manager._capture_lease_reason)

    def test_finalization_storage_failure_balances_queue_and_blocks_dispatch(self):
        for fail_persist in (False, True):
            with self.subTest(fail_persist=fail_persist):
                self._assert_finalization_storage_failure(fail_persist=fail_persist)

    def test_recovery_storage_failure_does_not_exit_scheduler_or_release_lease(self):
        for phase in ("recovery", "checkpoint", "failed-manifest"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                manager, _started = self._manager(base)
                job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                next_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
                original_persist = manager._persist_job
                def persist(job):
                    if phase == "failed-manifest" and job["status"] == "failed":
                        raise OSError("DUMMY_PRIVATE_STORAGE_PATH")
                    original_persist(job)
                with mock.patch.object(manager, "_persist_job", side_effect=persist), \
                     mock.patch.object(manager, "_recover_after_worker_failure", return_value=None,
                         side_effect=OSError("DUMMY_PRIVATE_STORAGE_PATH") if phase == "recovery" else None), \
                     mock.patch.object(manager, "_cleanup_job_process", return_value=True), \
                     mock.patch.object(manager, "_checkpoint_job_locked", return_value=None,
                         side_effect=OSError("DUMMY_PRIVATE_STORAGE_PATH") if phase == "checkpoint" else None), \
                     mock.patch.object(manager._wake_event, "wait", side_effect=KeyboardInterrupt), \
                     mock.patch.object(api, "run_capture_job", side_effect=api.ApiError("dummy capture failure")) as capture:
                    with self.assertRaises(KeyboardInterrupt):
                        manager._worker_loop()
                capture.assert_called_once()
                self.assertIsNone(manager._active_job_id)
                self.assertIsNone(manager._worker_runtime["worker-01"]["current_job_id"])
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertEqual(manager._capture_lease_job_id, job_id)
                self.assertIn("worker-01", manager._orphaned_worker_ids)
                self.assertEqual(manager._queue.unfinished_tasks, 1)
                self.assertEqual(manager._queue.qsize(), 1)
                self.assertEqual(manager._jobs[next_id]["status"], "queued")
                self.assertFalse(manager._assign_job_to_worker(next_id, self.workers[0]))
                self.assertNotIn("DUMMY_PRIVATE_STORAGE_PATH", manager._capture_lease_reason)

    def test_result_storage_failure_never_publishes_completed_or_recaptures(self):
        for phase in ("merge", "build", "checkpoint", "completed-manifest", "all-postcapture-writes"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                manager, _started = self._manager(base)
                job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                next_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
                original_persist = manager._persist_job
                original_get = manager._queue.get
                taken = []
                def get_once(*args, **kwargs):
                    if taken:
                        raise KeyboardInterrupt
                    taken.append(True)
                    return original_get(*args, **kwargs)
                captured = []
                def persist(job):
                    if job["status"] == "completed" or (captured and phase == "all-postcapture-writes"):
                        # Even re-entrant inspection at the writer must not see completed.
                        self.assertNotEqual(manager.public_job(job_id)["status"], "completed")
                        raise OSError("DUMMY_PRIVATE_OUTPUT_PATH")
                    original_persist(job)
                def capture(*args, **kwargs):
                    captured.append(True)
                    return {"ok": True}
                with mock.patch.object(manager._queue, "get", side_effect=get_once), \
                     mock.patch.object(manager, "_persist_job", side_effect=persist), \
                     mock.patch.object(api, "run_capture_job", side_effect=capture) as runner, \
                     mock.patch.object(api, "materialize_capture_attempts", side_effect=OSError("DUMMY_PRIVATE_OUTPUT_PATH") if phase == "merge" else None), \
                     mock.patch.object(manager, "_checkpoint_job_locked", side_effect=OSError("DUMMY_PRIVATE_OUTPUT_PATH") if phase == "checkpoint" else None), \
                     mock.patch.object(api, "build_api_result", return_value={"ok": True, "completion_scope": "terminal-complete"},
                         side_effect=OSError("DUMMY_PRIVATE_OUTPUT_PATH") if phase == "build" else None), \
                     mock.patch.object(manager, "_recover_after_worker_failure", return_value=None) as recover, \
                     mock.patch.object(manager, "_cleanup_job_process", return_value=True) as cleanup, \
                     mock.patch.object(manager._wake_event, "wait", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        manager._worker_loop()
                runner.assert_called_once()
                recover.assert_not_called()
                cleanup.assert_not_called()
                self.assertNotEqual(manager.public_job(job_id)["status"], "completed")
                with self.assertRaises(api.ApiError):
                    manager.result(job_id)
                self.assertEqual(manager._jobs[job_id]["attempts"], 1)
                self.assertIsNone(manager._active_job_id)
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertEqual(manager._capture_lease_job_id, job_id)
                self.assertIn("worker-01", manager._orphaned_worker_ids)
                self.assertEqual(manager._queue.qsize(), 1)
                self.assertEqual(manager._queue.unfinished_tasks, 1)
                self.assertEqual(manager._jobs[next_id]["status"], "queued")
                self.assertNotIn("DUMMY_PRIVATE_OUTPUT_PATH", manager._capture_lease_reason)
                restarted, _started = self._manager(base)
                self.assertTrue(restarted._capture_lease_uncertain)
                self.assertIsNone(restarted._active_worker_id)
                self.assertEqual(restarted._jobs[job_id]["status"], "failed")
                self.assertFalse(restarted._assign_job_to_worker(next_id, self.workers[0]))
                if phase != "all-postcapture-writes":
                    self.assertIn(job_id, restarted._persistence_blocked_jobs)
                else:
                    # Every post-capture write was unavailable, so no durable
                    # result-storage reason can exist.  The last durable running
                    # manifest is still reconstructed as a failed, unverified
                    # process identity and remains lease-blocked; it must not
                    # be treated as fresh queued work or auto-recaptured.
                    self.assertEqual(
                        restarted._jobs[job_id]["recovery_state"],
                        "restart-process-identity-unverified-no-auto-resume",
                    )
                    self.assertEqual(restarted._persistence_blocked_jobs, {})

    def test_completed_candidate_is_persisted_before_live_result_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            original_persist = manager._persist_job
            original_get = manager._queue.get
            takes, completed_writes = [], []
            def get_once(*args, **kwargs):
                if takes:
                    raise KeyboardInterrupt
                takes.append(True)
                return original_get(*args, **kwargs)
            def persist(job):
                if job["status"] == "completed" and not completed_writes:
                    completed_writes.append(True)
                    self.assertEqual(manager.public_job(job_id)["status"], "running")
                    with self.assertRaises(api.ApiError):
                        manager.result(job_id)
                original_persist(job)
            with mock.patch.object(manager._queue, "get", side_effect=get_once), \
                 mock.patch.object(manager, "_persist_job", side_effect=persist), \
                 mock.patch.object(api, "run_capture_job", return_value={"ok": True}) as capture, \
                 mock.patch.object(api, "materialize_capture_attempts"), \
                 mock.patch.object(api, "build_api_result", return_value={"ok": True, "completion_scope": "terminal-complete"}), \
                 mock.patch.object(manager, "_checkpoint_job_locked"), \
                 mock.patch.object(manager, "_recover_after_worker_failure") as recover:
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()
            capture.assert_called_once()
            recover.assert_not_called()
            self.assertTrue(completed_writes)
            self.assertEqual(manager.public_job(job_id)["status"], "completed")
            self.assertTrue(manager.result(job_id)["ok"])
            manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text())
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manager._queue.unfinished_tasks, 0)
            self.assertIsNone(manager._active_job_id)
            self.assertFalse(manager._capture_lease_uncertain)

    def test_late_stopped_callbacks_cannot_clear_storage_failure_block(self):
        for phase in ("result", "recovery", "finalization"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                self.workers[1] = replace(self.workers[1], enabled=True)
                manager, _started = self._manager(base)
                job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                next_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
                original_persist = manager._persist_job
                original_callback = manager._set_running_process
                original_get = manager._queue.get
                taken, final = [], []
                def get_once(*args, **kwargs):
                    if taken:
                        raise KeyboardInterrupt
                    taken.append(True)
                    return original_get(*args, **kwargs)
                def persist(job):
                    if ((phase == "result" and job["status"] == "completed")
                            or (phase == "recovery" and job["status"] == "failed")
                            or (phase == "finalization" and final)):
                        raise OSError("dummy storage fault")
                    original_persist(job)
                def callback(*args, **kwargs):
                    if kwargs.get("final"):
                        final.append(True)
                        try: return original_callback(*args, **kwargs)
                        finally: final.clear()
                    return original_callback(*args, **kwargs)
                with mock.patch.object(manager._queue, "get", side_effect=get_once), \
                     mock.patch.object(manager, "_persist_job", side_effect=persist), \
                     mock.patch.object(manager, "_set_running_process", side_effect=callback), \
                     mock.patch.object(manager, "_recover_after_worker_failure", return_value=None), \
                     mock.patch.object(manager, "_cleanup_job_process", return_value=True), \
                     mock.patch.object(manager, "_checkpoint_job_locked"), \
                     mock.patch.object(manager._wake_event, "wait", side_effect=KeyboardInterrupt), \
                     mock.patch.object(api, "run_capture_job", return_value={"ok": True},
                         side_effect=api.ApiError("dummy capture failure") if phase == "recovery" else None), \
                     mock.patch.object(api, "materialize_capture_attempts"), \
                     mock.patch.object(api, "build_api_result", return_value={"ok": True, "completion_scope": "terminal-complete"}):
                    with self.assertRaises(KeyboardInterrupt):
                        manager._worker_loop()
                self.assertTrue(manager._capture_lease_uncertain)
                reason = manager._capture_lease_reason
                # Storage works again, but a stopped callback alone cannot prove
                # result/recovery durability or authorize standby dispatch.
                for _ in range(2):
                    manager._set_running_process(job_id, None, final=True)
                    self.assertTrue(manager._capture_lease_uncertain)
                    self.assertEqual(manager._capture_lease_job_id, job_id)
                    self.assertEqual(manager._capture_lease_reason, reason)
                    self.assertIn("worker-01", manager._orphaned_worker_ids)
                    with manager._lock:
                        self.assertIsNone(manager._restore_worker_after_capture_cleanup_locked(job_id))
                    self.assertTrue(manager._capture_lease_uncertain)
                with mock.patch.object(api, "adb_snapshot", return_value={"state": "offline", "boot_completed": False, "proxy": ""}):
                    manager.set_worker_enabled("worker-01", False)
                self.assertFalse(manager._assign_job_to_worker(next_id, self.workers[1]))
                self.assertEqual(manager._jobs[next_id]["status"], "queued")

    def test_other_clean_job_cannot_erase_persistence_reason_or_worker_orphan(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            storage_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            clean_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
            for job_id in (storage_id, clean_id):
                manager._jobs[job_id].update(status="failed", process_state="stopped", process_pid=None,
                                            worker_id="worker-01", transport_cleanup_state="restored")
            reason = "dummy persistence acknowledgment required"
            with manager._lock:
                manager._orphaned_worker_ids.add("worker-01")
                manager._block_capture_lease_locked(storage_id, reason, persistence=True)
                # Simulate another owner finishing a distinct transport cleanup.
                manager._block_capture_lease_locked(clean_id, "dummy clean owner")
                manager._release_capture_lease_locked(clean_id)
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertEqual(manager._capture_lease_job_id, storage_id)
                self.assertEqual(manager._capture_lease_reason, reason)
                self.assertIsNone(manager._restore_worker_after_capture_cleanup_locked(clean_id))
                self.assertIn("worker-01", manager._orphaned_worker_ids)
                self.assertEqual(manager._persistence_blocked_jobs, {storage_id: reason})

    def test_restart_restores_persisted_result_storage_block_without_recapture(self):
        for cleanup_state in (None, "restored", "runner-owned", "failed"):
            with self.subTest(cleanup_state=cleanup_state), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                self.workers[1] = replace(self.workers[1], enabled=True)
                job_id = "fb_persisted_result_failure"
                (base / "api-jobs").mkdir()
                manifest = {
                    "version": 1, "job_id": job_id, "status": "failed",
                    "recovery_state": "failed-result-persistence",
                    "request": {"post_url": "https://www.facebook.com/example/posts/1"},
                    "worker_id": "worker-01", "serial": "emulator-5554",
                    "process_pid": None, "process_state": "stopped",
                    "transport_cleanup_state": cleanup_state,
                }
                (base / "api-jobs" / f"{job_id}.job.json").write_text(json.dumps(manifest), encoding="utf-8")
                with mock.patch.object(api, "_terminate_known_pid") as terminate, \
                     mock.patch.object(api, "_cleanup_transport_after_capture") as cleanup, \
                     mock.patch.object(api, "run_capture_job") as capture:
                    manager, started = self._manager(base)
                    self.assertTrue(manager._capture_lease_uncertain)
                    self.assertIn(job_id, manager._persistence_blocked_jobs)
                    self.assertEqual(manager._capture_lease_job_id, job_id)
                    self.assertIsNone(manager._active_worker_id)
                    self.assertIn("worker-01", manager._orphaned_worker_ids)
                    self.assertEqual(manager._queue.qsize(), 0)
                    self.assertEqual(manager.public_job(job_id)["status"], "failed")
                    queued = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
                    self.assertFalse(manager._assign_job_to_worker(queued, self.workers[1]))
                    reason = manager._persistence_blocked_jobs[job_id]
                    with manager._lock:
                        manager._release_capture_lease_locked(job_id)
                        self.assertIsNone(manager._restore_worker_after_capture_cleanup_locked(job_id))
                    self.assertEqual(manager._capture_lease_reason, reason)
                    second, second_started = self._manager(base)
                    self.assertTrue(second._capture_lease_uncertain)
                    self.assertIn(job_id, second._persistence_blocked_jobs)
                    self.assertIsNone(second._active_worker_id)
                    terminate.assert_not_called()
                    cleanup.assert_not_called()
                    capture.assert_not_called()
                    started.assert_not_called()
                    second_started.assert_not_called()

    def test_restart_normal_terminal_diagnostics_do_not_create_storage_block(self):
        for state in (None, "failed", "failed-after-persisted-checkpoint", "completed-with-deduplicated-attempts"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                (base / "api-jobs").mkdir()
                job_id = "fb_normal_terminal"
                status = "completed" if state == "completed-with-deduplicated-attempts" else "failed"
                (base / "api-jobs" / f"{job_id}.job.json").write_text(json.dumps({
                    "version": 1, "job_id": job_id, "status": status, "recovery_state": state,
                    "request": {"post_url": "https://www.facebook.com/example/posts/1"},
                    "worker_id": "worker-01", "serial": "emulator-5554",
                    "process_pid": None, "process_state": "stopped", "transport_cleanup_state": "restored",
                }), encoding="utf-8")
                manager, _started = self._manager(base)
                self.assertFalse(manager._capture_lease_uncertain)
                self.assertEqual(manager._persistence_blocked_jobs, {})
                self.assertFalse(manager._orphaned_worker_ids)
                self.assertEqual(manager._active_worker_id, "worker-01")
                self.assertEqual(manager._queue.qsize(), 0)
                queued = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
                self.assertTrue(manager._assign_job_to_worker(queued, self.workers[0]))

    def test_restart_storage_diagnostic_survives_uncertain_process_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "api-jobs").mkdir()
            job_id = "fb_result_error_unknown_process"
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.write_text(json.dumps({
                "version": 1, "job_id": job_id, "status": "failed",
                "recovery_state": "failed-result-persistence",
                "request": {"post_url": "https://www.facebook.com/example/posts/1"},
                "worker_id": "worker-01", "serial": "emulator-5554",
                "process_pid": 4242, "process_creation_time": 123.0,
                "process_state": "stop-unverified", "transport_cleanup_state": "restored",
            }), encoding="utf-8")
            with mock.patch.object(api, "_process_identity_state", return_value="unknown"), \
                 mock.patch.object(api, "_terminate_known_pid") as terminate:
                manager, _started = self._manager(base)
                self.assertIn(job_id, manager._persistence_blocked_jobs)
                self.assertEqual(manager._jobs[job_id]["process_pid"], 4242)
                self.assertEqual(json.loads(path.read_text())["recovery_state"], "failed-result-persistence")
                second, _started = self._manager(base)
                self.assertIn(job_id, second._persistence_blocked_jobs)
                self.assertTrue(second._capture_lease_uncertain)
                terminate.assert_not_called()

    def _result_storage_failure_fixture(self, manager, job_id):
        job = manager._jobs[job_id]
        job.update(status="failed", result=None, completion_scope=None,
                   recovery_state="failed-result-persistence", process_state="stopped",
                   process_pid=None, process_creation_time=None,
                   worker_id="worker-01", serial="emulator-5554",
                   transport_cleanup_state="runner-owned" if api.sys.platform in {"win32", "darwin"} else "not-required",
                   error="dummy result storage failure")
        manager._persist_job(job)
        manager._orphaned_worker_ids.add("worker-01")
        with manager._lock:
            manager._block_capture_lease_locked(job_id, "dummy storage acknowledgment required", persistence=True)

    def test_result_storage_reconciliation_requires_ack_and_confirmed_guards(self):
        for guard in ("ack", "truthy-ack", "active", "pid", "handle", "process-state", "transport", "transport-error", "wrong-platform-cleanup", "device-failed", "not-failed", "wrong-diagnostic", "registry-missing"):
            with self.subTest(guard=guard), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                manager, _started = self._manager(base)
                job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                self._result_storage_failure_fixture(manager, job_id)
                if guard == "active": manager._active_job_id = "fb_unrelated_active"
                if guard == "pid": manager._jobs[job_id]["process_pid"] = 4242
                if guard == "handle": manager._running_processes[job_id] = mock.Mock()
                if guard == "process-state": manager._jobs[job_id]["process_state"] = "stop-unverified"
                if guard == "transport": manager._jobs[job_id]["transport_cleanup_state"] = None
                if guard == "transport-error": manager._jobs[job_id]["transport_cleanup_error"] = "dummy contradictory restore"
                if guard == "wrong-platform-cleanup": manager._jobs[job_id]["transport_cleanup_state"] = "not-required" if api.sys.platform in {"win32", "darwin"} else "runner-owned"
                if guard == "device-failed": manager._failed_worker_ids.add("worker-01")
                if guard == "not-failed": manager._jobs[job_id]["status"] = "running"
                if guard == "wrong-diagnostic": manager._jobs[job_id]["recovery_state"] = "failed"
                if guard == "registry-missing": manager._persistence_blocked_jobs.pop(job_id)
                disk = (base / "api-jobs" / f"{job_id}.job.json").read_bytes()
                with mock.patch.object(manager, "_persist_job") as persist:
                    with self.assertRaises(api.ApiError):
                        manager.reconcile_result_persistence(job_id, acknowledge=1 if guard == "truthy-ack" else (guard != "ack"))
                persist.assert_not_called()
                self.assertEqual(job_id in manager._persistence_blocked_jobs, guard != "registry-missing")
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertEqual((base / "api-jobs" / f"{job_id}.job.json").read_bytes(), disk)

    def test_result_storage_reconciliation_write_failure_keeps_block(self):
        import copy
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            self._result_storage_failure_fixture(manager, job_id)
            before = copy.deepcopy(manager._jobs[job_id])
            disk = (base / "api-jobs" / f"{job_id}.job.json").read_bytes()
            with mock.patch.object(api.os, "replace", side_effect=PermissionError("dummy storage failure")):
                with self.assertRaises(OSError):
                    manager.reconcile_result_persistence(job_id, acknowledge=True)
            self.assertEqual(manager._jobs[job_id], before)
            self.assertIn(job_id, manager._persistence_blocked_jobs)
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertIn("worker-01", manager._orphaned_worker_ids)
            self.assertEqual((base / "api-jobs" / f"{job_id}.job.json").read_bytes(), disk)

    def test_concurrent_reconciliation_has_only_one_durable_ack(self):
        import queue
        for first_write_fails in (False, True):
            with self.subTest(first_write_fails=first_write_fails), tempfile.TemporaryDirectory() as temp, \
                 mock.patch.object(api, "BASE_DIR", Path(temp)), \
                 mock.patch.object(api.JobManager, "_start_worker", return_value=None) as startup, \
                 mock.patch.object(api, "run_capture_job") as capture:
                manager = api.JobManager(workers=self.workers)
                target = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                self._result_storage_failure_fixture(manager, target)
                manager._queue.get_nowait()
                manager._queue.task_done()
                before = api._job_manifest_path(target).read_bytes()
                original_persist = manager._persist_job
                writer_entered = threading.Event()
                release_writer = threading.Event()
                second_started = threading.Event()
                outcomes = queue.Queue()
                calls = []

                def persist(candidate):
                    calls.append(threading.current_thread().name)
                    if threading.current_thread().name == "ack-first":
                        writer_entered.set()
                        if not release_writer.wait(3):
                            raise RuntimeError("bounded fixture release timed out")
                        self.assertIn(target, manager._persistence_blocked_jobs)
                        self.assertEqual(manager._jobs[target]["recovery_state"], "failed-result-persistence")
                        self.assertEqual(api._job_manifest_path(target).read_bytes(), before)
                        if first_write_fails:
                            raise OSError("dummy first writer failure")
                    original_persist(candidate)

                def acknowledge(second=False):
                    if second:
                        second_started.set()
                    try:
                        manager.reconcile_result_persistence(target, acknowledge=True)
                        outcomes.put("reconciled")
                    except api.ApiError:
                        outcomes.put("conflict")
                    except OSError:
                        outcomes.put("io-error")
                    except Exception as exc:
                        outcomes.put(type(exc).__name__)

                first = threading.Thread(target=acknowledge, name="ack-first", daemon=True)
                second = threading.Thread(target=acknowledge, args=(True,), name="ack-second", daemon=True)
                with mock.patch.object(manager, "_persist_job", side_effect=persist):
                    try:
                        first.start()
                        self.assertTrue(writer_entered.wait(3))
                        second.start()
                        self.assertTrue(second_started.wait(3))
                        self.assertEqual(calls, ["ack-first"])
                    finally:
                        release_writer.set()
                        first.join(5)
                        if second.ident is not None:
                            second.join(5)
                    self.assertFalse(first.is_alive())
                    self.assertFalse(second.is_alive())
                actual = sorted([outcomes.get_nowait(), outcomes.get_nowait()])
                self.assertEqual(actual, sorted(["reconciled", "io-error" if first_write_fails else "conflict"]))
                self.assertEqual(len(calls), 2 if first_write_fails else 1)
                stored = json.loads(api._job_manifest_path(target).read_text())
                self.assertEqual(stored["status"], "failed")
                self.assertEqual(stored["recovery_state"], "failed-result-persistence-reconciled")
                self.assertEqual(manager._jobs[target]["status"], "failed")
                self.assertIsNone(manager._jobs[target]["result"])
                self.assertNotIn(target, manager._persistence_blocked_jobs)
                self.assertFalse(manager._capture_lease_uncertain)
                self.assertEqual(manager._queue.qsize(), 0)
                self.assertEqual(startup.call_count, 2)  # initial scheduler + one valid acknowledgment
                capture.assert_not_called()

    def test_result_storage_reconciliation_clears_only_target_and_never_recaptures(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            first = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            second = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
            self._result_storage_failure_fixture(manager, first)
            self._result_storage_failure_fixture(manager, second)
            # Remove stale queue items to match jobs already processed by scheduler.
            for _ in range(2): manager._queue.get_nowait(); manager._queue.task_done()
            queued = manager.submit({"post_url": "https://www.facebook.com/example/posts/3"})["job_id"]
            manager, startup = self._manager(base)
            self.assertIsNone(manager._worker)
            startup.assert_not_called()
            with mock.patch.object(api, "run_capture_job") as capture, \
                 mock.patch.object(api, "_cleanup_transport_after_capture") as cleanup:
                result = manager.reconcile_result_persistence(second, acknowledge=True)
                self.assertTrue(result["reconciled"])
                self.assertNotIn(second, manager._persistence_blocked_jobs)
                self.assertIn(first, manager._persistence_blocked_jobs)
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertIn("worker-01", manager._orphaned_worker_ids)
                self.assertFalse(manager._assign_job_to_worker(queued, self.workers[0]))
                self.assertIsNone(manager._worker)
                startup.assert_not_called()
                manager.reconcile_result_persistence(first, acknowledge=True)
                startup.assert_called_once()
                self.assertIsNotNone(manager._worker)
                self.assertFalse(manager._capture_lease_uncertain)
                self.assertFalse(manager._orphaned_worker_ids)
                self.assertEqual(manager._persistence_blocked_jobs, {})
                self.assertEqual(manager._jobs[first]["status"], "failed")
                self.assertIsNone(manager._jobs[first]["result"])
                self.assertEqual(manager._jobs[first]["error"], "dummy result storage failure")
                self.assertEqual(manager._queue.qsize(), 1)
                restarted, _started = self._manager(base)
                self.assertFalse(restarted._capture_lease_uncertain)
                self.assertEqual(restarted._jobs[first]["status"], "failed")
                self.assertEqual(restarted._queue.qsize(), 1)
                self.assertTrue(restarted._assign_job_to_worker(queued, self.workers[0]))
                capture.assert_not_called()
                cleanup.assert_not_called()

    def test_persisted_result_reconciliation_starts_scheduler_only_after_last_block(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
            self._result_storage_failure_fixture(manager, job_id)
            restarted, started = self._manager(base)
            self.assertIsNone(restarted._worker)
            started.assert_not_called()
            with mock.patch.object(restarted, "_start_worker", wraps=restarted._start_worker) as start_worker:
                restarted.reconcile_result_persistence(job_id, acknowledge=True)
            start_worker.assert_called_once_with("worker-01")
            self.assertIsNotNone(restarted._worker)
            self.assertFalse(restarted._capture_lease_uncertain)
            self.assertEqual(restarted._jobs[job_id]["status"], "failed")
            self.assertEqual(restarted._queue.qsize(), 0)

    def test_result_reconciliation_retains_sibling_process_and_transport_blocks(self):
        for kind in ("pid", "handle", "transport"):
            for sibling_worker in ("worker-01", "worker-02"):
                for owner in ("target", "sibling"):
                    with self.subTest(kind=kind, worker=sibling_worker, owner=owner), tempfile.TemporaryDirectory() as temp:
                        manager, _started = self._manager(Path(temp))
                        target = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                        sibling = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})["job_id"]
                        self._result_storage_failure_fixture(manager, target)
                        other = manager._jobs[sibling]
                        other.update(status="failed", process_state="stopped", process_pid=None,
                                     recovery_state="failed", worker_id=sibling_worker,
                                     transport_cleanup_state=manager._jobs[target]["transport_cleanup_state"])
                        if kind == "pid":
                            other.update(process_pid=4242, process_state="stop-unverified")
                        elif kind == "handle":
                            manager._running_processes[sibling] = mock.Mock()
                        else:
                            other["transport_cleanup_state"] = "restore-failed"
                        manager._persist_job(other)
                        manager._orphaned_worker_ids.add(sibling_worker)
                        for _ in range(2):
                            manager._queue.get_nowait()
                            manager._queue.task_done()
                        queued = manager.submit({"post_url": "https://www.facebook.com/example/posts/3"})["job_id"]
                        manager._capture_lease_job_id = target if owner == "target" else sibling
                        sibling_disk = api._job_manifest_path(sibling).read_bytes()
                        with mock.patch.object(manager, "_start_worker") as startup, \
                             mock.patch.object(api, "run_capture_job") as capture, \
                             mock.patch.object(api, "_cleanup_transport_after_capture") as cleanup:
                            result = manager.reconcile_result_persistence(target, acknowledge=True)
                            self.assertTrue(result["reconciled"])
                            self.assertNotIn(target, manager._persistence_blocked_jobs)
                            self.assertTrue(manager._capture_lease_uncertain)
                            self.assertEqual(manager._capture_lease_job_id, sibling)
                            self.assertIn(sibling_worker, manager._orphaned_worker_ids)
                            self.assertFalse(manager._assign_job_to_worker(queued, self.workers[0]))
                            self.assertFalse(manager._assign_job_to_worker(queued, self.workers[1]))
                            self.assertEqual(manager._queue.qsize(), 1)
                            self.assertEqual(api._job_manifest_path(sibling).read_bytes(), sibling_disk)
                            self.assertEqual(manager._jobs[target]["status"], "failed")
                            self.assertIsNone(manager._jobs[target]["result"])
                            stored = json.loads(api._job_manifest_path(target).read_text())
                            self.assertEqual(stored["recovery_state"], "failed-result-persistence-reconciled")
                            startup.assert_not_called()
                            capture.assert_not_called()
                            cleanup.assert_not_called()

    def test_result_reconciliation_obeys_platform_cleanup_markers(self):
        for platform, clean, accepted in (("win32", "restored", True), ("win32", "runner-owned", True), ("win32", "not-required", False),
                                          ("darwin", "runner-owned", True), ("darwin", "restored", False),
                                          ("linux", "not-required", True), ("linux", "runner-owned", False)):
            with self.subTest(platform=platform, cleanup=clean), tempfile.TemporaryDirectory() as temp:
                manager, _started = self._manager(Path(temp))
                job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                self._result_storage_failure_fixture(manager, job_id)
                manager._jobs[job_id]["transport_cleanup_state"] = clean
                with mock.patch.object(api.sys, "platform", platform):
                    if accepted:
                        self.assertTrue(manager.reconcile_result_persistence(job_id, acknowledge=True)["reconciled"])
                        self.assertFalse(manager._capture_lease_uncertain)
                    else:
                        with self.assertRaises(api.ApiError):
                            manager.reconcile_result_persistence(job_id, acknowledge=True)
                        self.assertTrue(manager._capture_lease_uncertain)

    def test_reload_workers_rejects_existing_identity_change(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            changed = replace(self.workers[0], serial="emulator-5560")
            api.write_worker_config(base / "facebook_workers.json", [changed, self.workers[1]])

            with self.assertRaisesRegex(api.ApiError, "immutable identity"):
                manager.reload_workers()

            self.assertEqual(manager.workers["worker-01"].serial, "emulator-5554")

    def test_reload_workers_rejects_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            api.write_worker_config(base / "facebook_workers.json", [self.workers[0]])

            with self.assertRaisesRegex(api.ApiError, "remove"):
                manager.reload_workers()

            self.assertEqual(set(manager.workers), {"worker-01", "worker-02"})


class RequestTests(unittest.TestCase):
    def test_normalize_request_defaults_to_reactions(self):
        request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        self.assertEqual(request["collect"], ["reactions"])
        self.assertEqual(request["post_id"], "")
        self.assertEqual(request["reaction_scroll_limit"], api.REACTION_SCROLL_LIMIT_DEFAULT)
        self.assertFalse(request["reaction_scroll_limit_explicit"])

    def test_normalize_reaction_scroll_limit_defaults_to_unlimited_and_accepts_boundaries(self):
        default_request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        self.assertEqual(default_request["reaction_scroll_limit"], api.REACTION_SCROLL_LIMIT_DEFAULT)
        self.assertFalse(default_request["reaction_scroll_limit_explicit"])
        for value in (api.REACTION_SCROLL_LIMIT_MIN, api.REACTION_SCROLL_LIMIT_MAX):
            with self.subTest(value=value):
                request = api.normalize_request({
                    "post_url": "https://www.facebook.com/example/posts/1",
                    "reaction_scroll_limit": value,
                })
                self.assertEqual(request["reaction_scroll_limit"], value)
                self.assertTrue(request["reaction_scroll_limit_explicit"])

    def test_normalize_reaction_scroll_limit_null_is_unlimited(self):
        request = api.normalize_request({
            "post_url": "https://www.facebook.com/example/posts/1",
            "reaction_scroll_limit": None,
        })
        self.assertIsNone(request["reaction_scroll_limit"])
        self.assertFalse(request["reaction_scroll_limit_explicit"])

    def test_normalize_reaction_scroll_limit_rejects_invalid_values(self):
        for value in (0, api.REACTION_SCROLL_LIMIT_MAX + 1, -1, 1.5, True, "3"):
            with self.subTest(value=value):
                with self.assertRaises(api.ApiError):
                    api.normalize_request({
                        "post_url": "https://www.facebook.com/example/posts/1",
                        "reaction_scroll_limit": value,
                    })

    def test_normalize_url_decodes_u0026(self):
        request = api.normalize_request({"post_url": "https://m.facebook.com/story.php?story_fbid=1\\u0026id=2"})
        self.assertIn("&id=2", request["post_url"])

    def test_rejects_non_facebook_url(self):
        with self.assertRaises(api.ApiError):
            api.normalize_request({"post_url": "https://example.com/post/1"})

    def test_save_artifacts_defaults_to_lightweight_and_accepts_empty_selection(self):
        default_request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        minimal_request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1", "save_artifacts": []})
        self.assertEqual(default_request["save_artifacts"], ["safe_capture", "progress"])
        self.assertEqual(default_request["save_artifacts"], list(api.DEFAULT_SAVE_ARTIFACTS))
        self.assertEqual(minimal_request["save_artifacts"], [])

    def test_rejects_unknown_artifact_group(self):
        with self.assertRaises(api.ApiError):
            api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1", "save_artifacts": ["unknown"]})

    def test_normalize_batch_request_deduplicates_urls_and_keeps_order(self):
        request = api.normalize_batch_request({
            "post_urls": [
                "https://www.facebook.com/example/posts/1",
                "https://www.facebook.com/example/posts/2",
                "https://www.facebook.com/example/posts/1",
            ],
            "save_artifacts": ["safe_capture"],
            "reaction_scroll_limit": api.REACTION_SCROLL_LIMIT_MAX,
        })
        self.assertEqual(request["post_urls"], [
            "https://www.facebook.com/example/posts/1",
            "https://www.facebook.com/example/posts/2",
        ])
        self.assertEqual(request["save_artifacts"], ["safe_capture"])
        self.assertEqual(request["reaction_scroll_limit"], api.REACTION_SCROLL_LIMIT_MAX)
        self.assertTrue(request["reaction_scroll_limit_explicit"])

    def test_normalize_batch_request_marks_default_limit_as_not_explicit(self):
        request = api.normalize_batch_request({
            "post_urls": ["https://www.facebook.com/example/posts/1"],
        })
        self.assertEqual(request["reaction_scroll_limit"], api.REACTION_SCROLL_LIMIT_DEFAULT)
        self.assertFalse(request["reaction_scroll_limit_explicit"])

    def test_normalize_batch_request_rejects_empty_and_over_limit_batches(self):
        with self.assertRaises(api.ApiError):
            api.normalize_batch_request({"post_urls": []})
        with self.assertRaises(api.ApiError):
            api.normalize_batch_request({
                "post_urls": [f"https://www.facebook.com/example/posts/{index}" for index in range(api.MAX_BATCH_POSTS + 1)]
            })

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_supported_facebook_link_families_are_forwarded_without_special_cases(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        urls = (
            "https://www.facebook.com/share/p/genericSharePost/",
            "https://www.facebook.com/share/r/genericShareReel/",
            "https://www.facebook.com/groups/123456789/permalink/987654321/",
            "https://www.facebook.com/example/posts/pfbidGenericPost",
            "https://www.facebook.com/reel/123456789/",
        )
        for url in urls:
            with self.subTest(url=url):
                request = api.normalize_request({"post_url": url})
                command = api.build_capture_command(request)
                self.assertEqual(command[command.index("-PostUrl") + 1], url)
                self.assertEqual(command[command.index("-ReactionNativeScrollEnabled") + 1], "1")

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_capture_command_uses_unlimited_defaults_when_limit_is_omitted(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        command = api.build_capture_command(request)
        self.assertIn("-OpenReactionList", command)
        self.assertIn("-AutoScreenThenDeep", command)
        self.assertNotIn("-EnableRawNetworkCapture", command)
        self.assertIn("-DisableRawHttpCapture", command)
        self.assertEqual(command[command.index("-ReactionScrollCount") + 1], "60")
        self.assertEqual(command[command.index("-ReactionAbsoluteCap") + 1], "0")
        self.assertEqual(command[command.index("-MaxFlows") + 1], "0")
        self.assertEqual(command[command.index("-DurationSeconds") + 1], "0")
        self.assertEqual(command[command.index("-ScrollCount") + 1], "0")
        self.assertEqual(command[command.index("-ReactionProgressPollIntervalMs") + 1], "500")
        self.assertEqual(command[command.index("-ReactionProgressPollTimeoutMs") + 1], "2200")
        self.assertEqual(command[command.index("-ReactionLiveProgressPollIntervalMs") + 1], "150")
        self.assertEqual(command[command.index("-ReactionLiveProgressPollTimeoutMs") + 1], "1200")
        self.assertEqual(command[command.index("-ReactionSteadyNoGrowthPollTimeoutMs") + 1], "500")
        self.assertEqual(command[command.index("-ReactionLongSwipeStartY") + 1], "1700")
        self.assertEqual(command[command.index("-ReactionLongSwipeEndY") + 1], "250")
        self.assertEqual(command[command.index("-ReactionLongSwipeDurationMs") + 1], "300")
        self.assertEqual(command[command.index("-ReactionPipelineBurstSize") + 1], "2")
        self.assertEqual(command[command.index("-ReactionPipelineSwipeGapMs") + 1], "150")
        self.assertEqual(command[command.index("-ReactionNativeScrollEnabled") + 1], "1")
        self.assertEqual(command[command.index("-ReactionNativeScrollTimeoutMs") + 1], "2500")
        self.assertEqual(command[command.index("-ReactionUiDumpEveryN") + 1], "64")
        self.assertEqual(command[command.index("-ReactionMinCycleMs") + 1], "700")
        self.assertEqual(command[command.index("-ReactionInitialPaginationCooldownSeconds") + 1], "4")
        self.assertEqual(command[command.index("-ReactionNoGrowthUiDumpEveryN") + 1], "1")

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_capture_command_treats_unmarked_legacy_limit_as_unlimited(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = {
            "post_url": "https://www.facebook.com/example/posts/1",
            "post_id": "",
            "collect": ["reactions"],
            "reaction_scroll_limit": 3,
            "reaction_scroll_limit_explicit": False,
        }
        command = api.build_capture_command(request)
        self.assertEqual(command[command.index("-ReactionScrollCount") + 1], "60")
        self.assertEqual(command[command.index("-ReactionAbsoluteCap") + 1], "0")

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch("facebook_capture_api.sys.platform", "win32")
    def test_capture_command_uses_explicit_limit_for_both_powershell_caps(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({
            "post_url": "https://www.facebook.com/example/posts/1",
            "reaction_scroll_limit": 17,
        })
        command = api.build_capture_command(request)
        self.assertEqual(command[command.index("-ReactionScrollCount") + 1], "17")
        self.assertEqual(command[command.index("-ReactionAbsoluteCap") + 1], "17")

    @mock.patch("facebook_capture_api.sys.platform", "darwin")
    def test_capture_command_forwards_limit_to_macos_runner(self):
        request = api.normalize_request({
            "post_url": "https://www.facebook.com/example/posts/1",
            "reaction_scroll_limit": 11,
        })
        command = api.build_capture_command(request)
        self.assertEqual(command[command.index("--max-scrolls") + 1], "11")

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_capture_command_skips_raw_pcap_when_not_selected(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({
            "post_url": "https://www.facebook.com/example/posts/1",
            "save_artifacts": ["mitm", "safe_capture", "progress"],
        })
        self.assertNotIn("-EnableRawNetworkCapture", api.build_capture_command(request))


class ProgressTests(unittest.TestCase):
    @staticmethod
    def _write_live_progress(path: Path, users: int, pages: int, terminal: bool) -> None:
        path.write_text(
            json.dumps(
                {
                    "record_line_count": users,
                    "families": {
                        "reaction": {
                            "sources": {
                                "post": {
                                    "user_count": users,
                                    "page_count": pages,
                                    "terminal_page_seen": terminal,
                                }
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_progress_snapshot_is_sanitized_and_lists_evidence_layers(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "analysis").mkdir()
            (run_dir / "raw-network.pcap").write_bytes(b"pcap-data")
            (run_dir / "raw-facebook-http.mitm").write_bytes(b"mitm-data")
            (run_dir / "safe-facebook-http-capture.jsonl").write_text("{}\n", encoding="utf-8")
            (run_dir / "analysis" / "facebook-reactor-users.csv").write_text("user_id,user_name\n1,Alice\n", encoding="utf-8")
            (run_dir / "facebook-live-progress.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-09-02T04:09:26Z",
                        "record_line_count": 10,
                        "families": {
                            "reaction": {
                                "sources": {
                                    "secret-post-id": {
                                        "query_records": 4,
                                        "user_count": 75,
                                        "page_count": 4,
                                        "terminal_page_seen": True,
                                        "rate_limit_count": 0,
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            snapshot = api.build_progress_snapshot(
                run_dir,
                job_status="completed",
                android={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
            )
            encoded = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn("secret-post-id", encoded)
            self.assertNotIn("Alice", encoded)
            self.assertEqual(snapshot["metrics"]["reaction_users"], 75)
            self.assertEqual(snapshot["metrics"]["reaction_pages"], 4)
            self.assertTrue(snapshot["metrics"]["terminal"])
            self.assertEqual(snapshot["metrics"]["valid_reactor_users"], 1)
            names = {item["name"] for item in snapshot["artifacts"]}
            self.assertIn("raw-network.pcap", names)
            self.assertIn("raw-facebook-http.mitm", names)
            self.assertIn("facebook-reactor-users.csv", names)
            users = next(item for item in snapshot["artifacts"] if item["name"] == "facebook-reactor-users.csv")
            pcap = next(item for item in snapshot["artifacts"] if item["name"] == "raw-network.pcap")
            self.assertTrue(users["viewable"])
            self.assertFalse(pcap["viewable"])
            self.assertIn("{job_id}", users["url"])

    def test_bounded_sample_progress_marks_pagination_complete_with_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "analysis").mkdir()
            (run_dir / "analysis" / "facebook-reactor-users.csv").write_text(
                "user_id,user_name\n1,Alice\n", encoding="utf-8"
            )
            (run_dir / "analysis" / "facebook-sample-patterns.json").write_text("{}", encoding="utf-8")
            snapshot = api.build_progress_snapshot(
                run_dir,
                job_status="completed",
                request={"save_artifacts": []},
                completion_scope="bounded-sample",
            )
            self.assertEqual(snapshot["completion_scope"], "bounded-sample")
            pagination = next(stage for stage in snapshot["stages"] if stage["id"] == "pagination")
            complete = next(stage for stage in snapshot["stages"] if stage["id"] == "complete")
            self.assertEqual(pagination["state"], "complete")
            self.assertIn("非全量", complete["detail"])
            self.assertIn("必留解析结果", complete["detail"])

    def test_progress_snapshot_noncompleted_statuses_do_not_claim_artifact_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            for status in ("failed", "cancelled", "cancelling", "queued", "running"):
                snapshot = api.build_progress_snapshot(run_dir, status)
                stages = {stage["id"]: stage for stage in snapshot["stages"]}
                self.assertNotIn("已落盘", stages["complete"]["detail"])
                self.assertNotIn("完成解密校验", stages["decrypt"]["detail"])

    def test_batch_stage_without_evidence_starts_at_prepare_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = api.build_progress_snapshot(Path(temp), "running")

            self.assertEqual(api._select_batch_current_stage(snapshot), "准备抓取")

    def test_batch_stage_after_verified_target_moves_to_reaction_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            (analysis / "facebook-sample-patterns.json").write_text(
                json.dumps({"target_verification": {"status": "matched"}}),
                encoding="utf-8",
            )

            snapshot = api.build_progress_snapshot(run_dir, "running")

            self.assertEqual(api._select_batch_current_stage(snapshot), "Reaction 分页")

    def test_batch_stage_with_reaction_data_stays_on_reaction_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_live_progress(run_dir / "facebook-live-progress.json", 50, 3, False)

            snapshot = api.build_progress_snapshot(run_dir, "running")

            self.assertEqual(api._select_batch_current_stage(snapshot), "Reaction 分页")

    def test_batch_stage_after_terminal_page_moves_to_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_live_progress(run_dir / "facebook-live-progress.json", 50, 3, True)

            snapshot = api.build_progress_snapshot(run_dir, "running")

            self.assertEqual(api._select_batch_current_stage(snapshot), "解析与去重")

    def test_completed_default_lightweight_progress_reports_selected_evidence_only(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            (analysis / "facebook-sample-patterns.json").write_text("{}", encoding="utf-8")
            (analysis / "facebook-reactor-users.csv").write_text("user_id\n1\n", encoding="utf-8")
            (run_dir / "safe-facebook-http-capture.jsonl").write_text("{}\n", encoding="utf-8")
            (run_dir / "facebook-live-progress.json").write_text("{}", encoding="utf-8")

            snapshot = api.build_progress_snapshot(run_dir, "completed")
            stages = {stage["id"]: stage for stage in snapshot["stages"]}

            self.assertIn("必留解析结果和所选证据均已落盘", stages["complete"]["detail"])
            self.assertNotIn("PCAP", stages["complete"]["detail"])
            self.assertNotIn("MITM", stages["complete"]["detail"])
            self.assertNotIn("完成解密校验", stages["decrypt"]["detail"])

    def test_completed_progress_reports_missing_selected_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            (analysis / "facebook-sample-patterns.json").write_text("{}", encoding="utf-8")
            (analysis / "facebook-reactor-users.csv").write_text("user_id\n1\n", encoding="utf-8")

            snapshot = api.build_progress_snapshot(
                run_dir,
                "completed",
                request={"save_artifacts": ["mitm"]},
            )
            stages = {stage["id"]: stage for stage in snapshot["stages"]}

            self.assertIn("所选证据未全部就绪", stages["complete"]["detail"])
            self.assertIn("MITM 归档与索引未全部就绪", stages["decrypt"]["detail"])

    def test_progress_merges_current_attempt_history_and_root_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "attempt-002"
            previous = root / "attempt-001"
            for attempt in (current, previous):
                (attempt / "analysis").mkdir(parents=True)
            self._write_live_progress(current / "facebook-live-progress.json", 3, 2, False)
            self._write_live_progress(previous / "facebook-live-progress.json", 10, 7, True)
            (current / "analysis" / "facebook-reactor-users.csv").write_text(
                "user_id,line\nu1,12\nu2,13\n", encoding="utf-8"
            )
            (previous / "analysis" / "facebook-reactor-users.csv").write_text(
                "user_id,line\nu2,8\nu3,9\n", encoding="utf-8"
            )
            checkpoint = root / "checkpoint"
            checkpoint.mkdir()
            (checkpoint / "facebook-reactor-users.csv").write_text(
                "user_id,line\nu4,2\n", encoding="utf-8"
            )

            snapshot = api.build_progress_snapshot(
                current,
                "running",
                evidence_dirs=[current, previous, root, checkpoint],
            )

            self.assertEqual(snapshot["metrics"]["reaction_users"], 10)
            self.assertEqual(snapshot["metrics"]["reaction_pages"], 7)
            self.assertEqual(snapshot["metrics"]["valid_reactor_users"], 4)
            self.assertFalse(snapshot["metrics"]["terminal"])

    def test_unselected_artifacts_are_reported_as_not_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "analysis").mkdir()
            (run_dir / "analysis" / "facebook-reactor-users.csv").write_text("user_id\n1\n", encoding="utf-8")
            (run_dir / "analysis" / "facebook-sample-patterns.json").write_text("{}", encoding="utf-8")
            snapshot = api.build_progress_snapshot(
                run_dir,
                "completed",
                request={"save_artifacts": []},
            )
            pcap = next(item for item in snapshot["artifacts"] if item["name"] == "raw-network.pcap")
            users = next(item for item in snapshot["artifacts"] if item["name"] == "facebook-reactor-users.csv")
            self.assertEqual(pcap["state"], "not_saved")
            self.assertFalse(pcap["selected"])
            self.assertEqual(users["state"], "ready")
            self.assertTrue(users["required"])

    def test_prune_optional_artifacts_keeps_required_results(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            for relative in api.ARTIFACT_PATHS.values():
                path = run_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("data", encoding="utf-8")
            api._prune_optional_artifacts(run_dir, {"save_artifacts": ["safe_capture"]})
            self.assertFalse((run_dir / "raw-network.pcap").exists())
            self.assertFalse((run_dir / "raw-facebook-http.mitm").exists())
            self.assertTrue((run_dir / "safe-facebook-http-capture.jsonl").exists())
            self.assertTrue((run_dir / "analysis" / "facebook-reactor-users.csv").exists())
            self.assertTrue((run_dir / "analysis" / "facebook-sample-patterns.json").exists())

    def test_static_asset_path_is_allowlisted(self):
        self.assertEqual(api.resolve_static_asset("/"), api.WEB_DIR / "index.html")
        self.assertEqual(api.resolve_static_asset("/app.js"), api.WEB_DIR / "app.js")
        self.assertIsNone(api.resolve_static_asset("/../facebook_capture_api.py"))

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_capture_command_accepts_deterministic_output_dir(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]}
        run_dir = Path("C:/work/facebook/runs/api-fb_test")
        command = api.build_capture_command(request, run_dir=run_dir)
        self.assertEqual(command[command.index("-OutputDir") + 1], str(run_dir))


class CaptureJobTests(unittest.TestCase):
    def _run_with_fake_process(self, timeout_seconds=None):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run_dir = base / "runs" / "fake-run"
            run_dir.mkdir(parents=True)
            process = mock.Mock()
            process.returncode = 0
            with mock.patch.object(api, "BASE_DIR", base), \
                 mock.patch("facebook_capture_api.ensure_emulator"), \
                 mock.patch("facebook_capture_api.build_capture_command", return_value=["powershell"]), \
                 mock.patch("facebook_capture_api.subprocess.Popen", return_value=process), \
                 mock.patch("facebook_capture_api.build_api_result", return_value={"ok": True}):
                api.run_capture_job(
                    {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                    "job-test",
                    timeout_seconds=timeout_seconds,
                )
            return process


class BatchAggregationTests(unittest.TestCase):
    def test_batch_outputs_keep_cancelling_child_nonterminal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = [
                {
                    "job_id": "fb_cancelling",
                    "status": "cancelling",
                    "run_dir": str(root / "post-001"),
                    "request": {"post_url": "https://www.facebook.com/example/posts/1"},
                },
                {
                    "job_id": "fb_queued",
                    "status": "queued",
                    "run_dir": str(root / "post-002"),
                    "request": {"post_url": "https://www.facebook.com/example/posts/2"},
                },
            ]

            summary = api.write_batch_outputs(root / "batch", "cancelling_batch", jobs)

            self.assertEqual(summary["status"], "running")
            self.assertEqual(summary["status_counts"]["cancelling"], 1)
            self.assertEqual(summary["status_counts"]["queued"], 1)
            with (root / "batch" / "batch-posts.csv").open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(next(csv.reader(handle))[:8], [
                    "post_index", "job_id", "post_url", "status", "completion_scope",
                    "valid_users", "reaction_pages", "run_dir",
                ])

    def test_batch_outputs_preserve_cross_post_users_and_dedupe_same_post_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = []
            post_rows = (
                (
                    ("1", "LIKE"),
                    ("1", "LOVE"),
                    ("2", "LIKE"),
                ),
                (
                    ("2", "LIKE"),
                    ("3", "LIKE"),
                ),
            )
            for index, rows in enumerate(post_rows, start=1):
                run_dir = root / f"post-{index:03d}"
                analysis = run_dir / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["user_id", "user_name", "avatar_url", "source_post_id", "reaction_id"])
                    writer.writeheader()
                    for user_id, reaction_id in rows:
                        writer.writerow({"user_id": user_id, "user_name": f"User {user_id}", "avatar_url": "", "source_post_id": f"post-{index}", "reaction_id": reaction_id})
                jobs.append({
                    "job_id": f"fb_{index}",
                    "status": "completed",
                    "run_dir": str(run_dir),
                    "request": {"post_url": f"https://www.facebook.com/example/posts/{index}"},
                    "error": None,
                })
            summary = api.write_batch_outputs(root / "batch", "batch_test", jobs)
            self.assertEqual(summary["per_post_valid_sum"], 4)
            self.assertEqual(summary["post_user_records"], 4)
            self.assertEqual(summary["within_post_duplicate_occurrences"], 1)
            self.assertEqual(summary["post_user_record_count"], 4)
            self.assertEqual(summary["same_post_duplicate_occurrences"], 1)
            self.assertEqual(summary["cross_post_unique_users"], 3)
            self.assertEqual(summary["cross_post_duplicate_occurrences"], 1)
            with (root / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            with (root / "batch" / "batch-user-post-relations.csv").open(encoding="utf-8-sig") as handle:
                relations = list(csv.DictReader(handle))
            self.assertEqual(len(users), 4)
            self.assertEqual(len(relations), 4)
            self.assertEqual(
                {(row["post_index"], row["user_id"]) for row in users},
                {("1", "1"), ("1", "2"), ("2", "2"), ("2", "3")},
            )
            self.assertEqual(
                {(row["post_index"], row["user_id"]) for row in relations},
                {("1", "1"), ("1", "2"), ("2", "2"), ("2", "3")},
            )
            summary_again = api.write_batch_outputs(root / "batch", "batch_test", jobs)
            self.assertEqual(summary_again["per_post_valid_sum"], summary["per_post_valid_sum"])
            self.assertEqual(summary_again["post_user_records"], summary["post_user_records"])
            self.assertEqual(summary_again["post_user_record_count"], summary["post_user_record_count"])
            self.assertEqual(summary_again["cross_post_unique_users"], summary["cross_post_unique_users"])
            with (root / "batch" / "batch-user-post-relations.csv").open(encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 4)

    def test_batch_outputs_keep_cross_post_rows_for_one_global_user(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = []
            for index in (1, 2):
                run_dir = root / f"post-{index:03d}"
                analysis = run_dir / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "user_id": "same-user",
                            "user_name": "Same User",
                            "source_post_id": f"post-{index}",
                            "reaction_id": "LIKE",
                        }
                    )
                jobs.append(
                    {
                        "job_id": f"fb_{index}",
                        "status": "completed",
                        "run_dir": str(run_dir),
                        "request": {"post_url": f"https://www.facebook.com/example/posts/{index}"},
                    }
                )

            summary = api.write_batch_outputs(root / "batch", "one_user_batch", jobs)
            with (root / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            self.assertEqual(summary["post_user_records"], 2)
            self.assertEqual(summary["cross_post_unique_users"], 1)
            self.assertEqual(len(users), 2)
            self.assertEqual({row["post_index"] for row in users}, {"1", "2"})

    def test_batch_scope_does_not_merge_jobs_with_same_source_post_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = []
            for index in (1, 2):
                run_dir = root / f"post-{index:03d}"
                analysis = run_dir / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "user_id": "same-user",
                            "user_name": "Same User",
                            "source_post_id": "same-source-id",
                            "reaction_id": "LIKE",
                        }
                    )
                jobs.append(
                    {
                        "job_id": f"fb_{index}",
                        "status": "completed",
                        "run_dir": str(run_dir),
                        "request": {"post_url": f"https://www.facebook.com/example/posts/{index}"},
                    }
                )

            summary = api.write_batch_outputs(root / "batch", "same_source_batch", jobs)
            with (root / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            self.assertEqual(summary["post_user_records"], 2)
            self.assertEqual(summary["cross_post_unique_users"], 1)
            self.assertEqual(len(users), 2)

    def test_batch_scope_merges_missing_source_id_across_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            attempt_dirs = [root / "attempt-001", root / "attempt-002"]
            for attempt_dir, source_post_id, reaction_id in (
                (attempt_dirs[0], "", "LIKE"),
                (attempt_dirs[1], "source-1", "LOVE"),
            ):
                analysis = attempt_dir / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "user_id": "same-user",
                            "user_name": "Same User",
                            "source_post_id": source_post_id,
                            "reaction_id": reaction_id,
                        }
                    )
            job = {
                "job_id": "fb_attempts",
                "status": "completed",
                "run_dir": str(root / "post-001"),
                "attempt_dirs": [str(path) for path in attempt_dirs],
                "request": {"post_url": "https://www.facebook.com/example/posts/1"},
            }

            summary = api.write_batch_outputs(root / "batch", "attempt_batch", [job])
            with (root / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            self.assertEqual(summary["post_user_records"], 1)
            self.assertEqual(summary["within_post_duplicate_occurrences"], 1)
            self.assertEqual(len(users), 1)

    def test_batch_outputs_read_root_checkpoint_rows_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "post-001"
            checkpoint = root / "checkpoint"
            checkpoint.mkdir(parents=True)
            with (checkpoint / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "user_id": "checkpoint-user",
                        "user_name": "Checkpoint User",
                        "source_post_id": "checkpoint-post",
                        "reaction_id": "LIKE",
                    }
                )
            job = {
                "job_id": "fb_checkpoint",
                "status": "completed",
                "run_dir": str(root),
                "request": {"post_url": "https://www.facebook.com/example/posts/checkpoint"},
            }

            summary = api.write_batch_outputs(root.parent / "batch", "checkpoint_batch", [job])
            with (root.parent / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            with (root.parent / "batch" / "batch-posts.csv").open(encoding="utf-8-sig") as handle:
                posts = list(csv.DictReader(handle))
            self.assertEqual(summary["post_user_records"], 1)
            self.assertEqual(summary["per_post_valid_sum"], 1)
            self.assertEqual(summary["within_post_duplicate_occurrences"], 0)
            self.assertEqual(len(users), 1)
            self.assertEqual(posts[0]["valid_users"], "1")


class CaptureJobBehaviorTests(CaptureJobTests):
    def test_capture_job_has_no_runtime_hard_timeout_by_default(self):
        process = self._run_with_fake_process()
        process.wait.assert_called_once_with()

    def test_capture_job_still_supports_explicit_positive_timeout(self):
        process = self._run_with_fake_process(37)
        process.wait.assert_called_once_with(timeout=37)

    def test_capture_job_registers_and_clears_process_callback(self):
        callbacks = []
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            process = mock.Mock()
            process.returncode = 0
            with mock.patch.object(api, "BASE_DIR", base), \
                 mock.patch("facebook_capture_api.ensure_emulator"), \
                 mock.patch("facebook_capture_api.build_capture_command", return_value=["powershell"]), \
                 mock.patch("facebook_capture_api.subprocess.Popen", return_value=process), \
                 mock.patch("facebook_capture_api.build_api_result", return_value={"ok": True}):
                api.run_capture_job(
                    {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                    "job-callback",
                    process_callback=callbacks.append,
                )
        self.assertIs(callbacks[0], process)
        self.assertIsNone(callbacks[-1])

    def test_capture_job_log_is_scoped_to_attempt_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            attempt = base / "runs" / "api-fb_log" / "attempt-002"
            process = mock.Mock()
            process.returncode = 0
            with mock.patch.object(api, "BASE_DIR", base), \
                 mock.patch("facebook_capture_api.ensure_emulator"), \
                 mock.patch("facebook_capture_api.build_capture_command", return_value=["powershell"]), \
                 mock.patch("facebook_capture_api.subprocess.Popen", return_value=process), \
                 mock.patch("facebook_capture_api.build_api_result", return_value={"ok": True}):
                result = api.run_capture_job(
                    {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                    "job-log",
                    run_dir=attempt,
                )

            self.assertEqual(result["job_log"], str(attempt / "capture.log"))
            self.assertTrue((attempt / "capture.log").is_file())


class JobPersistenceTests(unittest.TestCase):
    def test_existing_completed_private_list_is_labelled_empty_in_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run = base / "batch/post-001"
            ResultTests._write_empty_reaction_visibility_fixture(
                run, status="query-triggered-friends-only-visibility", query_records=2,
                source_post_ids=["p1"], reported_total_count=946,
                visibility_hint="friends-only-names", terminal_page_seen=True)
            # No stored outcome: an existing manifest must receive the new label.
            job = {"job_id": "private", "status": "completed", "run_dir": str(run),
                   "batch_index": 1, "completion_scope": "terminal",
                   "request": {"post_url": "https://www.facebook.com/share/p/private/"}}
            manager = self._manager(base)
            manager._jobs["private"] = job
            manager._batches["batch"] = {"batch_id": "batch", "run_dir": str(base / "batch"),
                                         "job_ids": ["private"]}
            progress = manager.batch_progress("batch")
            post = progress["posts"][0]
            self.assertEqual(post["result_outcome"]["reason"], "friends-only-names")
            self.assertIn("不代表帖子没有互动", post["current_stage"])
            self.assertEqual(post["valid_users"], 0)
            summary = api.write_batch_outputs(base / "batch", "batch", [job])
            rows = api._read_csv(base / "batch/batch-posts.csv")
            self.assertEqual(rows[0]["result_reason"], post["result_outcome"]["reason"])

    def _manager(self, base: Path):
        patcher = mock.patch.object(api, "BASE_DIR", base)
        patcher.start()
        self.addCleanup(patcher.stop)
        thread_patcher = mock.patch("facebook_capture_api.threading.Thread.start")
        thread_patcher.start()
        self.addCleanup(thread_patcher.stop)
        return api.JobManager()

    def test_submit_writes_unlimited_default_without_result_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            path = base / "api-jobs" / f"{job['job_id']}.job.json"
            self.assertTrue(path.exists())
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "queued")
            self.assertIsNone(manifest["run_dir"])
            self.assertEqual(
                manifest["request"]["reaction_scroll_limit"],
                api.REACTION_SCROLL_LIMIT_DEFAULT,
            )
            self.assertFalse(manifest["request"]["reaction_scroll_limit_explicit"])
            self.assertIsNone(manifest["completion_scope"])
            self.assertNotIn("result", manifest)

    def test_bounded_completion_scope_is_persisted_and_labeled_in_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch_id = "batch_bounded"
            job_id = "fb_bounded"
            batch_dir = base / "runs" / batch_id
            job = {
                "job_id": job_id,
                "batch_index": 1,
                "status": "completed",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": api.utc_now(),
                "request": {
                    "post_url": "https://www.facebook.com/example/posts/1",
                    "post_id": "",
                    "collect": ["reactions"],
                    "save_artifacts": [],
                    "reaction_scroll_limit": 3,
                    "reaction_scroll_limit_explicit": True,
                },
                "result": None,
                "error": None,
                "run_dir": str(batch_dir / "post-001"),
                "completion_scope": "bounded-sample",
            }
            batch = {
                "batch_id": batch_id,
                "created_at": api.utc_now(),
                "job_ids": [job_id],
                "run_dir": str(batch_dir),
                "completion_scope": "bounded-sample",
            }
            manager._jobs[job_id] = job
            manager._batches[batch_id] = batch
            manager._persist_job(job)
            manager._persist_batch(batch)
            refreshed = manager._refresh_batch_outputs(batch_id)
            progress = manager.batch_progress(batch_id)

            job_manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
            batch_manifest = json.loads((base / "api-batches" / f"{batch_id}.batch.json").read_text(encoding="utf-8"))
            self.assertEqual(job_manifest["completion_scope"], "bounded-sample")
            self.assertEqual(batch_manifest["completion_scope"], "bounded-sample")
            self.assertEqual(manager.public_job(job_id)["completion_scope"], "bounded-sample")
            self.assertEqual(refreshed["completion_scope"], "bounded-sample")
            self.assertEqual(progress["completion_scope"], "bounded-sample")
            self.assertEqual(progress["posts"][0]["completion_scope"], "bounded-sample")
            self.assertEqual(progress["posts"][0]["current_stage"], "样本完成（非全量）")

    def test_batch_progress_recomputes_post_user_counts_from_live_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch_id = "batch_live_counts"
            job_id = "fb_live_counts"
            batch_dir = base / "runs" / batch_id
            run_dir = batch_dir / "post-001"
            job = {
                "job_id": job_id,
                "batch_index": 1,
                "status": "completed",
                "created_at": api.utc_now(),
                "request": {"post_url": "https://www.facebook.com/example/posts/1"},
                "run_dir": str(run_dir),
            }
            batch = {
                "batch_id": batch_id,
                "created_at": api.utc_now(),
                "job_ids": [job_id],
                "run_dir": str(batch_dir),
            }
            manager._jobs[job_id] = job
            manager._batches[batch_id] = batch

            api.write_batch_outputs(batch_dir, batch_id, [job])
            analysis = run_dir / "analysis"
            analysis.mkdir(parents=True)
            with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"user_id": "1", "user_name": "User 1", "source_post_id": "post-1", "reaction_id": "LIKE"},
                        {"user_id": "1", "user_name": "User 1", "source_post_id": "post-1", "reaction_id": "LOVE"},
                    ]
                )

            progress = manager.batch_progress(batch_id)

            self.assertEqual(progress["post_user_record_count"], 1)
            self.assertEqual(progress["same_post_duplicate_occurrences"], 1)
            persisted = json.loads((batch_dir / "batch-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["post_user_record_count"], 0)

    def test_submit_batch_persists_ordered_children_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch = manager.submit_batch({
                "post_urls": [
                    "https://www.facebook.com/example/posts/1",
                    "https://www.facebook.com/example/posts/2",
                ],
                "save_artifacts": [],
                "reaction_scroll_limit": 17,
            })
            self.assertEqual(batch["total_posts"], 2)
            self.assertEqual([post["index"] for post in batch["posts"]], [1, 2])
            manifest_path = base / "api-batches" / f"{batch['batch_id']}.batch.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["job_ids"]), 2)
            for index, job_id in enumerate(manifest["job_ids"], start=1):
                job_manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
                self.assertEqual(job_manifest["batch_id"], batch["batch_id"])
                self.assertEqual(job_manifest["batch_index"], index)
                self.assertEqual(job_manifest["request"]["reaction_scroll_limit"], 17)
                self.assertTrue(job_manifest["request"]["reaction_scroll_limit_explicit"])
                self.assertIn(f"post-{index:03d}", job_manifest["run_dir"])

    def test_submit_batch_preserves_unlimited_default_as_not_explicit_on_children(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch = manager.submit_batch({
                "post_urls": ["https://www.facebook.com/example/posts/1"],
                "save_artifacts": [],
            })
            job_id = batch["posts"][0]["job_id"]
            job_manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
            self.assertEqual(
                job_manifest["request"]["reaction_scroll_limit"],
                api.REACTION_SCROLL_LIMIT_DEFAULT,
            )
            self.assertFalse(job_manifest["request"]["reaction_scroll_limit_explicit"])

    def test_submit_batch_explicit_child_request_accepts_bounded_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch = manager.submit_batch({
                "post_urls": ["https://www.facebook.com/example/posts/1"],
                "save_artifacts": [],
                "reaction_scroll_limit": 3,
            })
            job_id = batch["posts"][0]["job_id"]
            job = manager._jobs[job_id]
            job_manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
            self.assertTrue(job_manifest["request"]["reaction_scroll_limit_explicit"])

            run_dir = Path(job["run_dir"])
            ResultTests._write_reaction_fixture(run_dir)
            result = api.build_api_result(run_dir, job["request"])
            self.assertEqual(result["completion_scope"], api.BOUNDED_SAMPLE_COMPLETION_SCOPE)

    def test_failed_rate_limited_batch_post_is_labeled_as_partial_result(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            run_dir = base / "runs" / "batch_rate_limited" / "post-001"
            diagnostics = run_dir / "readonly-ui" / "exploration-diagnostics.json"
            diagnostics.parent.mkdir(parents=True)
            diagnostics.write_text(
                json.dumps({"pagination_runs": [{"stop_reason": "rate-limited", "attempts": 12}]}),
                encoding="utf-8",
            )
            (run_dir / "facebook-live-progress.json").write_text(
                json.dumps({
                    "families": {"reaction": {"sources": {"post-1": {"user_count": 25, "page_count": 5}}}}
                }),
                encoding="utf-8",
            )
            job_id = "fb_rate_limited"
            batch_id = "batch_rate_limited"
            manager._jobs[job_id] = {
                "job_id": job_id,
                "batch_index": 1,
                "status": "failed",
                "request": {"post_url": "https://www.facebook.com/share/r/generic/"},
                "run_dir": str(run_dir),
                "error": "reaction capture is incomplete: rate-limited",
            }
            manager._batches[batch_id] = {
                "batch_id": batch_id,
                "created_at": api.utc_now(),
                "job_ids": [job_id],
                "run_dir": str(run_dir.parent),
            }
            progress = manager.batch_progress(batch_id)
            self.assertEqual(progress["posts"][0]["current_stage"], "限流，已保留部分结果")

    def test_queued_job_can_be_cancelled_and_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            response = manager.cancel(job["job_id"])
            self.assertTrue(response["cancelled"])
            self.assertEqual(response["job"]["status"], "cancelled")
            path = base / "api-jobs" / f"{job['job_id']}.job.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "cancelled")

    def test_running_job_cancel_terminates_registered_process(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            job_id = "fb_runningcancel"
            manager._jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "result": None,
                "error": None,
                "run_dir": None,
            }
            process = mock.Mock()
            manager._running_processes[job_id] = process
            with mock.patch("facebook_capture_api._terminate_tree") as terminate:
                response = manager.cancel(job_id)
            terminate.assert_called_once_with(process)
            self.assertTrue(response["cancelled"])
            self.assertEqual(response["job"]["status"], "cancelling")

    def test_running_cancel_stays_cancelling_until_stop_is_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            job_id = "fb_pendingcancel"
            manager._jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "result": None,
                "error": None,
                "run_dir": None,
                "process_state": "running",
            }
            process = mock.Mock()
            process.poll.return_value = None
            manager._running_processes[job_id] = process
            with mock.patch("facebook_capture_api._terminate_tree", return_value=False) as terminate:
                response = manager.cancel(job_id)
                repeated = manager.cancel(job_id)

            self.assertEqual(response["job"]["status"], "cancelling")
            self.assertEqual(repeated["job"]["status"], "cancelling")
            self.assertIsNone(manager._jobs[job_id]["finished_at"])
            self.assertEqual(manager._jobs[job_id]["process_state"], "stop-unverified")
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertEqual(terminate.call_count, 2)

            process.poll.return_value = 0
            manager._set_running_process(job_id, None, final=True)

            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")
            self.assertEqual(manager._jobs[job_id]["process_state"], "stopped")
            self.assertFalse(manager._capture_lease_uncertain)
            self.assertIsNone(manager._capture_lease_job_id)

    def test_cancel_before_process_registration_stays_pending_until_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            job_id = "fb_startupcancel"
            manager._jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "result": None,
                "error": None,
                "run_dir": None,
                "process_state": "not-started",
            }
            response = manager.cancel(job_id)
            self.assertTrue(response["cancelled"])
            self.assertEqual(response["job"]["status"], "cancelling")
            self.assertIsNone(response["job"]["finished_at"])

            process = mock.Mock()
            process.pid = 4242
            process.poll.return_value = None
            with mock.patch("facebook_capture_api._process_creation_time", return_value=123.0), \
                 mock.patch("facebook_capture_api._terminate_tree", return_value=True) as terminate:
                manager._set_running_process(job_id, process)

            terminate.assert_called_once_with(process)
            self.assertEqual(manager.public_job(job_id)["status"], "cancelling")
            self.assertEqual(manager.public_job(job_id)["process_state"], "stopped")
            manager._set_running_process(job_id, None, final=True)
            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")

    def test_late_process_registration_reopens_cancelled_state_when_stop_is_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            job_id = "fb_lateprocesscancel"
            manager._jobs[job_id] = {
                "job_id": job_id,
                "status": "cancelled",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": api.utc_now(),
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "result": None,
                "error": None,
                "run_dir": None,
                "process_state": "stopped",
            }
            process = mock.Mock()
            process.pid = 4243
            process.poll.return_value = None
            with mock.patch("facebook_capture_api._process_creation_time", return_value=124.0), \
                 mock.patch("facebook_capture_api._terminate_tree", return_value=False):
                manager._set_running_process(job_id, process)

            pending = manager.public_job(job_id)
            self.assertEqual(pending["status"], "cancelling")
            self.assertIsNone(pending["finished_at"])
            self.assertEqual(pending["process_state"], "stop-unverified")
            self.assertTrue(manager._capture_lease_uncertain)

    def test_worker_completion_cannot_overwrite_pending_cancellation(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            manager._queue.get = mock.Mock(side_effect=[job_id, KeyboardInterrupt])
            manager._queue.task_done = mock.Mock()

            def fake_capture(*_args, **_kwargs):
                manager.cancel(job_id)
                return {"ok": True}

            with mock.patch(
                "facebook_capture_api.adb_snapshot",
                return_value={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
            ), mock.patch("facebook_capture_api.run_capture_job", side_effect=fake_capture):
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()

            job = manager.public_job(job_id)
            self.assertEqual(job["status"], "cancelled")
            self.assertIsNone(manager._jobs[job_id].get("result"))

    def test_worker_error_cannot_overwrite_pending_cancellation(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            manager._queue.get = mock.Mock(side_effect=[job_id, KeyboardInterrupt])
            manager._queue.task_done = mock.Mock()

            def fake_capture(*_args, **_kwargs):
                manager.cancel(job_id)
                raise api.ApiError("capture failed after cancellation")

            with mock.patch(
                "facebook_capture_api.adb_snapshot",
                return_value={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
            ), mock.patch("facebook_capture_api.run_capture_job", side_effect=fake_capture):
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()

            job = manager.public_job(job_id)
            self.assertEqual(job["status"], "cancelled")
            self.assertNotIn("capture failed after cancellation", str(job.get("error")))

    def test_cancelling_job_with_unverified_restart_evidence_keeps_lease_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_restartcancelling"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "cancelling",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "worker_id": "worker-01",
                "serial": "emulator-5554",
                "process_state": "not-started",
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            manager = self._manager(base)

            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "cancelling")
            self.assertIsNone(restored["finished_at"])
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertIsNone(manager._active_worker_id)

    def test_cancelling_job_with_stopped_restart_evidence_becomes_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_restartcancelled"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "cancelling",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "process_state": "stopped",
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            manager = self._manager(base)

            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "cancelled")
            self.assertIsNotNone(restored["finished_at"])
            self.assertFalse(manager._capture_lease_uncertain)

    def test_repeat_cancel_does_not_change_completed_job_or_result(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            job_id = "fb_donecancel"
            result = {"ok": True}
            manager._jobs[job_id] = {
                "job_id": job_id,
                "status": "completed",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": api.utc_now(),
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "result": result,
                "error": None,
                "run_dir": None,
            }
            response = manager.cancel(job_id)
            self.assertFalse(response["cancelled"])
            self.assertIn("already completed", response["reason"])
            self.assertEqual(manager._jobs[job_id]["status"], "completed")
            self.assertIs(manager._jobs[job_id]["result"], result)

    def test_cancelled_job_remains_cancelled_after_api_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_persistedcancelled"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "cancelled",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": None,
                "finished_at": "2026-08-18T00:00:02Z",
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            manager = self._manager(base)
            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")

    def test_legacy_cancelled_job_with_unverified_process_reopens_for_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_legacycancelledunverified"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "cancelled",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": "2026-08-18T00:00:02Z",
                "cancel_requested_at": "2026-08-18T00:00:02Z",
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "worker_id": "worker-01",
                "serial": "emulator-5554",
                "process_pid": 4242,
                "process_creation_time": 123.0,
                "process_state": "stop-unverified",
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with mock.patch.object(api, "_process_identity_state", return_value="match"), \
                 mock.patch.object(api, "_terminate_known_pid", side_effect=[False, True]) as terminate:
                manager = self._manager(base)

                restored = manager.public_job(job_id)
                self.assertEqual(restored["status"], "cancelling")
                self.assertIsNone(restored["finished_at"])
                self.assertEqual(restored["cancel_requested_at"], "2026-08-18T00:00:02Z")
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertEqual(manager._capture_lease_job_id, job_id)

                retried = manager.cancel(job_id)
                self.assertFalse(retried["cancelled"])
                self.assertEqual(retried["job"]["status"], "cancelled")
                self.assertEqual(terminate.call_count, 2)

            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")
            self.assertFalse(manager._capture_lease_uncertain)

    def test_legacy_cancelled_job_with_verified_absent_process_stays_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_legacycancelledabsent"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "cancelled",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": "2026-08-18T00:00:02Z",
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "worker_id": "worker-01",
                "serial": "emulator-5554",
                "process_pid": 4243,
                "process_creation_time": 124.0,
                "process_state": "stop-unverified",
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with mock.patch.object(api, "_process_identity_state", return_value="absent"), \
                 mock.patch.object(api, "_terminate_known_pid") as terminate:
                manager = self._manager(base)

            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "cancelled")
            self.assertEqual(restored["process_state"], "stopped")
            self.assertIsNone(restored["process_pid"])
            self.assertFalse(manager._capture_lease_uncertain)
            terminate.assert_not_called()

    def test_queued_job_is_requeued_after_api_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_persistedqueued"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "queued",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": None,
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "worker_id": None,
                "serial": None,
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            manager = self._manager(base)

            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "queued")
            self.assertEqual(restored["recovery_state"], "queued-after-restart")
            self.assertIsNone(restored["worker_id"])
            self.assertIsNone(restored["serial"])
            self.assertEqual(manager._queue.get_nowait(), job_id)
            self.assertTrue(manager._queue.empty())
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "queued")

    def test_stopped_running_job_is_safely_requeued_after_api_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_restartstopped"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "running",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "worker_id": "worker-01",
                "serial": "emulator-5554",
                "process_pid": None,
                "process_creation_time": None,
                "process_state": "stopped",
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            manager = self._manager(base)

            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "queued")
            self.assertEqual(restored["recovery_state"], "requeued-after-restart")
            self.assertIn("safely requeued", restored["error"])
            self.assertIsNone(restored["started_at"])
            self.assertIsNone(restored["worker_id"])
            self.assertIsNone(restored["serial"])
            self.assertEqual(restored["last_worker_id"], "worker-01")
            self.assertEqual(restored["last_serial"], "emulator-5554")
            self.assertEqual(restored["process_state"], "stopped")
            self.assertEqual(manager._queue.get_nowait(), job_id)
            self.assertTrue(manager._queue.empty())
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "queued")
            self.assertEqual(saved["recovery_state"], "requeued-after-restart")

            assigned = manager._assign_job_to_worker(job_id, manager.workers["worker-01"])
            self.assertTrue(assigned)
            running = manager.public_job(job_id)
            self.assertEqual(running["status"], "running")
            self.assertIsNone(running["error"])
            self.assertEqual(running["recovery_state"], "running")

    def test_legacy_job_limit_materialization_restores_as_unlimited(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_legacyboundeddefault"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "completed",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": api.utc_now(),
                "request": {
                    "post_url": "https://www.facebook.com/example/posts/1",
                    "post_id": "",
                    "collect": ["reactions"],
                    # This was a materialized default before explicitness was persisted.
                    "reaction_scroll_limit": 3,
                },
                "error": None,
                "run_dir": None,
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            manager = self._manager(base)

            restored_request = manager._jobs[job_id]["request"]
            self.assertIsNone(restored_request["reaction_scroll_limit"])
            self.assertFalse(restored_request["reaction_scroll_limit_explicit"])

    def test_completed_job_is_restored_and_result_is_built_lazily(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run_dir = base / "runs" / "done"
            run_dir.mkdir(parents=True)
            job_id = "fb_persistedcompleted"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "completed",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": "2026-08-18T00:01:00Z",
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": str(run_dir),
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            manager = self._manager(base)
            with mock.patch("facebook_capture_api.build_api_result", return_value={"ok": True}) as build:
                self.assertEqual(manager.public_job(job_id)["status"], "completed")
                self.assertEqual(manager.result(job_id), {"ok": True})
                self.assertEqual(manager.result(job_id), {"ok": True})
                build.assert_called_once()

    def test_running_job_becomes_failed_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_interruptedjob"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "running",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            manager = self._manager(base)
            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "failed")
            self.assertIn("interrupted by API service restart", restored["error"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "failed")

    def test_failed_restarted_job_keeps_partial_evidence_location(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run_dir = base / "runs" / "api-fb_interruptedpartial"
            run_dir.mkdir(parents=True)
            (run_dir / "safe-facebook-http-capture.jsonl").write_text('{"safe":true}\n', encoding="utf-8")
            job_id = "fb_interruptedpartial"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "running",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": str(run_dir),
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            manager = self._manager(base)
            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "failed")
            self.assertEqual(restored["run_dir"], str(run_dir))
            self.assertTrue(restored["partial_evidence_available"])

    def test_malformed_manifest_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            path = base / "api-jobs" / "fb_bad.job.json"
            path.parent.mkdir(parents=True)
            path.write_text("{not-json", encoding="utf-8")
            manager = self._manager(base)
            with self.assertRaises(KeyError):
                manager.public_job("fb_bad")

    def test_latest_job_returns_most_recent_created_job(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            manager._jobs = {
                "fb_old": {"job_id": "fb_old", "status": "completed", "created_at": "2026-01-01T00:00:00Z", "run_dir": None},
                "fb_new": {"job_id": "fb_new", "status": "completed", "created_at": "2026-02-01T00:00:00Z", "run_dir": None},
            }
            self.assertEqual(manager.latest_job()["job_id"], "fb_new")


class HttpCancelTests(unittest.TestCase):
    def test_cancel_endpoint_routes_without_request_body(self):
        class FakeManager:
            def __init__(self):
                self.job_id = None

            def cancel(self, job_id):
                self.job_id = job_id
                return {"cancelled": True, "reason": "queued job cancelled", "job": {"job_id": job_id, "status": "cancelled"}}

        manager = FakeManager()
        server = api.CaptureServer(("127.0.0.1", 0), manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/jobs/fb_httpcancel/cancel")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(payload["cancelled"])
            self.assertEqual(manager.job_id, "fb_httpcancel")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class HttpWorkerReloadTests(unittest.TestCase):
    def test_reload_workers_endpoint_routes_without_request_body(self):
        class FakeManager:
            def __init__(self):
                self.called = False

            def reload_workers(self):
                self.called = True
                return {"added": ["worker-03"], "updated": [], "worker_ids": ["worker-01", "worker-03"]}

        manager = FakeManager()
        server = api.CaptureServer(("127.0.0.1", 0), manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/workers/reload")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(manager.called)
            self.assertEqual(payload["added"], ["worker-03"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class HttpUiTests(unittest.TestCase):
    def test_ui_assets_and_progress_endpoint_are_served(self):
        class FakeManager:
            def health(self):
                return {"service": "ready", "worker": "idle", "android": {"state": "device"}, "serial": "emulator-5554"}

            def progress(self, job_id):
                return {"job_status": "running", "storage_root": "C:/runs/test", "metrics": {}, "stages": [], "artifacts": []}

        server = api.CaptureServer(("127.0.0.1", 0), FakeManager())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            for path, expected_type in (("/", "text/html"), ("/styles.css", "text/css"), ("/app.js", "javascript")):
                connection = http.client.HTTPConnection(host, port, timeout=3)
                connection.request("GET", path)
                response = connection.getresponse()
                body = response.read()
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertIn(expected_type, response.getheader("Content-Type"))
                self.assertGreater(len(body), 20)

            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("GET", "/v1/jobs/fb_ui/progress")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["job_status"], "running")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class HttpBatchTests(unittest.TestCase):
    def test_batch_create_latest_progress_and_cancel_routes(self):
        class FakeManager:
            def submit_batch(self, payload):
                return {"batch_id": "batch_http", "status": "queued", "total_posts": len(payload["post_urls"]), "posts": []}

            def latest_batch(self):
                return {"batch_id": "batch_http", "status": "running", "total_posts": 2, "posts": []}

            def batch_progress(self, batch_id):
                return {"batch_id": batch_id, "status": "running", "total_posts": 2, "posts": []}

            def cancel_batch(self, batch_id):
                return {"cancelled_jobs": 2, "batch": {"batch_id": batch_id}}

        server = api.CaptureServer(("127.0.0.1", 0), FakeManager())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            body = json.dumps({"post_urls": ["https://www.facebook.com/example/posts/1"]})
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/batches", body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            created = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 202)
            self.assertEqual(created["batch_id"], "batch_http")

            for path in ("/v1/batches/latest", "/v1/batches/batch_http/progress"):
                connection = http.client.HTTPConnection(host, port, timeout=3)
                connection.request("GET", path)
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["batch_id"], "batch_http")

            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/batches/batch_http/cancel")
            response = connection.getresponse()
            cancelled = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(cancelled["cancelled_jobs"], 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_artifact_can_be_viewed_downloaded_and_unknown_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            result_file = run_dir / "analysis" / "facebook-reactor-users.csv"
            result_file.parent.mkdir()
            result_file.write_text("user_id,user_name\n1,Alice\n", encoding="utf-8")

            class FakeManager:
                def artifact(self, job_id, artifact_name):
                    if artifact_name != "facebook-reactor-users.csv":
                        raise api.ApiError("unknown artifact")
                    return result_file

            server = api.CaptureServer(("127.0.0.1", 0), FakeManager())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                for suffix, disposition in (("", "inline"), ("?download=1", "attachment")):
                    connection = http.client.HTTPConnection(host, port, timeout=3)
                    connection.request("GET", f"/v1/jobs/fb_ui/artifacts/facebook-reactor-users.csv{suffix}")
                    response = connection.getresponse()
                    body = response.read().decode("utf-8")
                    connection.close()
                    self.assertEqual(response.status, 200)
                    self.assertIn("text/csv", response.getheader("Content-Type"))
                    self.assertTrue(response.getheader("Content-Disposition").startswith(disposition))
                    self.assertIn("Alice", body)

                connection = http.client.HTTPConnection(host, port, timeout=3)
                connection.request("GET", "/v1/jobs/fb_ui/artifacts/not-allowed.txt")
                response = connection.getresponse()
                response.read()
                connection.close()
                self.assertEqual(response.status, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class ResultTests(unittest.TestCase):
    @staticmethod
    def _write_reaction_fixture(
        run_dir: Path,
        *,
        users: int = 2,
        stop_reason: str = "absolute-cap",
        target_status: str = "matched",
        source_post_ids: list[str] | None = None,
        rate_limited_records: int = 0,
        temporary_feature_block_records: int = 0,
        line_parse_errors: int = 0,
        row_source_post_id: str = "p1",
    ) -> None:
        analysis = run_dir / "analysis"
        diagnostics = run_dir / "readonly-ui"
        analysis.mkdir(parents=True)
        diagnostics.mkdir(parents=True)
        source_post_ids = source_post_ids if source_post_ids is not None else ["p1"]
        report = {
            "line_parse_errors": line_parse_errors,
            "target_verification": {"status": target_status, "matched_post_id": "p1"},
            "source_post_ids_by_family": {"reactions": source_post_ids, "shares": []},
            "statuses": {"200": 8},
            "reaction_integrity": {
                "integrity_status": "partial-open-tail",
                "query_records": 1,
                "successful_page_count": 1,
                "raw_reactor_rows": users,
                "unique_reactor_identities": users,
                "rate_limited_records": rate_limited_records,
                "temporary_feature_block_records": temporary_feature_block_records,
                "terminal_page_seen": False,
                "open_tail_without_terminal": True,
                "reported_total_count": users + 10,
                "completion_claim_allowed": False,
            },
            "enumeration_status": {
                "reactions": {
                    "query_records": 1,
                    "reported_total_count": users + 10,
                    "enumerated_identity_count": users,
                    "pagination": {
                        "groups": {"__ALL__": {"open_page_seen": True, "terminal_page_seen": False}}
                    },
                }
            },
        }
        (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
        with (analysis / "facebook-reactor-users.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(api.REACTOR_USER_FIELDS))
            writer.writeheader()
            for index in range(users):
                writer.writerow(
                    {
                        "user_id": f"u{index + 1}",
                        "user_name": f"User {index + 1}",
                        "avatar_url": "",
                        "source_post_id": row_source_post_id,
                        "reaction_id": "LIKE",
                    }
                )
        (run_dir / "readonly-ui" / "exploration-diagnostics.json").write_text(
            json.dumps(
                {
                    "pagination_runs": [
                        {
                            "reaction_key": "__ALL__",
                            "stop_reason": stop_reason,
                            "attempts": 3,
                            "absolute_cap": 3,
                            "final_budget": 3,
                            "terminal_seen": False,
                            "open_seen": True,
                            "latest_graphql_error_code": None,
                        }
                    ],
                    "reaction_screening": {
                        "latest_graphql_error_code": None,
                        "session_restricted": False,
                    },
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_group_url_token_fixture(
        run_dir: Path,
        *,
        token: str = "2158908574674305",
        source_post_id: str | None = None,
        users: int = 2,
    ) -> None:
        source_post_id = source_post_id or token
        ResultTests._write_reaction_fixture(
            run_dir,
            users=users,
            source_post_ids=[source_post_id],
            row_source_post_id=source_post_id,
        )
        report_path = run_dir / "analysis" / "facebook-sample-patterns.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["target_verification"] = {
            "status": "matched",
            "matched_by": "graphql-response-url-token",
            "expected_url": (
                "https://www.facebook.com/groups/1020765891821918/posts/"
                f"{token}/"
            ),
            "expected_url_tokens": [token],
            "matched_token": token,
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")

    @staticmethod
    def _write_empty_reaction_visibility_fixture(
        run_dir: Path,
        *,
        status: str,
        query_records: int,
        source_post_ids: list[str],
        reported_total_count: int,
        visibility_hint: str | None = None,
        terminal_page_seen: bool = False,
        diagnostics: dict | None = None,
    ) -> None:
        analysis = run_dir / "analysis"
        analysis.mkdir(parents=True)
        readonly_ui = run_dir / "readonly-ui"
        readonly_ui.mkdir(parents=True)
        report = {
            "line_parse_errors": 0,
            "target_verification": {"status": "matched", "matched_post_id": "p1"},
            "source_post_ids_by_family": {"reactions": source_post_ids, "shares": []},
            "statuses": {"200": 4},
            "reaction_integrity": {
                "integrity_status": status,
                "query_records": query_records,
                "successful_page_count": query_records,
                "raw_reactor_rows": 0,
                "unique_reactor_identities": 0,
                "rate_limited_records": 0,
                "temporary_feature_block_records": 0,
                "terminal_page_seen": terminal_page_seen,
                "open_tail_without_terminal": False,
                "reported_total_count": reported_total_count,
                "reported_total_unique_gap": reported_total_count,
                "completion_claim_allowed": terminal_page_seen,
            },
            "enumeration_status": {
                "reactions": {
                    "query_records": query_records,
                    "status": status,
                    "ui_visibility_hint": visibility_hint,
                    "reported_total_count": reported_total_count,
                    "reported_total_source": "target-post-feedback",
                    "enumerated_identity_count": 0,
                    "identity_count_gap": reported_total_count,
                    "terminal_page_seen": terminal_page_seen,
                    "pagination": {
                        "groups": {
                            "__ALL__": {
                                "open_page_seen": False,
                                "terminal_page_seen": terminal_page_seen,
                            }
                        },
                        "all_observed_groups_terminal": terminal_page_seen,
                    },
                }
            },
        }
        (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
        (analysis / "facebook-reactor-users.csv").write_text(
            "user_id,user_name,source_post_id\n",
            encoding="utf-8",
        )
        default_diagnostics = {
            "pagination_runs": (
                []
                if query_records == 0
                else [{
                    "reaction_key": "__ALL__",
                    "terminal_seen": terminal_page_seen,
                    "parse_errors": 0,
                    "latest_rate_limited": False,
                    "session_restricted": False,
                    "latest_graphql_error_code": None,
                    "latest_api_error_code": None,
                }]
            ),
            "reaction_screening": (
                None
                if query_records == 0
                else {
                    "parse_errors": 0,
                    "latest_rate_limited": False,
                    "session_restricted": False,
                    "latest_graphql_error_code": None,
                    "latest_api_error_code": None,
                }
            ),
        }
        (readonly_ui / "exploration-diagnostics.json").write_text(
            json.dumps(default_diagnostics if diagnostics is None else diagnostics),
            encoding="utf-8",
        )

    def test_build_result_accepts_url_token_group_target_from_attempt_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "job-root"
            attempt = root / "attempt-001"
            self._write_group_url_token_fixture(attempt, users=2)
            request = api.normalize_request({
                "post_url": "https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/",
                "reaction_scroll_limit": 3,
            })

            result = api.build_api_result(root, request, evidence_dirs=[attempt])

            self.assertEqual(result["completion_scope"], "bounded-sample")
            self.assertEqual(
                {row["source_post_id"] for row in result["reaction_users"]},
                {"2158908574674305"},
            )

    def test_build_result_rejects_url_token_source_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "job-root"
            attempt = root / "attempt-001"
            self._write_group_url_token_fixture(
                attempt,
                source_post_id="different-source",
            )
            request = api.normalize_request({
                "post_url": "https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/",
                "reaction_scroll_limit": 3,
            })

            with self.assertRaises(api.ApiError):
                api.build_api_result(root, request, evidence_dirs=[attempt])

    def test_build_result_rejects_required_artifacts_split_across_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "job-root"
            first = root / "attempt-001"
            second = root / "attempt-002"
            self._write_group_url_token_fixture(first)
            self._write_group_url_token_fixture(second)
            (first / "analysis" / "facebook-reactor-users.csv").unlink()
            (second / "analysis" / "facebook-sample-patterns.json").unlink()
            request = api.normalize_request({
                "post_url": "https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/",
                "reaction_scroll_limit": 3,
            })

            with self.assertRaises(api.ApiError):
                api.build_api_result(root, request, evidence_dirs=[first, second])

    def test_build_result_accepts_explicit_bounded_sample_without_claiming_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_reaction_fixture(run_dir)
            request = api.normalize_request({
                "post_url": "https://facebook.com/x",
                "reaction_scroll_limit": 3,
            })
            result = api.build_api_result(run_dir, request)

            self.assertEqual(result["completion_scope"], "bounded-sample")
            completeness = result["completeness"]["reactions"]
            self.assertFalse(completeness["completion_claim_allowed"])
            self.assertFalse(completeness["terminal_page_seen"])
            self.assertEqual(completeness["integrity_status"], "partial-open-tail")

    def test_build_result_accepts_bounded_sample_with_unrelated_background_http_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_reaction_fixture(run_dir)
            report_path = run_dir / "analysis" / "facebook-sample-patterns.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["statuses"] = {"200": 139, "204": 2, "206": 2, "410": 8}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            request = api.normalize_request({
                "post_url": "https://facebook.com/x",
                "reaction_scroll_limit": 3,
            })

            result = api.build_api_result(run_dir, request)

            self.assertEqual(result["completion_scope"], "bounded-sample")
            self.assertEqual(result["transport_observation"]["http_statuses"]["410"], 8)

    def test_build_result_accepts_bounded_sample_from_fallback_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_reaction_fixture(run_dir)
            primary = run_dir / "readonly-ui"
            fallback = run_dir / "readonly-ui-fallback"
            fallback.mkdir()
            (primary / "exploration-diagnostics.json").replace(
                fallback / "exploration-diagnostics.json"
            )
            (primary / "exploration-diagnostics.json").write_text(
                json.dumps({"pagination_runs": []}), encoding="utf-8"
            )
            request = api.normalize_request({
                "post_url": "https://facebook.com/x",
                "reaction_scroll_limit": 3,
            })

            result = api.build_api_result(run_dir, request)

            self.assertEqual(result["completion_scope"], "bounded-sample")
            self.assertEqual(len(result["reaction_users"]), 2)

    def test_build_result_accepts_strict_reel_source_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            source_id = "122122050735339946"
            self._write_reaction_fixture(
                run_dir,
                target_status="not-matched",
                source_post_ids=[source_id],
                row_source_post_id=source_id,
            )
            (run_dir / "target-open").mkdir()
            (run_dir / "target-open" / "target-open-diagnostics.json").write_text(
                json.dumps({
                    "verified": True,
                    "reason": "reel-ui-source-discovery-required",
                    "resolved_url": "https://www.facebook.com/reel/1054985397317048/",
                }),
                encoding="utf-8",
            )
            diagnostics_path = run_dir / "readonly-ui" / "exploration-diagnostics.json"
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            diagnostics.update({
                "source_discovery": {
                    "status": "matched",
                    "source_post_id": source_id,
                    "observed_source_post_ids": [source_id],
                    "query_records": 1,
                },
                "target_guard": {"status": "matched", "expected_post_id": source_id},
            })
            diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")
            request = api.normalize_request({
                "post_url": "https://www.facebook.com/reel/1054985397317048/",
                "reaction_scroll_limit": 3,
            })

            result = api.build_api_result(run_dir, request)

            self.assertEqual(result["target_verification"]["status"], "matched")
            self.assertEqual(result["target_verification"]["matched_by"], "reel-ui-source-discovery")
            self.assertEqual(result["target_verification"]["matched_post_id"], source_id)

    def test_build_result_rejects_reel_source_discovery_guard_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            source_id = "122122050735339946"
            self._write_reaction_fixture(
                run_dir,
                target_status="not-matched",
                source_post_ids=[source_id],
                row_source_post_id=source_id,
            )
            (run_dir / "target-open").mkdir()
            (run_dir / "target-open" / "target-open-diagnostics.json").write_text(
                json.dumps({
                    "verified": True,
                    "reason": "reel-ui-source-discovery-required",
                    "resolved_url": "https://www.facebook.com/reel/1054985397317048/",
                }),
                encoding="utf-8",
            )
            diagnostics_path = run_dir / "readonly-ui" / "exploration-diagnostics.json"
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            diagnostics.update({
                "source_discovery": {
                    "status": "matched",
                    "source_post_id": source_id,
                    "observed_source_post_ids": [source_id],
                    "query_records": 1,
                },
                "target_guard": {"status": "mismatch", "expected_post_id": "other"},
            })
            diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")
            request = api.normalize_request({
                "post_url": "https://www.facebook.com/reel/1054985397317048/",
                "reaction_scroll_limit": 3,
            })

            with self.assertRaisesRegex(api.ApiError, "target verification failed"):
                api.build_api_result(run_dir, request)

    def test_build_result_accepts_terminal_visibility_restricted_reaction_list_as_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-triggered-friends-only-visibility",
                query_records=2,
                source_post_ids=["p1"],
                reported_total_count=4494,
                visibility_hint="friends-only-names",
                terminal_page_seen=True,
            )

            result = api.build_api_result(
                run_dir,
                {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
            )

            self.assertEqual(result["completion_scope"], api.TERMINAL_COMPLETION_SCOPE)
            self.assertEqual(result["reaction_users"], [])
            self.assertEqual(result["visibility"]["reactions"]["status"], "query-triggered-friends-only-visibility")
            self.assertEqual(result["result_outcome"]["code"], "empty-lists")
            self.assertEqual(result["result_outcome"]["reason"], "friends-only-names")
            progress = api.build_progress_snapshot(run_dir, "completed")
            self.assertEqual(progress["result_outcome"], result["result_outcome"])
            final = next(stage for stage in progress["stages"] if stage["id"] == "complete")
            self.assertEqual(final["state"], "warning")
            self.assertIn("Empty lists", final["label"])

    def test_empty_outcome_requires_completed_verified_terminal_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                root, status="query-triggered-friends-only-visibility", query_records=2,
                source_post_ids=["p1"], reported_total_count=946,
                visibility_hint="friends-only-names", terminal_page_seen=True)
            report = json.loads((root / "analysis/facebook-sample-patterns.json").read_text())
            for status in ("running", "failed", "cancelled", "queued"):
                self.assertIsNone(api._reaction_result_outcome(report, status, 0))
            self.assertIsNone(api._reaction_result_outcome(report, "completed", 1))
            for section, key, value in (
                ("target_verification", "status", "not-matched"),
                ("reaction_integrity", "terminal_page_seen", False),
                ("reaction_integrity", "rate_limited_records", 1),
                ("reaction_integrity", "temporary_feature_block_records", 1),
            ):
                changed = json.loads(json.dumps(report))
                changed[section][key] = value
                if key == "terminal_page_seen":
                    changed["enumeration_status"]["reactions"]["terminal_page_seen"] = False
                    changed["enumeration_status"]["reactions"]["pagination"] = {}
                self.assertIsNone(api._reaction_result_outcome(changed, "completed", 0))
            report["enumeration_status"]["reactions"] = {}
            self.assertIsNone(api._reaction_result_outcome(report, "completed", 0))
            report["reaction_integrity"]["reported_total_count"] = 0
            self.assertEqual(api._reaction_result_outcome(report, "completed", 0)["reason"], "zero-reactions")

    def test_build_result_accepts_bounded_visibility_restricted_reaction_list_as_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-triggered-friends-only-visibility",
                query_records=1,
                source_post_ids=["p1"],
                reported_total_count=4494,
                visibility_hint="friends-only-names",
                terminal_page_seen=False,
            )
            diagnostics_path = run_dir / "readonly-ui" / "exploration-diagnostics.json"
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            diagnostics["pagination_runs"][0].update(
                {"stop_reason": "absolute-cap", "absolute_cap": 1, "final_budget": 1}
            )
            diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")

            request = api.normalize_request({
                "post_url": "https://facebook.com/x",
                "reaction_scroll_limit": 1,
            })
            result = api.build_api_result(run_dir, request)

            self.assertEqual(result["completion_scope"], api.BOUNDED_SAMPLE_COMPLETION_SCOPE)
            self.assertEqual(result["reaction_users"], [])
            self.assertEqual(result["visibility"]["reactions"]["status"], "query-triggered-friends-only-visibility")

    def test_build_result_rejects_untriggered_reaction_list_when_target_feedback_reports_reactions(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-not-triggered",
                query_records=0,
                source_post_ids=[],
                reported_total_count=1162,
            )

            with self.assertRaisesRegex(api.ApiError, "reaction capture is incomplete"):
                api.build_api_result(
                    run_dir,
                    {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
                )

    def test_build_result_rejects_visible_rows_without_verified_reaction_source(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-triggered-friends-only-visibility",
                query_records=2,
                source_post_ids=[],
                reported_total_count=4494,
                visibility_hint="friends-only-names",
                terminal_page_seen=True,
            )
            (run_dir / "analysis" / "facebook-reactor-users.csv").write_text(
                "user_id,user_name,source_post_id\nu1,User 1,p1\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(api.ApiError, "reaction capture source mismatch"):
                api.build_api_result(
                    run_dir,
                    {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
                )

    def test_build_result_rejects_empty_visibility_with_diagnostic_errors(self):
        cases = (
            {"pagination_runs": [{"parse_errors": 1}]},
            {"pagination_runs": [{"latest_graphql_error_code": 1675004}]},
            {"pagination_runs": [{"latest_api_error_code": 368}]},
            {"reaction_screening": {"parse_errors": 1}},
            {"reaction_screening": {"session_restricted": True}},
            {"reaction_screening": {"session_restricted": 1}},
            {"pagination_runs": [{"session_restricted": "true"}]},
            {"reaction_screening": {"latest_graphql_error_code": 1675004}},
            {"reaction_screening": {"latest_api_error_code": 368}},
        )
        for diagnostics in cases:
            with self.subTest(diagnostics=diagnostics), tempfile.TemporaryDirectory() as temp:
                run_dir = Path(temp)
                self._write_empty_reaction_visibility_fixture(
                    run_dir,
                    status="query-triggered-friends-only-visibility",
                    query_records=2,
                    source_post_ids=["p1"],
                    reported_total_count=4494,
                    visibility_hint="friends-only-names",
                    terminal_page_seen=True,
                    diagnostics=diagnostics,
                )

                with self.assertRaises(api.ApiError):
                    api.build_api_result(
                        run_dir,
                        {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
                    )

    def test_build_result_rejects_empty_visibility_with_fallback_diagnostic_error(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-triggered-friends-only-visibility",
                query_records=2,
                source_post_ids=["p1"],
                reported_total_count=4494,
                visibility_hint="friends-only-names",
                terminal_page_seen=True,
            )
            fallback = run_dir / "readonly-ui-fallback"
            fallback.mkdir()
            (fallback / "exploration-diagnostics.json").write_text(
                json.dumps({"reaction_screening": {"session_restricted": True}, "pagination_runs": []}),
                encoding="utf-8",
            )

            with self.assertRaises(api.ApiError):
                api.build_api_result(
                    run_dir,
                    {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
                )

    def test_build_result_rejects_default_request_for_bounded_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_reaction_fixture(run_dir)
            with self.assertRaisesRegex(api.ApiError, "reaction capture is incomplete"):
                api.build_api_result(
                    run_dir,
                    api.normalize_request({"post_url": "https://facebook.com/x"}),
                )

    def test_bounded_sample_requires_positive_users_and_clean_transport(self):
        cases = (
            {"users": 0},
            {"rate_limited_records": 1},
            {"temporary_feature_block_records": 1},
            {"line_parse_errors": 1},
        )
        for options in cases:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                run_dir = Path(temp)
                self._write_reaction_fixture(run_dir, **options)
                request = api.normalize_request({
                    "post_url": "https://facebook.com/x",
                    "reaction_scroll_limit": 3,
                })
                with self.assertRaisesRegex(api.ApiError, "reaction capture is incomplete"):
                    api.build_api_result(run_dir, request)

    def test_bounded_sample_requires_matched_source_and_absolute_cap(self):
        cases = (
            {"target_status": "mismatch"},
            {"source_post_ids": ["other"]},
            {"stop_reason": "open-no-recent-growth"},
        )
        for options in cases:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                run_dir = Path(temp)
                self._write_reaction_fixture(run_dir, **options)
                request = api.normalize_request({
                    "post_url": "https://facebook.com/x",
                    "reaction_scroll_limit": 3,
                })
                with self.assertRaises(api.ApiError):
                    api.build_api_result(run_dir, request)

    def test_build_result_returns_users_avatar_and_visibility(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            report = {
                "target_verification": {"status": "matched", "matched_by": "post-id", "matched_post_id": "p1"},
                "source_post_ids_by_family": {"reactions": ["p1"], "shares": []},
                "statuses": {"200": 10},
                "reaction_integrity": {
                    "integrity_status": "complete-terminal",
                    "completion_claim_allowed": True,
                    "terminal_page_seen": True,
                    "open_tail_without_terminal": False,
                    "successful_page_count": 2,
                    "raw_reactor_rows": 2,
                    "unique_reactor_identities": 2,
                    "duplicate_reactor_rows": 0,
                    "reported_total_count": 2,
                    "reported_total_unique_gap": 0,
                    "rate_limited_records": 0,
                    "temporary_feature_block_records": 0,
                },
                "enumeration_status": {
                    "reactions": {
                        "status": "visible-users-enumerated",
                        "ui_total_count": 2,
                        "reported_total_count": 2,
                        "reported_total_source": "ui-entry-label",
                        "enumerated_identity_count": 2,
                        "identity_count_gap": 0,
                        "pagination": {"all_observed_groups_terminal": True, "open_groups": [], "terminal_groups": ["__ALL__"]},
                    },
                    "shares": {"status": "query-not-triggered", "enumerated_identity_count": 0},
                },
            }
            (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
            with (analysis / "facebook-reactor-users.csv").open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=["user_id", "user_name", "avatar_url", "reaction_id", "source_field", "source_post_id"])
                writer.writeheader()
                writer.writerow({"user_id": "1", "user_name": "Alice", "avatar_url": "https://cdn.example/a.jpg", "reaction_id": "r1", "source_field": "reactors", "source_post_id": "p1"})
                writer.writerow({"user_id": "2", "user_name": "Bob", "avatar_url": "", "reaction_id": "r1", "source_field": "reactors", "source_post_id": "p1"})
            with (analysis / "facebook-share-users.csv").open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=["actor_id", "actor_name", "source_post_id", "share_story_id"])
                writer.writeheader()
            result = api.build_api_result(run_dir, {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]})
            self.assertEqual(len(result["reaction_users"]), 2)
            self.assertEqual(result["reaction_users"][0]["avatar_url"], "https://cdn.example/a.jpg")
            self.assertEqual(result["reaction_users"][0]["avatar_status"], "source-only")
            self.assertEqual(result["reaction_users"][0]["avatar_proxy_url"], "/v1/avatars/1")
            self.assertEqual(result["reaction_users"][1]["avatar_status"], "unavailable")
            self.assertEqual(result["reaction_users"][1]["avatar_proxy_url"], "/v1/avatars/2")
            self.assertEqual(result["visibility"]["reactions"]["identity_gap"], 0)
            self.assertEqual(result["visibility"]["reactions"]["reported_total"], 2)
            self.assertFalse(result["visibility"]["reactions"]["limited"])
            self.assertFalse(result["visibility"]["reactions"]["count_difference_observed"])
            self.assertTrue(result["visibility"]["reactions"]["terminal"])
            self.assertTrue(result["completeness"]["reactions"]["completion_claim_allowed"])
            self.assertEqual(result["completeness"]["reactions"]["integrity_status"], "complete-terminal")
            self.assertEqual(result["completion_scope"], "terminal")
            self.assertFalse(result["transport_observation"]["rate_limit_http_seen"])

    def test_build_result_rejects_false_completed_reaction_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            report = {
                "target_verification": {"status": "matched", "matched_post_id": "p1"},
                "source_post_ids_by_family": {"reactions": [], "shares": []},
                "reaction_integrity": {
                    "integrity_status": "query-not-triggered",
                    "query_records": 0,
                    "completion_claim_allowed": False,
                    "terminal_page_seen": False,
                },
                "enumeration_status": {"reactions": {"query_records": 0}, "shares": {}},
            }
            (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
            (analysis / "facebook-reactor-users.csv").write_text("user_id,user_name,source_post_id\n", encoding="utf-8")
            (analysis / "facebook-share-users.csv").write_text("actor_id,actor_name,source_post_id\n", encoding="utf-8")

            with self.assertRaisesRegex(api.ApiError, "reaction capture is incomplete"):
                api.build_api_result(run_dir, {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]})

    def test_resolve_avatar_url_requires_server_token(self):
        with mock.patch.dict(api.os.environ, {}, clear=True):
            with self.assertRaises(api.ApiError):
                api.resolve_avatar_url("1")

    def test_resolve_avatar_url_returns_non_silhouette_signed_url(self):
        payload = {"data": {"url": "https://scontent.example/avatar.jpg?oh=abc&oe=123", "is_silhouette": False}}
        response = mock.Mock()
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        response.read.return_value = json.dumps(payload).encode("utf-8")
        with mock.patch.dict(api.os.environ, {"FACEBOOK_GRAPH_ACCESS_TOKEN": "test-token"}, clear=True), \
             mock.patch("facebook_capture_api.urlopen", return_value=response) as fetch:
            self.assertEqual(api.resolve_avatar_url("1"), payload["data"]["url"])
            self.assertIn("access_token=test-token", fetch.call_args.args[0].full_url)

    def test_reaction_completeness_fallback_never_marks_open_tail_complete(self):
        report = {
            "enumeration_status": {
                "reactions": {
                    "enumerated_identity_count": 1350,
                    "pagination": {
                        "groups": {"__ALL__": {"open_page_seen": True, "terminal_page_seen": False}}
                    },
                }
            }
        }
        completeness = api._reaction_completeness(report)
        self.assertFalse(completeness["completion_claim_allowed"])
        self.assertTrue(completeness["open_tail_without_terminal"])
        self.assertEqual(completeness["integrity_status"], "partial-open-tail")

    def test_reaction_all_group_terminal_is_terminal_even_if_category_group_remains_open(self):
        report = {
            "enumeration_status": {
                "reactions": {
                    "enumerated_identity_count": 3180,
                    "pagination": {
                        "all_observed_groups_terminal": False,
                        "groups": {
                            "__ALL__": {"terminal_page_seen": True, "open_page_seen": True},
                            "1635855486666999": {"terminal_page_seen": False, "open_page_seen": True},
                        },
                    },
                }
            }
        }
        summary = api._enumeration_summary(report, "reactions")
        self.assertTrue(summary["terminal"])

    def test_reaction_terminal_gap_is_reported_as_platform_difference_not_failure(self):
        report = {
            "enumeration_status": {
                "reactions": {
                    "status": "visible-users-enumerated-subset",
                    "ui_total_count": None,
                    "reported_total_count": 3217,
                    "reported_total_source": "target-post-feedback",
                    "enumerated_identity_count": 3216,
                    "identity_count_gap": 1,
                    "pagination": {"groups": {"__ALL__": {"terminal_page_seen": True}}},
                }
            }
        }
        summary = api._enumeration_summary(report, "reactions")
        self.assertTrue(summary["terminal"])
        self.assertEqual(summary["reported_total"], 3217)
        self.assertEqual(summary["enumerated_count"], 3216)
        self.assertEqual(summary["identity_gap"], 1)
        self.assertTrue(summary["limited"])
        self.assertTrue(summary["count_difference_observed"])
        self.assertIn("not a capture failure", summary["count_difference_note"])

    def test_reaction_open_tail_gap_is_not_reported_as_platform_difference(self):
        report = {
            "enumeration_status": {
                "reactions": {
                    "status": "visible-users-enumerated",
                    "reported_total_count": 3217,
                    "enumerated_identity_count": 50,
                    "identity_count_gap": 3167,
                    "pagination": {
                        "groups": {"__ALL__": {"open_page_seen": True, "terminal_page_seen": False}}
                    },
                }
            }
        }

        summary = api._enumeration_summary(report, "reactions")

        self.assertFalse(summary["terminal"])
        self.assertTrue(summary["count_difference_observed"])
        self.assertTrue(summary["limited"])
        self.assertIn("未采完", summary["count_difference_note"])
        self.assertIn("不能归因", summary["count_difference_note"])
        self.assertNotIn("not a capture failure", summary["count_difference_note"])

    def test_reaction_failed_terminal_gap_is_not_reported_as_platform_difference(self):
        report = {
            "reaction_integrity": {
                "integrity_status": "rate-limited",
                "rate_limited_records": 1,
                "terminal_page_seen": True,
            },
            "enumeration_status": {
                "reactions": {
                    "status": "visible-users-enumerated-subset",
                    "reported_total_count": 3217,
                    "enumerated_identity_count": 3216,
                    "identity_count_gap": 1,
                    "terminal_page_seen": True,
                    "pagination": {"groups": {"__ALL__": {"terminal_page_seen": True}}},
                }
            },
        }

        summary = api._enumeration_summary(report, "reactions")

        self.assertTrue(summary["terminal"])
        self.assertTrue(summary["count_difference_observed"])
        self.assertIn("抓取错误", summary["count_difference_note"])
        self.assertNotIn("not a capture failure", summary["count_difference_note"])

    def test_reaction_visibility_hint_without_terminal_stays_incomplete(self):
        report = {
            "enumeration_status": {
                "reactions": {
                    "status": "query-triggered-friends-only-visibility",
                    "ui_visibility_hint": "friends-only-names",
                    "reported_total_count": 4494,
                    "enumerated_identity_count": 0,
                    "identity_count_gap": 4494,
                    "pagination": {"groups": {"__ALL__": {"terminal_page_seen": False}}},
                }
            }
        }

        summary = api._enumeration_summary(report, "reactions")

        self.assertFalse(summary["terminal"])
        self.assertTrue(summary["limited"])
        self.assertIn("未采完", summary["count_difference_note"])
        self.assertNotIn("not a capture failure", summary["count_difference_note"])

    def test_target_mismatch_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            report = {"target_verification": {"status": "mismatch"}, "enumeration_status": {}}
            (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(api.ApiError):
                api.build_api_result(run_dir, {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]})

    def test_missing_target_verification_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            report = {"target_verification": {}, "enumeration_status": {}}
            (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(api.ApiError):
                api.build_api_result(run_dir, {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]})



class WorkerConfigurationHttpTests(unittest.TestCase):
    def _assert_io_failure(self, *, reload: bool, via_proxy: bool, submission=None, failure_type=OSError):
        from http.server import ThreadingHTTPServer
        from start_ui_v2 import V2ProxyHandler
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(api, "BASE_DIR", Path(temp)), \
             mock.patch.object(api.JobManager, "_start_worker", return_value=None) as start_worker, \
             mock.patch.object(api, "adb_snapshot", return_value={
                 "state": "offline", "boot_completed": False, "proxy": "",
             }):
            workers = [
                api.WorkerConfig("worker-01", "emulator-5554", "first", 8081, 18766, True),
                api.WorkerConfig("worker-02", "emulator-5556", "second", 18082, 18767, False),
            ]
            manager = api.JobManager(workers=workers, worker_config_path=Path(temp) / "workers.json")
            api.write_worker_config(manager.worker_config_path, workers)
            disk_before = manager.worker_config_path.read_bytes()
            live_before = dict(manager.workers)
            starts_before = start_worker.call_count
            backend = api.CaptureServer(("127.0.0.1", 0), manager)
            backend.handle_error = mock.Mock()
            servers = [backend]
            if via_proxy:
                proxy = ThreadingHTTPServer(("127.0.0.1", 0), V2ProxyHandler)
                proxy.api_target = f"http://127.0.0.1:{backend.server_port}"
                servers.append(proxy)
            threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
            for thread in threads:
                thread.start()
            connection = None
            try:
                port = servers[-1].server_port
                path = ("/v1/" + submission if submission else
                        "/v1/workers/reload" if reload else "/v1/workers/worker-02/enable")
                payload = ({"post_url": "https://www.facebook.com/example/posts/1"}
                           if submission == "jobs" else {"post_urls": ["https://www.facebook.com/example/posts/1"]})
                failure = failure_type("DUMMY_PRIVATE_CONFIG_PATH")
                patcher = (mock.patch.object(api, "load_worker_config", side_effect=failure)
                           if reload else mock.patch.object(api.os, "replace", side_effect=failure))
                with patcher:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    connection.request("POST", path, body=json.dumps(payload) if submission else None,
                                       headers={"Content-Type": "application/json"} if submission else {})
                    response = connection.getresponse()
                    body = response.read().decode("utf-8")
                    self.assertEqual(response.status, 503)
                    self.assertIn("application/json", response.getheader("Content-Type"))
                    self.assertEqual(set(json.loads(body)), {"error"})
                    self.assertIn("local I/O", json.loads(body)["error"])
                    self.assertNotIn("DUMMY_PRIVATE_CONFIG_PATH", body)
                    connection.close()
                    connection = None
                self.assertEqual(manager.workers, live_before)
                self.assertEqual(manager.worker_config_path.read_bytes(), disk_before)
                self.assertEqual(manager._active_worker_id, "worker-01")
                self.assertEqual(start_worker.call_count, starts_before)
                if submission:
                    self.assertEqual(manager._jobs, {})
                    self.assertEqual(manager._batches, {})
                    self.assertEqual(manager._queue.qsize(), 0)
                backend.handle_error.assert_not_called()
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                connection.request("GET", "/health")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["service"], "ready")
            finally:
                if connection is not None:
                    connection.close()
                for server in reversed(servers):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(3)
                    self.assertFalse(thread.is_alive())

    def test_client_disconnect_is_not_rewritten_as_configuration_error(self):
        from email.message import Message
        from types import SimpleNamespace
        for action in ("reload", "enable", "jobs", "batches"):
            with self.subTest(action=action):
                manager = mock.Mock()
                result = {"dummy": True}
                manager.reload_workers.return_value = result
                manager.set_worker_enabled.return_value = result
                manager.submit.return_value = result
                manager.submit_batch.return_value = result
                handler = object.__new__(api.ApiHandler)
                handler.server = SimpleNamespace(manager=manager)
                handler.path = ("/v1/" + action if action in {"jobs", "batches"} else
                                "/v1/workers/reload" if action == "reload"
                                else "/v1/workers/worker-02/enable")
                handler.headers = Message()
                if action in {"jobs", "batches"}:
                    from io import BytesIO
                    request = ({"post_url": "https://www.facebook.com/example/posts/1"}
                               if action == "jobs" else {"post_urls": ["https://www.facebook.com/example/posts/1"]})
                    payload = json.dumps(request).encode()
                    handler.headers["Content-Length"] = str(len(payload))
                    handler.rfile = BytesIO(payload)
                handler._trusted_request = mock.Mock(return_value=True)
                handler._json = mock.Mock(side_effect=BrokenPipeError("dummy closed client"))
                with self.assertRaises(BrokenPipeError):
                    handler.do_POST()
                handler._json.assert_called_once_with(202 if action in {"jobs", "batches"} else 200, result)

    def test_body_timeout_keeps_408_and_never_calls_submit(self):
        from email.message import Message
        from types import SimpleNamespace
        manager = mock.Mock()
        handler = object.__new__(api.ApiHandler)
        handler.server = SimpleNamespace(manager=manager)
        handler.path = "/v1/jobs"
        handler.headers = Message()
        handler.headers["Content-Length"] = "2"
        handler.rfile = mock.Mock()
        handler.rfile.read.side_effect = TimeoutError("dummy request-body timeout")
        handler._trusted_request = mock.Mock(return_value=True)
        handler._json = mock.Mock()
        handler.do_POST()
        handler._json.assert_called_once_with(408, {"error": "request body timed out"})
        manager.submit.assert_not_called()
        manager.submit_batch.assert_not_called()

    def test_submission_storage_failure_returns_sanitized_http_error(self):
        for submission in ("jobs", "batches"):
            for via_proxy, failure_type in ((False, OSError), (True, OSError), (False, TimeoutError), (True, TimeoutError)):
                with self.subTest(submission=submission, via_proxy=via_proxy, failure_type=failure_type):
                    self._assert_io_failure(reload=False, via_proxy=via_proxy, submission=submission, failure_type=failure_type)

    def test_worker_write_failure_returns_sanitized_http_error(self):
        for via_proxy in (False, True):
            with self.subTest(via_proxy=via_proxy):
                self._assert_io_failure(reload=False, via_proxy=via_proxy)

    def test_worker_reload_read_failure_returns_sanitized_http_error(self):
        for via_proxy in (False, True):
            with self.subTest(via_proxy=via_proxy):
                self._assert_io_failure(reload=True, via_proxy=via_proxy)



class ReconciliationHttpTests(unittest.TestCase):
    def _servers(self, manager, via_proxy=False):
        from http.server import ThreadingHTTPServer
        from start_ui_v2 import V2ProxyHandler
        backend = api.CaptureServer(("127.0.0.1", 0), manager)
        servers = [backend]
        if via_proxy:
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), V2ProxyHandler)
            proxy.api_target = f"http://127.0.0.1:{backend.server_port}"
            servers.append(proxy)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
        for thread in threads: thread.start()
        def cleanup():
            for server in reversed(servers): server.shutdown(); server.server_close()
            for thread in threads: thread.join(3)
        self.addCleanup(cleanup)
        return servers[-1]

    def _request(self, server, body=b'{"acknowledge":true}', *, method="POST", path=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            conn.request(method, path or "/v1/jobs/fb_dummy/reconcile-result-persistence", body=body,
                         headers=headers if headers is not None else {"Content-Type": "application/json"})
            reply = conn.getresponse()
            return reply.status, reply.read().decode("utf-8"), reply.getheader("Content-Type")
        finally: conn.close()

    def test_invalid_acknowledgments_and_sources_never_delegate(self):
        for via_proxy in (False, True):
            with self.subTest(via_proxy=via_proxy):
                manager = mock.Mock()
                server = self._servers(manager, via_proxy)
                for body in (b'', b'{}', b'{"acknowledge":false}', b'{"acknowledge":1}',
                             b'{"acknowledge":"true"}', b'[]', b'[["acknowledge",true]]',
                             b'{"acknowledge":true,"extra":0}', b'{"acknowledge":false,"acknowledge":true}',
                             b'{"acknowledge":true,"acknowledge":true}', b'{invalid', b'\xff', b'['*20000+b'0'+b']'*20000):
                    with self.subTest(body_length=len(body), body_prefix=body[:60]):
                        self.assertEqual(self._request(server, body)[0], 400)
                self.assertEqual(self._request(server, headers={"Content-Type": "text/plain"})[0], 400)
                self.assertEqual(self._request(server, body=None, headers={"Content-Type":"application/json", "Content-Length":"20", "Origin":"https://untrusted.invalid"})[0], 403)
                self.assertEqual(self._request(server, body=None, headers={"Content-Type":"application/json", "Content-Length":"20", "Host":"untrusted.invalid"})[0], 403)
                self.assertEqual(self._request(server, body=None, headers={"Content-Length":"65537"})[0], 400)
                self.assertEqual(self._request(server, method="GET", body=None)[0], 404)
                manager.reconcile_result_persistence.assert_not_called()

    def test_success_missing_conflict_and_io_responses_through_both_paths(self):
        for via_proxy in (False, True):
            with self.subTest(via_proxy=via_proxy):
                manager = mock.Mock()
                server = self._servers(manager, via_proxy)
                manager.reconcile_result_persistence.return_value = {"reconciled":True,"job":{"status":"failed"}}
                status, body, kind = self._request(server, headers={"Content-Type":"application/json","Origin":f"http://127.0.0.1:{server.server_port}"})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["job"]["status"], "failed")
                self.assertIn("application/json", kind)
                manager.reconcile_result_persistence.assert_called_once_with("fb_dummy", acknowledge=True)
                for exc, expected in ((KeyError("dummy"),404),(api.ApiError("dummy blocked"),409),(OSError("PRIVATE_STORAGE_PATH"),503),(TimeoutError("PRIVATE_STORAGE_PATH"),503)):
                    manager.reconcile_result_persistence.side_effect = exc
                    status, body, _ = self._request(server)
                    self.assertEqual(status, expected)
                    self.assertEqual(set(json.loads(body)), {"error"})
                    self.assertNotIn("PRIVATE_STORAGE_PATH", body)

    def test_scheduler_native_start_failure_has_one_sanitized_http_response(self):
        from email.message import Message
        from io import BytesIO
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(api, "BASE_DIR", Path(tmp)):
            start_impl = api.JobManager._start_worker
            with mock.patch.object(api.JobManager, "_start_worker", return_value=None):
                actual = api.JobManager(workers=[api.WorkerConfig("worker-01", "emulator-5554", "fixture", 8081, 18766, True)])
            native = mock.Mock()
            native.ident = None
            native.start.side_effect = RuntimeError("PRIVATE_START_DETAILS")
            for route, method in (("/v1/workers/reload", "reload_workers"),
                                  ("/v1/workers/worker-01/enable", "set_worker_enabled"),
                                  ("/v1/jobs/fb_dummy/reconcile-result-persistence", "reconcile_result_persistence")):
                with self.subTest(route=route):
                    manager = mock.Mock()
                    getattr(manager, method).side_effect = lambda *_args, **_kwargs: start_impl(actual, "worker-01")
                    handler = object.__new__(api.ApiHandler)
                    handler.server = SimpleNamespace(manager=manager)
                    handler.path = route
                    body = b'{"acknowledge":true}'
                    handler.headers = Message()
                    handler.headers["Content-Type"] = "application/json"
                    handler.headers["Content-Length"] = str(len(body))
                    handler.rfile = BytesIO(body)
                    handler._trusted_request = mock.Mock(return_value=True)
                    handler._json = mock.Mock()
                    with mock.patch.object(api.threading, "Thread", return_value=native):
                        handler.do_POST()
                    handler._json.assert_called_once()
                    code, payload = handler._json.call_args.args
                    self.assertEqual(code, 503)
                    self.assertEqual(set(payload), {"error"})
                    self.assertNotIn("PRIVATE_START_DETAILS", payload["error"])
                    self.assertIn("inspect state before retrying", payload["error"])
                    getattr(manager, method).assert_called_once()
                    self.assertIsNone(actual._worker)

    def test_scheduler_http_boundary_does_not_catch_logic_or_reply_failures(self):
        from email.message import Message
        from io import BytesIO
        from types import SimpleNamespace
        for route, method in (("/v1/workers/reload", "reload_workers"),
                              ("/v1/workers/worker-01/enable", "set_worker_enabled"),
                              ("/v1/jobs/fb_dummy/reconcile-result-persistence", "reconcile_result_persistence")):
            for phase in ("manager-logic", "response-write"):
                with self.subTest(route=route, phase=phase):
                    manager = mock.Mock()
                    getattr(manager, method).return_value = {"fixture": True}
                    handler = object.__new__(api.ApiHandler)
                    handler.server = SimpleNamespace(manager=manager)
                    handler.path = route
                    body = b'{"acknowledge":true}'
                    handler.headers = Message()
                    handler.headers["Content-Type"] = "application/json"
                    handler.headers["Content-Length"] = str(len(body))
                    handler.rfile = BytesIO(body)
                    handler._trusted_request = mock.Mock(return_value=True)
                    handler._json = mock.Mock()
                    if phase == "manager-logic":
                        error = RuntimeError("dummy unrelated logic failure")
                        getattr(manager, method).side_effect = error
                    else:
                        error = api.SchedulerStartError("dummy reply failure")
                        handler._json.side_effect = error
                    with self.assertRaises(type(error)) as caught:
                        handler.do_POST()
                    self.assertIs(caught.exception, error)
                    getattr(manager, method).assert_called_once()
                    self.assertEqual(handler._json.call_count, 0 if phase == "manager-logic" else 1)

    def test_scheduler_503_after_durable_ack_does_not_mean_storage_rollback(self):
        for via_proxy in (False, True):
            with self.subTest(via_proxy=via_proxy), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(api, "BASE_DIR", Path(tmp)):
                with mock.patch.object(api.JobManager, "_start_worker", return_value=None):
                    manager = api.JobManager(workers=[api.WorkerConfig("worker-01", "emulator-5554", "fixture", 8081, 18766, True)])
                job_id = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})["job_id"]
                job = manager._jobs[job_id]
                job.update(status="failed", result=None, recovery_state="failed-result-persistence",
                           process_state="stopped", process_pid=None, worker_id="worker-01",
                           transport_cleanup_state="runner-owned" if api.sys.platform in {"win32", "darwin"} else "not-required")
                manager._persist_job(job)
                manager._queue.get_nowait()
                manager._queue.task_done()
                manager._orphaned_worker_ids.add("worker-01")
                with manager._lock:
                    manager._block_capture_lease_locked(job_id, "fixture storage fault", persistence=True)
                server = self._servers(manager, via_proxy)
                native = mock.Mock()
                native.ident = None
                native.start.side_effect = RuntimeError("PRIVATE_THREAD_DETAILS")
                start_impl = api.JobManager._start_worker

                def fail_start(worker_id):
                    with mock.patch.object(api.threading, "Thread", return_value=native):
                        return start_impl(manager, worker_id)

                path = f"/v1/jobs/{job_id}/reconcile-result-persistence"
                with mock.patch.object(manager, "_start_worker", side_effect=fail_start) as startup, \
                     mock.patch.object(api, "run_capture_job") as capture:
                    status, body, _ = self._request(server, path=path)
                    self.assertEqual(status, 503)
                    self.assertNotIn("PRIVATE_THREAD_DETAILS", body)
                    self.assertIn("inspect state before retrying", body)
                    persisted = json.loads(api._job_manifest_path(job_id).read_text())
                    self.assertEqual(persisted["status"], "failed")
                    self.assertEqual(persisted["recovery_state"], "failed-result-persistence-reconciled")
                    self.assertIsNone(manager._worker)
                    self.assertNotIn(job_id, manager._persistence_blocked_jobs)
                    self.assertEqual(self._request(server, path=path)[0], 409)
                    startup.assert_called_once()
                    capture.assert_not_called()

    def test_body_timeout_and_short_read_do_not_reconcile(self):
        from email.message import Message
        from types import SimpleNamespace
        for mode in ("timeout", "short", "io"):
            with self.subTest(mode=mode):
                handler = object.__new__(api.ApiHandler)
                manager = mock.Mock()
                handler.server = SimpleNamespace(manager=manager)
                handler.path = "/v1/jobs/fb_dummy/reconcile-result-persistence"
                handler.headers = Message()
                handler.headers["Content-Type"] = "application/json"
                handler.headers["Content-Length"] = "20"
                handler.rfile = mock.Mock()
                if mode == "timeout": handler.rfile.read.side_effect = TimeoutError("dummy read timeout")
                elif mode == "io": handler.rfile.read.side_effect = OSError("PRIVATE_REQUEST_PATH")
                else: handler.rfile.read.return_value = b'{}'
                handler._trusted_request = mock.Mock(return_value=True)
                handler._json = mock.Mock()
                handler.do_POST()
                self.assertEqual(handler._json.call_args.args[0], 408 if mode == "timeout" else 503 if mode == "io" else 400)
                manager.reconcile_result_persistence.assert_not_called()

    def test_source_and_framing_reject_before_any_body_read(self):
        from email.message import Message
        from types import SimpleNamespace
        for case in ("host", "origin", "duplicate-length", "transfer", "bad-length", "missing-length"):
            with self.subTest(case=case):
                handler = object.__new__(api.ApiHandler)
                manager = mock.Mock()
                handler.server = SimpleNamespace(manager=manager, server_port=8765)
                handler.path = "/v1/jobs/fb_dummy/reconcile-result-persistence"
                handler.headers = Message()
                handler.headers["Host"] = "untrusted.invalid" if case == "host" else "127.0.0.1:8765"
                handler.headers["Content-Type"] = "application/json"
                if case != "missing-length": handler.headers["Content-Length"] = "bad" if case == "bad-length" else "20"
                if case == "origin": handler.headers["Origin"] = "https://untrusted.invalid"
                if case == "duplicate-length": handler.headers.add_header("Content-Length", "20")
                if case == "transfer": handler.headers["Transfer-Encoding"] = "chunked"
                handler.rfile = mock.Mock()
                handler._json = mock.Mock()
                handler.do_POST()
                self.assertEqual(handler._json.call_args.args[0], 403 if case in {"host", "origin"} else 400)
                handler.rfile.read.assert_not_called()
                manager.reconcile_result_persistence.assert_not_called()

    def test_exact_body_limit_still_allows_valid_acknowledgment(self):
        manager = mock.Mock()
        manager.reconcile_result_persistence.return_value = {"reconciled":True,"job":{"status":"failed"}}
        server = self._servers(manager)
        body = b'{"acknowledge":true}'
        body += b' ' * (65536 - len(body))
        self.assertEqual(self._request(server, body)[0], 200)
        manager.reconcile_result_persistence.assert_called_once_with("fb_dummy", acknowledge=True)

    def test_successful_reply_disconnect_never_repeats_ack(self):
        from email.message import Message
        from types import SimpleNamespace
        from io import BytesIO
        handler = object.__new__(api.ApiHandler)
        manager = mock.Mock()
        result = {"reconciled": True, "job": {"status": "failed"}}
        manager.reconcile_result_persistence.return_value = result
        handler.server = SimpleNamespace(manager=manager)
        handler.path = "/v1/jobs/fb_dummy/reconcile-result-persistence"
        body = b'{"acknowledge":true}'
        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = BytesIO(body)
        handler._trusted_request = mock.Mock(return_value=True)
        handler._json = mock.Mock(side_effect=BrokenPipeError("dummy closed client"))
        with self.assertRaises(BrokenPipeError): handler.do_POST()
        handler._json.assert_called_once_with(200, result)
        manager.reconcile_result_persistence.assert_called_once_with("fb_dummy", acknowledge=True)

    def test_actual_manager_ack_write_failure_keeps_block_and_success_stays_failed(self):
        for via_proxy in (False, True):
            with self.subTest(via_proxy=via_proxy), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(api,"BASE_DIR",Path(tmp)), \
                 mock.patch.object(api.JobManager,"_start_worker",return_value=None):
                worker=api.WorkerConfig("worker-01","emulator-5554","first",8081,18766,True)
                manager=api.JobManager(workers=[worker])
                job_id=manager.submit({"post_url":"https://www.facebook.com/example/posts/1"})["job_id"]
                job=manager._jobs[job_id]
                job.update(status="failed",result=None,recovery_state="failed-result-persistence",
                           process_state="stopped",process_pid=None,worker_id="worker-01",
                           transport_cleanup_state="runner-owned" if api.sys.platform in {"win32","darwin"} else "not-required")
                manager._persist_job(job)
                manager._orphaned_worker_ids.add("worker-01")
                with manager._lock: manager._block_capture_lease_locked(job_id,"dummy storage fault",persistence=True)
                manager._queue.get_nowait();manager._queue.task_done()
                server=self._servers(manager,via_proxy)
                path=f"/v1/jobs/{job_id}/reconcile-result-persistence"
                with mock.patch.object(api.os,"replace",side_effect=OSError("PRIVATE_STORAGE_PATH")):
                    status,body,_=self._request(server,path=path)
                self.assertEqual(status,503)
                self.assertNotIn("PRIVATE_STORAGE_PATH",body)
                self.assertTrue(manager._capture_lease_uncertain)
                self.assertIn(job_id,manager._persistence_blocked_jobs)
                status,body,_=self._request(server,path=path)
                self.assertEqual(status,200)
                self.assertTrue(json.loads(body)["reconciled"])
                self.assertEqual(manager._jobs[job_id]["status"],"failed")
                self.assertIsNone(manager._jobs[job_id]["result"])
                self.assertFalse(manager._capture_lease_uncertain)
                self.assertEqual(manager._queue.qsize(),0)
                self.assertEqual(self._request(server,path=path)[0],409)


class ReactorFieldnameTests(unittest.TestCase):
    @staticmethod
    def _legacy(rows):
        names = list(api.REACTOR_USER_FIELDS)
        for row in rows:
            for field in row:
                name = str(field)
                if name and name not in names:
                    names.append(name)
        return names

    def test_field_order_duplicates_empty_and_nonstring_keys_keep_legacy_behavior(self):
        rows = [
            {"user_id": "dummy", "extra_b": "b", "extra_a": "a", "": "empty", 1: "numeric", None: "none"},
            {"extra_a": "repeat", "1": "repeat-normalized", "None": "repeat-normalized", "extra_c": "c"},
        ]
        import copy
        snapshot = copy.deepcopy(rows)
        self.assertEqual(api._reactor_fieldnames(rows), self._legacy(rows))
        self.assertEqual(rows, snapshot)
        self.assertEqual(api._reactor_fieldnames([]), ["user_id", "user_name", "avatar_url", "source_post_id", "reaction_id"])

    def test_batch_csv_field_order_and_post_scoped_identity_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [
                {"user_id": "shared", "user_name": "Dummy", "avatar_url": "", "source_post_id": "dummy",
                 "reaction_id": "LIKE", "extra_z": "z", "extra_a": "a"},
                {"user_id": "other", "user_name": "Other", "avatar_url": "", "source_post_id": "dummy",
                 "reaction_id": "LIKE", "extra_new": "n"},
            ]
            jobs = [{"job_id": job_id, "status": "queued", "request": {"post_url": "https://www.facebook.com/example/posts/1"}}
                    for job_id in ("fb_first", "fb_second")]
            with mock.patch.object(api, "_read_batch_reactor_rows", return_value=rows + rows[:1]), \
                 mock.patch.object(api, "_job_evidence_dirs", return_value=[]):
                summary = api.write_batch_outputs(root, "batch_dummy", jobs)
            self.assertEqual(summary["post_user_records"], 4)
            self.assertEqual(summary["within_post_duplicate_occurrences"], 2)
            self.assertEqual(summary["cross_post_unique_users"], 2)
            self.assertEqual(summary["cross_post_duplicate_occurrences"], 2)
            self.assertEqual(summary["per_post_valid_sum"], 4)
            specs = [
                ("batch-reactor-users.csv", ["user_id", "user_name", "avatar_url", "post_index", "job_id", "post_url",
                                             "source_post_id", "reaction_id", "post_count", "extra_new", "extra_z", "extra_a"]),
                ("batch-user-post-relations.csv", ["user_id", "job_id", "post_index", "post_url", "source_post_id", "reaction_id",
                                                   "user_name", "avatar_url", "extra_new", "extra_z", "extra_a"]),
            ]
            for name, expected_fields in specs:
                with self.subTest(file=name), (root / name).open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    exported = list(reader)
                    self.assertEqual(reader.fieldnames, expected_fields)
                    self.assertEqual(len(exported), 4)
                    shared = [row for row in exported if row["user_id"] == "shared"]
                    self.assertEqual([row["job_id"] for row in shared], ["fb_first", "fb_second"])
                    self.assertTrue(all(row["extra_z"] == "z" and row["extra_a"] == "a" for row in shared))
                    self.assertEqual([row["post_index"] for row in shared], ["1", "2"])
                    self.assertTrue(all(row["post_url"] == jobs[0]["request"]["post_url"] for row in shared))
                    self.assertTrue(all(row["source_post_id"] == "dummy" and row["reaction_id"] == "LIKE" for row in shared))
                    self.assertTrue(all(row["post_count"] == "2" for row in shared) if "post_count" in reader.fieldnames else True)

    def test_batch_keeps_first_per_job_user_and_all_cross_post_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = [{"job_id": f"fb_{i}", "status": "queued", "request": {"post_url": f"https://www.facebook.com/example/posts/{i}"}}
                    for i in (1, 2)]
            first = {"user_id": "shared", "user_name": "First", "source_post_id": "source-a", "reaction_id": "LIKE", "extra_a": "a"}
            second = {"user_id": "shared", "user_name": "Second", "source_post_id": "source-b", "reaction_id": "LOVE", "extra_b": "b"}
            variants = [first, {**first, "source_post_id": "different", "reaction_id": "WOW"}]
            with mock.patch.object(api, "_read_batch_reactor_rows", side_effect=[variants, [second, second]]), \
                 mock.patch.object(api, "_job_evidence_dirs", return_value=[]):
                summary = api.write_batch_outputs(root, "batch_dummy", jobs)
            self.assertEqual(summary["post_user_records"], 2)
            self.assertEqual(summary["within_post_duplicate_occurrences"], 2)
            self.assertEqual(summary["cross_post_unique_users"], 1)
            for name in ("batch-reactor-users.csv", "batch-user-post-relations.csv"):
                with (root / name).open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    actual = list(reader)
                    self.assertEqual(reader.fieldnames[-2:], ["extra_a", "extra_b"])
                self.assertEqual(len(actual), 2)
                for index, (row, original) in enumerate(zip(actual, (first, second)), 1):
                    self.assertEqual(row["post_index"], str(index))
                    self.assertEqual(row["job_id"], f"fb_{index}")
                    self.assertEqual(row["post_url"], jobs[index - 1]["request"]["post_url"])
                    self.assertEqual(row["source_post_id"], original["source_post_id"])
                    self.assertEqual(row["reaction_id"], original["reaction_id"])
                    self.assertEqual(row["user_name"], original["user_name"])
                self.assertEqual(actual[0]["extra_a"], "a")
                self.assertEqual(actual[1]["extra_b"], "b")

    def test_actual_checkpoint_merge_preserves_tuple_identity_and_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "attempt-001"
            second = root / "attempt-002"
            rows = [
                {"user_id": "shared", "user_name": "Dummy", "source_post_id": "source-a", "reaction_id": "LIKE", "extra_b": "b"},
                {"user_id": "shared", "user_name": "Dummy", "source_post_id": "source-a", "reaction_id": "LOVE", "extra_a": "a"},
                {"user_id": "shared", "user_name": "Dummy", "source_post_id": "source-b", "reaction_id": "LIKE", "extra_new": "n"},
            ]
            for attempt, values in ((first, rows[:2]), (second, [rows[0], rows[2]])):
                names = self._legacy(values)
                api._atomic_write_csv(attempt / "analysis" / "facebook-reactor-users.csv", names, values)
            result = api._persist_checkpoint_users(root, [first, second])
            self.assertEqual(len(result), 3)
            with (root / "checkpoint" / "facebook-reactor-users.csv").open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                persisted = list(reader)
                self.assertEqual(reader.fieldnames, ["user_id", "user_name", "avatar_url", "source_post_id", "reaction_id", "extra_b", "extra_a", "extra_new"])
            self.assertEqual([(r["user_id"], r["source_post_id"], r["reaction_id"]) for r in persisted],
                             [("shared", "source-a", "LIKE"), ("shared", "source-a", "LOVE"), ("shared", "source-b", "LIKE")])
            self.assertEqual(persisted[0]["extra_b"], "b")
            self.assertEqual(persisted[1]["extra_a"], "a")
            self.assertEqual(persisted[2]["extra_new"], "n")
            # CSV round-tripping fills absent columns with empty strings. The
            # persisted table, not sparse pre-write dicts, is the repeat baseline.
            before = (root / "checkpoint" / "facebook-reactor-users.csv").read_bytes()
            self.assertEqual(api._persist_checkpoint_users(root, [first, second]), persisted)
            self.assertEqual((root / "checkpoint" / "facebook-reactor-users.csv").read_bytes(), before)

    def test_checkpoint_csv_headers_and_rows_preserve_provenance_order(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dummy.csv"
            rows = [{"user_id": "dummy", "user_name": "Dummy", "source_post_id": "post",
                     "reaction_id": "LIKE", "extra_b": "b", "extra_a": "a"}]
            names = api._reactor_fieldnames(rows)
            api._atomic_write_csv(path, names, rows)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                actual = list(reader)
                self.assertEqual(reader.fieldnames, self._legacy(rows))
            self.assertEqual(len(actual), 1)
            self.assertEqual(actual[0]["source_post_id"], "post")
            self.assertEqual(actual[0]["reaction_id"], "LIKE")
            self.assertEqual(actual[0]["user_id"], "dummy")
            self.assertEqual(actual[0]["extra_b"], "b")
            self.assertEqual(actual[0]["extra_a"], "a")

class ArtifactPhysicalBoundaryTests(unittest.TestCase):
    def _manager(self, root):
        manager = object.__new__(api.JobManager)
        manager._lock = threading.RLock()
        manager._jobs = {"fixture": {"job_id": "fixture", "status": "completed", "run_dir": str(root), "request": {}}}
        manager._batches = {"fixture-batch": {"run_dir": str(root), "job_ids": []}}
        return manager

    def _directory_link(self, link, target):
        import os, shutil, subprocess
        self.assertTrue(target.resolve().is_relative_to(self._scratch_root))
        self.assertTrue(link.parent.resolve().is_relative_to(self._scratch_root))
        if os.name == "nt":
            shell = shutil.which("pwsh") or shutil.which("powershell.exe")
            if not shell:
                self.skipTest("PowerShell unavailable for synthetic junction")
            command = "New-Item -ItemType Junction -Path '{}' -Value '{}' -ErrorAction Stop | Out-Null".format(
                str(link).replace("'", "''"), str(target).replace("'", "''"))
            r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, timeout=15)
            if r.returncode:
                self.skipTest("Synthetic junction creation unavailable")
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_child_link_cannot_escape_job_or_batch_and_metadata_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self._scratch_root = base.resolve()
            self.assertTrue(self._scratch_root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            root, outside = base / "job", base / "outside"
            root.mkdir(); outside.mkdir()
            (outside / "facebook-reactor-users.csv").write_text("fixture-only", encoding="utf-8")
            self._directory_link(root / "analysis", outside)
            manager = self._manager(root)
            with self.assertRaises(api.ApiError):
                manager.artifact("fixture", "facebook-reactor-users.csv")
            meta = next(x for x in api._artifact_metadata(root, "completed") if x["name"] == "facebook-reactor-users.csv")
            self.assertEqual(meta["state"], "missing")
            self.assertEqual(meta["size_bytes"], 0)
            # Batch links are tested below their trusted root as well.
            batch_parent = root / "batch-links"
            self._directory_link(batch_parent, outside)
            with mock.patch.object(api, "BATCH_ARTIFACT_SPECS", (("batch-links/facebook-reactor-users.csv", "fixture", "fixture"),)):
                with self.assertRaises(api.ApiError):
                    manager.batch_artifact("fixture-batch", "batch-links/facebook-reactor-users.csv")
                with mock.patch.object(api, "write_batch_outputs", return_value={"completion_scope": None}):
                    progress = manager.batch_progress("fixture-batch")
                self.assertEqual(progress["artifacts"][0]["size_bytes"], 0)
                self.assertNotEqual(progress["artifacts"][0]["state"], "ready")
            self.assertEqual((outside / "facebook-reactor-users.csv").read_text(), "fixture-only")

    def test_regular_artifacts_and_trusted_root_relocation_still_work(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "actual"
            self._scratch_root = base.resolve()
            self.assertTrue(self._scratch_root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            (root / "analysis").mkdir(parents=True)
            artifact = root / "analysis/facebook-reactor-users.csv"
            artifact.write_text("fixture-only", encoding="utf-8")
            (root / "batch-summary.json").write_text("{}", encoding="utf-8")
            alias = base / "relocated-root"
            self._directory_link(alias, root)
            manager = self._manager(alias)
            self.assertEqual(manager.artifact("fixture", "facebook-reactor-users.csv").read_text(), "fixture-only")
            self.assertEqual(manager.batch_artifact("fixture-batch", "batch-summary.json").read_text(), "{}")

    def test_missing_empty_and_resolution_error_are_not_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "analysis").mkdir()
            manager = self._manager(root)
            with self.assertRaises(api.ApiError): manager.artifact("fixture", "facebook-reactor-users.csv")
            (root / "analysis/facebook-reactor-users.csv").touch()
            with self.assertRaises(api.ApiError): manager.artifact("fixture", "facebook-reactor-users.csv")
            with mock.patch.object(Path, "resolve", side_effect=RuntimeError("PRIVATE_PATH")):
                with self.assertRaises(api.ApiError) as error:
                    manager.artifact("fixture", "facebook-reactor-users.csv")
                self.assertNotIn("PRIVATE_PATH", str(error.exception))


class RequestLocalHealthAdbTests(unittest.TestCase):
    def _manager(self):
        manager = object.__new__(api.JobManager)
        manager._lock = threading.RLock()
        manager.workers = {w.worker_id: w for w in [
            api.WorkerConfig("worker-01", "emulator-5554", "fixture", 8081, 18766, True),
            api.WorkerConfig("worker-02", "emulator-5556", "fixture2", 18082, 18767, False)]}
        manager._jobs = {}; manager._worker = None
        manager._worker_runtime = {k: {"current_job_id": None, "cooldown_until": 0} for k in manager.workers}
        manager._active_worker_id = "worker-01"
        manager._failed_worker_ids = set(); manager._orphaned_worker_ids = set()
        manager._capture_lease_uncertain = False; manager._capture_lease_job_id = None
        manager._capture_lease_reason = None; manager._last_failover = None
        return manager

    def _reply(self, text="", code=0):
        from types import SimpleNamespace
        return SimpleNamespace(stdout=text, stderr="", returncode=code)

    def test_health_uses_one_inventory_and_next_request_is_fresh(self):
        manager = self._manager(); state = {"online": True}; calls = []
        def run(args, timeout):
            calls.append((args, timeout))
            if args[-1] == "devices":
                return self._reply("List of devices attached\nemulator-5554\tdevice\n" if state["online"] else "List of devices attached\n")
            if args[-1] == "get-state":
                return self._reply("device" if state["online"] and args[2] == "emulator-5554" else "", 0 if state["online"] and args[2] == "emulator-5554" else 1)
            return self._reply("1" if args[-1] == "sys.boot_completed" else "fixture-proxy")
        with mock.patch.object(api.shutil, "which", return_value="fixture-adb"), mock.patch.object(api, "_run_quiet", side_effect=run):
            report = manager.health()
            self.assertEqual(sum(args[-1] == "devices" for args, _ in calls), 1)
            self.assertEqual(len(calls), 3)
            self.assertEqual(report["android"], {"state": "device", "boot_completed": True, "proxy": "fixture-proxy"})
            self.assertEqual(report["workers"][1]["android"]["state"], "offline")
            state["online"] = False; calls.clear()
            fresh = manager.health()
            self.assertEqual(fresh["android"]["state"], "offline")
            self.assertEqual(len(calls), 1)
            calls.clear(); api.adb_snapshot("emulator-5554")
            self.assertEqual(calls[0][0][-1], "get-state")

    def test_invalid_inventory_falls_back_and_proxy_timeout_keeps_device(self):
        for inventory in ("malformed", "List of devices attached\nemulator-5554\tdevice\nemulator-5554\toffline\n"):
            with self.subTest(inventory=inventory):
                manager = self._manager(); calls = []
                def run(args, timeout):
                    calls.append(args)
                    if args[-1] == "devices": return self._reply(inventory)
                    if args[-1] == "get-state": return self._reply("device", 0)
                    if args[-1] == "sys.boot_completed": return self._reply("1")
                    raise api.subprocess.TimeoutExpired(args, timeout)
                with mock.patch.object(api.shutil, "which", return_value="fixture-adb"), mock.patch.object(api, "_run_quiet", side_effect=run):
                    report = manager.health()
                self.assertEqual(sum(args[-1] == "devices" for args in calls), 1)
                self.assertEqual(sum(args[-1] == "get-state" for args in calls), 2)
                self.assertEqual(report["android"]["state"], "device")
                self.assertTrue(report["android"]["boot_completed"])
                self.assertEqual(report["android"]["proxy_error"], "TimeoutExpired")

    def test_failed_inventory_query_falls_back_without_changing_snapshot(self):
        for failure in ("exit", "os", "timeout"):
            with self.subTest(failure=failure):
                manager = self._manager(); calls = []
                def run(args, timeout):
                    calls.append(args)
                    if args[-1] == "devices":
                        if failure == "os": raise OSError("fixture inventory failure")
                        if failure == "timeout": raise api.subprocess.TimeoutExpired(args, timeout)
                        return self._reply("", 1)
                    if args[-1] == "get-state":
                        return self._reply("device", 0) if args[2] == "emulator-5554" else self._reply("", 1)
                    return self._reply("1" if args[-1] == "sys.boot_completed" else "fixture-proxy")
                with mock.patch.object(api.shutil, "which", return_value="fixture-adb"), \
                     mock.patch.object(api, "_run_quiet", side_effect=run):
                    report = manager.health()
                self.assertEqual(report["android"], {"state": "device", "boot_completed": True, "proxy": "fixture-proxy"})
                self.assertEqual(report["workers"][1]["android"]["state"], "offline")
                self.assertEqual(sum(args[-1] == "devices" for args in calls), 1)
                self.assertEqual(sum(args[-1] == "get-state" for args in calls), 2)

    def test_concurrent_health_requests_do_not_share_inventory_context(self):
        import queue
        manager = self._manager(); barrier = threading.Barrier(2)
        lock = threading.Lock(); calls = []; results = queue.Queue()
        def run(args, timeout):
            with lock: calls.append((threading.current_thread().name, args[-1]))
            if args[-1] == "devices":
                barrier.wait(timeout=3)
                return self._reply("List of devices attached\nemulator-5554\tdevice\n")
            if args[-1] == "get-state": return self._reply("device")
            return self._reply("1" if args[-1] == "sys.boot_completed" else "fixture-proxy")
        def read_health():
            try: results.put(manager.health())
            except Exception as exc: results.put(type(exc).__name__)
        threads = [threading.Thread(target=read_health, name=f"health-{i}", daemon=True) for i in range(2)]
        with mock.patch.object(api.shutil, "which", return_value="fixture-adb"), \
             mock.patch.object(api, "_run_quiet", side_effect=run):
            try:
                for thread in threads: thread.start()
            finally:
                for thread in threads:
                    if thread.ident is not None: thread.join(5)
            self.assertFalse(any(t.is_alive() for t in threads))
        first, second = results.get_nowait(), results.get_nowait()
        self.assertIsInstance(first, dict)
        self.assertEqual(first, second)
        self.assertEqual(sum(kind == "devices" for _, kind in calls), 2)
        self.assertEqual(len(calls), 6)

    def test_health_exception_cleans_context_without_probing_injected_provider(self):
        manager = self._manager()
        with mock.patch.object(api, "adb_snapshot", side_effect=ValueError("fixture provider failure")), \
             mock.patch.object(api, "_run_quiet") as run:
            with self.assertRaises(ValueError): manager.health()
            run.assert_not_called()
        with mock.patch.object(api.shutil, "which", return_value="fixture-adb"), \
             mock.patch.object(api, "_run_quiet", return_value=self._reply("", 1)) as run:
            api.adb_snapshot("emulator-5554")
            self.assertEqual(run.call_args.args[0][-1], "get-state")


if __name__ == "__main__":
    unittest.main()
