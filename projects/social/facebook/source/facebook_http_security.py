"""Shared request boundaries for the local API and UI proxy."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 15
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def request_error(handler) -> tuple[int, str] | None:
    """Keep browser requests same-origin without requiring tokens from CLI clients."""
    target = handler.path
    if (not target.startswith("/") or target.startswith("//") or "\\" in target
            or any(ord(c) < 32 or ord(c) == 127 for c in target)):
        return 400, "request target must be an origin-form path"
    hosts = handler.headers.get_all("Host", [])
    if len(hosts) != 1:
        return 400, "exactly one Host header is required"
    try:
        authority = urlsplit("http://" + hosts[0])
        port = authority.port or 80
        if (authority.hostname not in LOCAL_HOSTS or authority.username is not None
                or authority.password is not None or authority.path or authority.query
                or authority.fragment or port != handler.server.server_port):
            return 403, "untrusted Host"
        origins = handler.headers.get_all("Origin", [])
        if origins:
            if len(origins) != 1:
                return 403, "untrusted Origin"
            origin = urlsplit(origins[0])
            if (origin.scheme != "http" or origin.hostname != authority.hostname
                    or (origin.port or 80) != port or origin.username is not None
                    or origin.password is not None or origin.path or origin.query or origin.fragment):
                return 403, "cross-origin requests are not allowed"
    except ValueError:
        return 400, "invalid request authority"
    if handler.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return 403, "cross-site requests are not allowed"
    return None


def body_length(headers) -> int:
    """Reject ambiguous framing before any request-body allocation or state change."""
    if headers.get_all("Transfer-Encoding", []):
        raise ValueError("transfer encoding is not supported")
    lengths = headers.get_all("Content-Length", [])
    if not lengths:
        return 0
    if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
        raise ValueError("invalid Content-Length")
    length = int(lengths[0])
    if length > MAX_REQUEST_BYTES:
        raise ValueError("request body exceeds 65536 bytes")
    return length
