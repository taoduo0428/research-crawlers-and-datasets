import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from mitmproxy import http, io


OUT = Path(os.environ.get("FACEBOOK_CAPTURE_DIR", Path(__file__).resolve().parent))
RAW_FLOW_FILE = Path(os.environ.get("FACEBOOK_RAW_CAPTURE_FILE", OUT / "raw-facebook-http.mitm"))
RAW_INDEX_FILE = Path(os.environ.get("FACEBOOK_RAW_INDEX_FILE", OUT / "raw-facebook-http-index.jsonl"))
ALLOWED_SUFFIXES = ("facebook.com", "fbcdn.net", "fbsbx.com", "instagram.com")


def _is_allowed(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_SUFFIXES)


def _query_keys(url: str) -> list[str]:
    try:
        return sorted({key for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)})
    except Exception:
        return []


def _friendly_name(flow: http.HTTPFlow) -> str:
    value = flow.request.headers.get("x-fb-friendly-name")
    if value:
        return str(value)
    try:
        body = flow.request.get_text(strict=False) or ""
    except Exception:
        body = ""
    for key, value in parse_qsl(body, keep_blank_values=True):
        if key == "fb_api_req_friendly_name":
            return value
    return ""


def _index_record(flow: http.HTTPFlow, terminal_event: str) -> dict:
    request = flow.request
    response = flow.response
    path = urlsplit(request.pretty_url).path or "/"
    friendly = _friendly_name(flow)
    graphql_like = bool(friendly) or "graphql" in path.lower()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "flow_id": flow.id,
        "terminal_event": terminal_event,
        "host": request.pretty_host,
        "method": request.method,
        "path": path,
        "url_query_keys": _query_keys(request.pretty_url),
        "friendly_name": friendly,
        "graphql_like": graphql_like,
        "http_version": request.http_version,
        "request_content_type": request.headers.get("content-type"),
        "request_body_bytes": len(request.raw_content or b""),
        "status_code": response.status_code if response else None,
        "response_content_type": response.headers.get("content-type") if response else None,
        "response_body_bytes": len(response.raw_content or b"") if response else 0,
        "error": str(flow.error) if flow.error else None,
    }


class RawFacebookFlowWriter:
    """Independent raw HTTP-flow archive for later mitmproxy/mitmweb inspection."""

    def __init__(self) -> None:
        self._stream = None
        self._writer = None
        self._written_ids: set[str] = set()

    def _ensure_open(self) -> None:
        if self._writer is not None:
            return
        RAW_FLOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        RAW_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._stream = RAW_FLOW_FILE.open("ab")
        self._writer = io.FlowWriter(self._stream)

    def _write(self, flow: http.HTTPFlow, terminal_event: str) -> None:
        if not _is_allowed(flow.request.pretty_host):
            return
        if flow.id in self._written_ids:
            return
        self._ensure_open()
        self._writer.add(flow)
        self._stream.flush()
        with RAW_INDEX_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_index_record(flow, terminal_event), ensure_ascii=False) + "\n")
        self._written_ids.add(flow.id)

    def response(self, flow: http.HTTPFlow) -> None:
        self._write(flow, "response")

    def error(self, flow: http.HTTPFlow) -> None:
        self._write(flow, "error")

    def done(self) -> None:
        if self._stream is not None:
            self._stream.flush()
            self._stream.close()
            self._stream = None
            self._writer = None


addons = [RawFacebookFlowWriter()]
