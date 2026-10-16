from __future__ import annotations

import gzip
import io
import json
import math
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

import facebook_capture_api as capture_api
import facebook_api_client as client_module


class SequenceManager:
    def __init__(self, statuses: list[dict[str, Any]], artifact: Path | None = None) -> None:
        self.statuses = list(statuses)
        self.artifact_path = artifact
        self.submitted_payload: dict[str, Any] | None = None
        self.status_calls: list[str] = []
        self.cancel_calls: list[str] = []

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submitted_payload = payload
        return {"job_id": "fb_client_test", "status": "queued", "request": payload}

    def public_job(self, job_id: str) -> dict[str, Any]:
        self.status_calls.append(job_id)
        if self.statuses:
            current = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        else:
            current = {"status": "queued"}
        return {"job_id": job_id, **current}

    def result(self, job_id: str) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "completion_scope": "terminal",
            "reaction_users": [{"id": "u1", "name": "Alice"}],
        }

    def cancel(self, job_id: str) -> dict[str, Any]:
        self.cancel_calls.append(job_id)
        return {
            "cancelled": True,
            "reason": "queued job cancelled",
            "job": {"job_id": job_id, "status": "cancelled"},
        }

    def artifact(self, job_id: str, artifact_name: str) -> Path:
        if artifact_name != "facebook-reactor-users.csv" or self.artifact_path is None:
            raise capture_api.ApiError("unknown artifact")
        return self.artifact_path


@contextmanager
def running_capture_server(manager: SequenceManager):
    server = capture_api.CaptureServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class RawServerState:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.paths: list[str] = []


