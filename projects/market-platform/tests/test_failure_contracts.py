from __future__ import annotations

import tempfile
import threading
import json
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from drv_crawler.core import (
    BlockedResponseError,
    ProjectConfig,
    ResilientHttpClient,
    RobotsDeniedError,
    atomic_write_text,
    safe_error_message,
)


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /private\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/range":
            body = (self.headers.get("Range") or "missing").encode("ascii")
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/limit":
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return None


class FailureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.root = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def make_config(self) -> ProjectConfig:
        config = Path(tempfile.mkdtemp()) / "config.yaml"
        config.write_text(
            """
crawler:
  obey_robots_txt: true
  proxy: {mode: off, url_env: CRAWLER_PROXY_URL, default_url: ''}
  headers: {accept_language: zh-CN, user_agent_env: TEST_UA}
  timeout: {connect_timeout_seconds: 2, read_timeout_seconds: 2}
  limits: {max_response_bytes: 1024}
  retry: {retry_times: 2, retry_http_codes: [429], max_retry_after_seconds: 2}
  anti_bot: {block_status_codes: [401, 403, 407, 418, 429]}
  throttling: {download_delay: 0, randomize_download_delay: false}
""".strip(),
            encoding="utf-8",
        )
        return ProjectConfig(config)

    def test_atomic_write_retries_transient_windows_replace_lock(self) -> None:
        target = Path(tempfile.mkdtemp()) / "checkpoint.json"
        real_replace = __import__("os").replace
        attempts = 0

        def flaky_replace(source: object, destination: object) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError(5, "transient lock")
            real_replace(source, destination)

        with patch("drv_crawler.core.os.replace", side_effect=flaky_replace), patch(
            "drv_crawler.core.time.sleep"
        ):
            atomic_write_text(target, "ok")

        self.assertEqual(3, attempts)
        self.assertEqual("ok", target.read_text(encoding="utf-8"))

    def test_429_without_retry_after_stops_immediately(self) -> None:
        with ResilientHttpClient(self.make_config()) as client:
            with self.assertRaises(BlockedResponseError):
                client.get(f"{self.root}/limit")

    def test_robots_denial_blocks_before_content_request(self) -> None:
        with ResilientHttpClient(self.make_config()) as client:
            with self.assertRaises(RobotsDeniedError):
                client.get(f"{self.root}/private")

    def test_documented_public_api_can_explicitly_skip_robots_check(self) -> None:
        with ResilientHttpClient(self.make_config()) as client:
            page = client.get(f"{self.root}/private", obey_robots=False)
        self.assertEqual(b"ok", page.content)

    def test_bounded_get_supports_range_header(self) -> None:
        with ResilientHttpClient(self.make_config()) as client:
            page = client.get(
                f"{self.root}/range", headers={"Range": "bytes=10-19"}
            )
        self.assertEqual(206, page.status_code)
        self.assertEqual(b"bytes=10-19", page.content)

    def test_error_message_redacts_url_query_and_fragment(self) -> None:
        message = safe_error_message(
            "fetch failed: https://task.example/sitemap.xml?token=test-secret#trace"
        )

        self.assertEqual(
            "fetch failed: https://task.example/sitemap.xml",
            message,
        )
        self.assertNotIn("test-secret", message)

    def test_storage_state_filters_expired_and_foreign_cookies(self) -> None:
        state = Path(tempfile.mkdtemp()) / "state.json"
        state.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "expired",
                            "value": "redacted",
                            "domain": ".example.com",
                            "path": "/",
                            "expires": time.time() - 10,
                        },
                        {
                            "name": "valid",
                            "value": "redacted",
                            "domain": ".example.com",
                            "path": "/",
                            "expires": time.time() + 300,
                        },
                        {
                            "name": "foreign",
                            "value": "redacted",
                            "domain": ".other.example",
                            "path": "/",
                            "expires": time.time() + 300,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        with ResilientHttpClient(self.make_config()) as client:
            imported = client.load_storage_state(
                state, allowed_domains={"example.com"}
            )
            self.assertEqual(1, imported)
            self.assertIsNone(client.session.cookies.get("expired"))
            self.assertEqual("redacted", client.session.cookies.get("valid"))


if __name__ == "__main__":
    unittest.main()
