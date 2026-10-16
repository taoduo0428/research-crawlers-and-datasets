import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
QUEUE_SCRIPT = (BASE / "run_facebook_post_queue.ps1").read_text(encoding="utf-8-sig")
RUNTIME = (BASE / "facebook_runtime.ps1").read_text(encoding="utf-8-sig")
RELEASE = (BASE / "run_facebook_release_gate.ps1").read_text(encoding="utf-8-sig")
BATCH = (BASE / "run_facebook_batch.ps1").read_text(encoding="utf-8-sig")


class FacebookPostQueueTests(unittest.TestCase):
    def test_queue_requires_strict_post_id_and_url(self):
        self.assertIn("Every queue item requires post_id and post_url", QUEUE_SCRIPT)
        self.assertIn("'-PostUrl', $PostUrl", QUEUE_SCRIPT)
        self.assertIn("'-PostId', $PostId", QUEUE_SCRIPT)
        self.assertIn("Duplicate post_id in queue", QUEUE_SCRIPT)

    def test_terminal_checkpoint_is_skipped_before_probe(self):
        skip = QUEUE_SCRIPT.index("skip terminal checkpoint")
        probe = QUEUE_SCRIPT.index("Invoke-QueueCapture -Kind 'probe'", skip)
        self.assertLess(skip, probe)
        self.assertIn("Test-Path ([string]$task.terminal_run_dir)", QUEUE_SCRIPT)

    def test_fresh_probe_gates_every_unfinished_deep_capture(self):
        probe = QUEUE_SCRIPT.index("Invoke-QueueCapture -Kind 'probe'")
        inspect_probe = QUEUE_SCRIPT.index("Invoke-InspectorJson -Stage 'probe'", probe)
        full = QUEUE_SCRIPT.index("Invoke-QueueCapture -Kind 'full'", inspect_probe)
        self.assertLess(probe, inspect_probe)
        self.assertLess(inspect_probe, full)
        self.assertIn("if (-not [bool]$probe.passed)", QUEUE_SCRIPT)
        self.assertIn("$task.status = 'blocked-live'", QUEUE_SCRIPT)

    def test_only_terminal_full_run_becomes_checkpoint(self):
        inspect_full = QUEUE_SCRIPT.index("Invoke-InspectorJson -Stage 'full'")
        fail_branch = QUEUE_SCRIPT.index("if (-not [bool]$full.passed)", inspect_full)
        checkpoint = QUEUE_SCRIPT.index("$task.status = 'terminal-complete'", fail_branch)
        self.assertLess(inspect_full, fail_branch)
        self.assertLess(fail_branch, checkpoint)
        self.assertIn("$task.terminal_run_dir = [string]$fullCapture.run_dir", QUEUE_SCRIPT)

    def test_pressure_block_pauses_queue_instead_of_continuing(self):
        self.assertIn("facebook_batch_pressure.py", QUEUE_SCRIPT)
        self.assertIn("[string]$pressure.decision -eq 'stop-batch'", QUEUE_SCRIPT)
        self.assertIn("$script:QueueState.status = 'blocked-live'", QUEUE_SCRIPT)
        self.assertIn("$script:QueueState.status = 'paused-incomplete'", QUEUE_SCRIPT)

    def test_release_batch_and_queue_share_one_live_capture_lock(self):
        self.assertIn("facebook-live-capture.lock", RUNTIME)
        self.assertIn("Acquire-FacebookLiveCaptureLock", RELEASE)
        self.assertIn("-Owner 'release-gate'", RELEASE)
        self.assertIn("Acquire-FacebookLiveCaptureLock", BATCH)
        self.assertIn("-Owner 'batch'", BATCH)
        self.assertIn("Acquire-FacebookLiveCaptureLock", QUEUE_SCRIPT)
        self.assertIn("-Owner 'post-queue'", QUEUE_SCRIPT)
        self.assertIn("Release-FacebookLiveCaptureLock", RELEASE)
        self.assertIn("Release-FacebookLiveCaptureLock", BATCH)
        self.assertIn("Release-FacebookLiveCaptureLock", QUEUE_SCRIPT)

    def test_queue_uses_shared_python_resolver(self):
        self.assertIn("Resolve-FacebookPythonExe -BasePath $Base", QUEUE_SCRIPT)
        self.assertIn("Invoke-FacebookPythonCaptured", QUEUE_SCRIPT)
        self.assertNotIn("& py", QUEUE_SCRIPT)
        self.assertNotIn("& python", QUEUE_SCRIPT)


if __name__ == "__main__":
    unittest.main()
