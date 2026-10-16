#!/usr/bin/env python3
"""
OmniCapture Console v2 - Standalone UI Server & Reverse Proxy
Serves the new web_v2 UI on port 8766 and seamlessly proxies /v1/* and /health to the backend API on port 8765.
Guaranteed to touch ZERO existing files in the repository.
"""
from __future__ import annotations

import argparse
import mimetypes
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from facebook_http_security import body_length, request_error, REQUEST_TIMEOUT_SECONDS

# Protect against Windows cp936 / GBK console encoding errors with non-ascii characters
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
WEB_V2_DIR = BASE_DIR / "web_v2"
DEFAULT_UI_PORT = 8766
DEFAULT_API_TARGET = "http://127.0.0.1:8765"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class V2ProxyHandler(BaseHTTPRequestHandler):
    server_version = "OmniCaptureV2-Server/1.0"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def _trusted_request(self) -> bool:
        error = request_error(self)
        if error:
            self.send_error(*error)
            return False
        return True

    def log_message(self, format: str, *args: object) -> None:
        # Keep server log clean and concise
        sys.stderr.write(f"[UI v2] {self.address_string()} - {format % args}\n")

    def _send_static(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "File not found")
            return

        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            content_type = "text/plain"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"

        try:
            body = file_path.read_bytes()
        except OSError as exc:
            self.send_error(500, f"Error reading file: {exc}")
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _proxy_to_backend(self) -> None:
        backend = urllib.parse.urlsplit(self.server.api_target)  # type: ignore[attr-defined]
        incoming = urllib.parse.urlsplit(self.path)
        target_url = urllib.parse.urlunsplit((backend.scheme, backend.netloc, incoming.path, incoming.query, ""))
        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in {"host", "connection", "transfer-encoding", "proxy-authorization", "proxy-connection", "origin"}:
                headers[k] = v
        headers["Host"] = backend.netloc
        # The UI's browser origin was checked before forwarding. Rebind it to
        # the configured backend so its own same-origin check remains strict.
        if self.headers.get("Origin") is not None:
            headers["Origin"] = urllib.parse.urlunsplit((backend.scheme, backend.netloc, "", "", ""))

        body = None
        if self.command == "POST":
            try:
                length = body_length(self.headers)
            except ValueError as exc:
                self.send_error(400, str(exc))
                return
            if length > 0:
                try:
                    body = self.rfile.read(length)
                except TimeoutError:
                    self.send_error(408, "request body timed out")
                    return
                if len(body) != length:
                    self.send_error(400, "incomplete request body")
                    return

        req = urllib.request.Request(target_url, data=body, headers=headers, method=self.command)

        try:
            # A local reverse proxy must not use ambient HTTP proxies or fetch
            # a redirected avatar/artifact destination on the caller's behalf.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            with opener.open(req, timeout=30) as resp:
                self.send_response(resp.status)
                for header, value in resp.headers.items():
                    if header.lower() not in {"transfer-encoding", "connection"}:
                        self.send_header(header, value)
                self.end_headers()
                while chunk := resp.read(64 * 1024):
                    self.wfile.write(chunk)
        except urllib.error.HTTPError as err:
            with err:
                self.send_response(err.code)
                for header, value in err.headers.items():
                    if header.lower() not in {"transfer-encoding", "connection"}:
                        self.send_header(header, value)
                self.end_headers()
                while chunk := err.read(64 * 1024):
                    self.wfile.write(chunk)
        except (urllib.error.URLError, ConnectionError, OSError):
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b'{"error":"Backend API on port 8765 is not reachable. Please start it using start_facebook_capture_api.ps1"}'
            )

    def do_GET(self) -> None:
        if not self._trusted_request():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Proxy API & health endpoints to backend
        if path.startswith("/v1/") or path == "/health":
            self._proxy_to_backend()
            return

        # Serve static assets from web_v2
        if path in {"", "/", "/index.html"}:
            self._send_static(WEB_V2_DIR / "index.html")
        elif path == "/styles.css":
            self._send_static(WEB_V2_DIR / "styles.css")
        elif path == "/app.js":
            self._send_static(WEB_V2_DIR / "app.js")
        else:
            self.send_error(404, "Asset not found")

    def do_POST(self) -> None:
        if not self._trusted_request():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/v1/"):
            self._proxy_to_backend()
            return

        self.send_error(404, "Not Found")


def run_server(port: int = DEFAULT_UI_PORT, api_target: str = DEFAULT_API_TARGET, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), V2ProxyHandler)
    server.api_target = api_target  # type: ignore[attr-defined]

    url = f"http://127.0.0.1:{port}/"
    print("=" * 66)
    print(f"  [OmniCapture v2] UI Console is LIVE at: {url}")
    print(f"  [OmniCapture v2] Backend Target API: {api_target}")
    print(f"  [OmniCapture v2] Serving Static Dir: {WEB_V2_DIR}")
    print("=" * 66)

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down OmniCapture v2 UI Server...")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="OmniCapture Console v2 Standalone UI Server")
    parser.add_argument("--port", type=int, default=DEFAULT_UI_PORT, help="UI port (default: 8766)")
    parser.add_argument("--api-target", default=DEFAULT_API_TARGET, help="Backend API base URL (default: http://127.0.0.1:8765)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch browser")
    args = parser.parse_args()

    run_server(port=args.port, api_target=args.api_target, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