class RawHandler(BaseHTTPRequestHandler):
    server_version = "FacebookClientTest/1.0"

    def do_GET(self) -> None:
        state: RawServerState = self.server.state  # type: ignore[attr-defined]
        state.paths.append(self.path)
        if state.mode == "plain-error":
            body = b"upstream failed without json"
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if state.mode == "truncated":
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", "20")
            self.end_headers()
            self.wfile.write(b"partial")
            return
        if state.mode == "gzip":
            body = gzip.compress(b"user_id,user_name\n1,Alice\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        raise AssertionError(f"unexpected raw server mode: {state.mode}")

    def log_message(self, _fmt: str, *_args: Any) -> None:
        return


@contextmanager
def running_raw_server(state: RawServerState):
    server = ThreadingHTTPServer(("127.0.0.1", 0), RawHandler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class FacebookApiClientTests(unittest.TestCase):
    def test_timeout_validation_rejects_nonfinite_values_and_zero_poll_interval(self) -> None:
        for timeout in (math.nan, math.inf, -math.inf, (1.0, math.nan), (1.0, math.inf)):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                client_module.FacebookApiClient("http://127.0.0.1:8765", timeout=timeout)

        client = client_module.FacebookApiClient("http://127.0.0.1:8765")
        try:
            with mock.patch.object(client, "_request_json", side_effect=AssertionError("validation expected")):
                for overall_timeout in (math.nan, math.inf, -math.inf):
                    with self.subTest(overall_timeout=overall_timeout), self.assertRaises(ValueError):
                        client.wait("fb_client_test", overall_timeout=overall_timeout, poll_interval=0.001)
                for poll_interval in (0, math.nan, math.inf, -math.inf):
                    with self.subTest(poll_interval=poll_interval), self.assertRaises(ValueError):
                        client.wait("fb_client_test", overall_timeout=1, poll_interval=poll_interval)
        finally:
            client.close()

    def test_wait_allows_zero_overall_timeout_as_immediate_local_timeout(self) -> None:
        manager = SequenceManager([{"status": "queued"}])
        with running_capture_server(manager) as base_url:
            client = client_module.FacebookApiClient(base_url, timeout=1)
            with self.assertRaises(client_module.FacebookWaitTimeoutError) as caught:
                client.wait("fb_client_test", overall_timeout=0, poll_interval=0.001)
        self.assertEqual(caught.exception.job_id, "fb_client_test")
        self.assertEqual(caught.exception.overall_timeout, 0.0)
        self.assertEqual(manager.status_calls, ["fb_client_test"])

    def test_cli_flushes_submitted_job_id_before_wait_timeout_without_leaking_headers(self) -> None:
        manager = SequenceManager([{"status": "queued"}])

        class FlushCapture(io.StringIO):
            flush_calls = 0

            def flush(self) -> None:
                self.flush_calls += 1
                super().flush()

        with running_capture_server(manager) as base_url, TemporaryDirectory() as temp:
            stdout = FlushCapture()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = client_module.main(
                    [
                        "https://www.facebook.com/example/posts/1",
                        "--base-url",
                        base_url,
                        "--header",
                        "Authorization=secret-token",
                        "--overall-timeout",
                        "0",
                        "--poll-interval",
                        "0.001",
                        "--output-dir",
                        temp,
                    ]
                )

        self.assertEqual(code, 1)
        self.assertGreaterEqual(stdout.flush_calls, 1)
        lines = stdout.getvalue().splitlines()
        self.assertGreaterEqual(len(lines), 1)
        json_lines = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and "job_id" in value and "status" in value:
                json_lines.append(value)
        self.assertGreaterEqual(len(json_lines), 1)
        submitted = json_lines[0]
        self.assertEqual(submitted, {"job_id": "fb_client_test", "status": "queued"})
        self.assertNotIn("secret-token", stdout.getvalue() + stderr.getvalue())

    def test_submit_wait_result_uses_real_api_routes_and_preserves_full_default(self) -> None:
        manager = SequenceManager(
            [
                {"status": "queued"},
                {"status": "running"},
                {"status": "completed", "completion_scope": "terminal"},
            ]
        )
        with running_capture_server(manager) as base_url:
            client = client_module.FacebookApiClient(base_url, timeout=2)
            job = client.submit("https://www.facebook.com/example/posts/1")
            completed = client.wait(job["job_id"], overall_timeout=1, poll_interval=0.001)
            result = client.result(job["job_id"])

        self.assertEqual(job["job_id"], "fb_client_test")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(result["reaction_users"][0]["id"], "u1")
        self.assertEqual(manager.submitted_payload, {"post_url": "https://www.facebook.com/example/posts/1"})
        self.assertEqual(manager.status_calls, ["fb_client_test"] * 3)

    def test_wait_keeps_polling_cancelling_until_cancelled_terminal_state(self) -> None:
        manager = SequenceManager(
            [
                {"status": "queued"},
                {"status": "cancelling"},
                {"status": "cancelled", "error": "capture stop confirmed"},
            ]
        )
        with running_capture_server(manager) as base_url:
            client = client_module.FacebookApiClient(base_url)
            with self.assertRaises(client_module.FacebookJobError) as caught:
                client.wait("fb_client_test", overall_timeout=1, poll_interval=0.001)
        self.assertEqual(caught.exception.status, "cancelled")
        self.assertEqual(manager.status_calls, ["fb_client_test"] * 3)

    def test_submit_forwards_explicit_collect_artifacts_and_bounded_sample(self) -> None:
        manager = SequenceManager([{"status": "queued"}])
        with running_capture_server(manager) as base_url:
            client = client_module.FacebookApiClient(base_url)
            client.submit(
                "https://www.facebook.com/example/posts/2",
                collect=["reactions"],
                save_artifacts=["progress"],
                reaction_scroll_limit=3,
            )
        self.assertEqual(
            manager.submitted_payload,
            {
                "post_url": "https://www.facebook.com/example/posts/2",
                "collect": ["reactions"],
                "save_artifacts": ["progress"],
                "reaction_scroll_limit": 3,
            },
        )

    def test_failed_and_cancelled_terminal_states_raise_job_error_with_context(self) -> None:
        for terminal_status in ("failed", "cancelled"):
            with self.subTest(status=terminal_status):
                manager = SequenceManager([{"status": terminal_status, "error": "capture blocked"}])
                with running_capture_server(manager) as base_url:
                    client = client_module.FacebookApiClient(base_url)
                    with self.assertRaises(client_module.FacebookJobError) as caught:
                        client.wait("fb_client_test", overall_timeout=1, poll_interval=0.001)
                self.assertIn("fb_client_test", str(caught.exception))
                self.assertIn("capture blocked", str(caught.exception))
                self.assertEqual(caught.exception.job_id, "fb_client_test")

    def test_cancel_is_explicit_and_wait_timeout_does_not_cancel_service_job(self) -> None:
        manager = SequenceManager([{"status": "queued"}])
        with running_capture_server(manager) as base_url:
            client = client_module.FacebookApiClient(base_url, timeout=2)
            with self.assertRaises(client_module.FacebookWaitTimeoutError):
                client.wait("fb_client_test", overall_timeout=0.03, poll_interval=0.005)
            self.assertEqual(manager.cancel_calls, [])
            cancelled = client.cancel("fb_client_test")
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(manager.cancel_calls, ["fb_client_test"])

    def test_unknown_status_is_reported_instead_of_being_polled_forever(self) -> None:
        manager = SequenceManager([{"status": "mysterious-new-state"}])
        with running_capture_server(manager) as base_url:
            client = client_module.FacebookApiClient(base_url)
            with self.assertRaises(client_module.FacebookUnknownStatusError) as caught:
                client.wait("fb_client_test", overall_timeout=1, poll_interval=0.001)
        self.assertIn("mysterious-new-state", str(caught.exception))
        self.assertIn("fb_client_test", str(caught.exception))

    def test_non_json_http_error_is_descriptive(self) -> None:
        state = RawServerState("plain-error")
        with running_raw_server(state) as base_url:
            client = client_module.FacebookApiClient(base_url, timeout=1)
            with self.assertRaises(client_module.FacebookApiHttpError) as caught:
                client.get_status("fb_plain_error")
        self.assertEqual(caught.exception.status_code, 500)
        self.assertIn("upstream failed without json", str(caught.exception))
        self.assertEqual(state.paths, ["/v1/jobs/fb_plain_error"])

    def test_download_is_atomic_and_does_not_use_service_local_paths(self) -> None:
        with TemporaryDirectory() as temp:
            artifact = Path(temp) / "facebook-reactor-users.csv"
            artifact.write_bytes(b"user_id,user_name\n1,Alice\n")
            manager = SequenceManager([{"status": "completed"}], artifact=artifact)
            target = Path(temp) / "nested" / "download.csv"
            with running_capture_server(manager) as base_url:
                client = client_module.FacebookApiClient(base_url)
                returned = client.download("fb_client_test", "facebook-reactor-users.csv", target)
            self.assertEqual(returned, target)
            self.assertEqual(target.read_bytes(), artifact.read_bytes())
            self.assertNotIn(str(artifact), target.read_text(encoding="utf-8"))

    def test_interrupted_download_preserves_existing_target_and_cleans_temp_file(self) -> None:
        state = RawServerState("truncated")
        with TemporaryDirectory() as temp:
            target = Path(temp) / "download.csv"
            target.write_bytes(b"old complete file\n")
            with running_raw_server(state) as base_url:
                client = client_module.FacebookApiClient(base_url, timeout=1)
                with self.assertRaises(client_module.FacebookApiDownloadError):
                    client.download("fb_client_test", "facebook-reactor-users.csv", target)
            self.assertEqual(target.read_bytes(), b"old complete file\n")
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])
        self.assertEqual(state.paths, ["/v1/jobs/fb_client_test/artifacts/facebook-reactor-users.csv?download=1"])

    def test_download_rejects_nonidentity_encoding_before_length_check(self) -> None:
        state = RawServerState("gzip")
        with TemporaryDirectory() as temp:
            target = Path(temp) / "download.csv"
            target.write_bytes(b"old complete file\n")
            with running_raw_server(state) as base_url:
                client = client_module.FacebookApiClient(base_url, timeout=1)
                with self.assertRaisesRegex(client_module.FacebookApiDownloadError, "unsupported Content-Encoding"):
                    client.download("fb_client_test", "facebook-reactor-users.csv", target)
            self.assertEqual(target.read_bytes(), b"old complete file\n")
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
