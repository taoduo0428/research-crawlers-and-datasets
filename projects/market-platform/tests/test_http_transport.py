from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from drv_crawler import core
from drv_crawler.core import (
    CrawlerError,
    ProjectConfig,
    ResilientHttpClient,
    ResponseTooLargeError,
)


class CurlFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
        else:
            body = b"curl-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return None


class HttpTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), CurlFixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.root = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    @staticmethod
    def fake_session() -> MagicMock:
        session = MagicMock()
        session.headers = {}
        session.cookies = requests.cookies.RequestsCookieJar()
        return session

    def test_fetched_page_soup_respects_transport_encoding(self) -> None:
        expected = "\u0416\u0410\u0411\u0412"
        page = core.FetchedPage(
            requested_url="https://example.test/",
            final_url="https://example.test/",
            status_code=200,
            content=f"<html><h1>{expected}</h1></html>".encode("cp1251"),
            encoding="cp1251",
        )

        self.assertEqual(expected, page.soup().h1.get_text(strip=True))

    def test_epwk_uses_curl_cffi_chrome_session_for_robots_and_pages(self) -> None:
        if core.curl_requests is None:
            self.skipTest("curl_cffi is not installed")
        session = self.fake_session()
        with (
            patch("drv_crawler.core.resolve_proxy", return_value=None),
            patch(
                "drv_crawler.core._curl_ca_bundle_path",
                return_value=r"C:\Temp\drv-crawler-ca.pem",
            ),
            patch.object(
                core.curl_requests, "Session", return_value=session
            ) as session_factory,
        ):
            client = ResilientHttpClient(
                ProjectConfig("crawler.config.yaml"), site_name="epwk"
            )

        session_factory.assert_called_once_with(
            impersonate="chrome",
            verify=r"C:\Temp\drv-crawler-ca.pem",
        )
        self.assertEqual("curl_cffi", client.transport)
        self.assertIs(session, client.session)
        self.assertIs(session, client.robots.session)
        self.assertNotIn("User-Agent", session.headers)
        self.assertNotIn("Connection", session.headers)
        client.close()

    def test_non_epwk_site_keeps_requests_transport(self) -> None:
        session = self.fake_session()
        curl_session = (
            patch.object(core.curl_requests, "Session")
            if core.curl_requests is not None
            else patch("drv_crawler.core.requests.Session")
        )
        with (
            patch("drv_crawler.core.resolve_proxy", return_value=None),
            patch("drv_crawler.core.requests.Session", return_value=session) as factory,
            curl_session as curl_factory,
        ):
            client = ResilientHttpClient(
                ProjectConfig("crawler.config.yaml"), site_name="zbj"
            )

        factory.assert_called_once_with()
        if core.curl_requests is not None:
            curl_factory.assert_not_called()
        self.assertEqual("requests", client.transport)
        self.assertEqual(client.user_agent, session.headers["User-Agent"])
        client.close()

    def test_site_can_override_download_delay(self) -> None:
        config = ProjectConfig("crawler.config.yaml")
        config.data["sites"]["epwk"]["download_delay"] = 0.5
        with ResilientHttpClient(config, site_name="epwk") as client:
            self.assertEqual(0.5, client.delay)

    def test_unicode_project_copies_certifi_bundle_to_ascii_temp_atomically(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-项目" / "cacert.pem"
            source.parent.mkdir()
            source.write_bytes(b"test-ca-data")
            ascii_temp = root / "ascii-ca-cache"
            project_config = root / "项目" / "crawler.config.yaml"
            with (
                patch("drv_crawler.core.certifi.where", return_value=str(source)),
                patch(
                    "drv_crawler.core._ascii_system_temp_dir",
                    return_value=ascii_temp,
                ),
                patch(
                    "drv_crawler.core.atomic_write_bytes",
                    wraps=core.atomic_write_bytes,
                ) as atomic_write,
            ):
                destination = Path(core._curl_ca_bundle_path(project_config))

            self.assertTrue(str(destination).isascii())
            self.assertEqual(b"test-ca-data", destination.read_bytes())
            atomic_write.assert_called_once()

    def test_curl_request_exception_uses_bounded_retry_budget(self) -> None:
        if core.curl_requests is None:
            self.skipTest("curl_cffi is not installed")

        class FailingCurlSession:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *_: object, **__: object) -> None:
                self.calls += 1
                raise core.curl_requests.RequestsError("simulated curl failure")

            def close(self) -> None:
                return None

        with patch("drv_crawler.core.resolve_proxy", return_value=None):
            client = ResilientHttpClient(ProjectConfig("crawler.config.yaml"))
        failing_session = FailingCurlSession()
        client.session = failing_session
        with patch("drv_crawler.core.time.sleep"), self.assertRaises(CrawlerError):
            client.get("https://example.test/task", obey_robots=False)

        self.assertEqual(client.retry_times + 1, failing_session.calls)

    def test_curl_cookie_import_keeps_domain_and_expiry_filters(self) -> None:
        if core.curl_requests is None:
            self.skipTest("curl_cffi is not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir) / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "valid",
                                "value": "redacted",
                                "domain": ".epwk.com",
                                "path": "/",
                                "expires": time.time() + 300,
                            },
                            {
                                "name": "foreign",
                                "value": "redacted",
                                "domain": ".example.com",
                                "path": "/",
                                "expires": time.time() + 300,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch("drv_crawler.core.resolve_proxy", return_value=None):
                client = ResilientHttpClient(
                    ProjectConfig("crawler.config.yaml"), site_name="epwk"
                )
            try:
                imported = client.load_storage_state(
                    state, allowed_domains={"epwk.com"}
                )
                self.assertEqual(1, imported)
                self.assertEqual("redacted", client.session.cookies.get("valid"))
                self.assertIsNone(client.session.cookies.get("foreign"))
            finally:
                client.close()

    def test_real_curl_session_preserves_robots_and_response_limit(self) -> None:
        if core.curl_requests is None:
            self.skipTest("curl_cffi is not installed")
        with patch("drv_crawler.core.resolve_proxy", return_value=None):
            client = ResilientHttpClient(
                ProjectConfig("crawler.config.yaml"), site_name="epwk"
            )
        client.delay = 0
        try:
            page = client.get(f"{self.root}/task")
            self.assertEqual(b"curl-ok", page.content)
            client.max_response_bytes = 3
            with self.assertRaises(ResponseTooLargeError):
                client.get(f"{self.root}/too-large")
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
