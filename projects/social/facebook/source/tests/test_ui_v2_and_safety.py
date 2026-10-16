#!/usr/bin/env python3
"""Regression tests for the v2 UI, batch selection, and Windows startup."""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class WebV2IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A shared CI host may already use 8769 or configure a system proxy.
        # Reserve a loopback port and keep these local requests off that proxy.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.test_port = probe.getsockname()[1]
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        cls.server_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "start_ui_v2.py"), "--port", str(cls.test_port), "--no-browser"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Poll the child and preserve its diagnostics if startup fails.
        url = f"http://127.0.0.1:{cls.test_port}/"
        ready = False
        for _ in range(30):
            time.sleep(0.2)
            if cls.server_proc.poll() is not None:
                break
            try:
                with cls.opener.open(url, timeout=1) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                pass
        if not ready:
            cls.server_proc.terminate()
            try:
                stdout, stderr = cls.server_proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                cls.server_proc.kill()
                stdout, stderr = cls.server_proc.communicate(timeout=3)
            raise RuntimeError("Test UI v2 server failed to start: " +
                               (stdout + stderr).decode("utf-8", errors="replace")[-4000:])

    @classmethod
    def tearDownClass(cls):
        if cls.server_proc:
            cls.server_proc.terminate()
            try:
                cls.server_proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                cls.server_proc.kill()
                cls.server_proc.communicate(timeout=3)

    def test_serves_index_html(self):
        url = f"http://127.0.0.1:{self.test_port}/"
        with self.opener.open(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            content_type = resp.headers.get("Content-Type", "")
            self.assertIn("text/html", content_type)
            body = resp.read().decode("utf-8")
            self.assertIn("OmniCapture Console v2", body)
            self.assertIn("01 / BATCH INGEST", body)
            self.assertIn("02 / SWARM MATRIX", body)
            self.assertIn("03 / STAGE PIPELINE", body)
            self.assertIn("04 / LIVE TELEMETRY", body)
            self.assertIn("05 / ARTIFACTS VAULT", body)

    def test_serves_styles_css(self):
        url = f"http://127.0.0.1:{self.test_port}/styles.css"
        with self.opener.open(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            content_type = resp.headers.get("Content-Type", "")
            self.assertIn("text/css", content_type)
            body = resp.read().decode("utf-8")
            self.assertIn("theme-dark", body)
            self.assertIn("kpi-ribbon", body)
            self.assertIn("stage-pulse", body)

    def test_serves_app_js(self):
        url = f"http://127.0.0.1:{self.test_port}/app.js"
        with self.opener.open(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            content_type = resp.headers.get("Content-Type", "")
            self.assertTrue("javascript" in content_type or "text/" in content_type)
            body = resp.read().decode("utf-8")
            self.assertIn("OmniCapture Console v2", body)
            self.assertIn("drawNetworkGraph", body)
            self.assertIn("renderBatchMatrix", body)

    def test_proxies_health_or_returns_graceful_503(self):
        url = f"http://127.0.0.1:{self.test_port}/health"
        try:
            with self.opener.open(url, timeout=5) as resp:
                self.assertIn(resp.status, (200, 503))
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, 503)
            body = err.read().decode("utf-8")
            self.assertIn("Backend API on port 8765 is not reachable", body)


class WebV2BatchSelectionTests(unittest.TestCase):
    def test_empty_list_outcomes_use_verified_results_and_preserve_other_states(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required for UI behavior fixture")
        script = r'''
const fs = require('fs');
const assert = require('assert/strict');
const source = fs.readFileSync('web_v2/app.js', 'utf8');
const helpers = source.slice(source.indexOf('const resultOutcomeCache'), source.indexOf('// API Call Wrapper'));
const result = {target_verification:{status:'matched'}, reaction_users:[],
  completeness:{reactions:{terminal_page_seen:true, completion_claim_allowed:true, successful_page_count:2, reported_total_count:946}},
  visibility:{reactions:{status:'query-triggered-friends-only-visibility', visibility_hint:'friends-only-names'}}};
let calls = 0;
async function api() { calls++; return structuredClone(result); }
eval(helpers + `
(async () => {
  const batch = {posts:[
    {job_id:'private',status:'completed',valid_users:0},
    {job_id:'group',status:'completed',valid_users:27},
    {job_id:'failed',status:'failed',valid_users:0},
    {job_id:'running',status:'running',valid_users:0}]};
  await enrichResultOutcomes('/v1/batches/test/progress', batch);
  assert.equal(batch.posts[0].result_outcome.reason, 'friends-only-names');
  for (const post of batch.posts.slice(1)) assert.equal(post.result_outcome, undefined);
  const progress = {job_status:'completed',metrics:{reaction_users:0},stages:[{id:'complete',state:'complete'}]};
  await enrichResultOutcomes('/v1/jobs/private/progress', progress);
  assert.equal(progress.stages[0].state, 'warning');
  assert.equal(calls, 1);
  for (const mutate of [
    r => r.target_verification.status = 'not-matched',
    r => r.reaction_users.push({id:'1'}),
    r => r.completeness.reactions.terminal_page_seen = false,
    r => r.completeness.reactions.rate_limited_records = 1,
    r => r.completeness.reactions.completion_claim_allowed = false,
    r => r.visibility.reactions = {}]) {
    const changed = structuredClone(result); mutate(changed);
    assert.equal(emptyListOutcome(changed), null);
  }
  const zero = structuredClone(result);
  zero.visibility.reactions = {};
  zero.completeness.reactions.reported_total_count = 0;
  assert.equal(emptyListOutcome(zero).reason, 'zero-reactions');
})().catch(error => { console.error(error); process.exitCode = 1; });
`);
'''
        result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_startup_restores_latest_batch_without_jumping_active_selection(self):
        source = (ROOT / "web_v2" / "app.js").read_text(encoding="utf-8")
        self.assertIn('latest = await api("/v1/batches/latest")', source)
        self.assertIn("await restoreLatestBatch();", source)
        self.assertIn('const ACTIVE_BATCH_STATES = new Set(["running", "queued", "cancelling"]);', source)
        self.assertIn('if (ACTIVE_BATCH_STATES.has(latest.status) || !state.batchId)', source)
        self.assertIn('localStorage.setItem("omniCaptureBatchId", state.batchId);', source)
        self.assertIn("if (currentBatch && !TERMINAL_BATCH_STATES.has(currentBatch.status)) return;", source)

    def test_terminal_or_missing_batch_rechecks_latest_during_polling(self):
        source = (ROOT / "web_v2" / "app.js").read_text(encoding="utf-8")
        self.assertIn("await adoptLatestBatchIfCurrentUnavailable(batch);", source)
        self.assertIn("await adoptLatestBatchIfCurrentUnavailable();", source)


class WindowsOneClickRuntimeContractTests(unittest.TestCase):
    def test_legacy_start_cmd_delegates_to_api_and_emulator_entrypoint(self):
        source = (ROOT / "START.cmd").read_text(encoding="utf-8")
        lowered = source.lower()
        self.assertIn('set "facebook_root=%~dp0"', lowered)
        self.assertIn('call "%facebook_root%start_api_and_emulator.cmd" %*', lowered)
        self.assertIn('set "exit_code=%errorlevel%"', lowered)
        self.assertIn("exit /b %exit_code%", lowered)
        self.assertNotIn("setup.ps1", lowered)
        self.assertNotIn("start_facebook_capture_ui.ps1", lowered)

    def test_cmd_delegates_to_powershell_orchestrator_without_starting_a_batch(self):
        source = (ROOT / "START_API_AND_EMULATOR.cmd").read_text(encoding="utf-8")
        self.assertIn("start_api_and_emulator.ps1", source)
        self.assertIn("-NoProfile", source)
        self.assertIn("-ExecutionPolicy Bypass", source)
        self.assertIn("-File", source)
        self.assertIn("%~dp0", source)
        self.assertIn("pause", source.lower())
        self.assertIn("exit /b %exit_code%", source.lower())
        self.assertNotIn("/v1/jobs", source.lower())
        self.assertNotIn("/v1/batches", source.lower())
        self.assertNotIn("stop_facebook", source.lower())

    def test_orchestrator_reads_local_and_worker_configuration(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("facebook.local.json", source)
        self.assertIn("facebook_workers.json", source)
        self.assertIn("ConvertFrom-Json", source)
        self.assertIn("enabled", source)
        self.assertIn("workers", source)

    def test_orchestrator_initializes_dependencies_before_live_checks(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("setup.ps1", source)
        self.assertIn("-InstallOnly", source)

    def test_orchestrator_reuses_services_and_only_starts_missing_components(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-FacebookAdbState", source)
        self.assertIn("/health", source)
        self.assertIn("start_facebook_stable.ps1", source)
        self.assertIn("start_facebook_capture_api.ps1", source)
        self.assertIn("Start-Process", source)
        self.assertIn("-WindowStyle", source)
        self.assertIn("Hidden", source)
        self.assertIn("-Serial", source)
        self.assertIn("-AvdName", source)
        self.assertIn("-CapturePort", source)
        self.assertIn("-NativePort", source)

    def test_orchestrator_routes_session_zero_emulator_start_to_interactive_session(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("EmulatorBootTimeoutSeconds = 240", source)
        self.assertIn("GetCurrentProcess().SessionId", source)
        self.assertIn("if ($currentSessionId -eq 0)", source)
        self.assertIn("launch_visible_in_user_session.ps1", source)
        self.assertIn("-WorkerId $workerId", source)
        self.assertIn("Interactive-session emulator did not become ADB-ready", source)
        self.assertIn("-BootTimeoutSeconds', [string]$EmulatorBootTimeoutSeconds", source)

    def test_visible_launcher_only_reuses_interactive_qemu_and_guards_stale_session_zero_cleanup(self):
        source = (ROOT / "launch_visible_in_user_session.ps1").read_text(encoding="utf-8")
        self.assertIn("$EmulatorPort", source)
        self.assertIn("$interactiveEmulators", source)
        self.assertIn("SessionId -ne 0", source)
        self.assertIn("$sessionZeroEmulators", source)
        self.assertIn("Get-FacebookAdbState -Serial $Serial", source)
        self.assertIn("Get-FacebookCaptureMarker -Serial $Serial", source)
        self.assertIn("identity changed before cleanup", source)
        self.assertIn("Stop-Process -Id $stalePid -Force", source)
        self.assertIn("refusing ambiguous cleanup", source)
        self.assertIn("CreateProcessWithTokenW", source)
        self.assertNotIn("CreateProcessAsUserW", source)
        self.assertIn("ProcessIdToSessionId", source)
        self.assertIn("launched_session=", source)
        self.assertIn("if ($currentSessionId -ne 0)", source)
        self.assertIn("/d /s /c", source.lower())
        self.assertIn("$si.lpDesktop = 'winsta0\\default'", source)
        self.assertNotIn("$si.lpDesktop = 'winsta0\\\\default'", source)

    def test_orchestrator_starts_or_reuses_v2_server_without_opening_browser(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("start_ui_v2.ps1", source)
        self.assertIn("-UiPort", source)
        self.assertIn("-ApiPort", source)
        self.assertIn("-NoBrowser", source)
        self.assertIn("8766", source)
        self.assertIn("-WindowStyle", source)
        self.assertIn("Hidden", source)

    def test_orchestrator_reuses_only_the_project_ui_on_8766(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-TcpPort -Port $uiPort", source)
        self.assertIn("Invoke-WebRequest", source)
        self.assertIn("http://127.0.0.1:8766/", source)
        self.assertIn("Port $uiPort is occupied", source)

    def test_queued_jobs_do_not_block_failed_worker_recovery(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertNotIn("[int]$queued -gt 0", source)
        self.assertIn("current_job_id", source)
        self.assertIn("worker", source)

    def test_orchestrator_clears_failed_worker_only_when_no_capture_is_running(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("/v1/workers/", source)
        self.assertIn("/enable", source)
        self.assertIn("current_job_id", source)
        self.assertIn("failed", source)
        self.assertIn("Invoke-RestMethod", source)

    def test_orchestrator_has_no_batch_mutation_or_delete_operations(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8").lower()
        self.assertNotIn("/v1/jobs", source)
        self.assertNotIn("/v1/batches", source)
        self.assertNotIn("remove-item", source)
        self.assertIn("http://127.0.0.1:8766/", source)


if __name__ == "__main__":
    unittest.main()
