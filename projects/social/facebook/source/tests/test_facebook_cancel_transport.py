import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import facebook_capture_api as api


class CancelTransportCleanupTests(unittest.TestCase):
    def _manager(self, base: Path) -> api.JobManager:
        base_patcher = mock.patch.object(api, "BASE_DIR", base)
        base_patcher.start()
        self.addCleanup(base_patcher.stop)
        thread_patcher = mock.patch("facebook_capture_api.threading.Thread.start")
        thread_patcher.start()
        self.addCleanup(thread_patcher.stop)
        self._thread_patcher = thread_patcher
        worker = api.WorkerConfig(
            "worker-02", "emulator-5556", "fb-worker-02", 18082, 18767, True
        )
        return api.JobManager(workers=[worker], worker_config_path=base / "workers.json")

    @staticmethod
    def _job(job_id: str = "fb_cancel_transport") -> dict:
        return {
            "job_id": job_id,
            "status": "running",
            "created_at": api.utc_now(),
            "started_at": api.utc_now(),
            "finished_at": None,
            "cancel_requested_at": None,
            "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
            "result": None,
            "error": None,
            "run_dir": None,
            "worker_id": "worker-02",
            "serial": "emulator-5556",
            "process_pid": 4242,
            "process_creation_time": 123.0,
            "process_started_at": api.utc_now(),
            "process_state": "running",
            "transport_cleanup_state": "pending",
            "transport_cleanup_error": None,
        }

    @staticmethod
    def _macos_status(**overrides: object) -> dict:
        status = {
            "run_id": "capture-1",
            "exit_code": 143,
            "stop_reason": "SIGTERM",
            "pagination_outcome": None,
            "completion_scope": None,
            "source_post_id": "post-1",
            "attempts": 1,
            "completion_claim_allowed": False,
            "cleanup_errors": [],
            "native_scroll": "disabled: guarded ADB XML gestures; native-port reserved",
            "save_artifacts": ["progress", "safe_capture"],
            "partial_evidence_retained": True,
        }
        status.update(overrides)
        return status

    def test_cleanup_runs_only_after_process_tree_stop_is_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job()
            manager._jobs[job["job_id"]] = job
            process = mock.Mock()
            process.poll.return_value = None
            manager._running_processes[job["job_id"]] = process
            events = []

            def stop(_process):
                events.append("stop")
                return True

            def cleanup(_worker):
                events.append("cleanup")
                return {"transport_cleanup_state": "restored", "error": None}

            with mock.patch.object(api, "_terminate_tree", side_effect=stop), \
                 mock.patch.object(api, "_cleanup_transport_after_capture", side_effect=cleanup), \
                 mock.patch.object(api, "_process_identity_matches", return_value=True):
                self.assertTrue(manager._cleanup_job_process(job["job_id"]))

            self.assertEqual(events, ["stop", "cleanup"])
            self.assertEqual(job["transport_cleanup_state"], "restored")

    def test_external_proxy_change_is_reported_without_overwrite(self):
        worker = api.WorkerConfig("worker-02", "emulator-5556", "fb-worker-02", 18082, 18767, True)
        result = subprocess.CompletedProcess(
            ["powershell"],
            23,
            stdout=json.dumps({
                "transport_cleanup_state": "blocked-external-proxy",
                "current_proxy": "127.0.0.1:9999",
                "error": "Android proxy changed externally; left untouched",
            }),
            stderr="",
        )
        with mock.patch.object(api.sys, "platform", "win32"), \
             mock.patch("facebook_capture_api.shutil.which", return_value="powershell"), \
             mock.patch("facebook_capture_api._run_quiet", return_value=result) as run_quiet:
            cleanup = api._cleanup_transport_after_capture(worker)

        self.assertEqual(cleanup["transport_cleanup_state"], "blocked-external-proxy")
        self.assertEqual(cleanup["current_proxy"], "127.0.0.1:9999")
        self.assertEqual(cleanup["error"], "Android proxy changed externally; left untouched")
        run_quiet.assert_called_once()

    def test_cleanup_failure_preserves_error_and_blocks_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_cleanup_failed")
            manager._jobs[job["job_id"]] = job
            process = mock.Mock()
            process.poll.return_value = None
            manager._running_processes[job["job_id"]] = process

            failure = {
                "transport_cleanup_state": "failed",
                "error": "watchdog could not be started",
            }
            with mock.patch.object(api, "_terminate_tree", return_value=True), \
                 mock.patch.object(api, "_cleanup_transport_after_capture", return_value=failure), \
                 mock.patch.object(api, "_process_identity_matches", return_value=True):
                self.assertTrue(manager._cleanup_job_process(job["job_id"]))

            self.assertEqual(job["transport_cleanup_state"], "failed")
            self.assertEqual(job["transport_cleanup_error"], "watchdog could not be started")
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertIn("worker-02", manager._orphaned_worker_ids)
            self.assertEqual(job["process_state"], "stopped")

    def test_cleanup_unexpected_exception_is_persisted_and_blocks_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_cleanup_exception")
            manager._jobs[job["job_id"]] = job

            with mock.patch.object(api, "_cleanup_transport_after_capture", side_effect=RuntimeError("watchdog exploded")):
                self.assertFalse(manager._cleanup_job_transport(job["job_id"]))

            self.assertEqual(job["transport_cleanup_state"], "failed")
            self.assertEqual(job["transport_cleanup_error"], "RuntimeError: watchdog exploded")
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertEqual(manager._transport_cleanup_inflight, {})

    def test_concurrent_cancel_and_final_callback_run_one_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            self._thread_patcher.stop()
            job = self._job("fb_cleanup_once")
            manager._jobs[job["job_id"]] = job
            entered = threading.Event()
            release = threading.Event()
            calls = []
            results = []

            def cleanup(_worker):
                calls.append("cleanup")
                entered.set()
                self.assertTrue(release.wait(2))
                return {"transport_cleanup_state": "restored"}

            def run_cleanup():
                results.append(manager._cleanup_job_transport(job["job_id"]))

            first = threading.Thread(target=run_cleanup)
            second = threading.Thread(target=run_cleanup)
            with mock.patch.object(api.sys, "platform", "win32"), \
                 mock.patch.object(api, "_cleanup_transport_after_capture", side_effect=cleanup):
                first.start()
                self.assertTrue(entered.wait(2))
                second.start()
                release.set()
                first.join(3)
                second.join(3)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(calls, ["cleanup"])
            self.assertEqual(sorted(results), [True, True])
            self.assertEqual(job["transport_cleanup_state"], "restored")

    def test_normal_success_does_not_probe_transport_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            manager._queue.get = mock.Mock(side_effect=[job_id, KeyboardInterrupt])
            manager._queue.task_done = mock.Mock()

            with mock.patch.object(api, "run_capture_job", return_value={"ok": True}), \
                 mock.patch.object(api, "build_api_result", return_value={"ok": True, "completion_scope": "terminal-complete"}), \
                 mock.patch.object(api, "_cleanup_transport_after_capture") as cleanup:
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()

            cleanup.assert_not_called()
            self.assertEqual(manager.public_job(job_id)["status"], "completed")

    def test_failed_runner_releases_worker_for_next_job(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            first = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            second = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})
            manager._queue.get = mock.Mock(side_effect=[first["job_id"], second["job_id"], KeyboardInterrupt])
            manager._queue.task_done = mock.Mock()

            with mock.patch.object(manager, "_worker_failure_confirmed", return_value=False), \
                 mock.patch.object(api, "run_capture_job", side_effect=[api.ApiError("ordinary runner failure"), {"ok": True}]), \
                 mock.patch.object(api, "build_api_result", return_value={"ok": True, "completion_scope": "terminal-complete"}):
                with self.assertRaises(KeyboardInterrupt):
                    manager._worker_loop()

            self.assertEqual(manager.public_job(first["job_id"])["status"], "failed")
            self.assertEqual(manager.public_job(second["job_id"])["status"], "completed")
            self.assertFalse(manager._capture_lease_uncertain)

    def test_normal_success_callback_order_releases_lease_for_next_job(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            first = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            second = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})
            worker = manager.workers["worker-02"]
            self.assertTrue(manager._assign_job_to_worker(first["job_id"], worker))

            # Match run_capture_job's callback order: bootstrap first, then
            # the capture runner marker/process, then the runner's final
            # process callback before the worker persists success.
            bootstrap = mock.Mock()
            bootstrap.poll.return_value = 0
            capture = mock.Mock()
            capture.poll.return_value = 0
            capture.returncode = 0
            with mock.patch.object(api, "_cleanup_transport_after_capture") as cleanup:
                manager._set_running_process(first["job_id"], bootstrap)
                manager._set_running_process(first["job_id"], None)
                manager._mark_capture_runner_started(first["job_id"])
                manager._set_running_process(first["job_id"], capture)
                manager._set_running_process(first["job_id"], None)

                with manager._lock:
                    job = manager._jobs[first["job_id"]]
                    job["status"] = "completed"
                    job["finished_at"] = api.utc_now()
                    job["recovery_state"] = "completed-with-deduplicated-attempts"
                    job["transport_cleanup_state"] = api.TRANSPORT_CLEANUP_RUNNER_OWNED
                    job["transport_cleanup_error"] = None
                    manager._persist_job(job)

                manager._set_running_process(first["job_id"], None, final=True)

                self.assertEqual(manager.public_job(first["job_id"])["status"], "completed")
                self.assertFalse(manager._capture_lease_uncertain)
                manager._worker_runtime[worker.worker_id]["current_job_id"] = None
                manager._active_job_id = None
                self.assertTrue(manager._assign_job_to_worker(second["job_id"], worker))

            cleanup.assert_not_called()

    def test_no_windows_runner_keeps_cleanup_safe_without_adb_probe(self):
        worker = api.WorkerConfig("worker-02", "emulator-5556", "fb-worker-02", 18082, 18767, True)
        with mock.patch.object(api.sys, "platform", "darwin"), \
             mock.patch.object(api, "_run_quiet") as run_quiet:
            cleanup = api._cleanup_transport_after_capture(worker)

        self.assertEqual(cleanup["transport_cleanup_state"], "not-required")
        run_quiet.assert_not_called()

    def test_posix_termination_signals_session_process_group(self):
        process = mock.Mock()
        process.pid = 4244
        process.poll.side_effect = [None, 0]
        with mock.patch.object(api.os, "name", "posix"), \
             mock.patch.object(api.os, "getpgid", return_value=4244, create=True) as getpgid, \
             mock.patch.object(api.os, "killpg", create=True) as killpg, \
             mock.patch.object(api.time, "monotonic", side_effect=[0.0, 0.1]):
            self.assertTrue(api._terminate_tree(process))

        getpgid.assert_called_once_with(4244)
        killpg.assert_called_once_with(4244, api.signal.SIGTERM)
        process.terminate.assert_not_called()

    def test_terminate_tree_poll_oserror_keeps_stop_unverified(self):
        process = mock.Mock()
        process.pid = 4245
        process.poll.side_effect = [None, OSError("poll unavailable")]
        with mock.patch.object(api.os, "name", "posix"), \
             mock.patch.object(api.os, "getpgid", return_value=4245, create=True), \
             mock.patch.object(api.os, "killpg", create=True), \
             mock.patch.object(api.time, "monotonic", side_effect=[0.0, 3.0]):
            self.assertFalse(api._terminate_tree(process))

    def test_terminate_known_pid_permission_error_keeps_stop_unverified(self):
        with mock.patch.object(api, "_process_identity_state", return_value="match"), \
             mock.patch.object(api.os, "name", "posix"), \
             mock.patch.object(api.os, "getpgid", return_value=4246, create=True), \
             mock.patch.object(api.os, "killpg", side_effect=PermissionError("denied"), create=True):
            self.assertFalse(api._terminate_known_pid(4246, 123.0))

    def test_terminate_known_pid_process_lookup_error_is_confirmed_stopped(self):
        with mock.patch.object(api, "_process_identity_state", return_value="match"), \
             mock.patch.object(api.os, "name", "posix"), \
             mock.patch.object(api.os, "getpgid", return_value=4247, create=True), \
             mock.patch.object(api.os, "killpg", side_effect=ProcessLookupError("gone"), create=True):
            self.assertTrue(api._terminate_known_pid(4247, 123.0))

    def test_macos_cleanup_requires_runner_finally_status(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_macos_cleanup")
            attempt = base / "runs" / "attempt-001"
            attempt.mkdir(parents=True)
            job["capture_run_dir"] = str(attempt)
            job["attempt_dirs"] = [str(attempt)]
            manager._jobs[job["job_id"]] = job

            with mock.patch.object(api.sys, "platform", "darwin"):
                self.assertFalse(manager._cleanup_job_transport(job["job_id"]))
            self.assertEqual(job["transport_cleanup_state"], "failed")
            self.assertIn("status is missing", job["transport_cleanup_error"])
            self.assertTrue(manager._capture_lease_uncertain)

            job["transport_cleanup_state"] = "pending"
            job["transport_cleanup_error"] = None
            job["process_pid"] = None
            job["process_creation_time"] = None
            job["process_state"] = "stopped"
            (attempt / "android-capture-status.json").write_text(
                json.dumps(self._macos_status()), encoding="utf-8"
            )
            with mock.patch.object(api.sys, "platform", "darwin"):
                self.assertTrue(manager._cleanup_job_transport(job["job_id"]))
            self.assertEqual(job["transport_cleanup_state"], api.TRANSPORT_CLEANUP_RUNNER_OWNED)
            self.assertFalse(manager._capture_lease_uncertain)

    def test_macos_cleanup_rejects_incomplete_runner_status_schema(self):
        for field in ("cleanup_errors", "stop_reason", "save_artifacts"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                manager = self._manager(base)
                job = self._job(f"fb_macos_incomplete_{field}")
                attempt = base / "runs" / "attempt-001"
                attempt.mkdir(parents=True)
                job["capture_run_dir"] = str(attempt)
                job["attempt_dirs"] = [str(attempt)]
                manager._jobs[job["job_id"]] = job
                status = self._macos_status()
                status.pop(field)
                (attempt / "android-capture-status.json").write_text(
                    json.dumps(status), encoding="utf-8"
                )

                with mock.patch.object(api.sys, "platform", "darwin"):
                    self.assertFalse(manager._cleanup_job_transport(job["job_id"]))
                self.assertEqual(job["transport_cleanup_state"], api.TRANSPORT_CLEANUP_FAILED)
                self.assertIn("status", job["transport_cleanup_error"])
                self.assertTrue(manager._capture_lease_uncertain)

    def test_macos_cleanup_rejects_null_or_non_list_cleanup_errors(self):
        for value in (None, "cleanup failed"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                manager = self._manager(base)
                job = self._job(f"fb_macos_cleanup_errors_{value is None}")
                attempt = base / "runs" / "attempt-001"
                attempt.mkdir(parents=True)
                job["capture_run_dir"] = str(attempt)
                job["attempt_dirs"] = [str(attempt)]
                manager._jobs[job["job_id"]] = job
                (attempt / "android-capture-status.json").write_text(
                    json.dumps(self._macos_status(cleanup_errors=value)), encoding="utf-8"
                )

                with mock.patch.object(api.sys, "platform", "darwin"):
                    self.assertFalse(manager._cleanup_job_transport(job["job_id"]))
                self.assertEqual(job["transport_cleanup_state"], api.TRANSPORT_CLEANUP_FAILED)
                self.assertIn("cleanup_errors", job["transport_cleanup_error"])
                self.assertTrue(manager._capture_lease_uncertain)

    def test_cleanup_none_result_is_persisted_as_blocked_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_cleanup_none")
            manager._jobs[job["job_id"]] = job

            with mock.patch.object(api.sys, "platform", "win32"), \
                 mock.patch.object(api, "_cleanup_transport_after_capture", return_value=None):
                self.assertFalse(manager._cleanup_job_transport(job["job_id"]))

            self.assertEqual(job["transport_cleanup_state"], api.TRANSPORT_CLEANUP_FAILED)
            self.assertIn("no result", job["transport_cleanup_error"])
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertEqual(manager._transport_cleanup_inflight, {})

    def test_cleanup_success_state_with_error_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_cleanup_conflicting_success")
            manager._jobs[job["job_id"]] = job

            with mock.patch.object(api.sys, "platform", "win32"), \
                 mock.patch.object(
                     api,
                     "_cleanup_transport_after_capture",
                     return_value={
                         "transport_cleanup_state": api.TRANSPORT_CLEANUP_RESTORED,
                         "transport_cleanup_error": "restore was not verified",
                     },
                 ):
                self.assertFalse(manager._cleanup_job_transport(job["job_id"]))

            self.assertEqual(job["transport_cleanup_state"], api.TRANSPORT_CLEANUP_FAILED)
            self.assertEqual(job["transport_cleanup_error"], "restore was not verified")
            self.assertTrue(manager._capture_lease_uncertain)

    def test_linux_not_required_cleanup_releases_pending_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_linux_cleanup")
            manager._jobs[job["job_id"]] = job

            with mock.patch.object(api.sys, "platform", "linux"):
                self.assertTrue(manager._cleanup_job_transport(job["job_id"]))

            self.assertEqual(job["transport_cleanup_state"], api.TRANSPORT_CLEANUP_NOT_REQUIRED)
            self.assertIsNone(job["transport_cleanup_error"])
            self.assertFalse(manager._capture_lease_uncertain)

    def test_failed_job_pending_cleanup_retries_after_stopped_process_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_failed_cleanup_retry")
            job.update(
                status="failed",
                finished_at=api.utc_now(),
                error="capture command failed",
                process_pid=None,
                process_creation_time=None,
                process_state="stopped",
                transport_cleanup_state=api.TRANSPORT_CLEANUP_FAILED,
                transport_cleanup_error="initial restore failed",
            )
            manager._jobs[job["job_id"]] = job
            with manager._lock:
                manager._block_capture_lease_locked(job["job_id"], "cleanup pending")
                manager._failed_worker_ids.add("worker-02")
                manager._orphaned_worker_ids.add("worker-02")
                manager._active_worker_id = None

            with mock.patch.object(api, "_cleanup_transport_after_capture", return_value={
                "transport_cleanup_state": api.TRANSPORT_CLEANUP_RESTORED,
                "transport_cleanup_error": None,
            }) as cleanup:
                response = manager.cancel(job["job_id"])

            cleanup.assert_called_once()
            self.assertFalse(response["cancelled"])
            self.assertEqual(response["job"]["status"], "failed")
            self.assertEqual(response["job"]["error"], "capture command failed")
            self.assertEqual(response["job"]["transport_cleanup_state"], api.TRANSPORT_CLEANUP_RESTORED)
            self.assertFalse(manager._capture_lease_uncertain)
            self.assertNotIn("worker-02", manager._failed_worker_ids)
            self.assertNotIn("worker-02", manager._orphaned_worker_ids)
            self.assertEqual(manager._active_worker_id, "worker-02")

    def test_final_callback_preserves_stopped_failed_job_for_cleanup_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_failed_final_callback_cleanup_retry")
            job.update(
                status="failed",
                finished_at=api.utc_now(),
                error="capture command failed",
                # Zero is an invalid PID and must be treated as no persisted
                # process identity by the final callback.
                process_pid=0,
                process_creation_time=None,
                process_state="stopped",
                transport_cleanup_state=api.TRANSPORT_CLEANUP_FAILED,
                transport_cleanup_error="initial restore failed",
            )
            manager._jobs[job["job_id"]] = job
            with manager._lock:
                manager._block_capture_lease_locked(job["job_id"], "cleanup pending")

            manager._set_running_process(job["job_id"], None, final=True)
            self.assertEqual(job["process_state"], "stopped")

            with mock.patch.object(api, "_cleanup_transport_after_capture", return_value={
                "transport_cleanup_state": api.TRANSPORT_CLEANUP_RESTORED,
                "transport_cleanup_error": None,
            }) as cleanup:
                response = manager.cancel(job["job_id"])

            cleanup.assert_called_once()
            self.assertFalse(response["cancelled"])
            self.assertEqual(response["job"]["status"], "failed")
            self.assertEqual(response["job"]["error"], "capture command failed")
            self.assertEqual(response["job"]["process_state"], "stopped")
            self.assertEqual(response["job"]["transport_cleanup_state"], api.TRANSPORT_CLEANUP_RESTORED)
            self.assertFalse(manager._capture_lease_uncertain)

    def test_final_callback_preserves_stopped_cancelled_job_for_cleanup_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_cancelled_final_callback_cleanup_retry")
            job.update(
                status="cancelled",
                finished_at=api.utc_now(),
                error=None,
                # Zero is an invalid PID and must be treated as no persisted
                # process identity by the final callback.
                process_pid=0,
                process_creation_time=None,
                process_state="stopped",
                transport_cleanup_state=api.TRANSPORT_CLEANUP_FAILED,
                transport_cleanup_error="initial restore failed",
            )
            manager._jobs[job["job_id"]] = job
            with manager._lock:
                manager._block_capture_lease_locked(job["job_id"], "cleanup pending")

            manager._set_running_process(job["job_id"], None, final=True)
            self.assertEqual(job["process_state"], "stopped")

            with mock.patch.object(api, "_cleanup_transport_after_capture", return_value={
                "transport_cleanup_state": api.TRANSPORT_CLEANUP_RESTORED,
                "transport_cleanup_error": None,
            }) as cleanup:
                response = manager.cancel(job["job_id"])

            cleanup.assert_called_once()
            self.assertFalse(response["cancelled"])
            self.assertEqual(response["job"]["status"], "cancelled")
            self.assertIsNone(response["job"]["error"])
            self.assertEqual(response["job"]["process_state"], "stopped")
            self.assertEqual(response["job"]["transport_cleanup_state"], api.TRANSPORT_CLEANUP_RESTORED)
            self.assertFalse(manager._capture_lease_uncertain)

    def test_final_callback_without_stop_proof_keeps_cleanup_retry_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_final_callback_without_stop_proof")
            job.update(
                status="cancelled",
                finished_at=api.utc_now(),
                error=None,
                process_pid=None,
                process_creation_time=None,
                process_state="cancelling",
                transport_cleanup_state=api.TRANSPORT_CLEANUP_FAILED,
                transport_cleanup_error="initial restore failed",
            )
            manager._jobs[job["job_id"]] = job
            with manager._lock:
                manager._block_capture_lease_locked(job["job_id"], "cleanup pending")

            manager._set_running_process(job["job_id"], None, final=True)

            with mock.patch.object(api, "_cleanup_transport_after_capture") as cleanup:
                response = manager.cancel(job["job_id"])

            cleanup.assert_not_called()
            self.assertFalse(response["cancelled"])
            self.assertEqual(response["job"]["status"], "cancelled")
            self.assertEqual(response["job"]["process_state"], "stop-unverified")
            self.assertTrue(manager._capture_lease_uncertain)

    def test_failed_job_unverified_process_does_not_retry_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_failed_cleanup_blocked")
            job.update(
                status="failed",
                finished_at=api.utc_now(),
                error="capture command failed",
                transport_cleanup_state=api.TRANSPORT_CLEANUP_FAILED,
                transport_cleanup_error="initial restore failed",
            )
            manager._jobs[job["job_id"]] = job
            process = mock.Mock()
            process.poll.return_value = None
            manager._running_processes[job["job_id"]] = process
            with manager._lock:
                manager._block_capture_lease_locked(job["job_id"], "cleanup pending")

            with mock.patch.object(api, "_terminate_tree", return_value=False), \
                 mock.patch.object(api, "_cleanup_transport_after_capture") as cleanup:
                response = manager.cancel(job["job_id"])

            cleanup.assert_not_called()
            self.assertFalse(response["cancelled"])
            self.assertEqual(response["job"]["status"], "failed")
            self.assertEqual(response["job"]["error"], "capture command failed")
            self.assertEqual(response["job"]["process_state"], "stop-unverified")
            self.assertTrue(manager._capture_lease_uncertain)

    def test_failed_job_without_pending_cleanup_remains_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_failed_noop")
            job.update(
                status="failed",
                finished_at=api.utc_now(),
                error="capture command failed",
                process_pid=None,
                process_creation_time=None,
                process_state="stopped",
                transport_cleanup_state=api.TRANSPORT_CLEANUP_RESTORED,
                transport_cleanup_error=None,
            )
            manager._jobs[job["job_id"]] = job

            with mock.patch.object(api, "_cleanup_transport_after_capture") as cleanup:
                response = manager.cancel(job["job_id"])

            cleanup.assert_not_called()
            self.assertFalse(response["cancelled"])
            self.assertIn("already failed", response["reason"])
            self.assertEqual(response["job"]["status"], "failed")
            self.assertEqual(response["job"]["error"], "capture command failed")

    def test_macos_cleanup_does_not_accept_older_attempt_status(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = self._job("fb_macos_stale_cleanup")
            old_attempt = base / "runs" / "attempt-001"
            current_attempt = base / "runs" / "attempt-002"
            old_attempt.mkdir(parents=True)
            current_attempt.mkdir(parents=True)
            (old_attempt / "android-capture-status.json").write_text(
                json.dumps({"run_id": "old", "exit_code": 143, "cleanup_errors": []}), encoding="utf-8"
            )
            job["capture_run_dir"] = str(current_attempt)
            job["attempt_dirs"] = [str(old_attempt), str(current_attempt)]
            manager._jobs[job["job_id"]] = job

            with mock.patch.object(api.sys, "platform", "darwin"):
                self.assertFalse(manager._cleanup_job_transport(job["job_id"]))
            self.assertEqual(job["transport_cleanup_state"], "failed")
            self.assertIn("status is missing", job["transport_cleanup_error"])


if __name__ == "__main__":
    unittest.main()
