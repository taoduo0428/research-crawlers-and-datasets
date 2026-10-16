import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from mitmproxy import http, io
from mitmproxy.exceptions import FlowReadException


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect the independent raw Facebook mitmproxy archive.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None, help="Write a non-sensitive metadata summary JSON.")
    parser.add_argument("--flow-id", default="", help="Export one exact captured HTTP flow by mitmproxy flow id.")
    parser.add_argument("--export-dir", type=Path, default=None, help="Directory for exact request/response header+body export.")
    return parser.parse_args()


def _friendly_name(flow: http.HTTPFlow) -> str:
    value = flow.request.headers.get("x-fb-friendly-name")
    if value:
        return str(value)
    try:
        text = flow.request.get_text(strict=False) or ""
    except Exception:
        return ""
    try:
        for key, value in parse_qsl(text, keep_blank_values=True):
            if key == "fb_api_req_friendly_name":
                return value
    except Exception:
        return ""
    return ""


def _summary_row(position: int, flow: http.HTTPFlow) -> dict:
    request = flow.request
    response = flow.response
    path = urlsplit(request.pretty_url).path or "/"
    friendly = _friendly_name(flow)
    return {
        "position": position,
        "flow_id": flow.id,
        "timestamp_start": request.timestamp_start,
        "host": request.pretty_host,
        "method": request.method,
        "path": path,
        "friendly_name": friendly,
        "graphql_like": bool(friendly) or "graphql" in path.lower(),
        "http_version": request.http_version,
        "request_body_bytes": len(request.raw_content or b""),
        "status_code": response.status_code if response else None,
        "response_body_bytes": len(response.raw_content or b"") if response else 0,
        "error": str(flow.error) if flow.error else None,
    }


def _headers_bytes(message) -> bytes:
    return b"".join(
        str(name).encode("utf-8", errors="replace")
        + b": "
        + str(value).encode("utf-8", errors="replace")
        + b"\r\n"
        for name, value in message.headers.items(multi=True)
    )


def _export_exact(flow: http.HTTPFlow, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = flow.id
    request = flow.request
    response = flow.response
    request_head = (
        f"{request.method} {request.path} {request.http_version}\r\n".encode("utf-8", errors="replace")
        + _headers_bytes(request)
        + b"\r\n"
    )
    request_head_path = out_dir / f"{stem}-request-head.txt"
    request_body_path = out_dir / f"{stem}-request-body.bin"
    request_head_path.write_bytes(request_head)
    request_body_path.write_bytes(request.raw_content or b"")

    result = {
        "flow_id": flow.id,
        "request_head": str(request_head_path),
        "request_body": str(request_body_path),
    }
    if response is not None:
        response_head = (
            f"{response.http_version} {response.status_code} {response.reason}\r\n".encode("utf-8", errors="replace")
            + _headers_bytes(response)
            + b"\r\n"
        )
        response_head_path = out_dir / f"{stem}-response-head.txt"
        response_body_path = out_dir / f"{stem}-response-body.bin"
        response_head_path.write_bytes(response_head)
        response_body_path.write_bytes(response.raw_content or b"")
        result["response_head"] = str(response_head_path)
        result["response_body"] = str(response_body_path)
    return result


def main() -> int:
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(f"raw capture not found: {args.input}")

    rows = []
    selected = None
    with args.input.open("rb") as handle:
        reader = io.FlowReader(handle)
        try:
            for flow in reader.stream():
                if not isinstance(flow, http.HTTPFlow):
                    continue
                rows.append(_summary_row(len(rows) + 1, flow))
                if args.flow_id and flow.id == args.flow_id:
                    selected = flow
        except FlowReadException as exc:
            raise SystemExit(f"raw capture is corrupted or incomplete: {exc}") from exc

    digest = hashlib.sha256(args.input.read_bytes()).hexdigest()
    report = {
        "input": str(args.input),
        "sha256": digest,
        "http_flow_count": len(rows),
        "graphql_like_count": sum(1 for row in rows if row["graphql_like"]),
        "hosts": sorted({row["host"] for row in rows}),
        "flows": rows,
    }

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.flow_id:
        if selected is None:
            raise SystemExit(f"flow id not found: {args.flow_id}")
        if args.export_dir is None:
            raise SystemExit("--export-dir is required with --flow-id")
        report["exported"] = _export_exact(selected, args.export_dir)

    print(json.dumps({
        "input": str(args.input),
        "sha256": digest,
        "http_flow_count": len(rows),
        "graphql_like_count": report["graphql_like_count"],
        "out": str(args.out) if args.out else None,
        "exported": report.get("exported"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
