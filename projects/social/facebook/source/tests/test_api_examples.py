"""Exercise shipped clients against real API routing without using a device."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import facebook_capture_api as api
from tests.test_facebook_api_client import SequenceManager, running_capture_server

ROOT = Path(__file__).resolve().parents[1]


class ExampleManager(SequenceManager):
    def __init__(self, statuses, artifact, empty=False):
        super().__init__(statuses, artifact)
        self.empty = empty
        self.submits = 0
        self.progress_calls = 0
        self.result_calls = 0

    def health(self):
        return {"service": "ready", "mode": "primary-standby-serial"}

    def submit(self, payload):
        api.normalize_request(payload)
        self.submits += 1
        return super().submit(payload)

    def progress(self, job_id):
        self.progress_calls += 1
        return {"job_id": job_id, "status": "running", "metrics": {}}

    def result(self, job_id):
        self.result_calls += 1
        result = super().result(job_id)
        if self.empty:
            result.update(reaction_users=[], result_outcome={"code": "empty-lists", "reason": "friends-only-names"})
        return result


class ApiExampleTests(unittest.TestCase):
    def client_command(self, name, base, output, *, resume=False, invalid=False, wait_seconds=15):
        url = "https://example.org/not-facebook" if invalid else "https://www.facebook.com/reel/1054985397317048"
        source = ["--job-id", "fb_resume"] if resume else ["--url", url]
        env = os.environ.copy()
        if name in {"python", "node"}:
            executable = sys.executable if name == "python" else shutil.which("node")
            if not executable:
                self.skipTest("Node unavailable")
            script = "capture_requests.py" if name == "python" else "capture_fetch.mjs"
            return [executable, str(ROOT / "examples" / script), *source, "--base-url", base,
                    "--output", str(output), "--timeout", str(wait_seconds)], env
        if name == "powershell":
            executable = shutil.which("powershell") or shutil.which("pwsh")
            if not executable:
                self.skipTest("PowerShell unavailable")
            source = ["-JobId", "fb_resume"] if resume else ["-Url", url]
            return [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                    str(ROOT / "examples/capture.ps1"), *source, "-BaseUrl", base,
                    "-OutputPath", str(output), "-TimeoutSeconds", str(wait_seconds)], env
        git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        executable = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
        if not executable or not shutil.which("curl"):
            self.skipTest("Bash/curl unavailable")
        env.update(PYTHON=Path(sys.executable).as_posix(), FACEBOOK_API_BASE=base, WAIT_SECONDS=str(wait_seconds))
        source = ["--job-id", "fb_resume"] if resume else [url]
        return [executable, (ROOT / "examples/capture_curl.sh").as_posix(), *source, output.as_posix()], env

    def exercise(self, name, mode):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.csv"
            data = b"user_id,name\n" if mode == "empty" else b"user_id,name\nu1,Alice\n"
            fixture.write_bytes(data)
            output = Path(directory) / "download.csv"
            states = [{"status": "completed"}]
            if mode == "success":
                states.insert(0, {"status": "running"})
            elif mode in {"failed", "cancelled", "unknown", "timeout"}:
                states = [{"status": "running" if mode == "timeout" else mode, "error": "fixture"}]
            manager = ExampleManager(states, fixture, empty=mode == "empty")
            with running_capture_server(manager) as base:
                # Only the timeout test needs a one-second deadline. Interpreter
                # startup for curl's JSON reader can itself take a second on CI.
                command, env = self.client_command(name, base, output,
                    resume=mode not in {"success", "invalid"}, invalid=mode == "invalid",
                    wait_seconds=1 if mode == "timeout" else 15)
                result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=30)
            message = (result.stdout + result.stderr).decode("utf-8", errors="replace")[-2500:]
            if mode in {"success", "empty"}:
                self.assertEqual(result.returncode, 0, message)
                self.assertEqual(output.read_bytes(), data)
                self.assertEqual(manager.result_calls, 1)
                if mode == "success":
                    self.assertEqual(manager.progress_calls, 1)
                else:
                    self.assertIn("friends-only-names", message)
            else:
                self.assertNotEqual(result.returncode, 0, message)
                self.assertFalse(output.exists())
                self.assertEqual(manager.result_calls, 0)
            self.assertEqual(manager.submits, 1 if mode == "success" else 0)
            self.assertEqual(manager.cancel_calls, [])

    def test_documented_urls_and_batch_use_real_normalizer(self):
        document = (ROOT / "examples/README.md").read_text(encoding="utf-8")
        urls = list(dict.fromkeys(re.findall(r"`(https://www\.facebook\.com/[^`]+)`", document)))
        self.assertGreaterEqual(len(urls), 5)
        for url in urls:
            self.assertFalse(api.normalize_request({"post_url": url})["reaction_scroll_limit_explicit"])
        batch = api.normalize_batch_request({"post_urls": urls + [urls[0]], "collect": ["reactions"], "save_artifacts": ["progress", "safe_capture"]})
        self.assertEqual(len(batch["post_urls"]), len(urls))


def _case(name, mode):
    def test(self):
        self.exercise(name, mode)
    return test


for _name in ("python", "node", "powershell", "curl"):
    for _mode in ("success", "empty", "failed", "cancelled", "unknown", "timeout", "invalid"):
        setattr(ApiExampleTests, f"test_{_name}_{_mode}", _case(_name, _mode))


if __name__ == "__main__":
    unittest.main()
