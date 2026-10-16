"""HTTP boundary regressions using dummy managers and loopback-only servers."""
import http.client
import json
import threading
import unittest
import shutil
import subprocess
from pathlib import Path
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import facebook_capture_api as api
import start_ui_v2 as ui
from facebook_http_security import body_length


class DummyManager:
    def __init__(self):
        self.calls = []

    def health(self):
        return {"dummy": True}

    def set_worker_enabled(self, worker, enabled):
        self.calls.append((worker, enabled))
        return {"enabled": enabled}

    def submit(self, payload):
        self.calls.append(payload)
        return {"job_id": "dummy"}


class HttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = DummyManager()
        cls.backend = api.CaptureServer(("127.0.0.1", 0), cls.manager)
        cls.proxy = ThreadingHTTPServer(("127.0.0.1", 0), ui.V2ProxyHandler)
        cls.proxy.api_target = f"http://127.0.0.1:{cls.backend.server_port}"
        cls.threads = []
        for server in (cls.backend, cls.proxy):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.backend, cls.proxy):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(3)

    def request(self, server, method="GET", path="/health", body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            reply = conn.getresponse()
            return reply.status, reply.read(), dict(reply.headers)
        finally:
            conn.close()

    def test_cli_and_same_origin_ui_keep_working(self):
        for server in (self.backend, self.proxy):
            with self.subTest(port=server.server_port):
                status, body, _ = self.request(server)
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(body)["dummy"])
                status, _, _ = self.request(server, "POST", "/v1/jobs", '{"post_id":"123"}', {
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                })
                self.assertEqual(status, 202)

    def test_external_and_null_origins_cannot_change_workers(self):
        for server in (self.backend, self.proxy):
            for origin in ("https://untrusted.invalid", "null", "http://127.0.0.1:1"):
                before = list(self.manager.calls)
                status, _, _ = self.request(server, "POST", "/v1/workers/dummy/disable", headers={"Origin": origin})
                self.assertEqual(status, 403)
                self.assertEqual(self.manager.calls, before)

    def test_nonlocal_host_and_cross_site_get_are_rejected(self):
        for server in (self.backend, self.proxy):
            for headers in ({"Host": "untrusted.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(self.request(server, headers=headers)[0], 403)

    def test_absolute_request_target_cannot_replace_backend(self):
        class Alternate(BaseHTTPRequestHandler):
            calls = 0
            def do_GET(self):
                type(self).calls += 1
                self.send_response(200)
                self.end_headers()
            def log_message(self, *args):
                pass
        alternate = ThreadingHTTPServer(("127.0.0.1", 0), Alternate)
        thread = threading.Thread(target=alternate.serve_forever, daemon=True)
        thread.start()
        try:
            path = f"http://127.0.0.1:{alternate.server_port}/v1/test"
            self.assertEqual(self.request(self.proxy, path=path)[0], 400)
            self.assertEqual(Alternate.calls, 0)
        finally:
            alternate.shutdown()
            alternate.server_close()
            thread.join(3)

    def test_oversized_or_malformed_bodies_rejected_before_read(self):
        for server in (self.backend, self.proxy):
            for headers in ({"Content-Length": "65537"}, {"Content-Length": "-1"},
                            {"Content-Length": "nonsense"}, {"Transfer-Encoding": "chunked"}):
                self.assertEqual(self.request(server, "POST", "/v1/jobs", headers=headers)[0], 400)

    def test_proxy_preserves_redirect_without_fetching_destination(self):
        with patch.object(api, "resolve_avatar_url", return_value="https://example.invalid/avatar.png"):
            status, _, headers = self.request(self.proxy, path="/v1/avatars/123")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "https://example.invalid/avatar.png")


class InputValidationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell.exe") or shutil.which("pwsh"), "PowerShell runtime unavailable")
    def test_windows_navigation_quotes_url_for_remote_shell(self):
        import shlex
        root = Path(__file__).resolve().parents[1]
        source = (root / "facebook_transport.ps1").read_text(encoding="utf-8-sig")
        function = source.split("function Start-FacebookUrlBounded", 1)[1].split("\nfunction ", 1)[0]
        url = "https://www.facebook.com/story.php?story_fbid=123&id=456&label=O'Reilly;$literal"
        script = "function Start-FacebookUrlBounded" + function + r'''
function Invoke-FacebookAdbCommand {
    param($Serial, $Arguments, $TimeoutSeconds)
    $script:Captured = $Arguments
    return [pscustomobject]@{Success=$true}
}
''' + "\n$null = Start-FacebookUrlBounded -Serial 'dummy' -Url '" + url.replace("'", "''") + "'\nConvertTo-Json -InputObject $script:Captured -Compress"
        result = subprocess.run([shutil.which("powershell.exe") or shutil.which("pwsh"),
                                 "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=10, check=True)
        argv = json.loads(result.stdout)
        remote = shlex.split(" ".join(argv[1:]))
        self.assertEqual(remote[remote.index("-d") + 1], url)
        self.assertEqual(remote[-4:], ["-n", "com.facebook.katana/.IntentUriHandler", "-f", "0x34000000"])

    def test_duplicate_content_length_rejected(self):
        headers = Message()
        headers.add_header("Content-Length", "1")
        headers.add_header("Content-Length", "2")
        with self.assertRaises(ValueError):
            body_length(headers)

    def test_facebook_hosts_and_five_url_families_preserved(self):
        paths = ("/123/posts/456", "/share/p/Abc/", "/user/posts/pfbidAbc123",
                 "/groups/123/permalink/456/", "/reel/456/", "/share/r/Abc/")
        for host in ("facebook.com", "www.facebook.com", "m.facebook.com"):
            for path in paths:
                url = "https://" + host + path
                self.assertEqual(api.normalize_post_url(url), url)

    def test_spoofed_authorities_rejected(self):
        for url in ("https://facebook.com.evil.invalid/posts/1", "https://evilfacebook.com/posts/1",
                    "https://facebook.com@127.0.0.1/posts/1", "https://user@www.facebook.com/posts/1",
                    "https://www.facebook.com:8080/posts/1", "https://[bad/posts/1",
                    "https://www.facebook.com/posts/1\nextra", "https://www.facebook.com\\evil/posts/1"):
            with self.subTest(url=url), self.assertRaises(api.ApiError):
                api.normalize_post_url(url)


if __name__ == "__main__":
    unittest.main()
