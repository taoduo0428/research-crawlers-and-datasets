#!/usr/bin/env python3
"""Serve Liepin detail tasks to a normal Edge MV3 extension over loopback."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.liepin_coordination import (
    FORWARD_OWNER,
    REVERSE_OWNERS,
    REVERSE_SECONDARY_OWNER,
    LiepinCoordination,
)
from tools.run_liepin_browser_single import (
    COORD_DB_DEFAULT,
    SOURCE_DEFAULT,
    append_checkpoint,
    completed_ids,
    iter_rows,
    load_terminal_ids,
    record_key,
)
from tools.run_liepin_camoufox_reverse import append_output
from tools.run_liepin_edge_cdp_worker import WORKER_CONFIGS


LOOPBACK_HOST = "127.0.0.1"
MAX_BODY_BYTES = 1_048_576
REQUEST_TIMEOUT_SECONDS = 5.0
TOKEN_FILE_DEFAULT = (
    PROJECT_ROOT / ".crawler-state" / "liepin-edge-extension" / "bridge-token"
)
EXTENSION_RUNTIME_CONFIG_DEFAULT = (
    PROJECT_ROOT
    / "tools"
    / "liepin_edge_extension"
    / ".runtime"
    / "bridge-config.json"
)
STATUS_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
TERMINAL_STATUSES = frozenset({"unavailable", "expired"})
FINALIZED_PREFIX = "deferred_"


@dataclass(frozen=True)
class Assignment:
    worker: str
    record_id: str
    source_index: int
    detail_url: str

    def as_dict(self) -> dict[str, object]:
        return {
            "worker": self.worker,
            "record_id": self.record_id,
            "source_index": self.source_index,
            "detail_url": self.detail_url,
        }


class BridgeInputError(ValueError):
    """A request was structurally valid HTTP but invalid for this bridge."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.UNPROCESSABLE_ENTITY):
        super().__init__(message)
        self.status = status


class LiepinExtensionBridge:
    """Thread-safe adapter around the existing JSONL source and SQLite claims."""

    def __init__(
        self,
        *,
        source: Path,
        coord_db: Path,
        workers: tuple[str, ...],
        limit: int = 0,
        worker_configs: Mapping[str, Mapping[str, object]] = WORKER_CONFIGS,
    ) -> None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if not source.is_file():
            raise FileNotFoundError(source)
        if not workers or len(set(workers)) != len(workers):
            raise ValueError("workers must be a non-empty unique sequence")
        unknown = set(workers).difference(worker_configs)
        if unknown:
            raise ValueError(f"unknown workers: {sorted(unknown)}")

        self.source = source
        self.coord_db = coord_db
        self.workers = workers
        self.limit = limit
        self.worker_configs = worker_configs
        self.rows = tuple(iter_rows(source))
        self.total_rows = len(self.rows)
        if not self.rows:
            raise ValueError("source contains no JSON object rows")

        self.lock = threading.RLock()
        self.outstanding: dict[str, Assignment] = {}
        self.completed: set[str] = set()
        self.terminal: set[str] = set()
        self.deferred: set[str] = set()
        self.deferred_attempted: set[str] = set()
        self.issued = 0
        self.successes = 0
        self.deferred_results = 0
        self.terminal_results = 0

        self.completed.update(
            completed_ids(Path(worker_configs[workers[0]]["output"]))
        )
        for worker in worker_configs:
            config = worker_configs[worker]
            checkpoint = Path(config["state_dir"]) / "checkpoint.jsonl"
            self.terminal.update(load_terminal_ids(checkpoint))

        with LiepinCoordination(coord_db) as coordination:
            if REVERSE_SECONDARY_OWNER in workers:
                coordination.migrate_legacy_middle_claims()
            self.deferred.update(record_id for record_id, _, _ in coordination.deferred_claims())
            for record_id, status in coordination.connection.execute(
                "SELECT record_id, status FROM claims WHERE status IN "
                "('success', 'unavailable', 'expired', 'blocked')"
            ):
                # JSONL with a non-empty JD is authoritative. A stale database
                # success without an output row must be reclaimed and repaired.
                if status != "success":
                    self.terminal.add(str(record_id))

    def _direction(self, worker: str) -> str:
        return str(self.worker_configs[worker]["direction"])

    def _start_index(self, worker: str, progress: Mapping[str, object]) -> int:
        prior = progress.get(worker)
        if prior is not None:
            return max(1, min(self.total_rows, int(prior)))
        return self.total_rows if self._direction(worker) == "reverse" else 1

    def _source_indexes(self, worker: str, start_index: int) -> range:
        if self._direction(worker) == "reverse":
            return range(start_index, 0, -1)
        return range(start_index, self.total_rows + 1)

    def next_assignment(self, worker: str) -> tuple[Assignment | None, str]:
        if worker not in self.workers:
            raise BridgeInputError("unknown or disabled worker", HTTPStatus.BAD_REQUEST)

        with self.lock:
            current = self.outstanding.get(worker)
            if current is not None:
                return current, "outstanding"
            if self.limit and self.issued >= self.limit:
                return None, "limit_reached"

            with LiepinCoordination(self.coord_db) as coordination:
                progress = coordination.status()["progress"]
                start_index = self._start_index(worker, progress)
                stop_reason = "source_exhausted"
                for source_index in self._source_indexes(worker, start_index):
                    if coordination.has_met(worker, source_index):
                        stop_reason = "workers_met"
                        break
                    coordination.advance(worker, source_index)
                    row = self.rows[source_index - 1]
                    record_id = record_key(row)
                    detail_url = str(row.get("detail_url") or "").strip()
                    if (
                        not record_id
                        or not detail_url
                        or record_id in self.completed
                        or record_id in self.terminal
                        or record_id in self.deferred
                    ):
                        continue
                    if not coordination.claim(record_id, worker, source_index):
                        continue
                    assignment = Assignment(worker, record_id, source_index, detail_url)
                    self.outstanding[worker] = assignment
                    self.issued += 1
                    return assignment, "claimed"
                deferred = self._claim_deferred(coordination, worker)
                if deferred is not None:
                    self.outstanding[worker] = deferred
                    self.deferred_attempted.add(deferred.record_id)
                    self.issued += 1
                    return deferred, "deferred_claimed"
            return None, stop_reason

    def _claim_deferred(
        self,
        coordination: LiepinCoordination,
        worker: str,
    ) -> Assignment | None:
        """Claim one legacy failed/deferred row after the main frontiers meet."""
        order = "DESC" if self._direction(worker) == "reverse" else "ASC"
        outstanding_ids = {item.record_id for item in self.outstanding.values()}
        candidates = coordination.connection.execute(
            f"""
            SELECT record_id, source_index, status
            FROM claims
            WHERE status = 'challenge'
               OR status LIKE 'deferred_%'
               OR status IN ('empty', 'error', 'claimed', 'success')
            ORDER BY source_index {order}
            """
        ).fetchall()
        for raw_record_id, raw_source_index, raw_status in candidates:
            record_id = str(raw_record_id)
            source_index = int(raw_source_index)
            status = str(raw_status)
            if (
                record_id in self.completed
                or record_id in self.terminal
                or record_id in self.deferred_attempted
                or record_id in outstanding_ids
                or not 1 <= source_index <= self.total_rows
            ):
                continue
            row = self.rows[source_index - 1]
            detail_url = str(row.get("detail_url") or "").strip()
            if record_key(row) != record_id or not detail_url:
                continue
            cursor = coordination.connection.execute(
                """
                UPDATE claims
                SET owner = ?, status = 'claimed', updated_at = ?
                WHERE record_id = ? AND status = ?
                """,
                (worker, time.time(), record_id, status),
            )
            if cursor.rowcount == 1:
                return Assignment(worker, record_id, source_index, detail_url)
        return None

    @staticmethod
    def _normalized_deferred_status(status: str) -> str:
        if status == "challenge":
            return "deferred_challenge"
        if status.startswith(FINALIZED_PREFIX):
            return status
        return f"deferred_{status}"

    def _advance_after_result(
        self,
        coordination: LiepinCoordination,
        worker: str,
        source_index: int,
    ) -> None:
        step = -1 if self._direction(worker) == "reverse" else 1
        next_index = source_index + step
        if 1 <= next_index <= self.total_rows:
            coordination.advance(worker, next_index)

    def accept_result(self, payload: Mapping[str, object]) -> dict[str, object]:
        record_id = str(payload.get("record_id") or "").strip()
        status = str(payload.get("status") or "").strip().lower()
        jd_value = payload.get("jd")
        jd = jd_value.strip() if isinstance(jd_value, str) else ""
        final_url = str(payload.get("final_url") or "").strip()
        source_index_value = payload.get("source_index")

        if not record_id or len(record_id) > 512:
            raise BridgeInputError("invalid record_id")
        if isinstance(source_index_value, bool) or not isinstance(source_index_value, int):
            raise BridgeInputError("source_index must be an integer")
        source_index = source_index_value
        if not 1 <= source_index <= self.total_rows:
            raise BridgeInputError("source_index is outside the source")
        if not STATUS_PATTERN.fullmatch(status):
            raise BridgeInputError("invalid status")
        if len(jd.encode("utf-8")) > MAX_BODY_BYTES // 2:
            raise BridgeInputError("jd is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if final_url:
            parsed = urlparse(final_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(final_url) > 4096:
                raise BridgeInputError("invalid final_url")
        expected_id = record_key(self.rows[source_index - 1])
        if record_id != expected_id:
            raise BridgeInputError("record_id does not match source_index", HTTPStatus.CONFLICT)
        if status == "success" and not jd:
            raise BridgeInputError("success requires a non-empty jd")

        with self.lock, LiepinCoordination(self.coord_db) as coordination:
            claim = coordination.connection.execute(
                "SELECT owner, source_index, status FROM claims WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if claim is None:
                raise BridgeInputError("record was not claimed", HTTPStatus.CONFLICT)
            worker, claimed_index, claim_status = str(claim[0]), int(claim[1]), str(claim[2])
            if worker not in self.workers or claimed_index != source_index:
                raise BridgeInputError("result does not match the active claim", HTTPStatus.CONFLICT)
            if claim_status != "claimed":
                self.outstanding.pop(worker, None)
                return {
                    "accepted": True,
                    "idempotent": True,
                    "record_id": record_id,
                    "status": claim_status,
                }

            config = self.worker_configs[worker]
            output = Path(config["output"])
            checkpoint = Path(config["state_dir"]) / "checkpoint.jsonl"
            row = self.rows[source_index - 1]
            result_status = status

            if status == "success":
                if record_id not in self.completed:
                    enriched = dict(row)
                    enriched.update(
                        {
                            "description_raw": jd,
                            "jd": jd,
                            "detail_source": f"edge_extension_{worker}",
                        }
                    )
                    if final_url:
                        enriched["detail_final_url"] = final_url
                    append_output(output, enriched)
                    self.completed.add(record_id)
                    self.successes += 1
            elif status in TERMINAL_STATUSES:
                append_checkpoint(checkpoint, record_id, str(row.get("detail_url") or ""), status)
                self.terminal.add(record_id)
                self.terminal_results += 1
            else:
                result_status = self._normalized_deferred_status(status)
                append_checkpoint(
                    checkpoint,
                    record_id,
                    str(row.get("detail_url") or ""),
                    result_status,
                )
                self.deferred.add(record_id)
                self.deferred_results += 1

            coordination.finish(record_id, worker, result_status)
            self._advance_after_result(coordination, worker, source_index)
            self.outstanding.pop(worker, None)
            return {
                "accepted": True,
                "idempotent": False,
                "record_id": record_id,
                "status": result_status,
            }

    def status(self) -> dict[str, object]:
        with self.lock, LiepinCoordination(self.coord_db) as coordination:
            database_status = coordination.status()
            status_counts = {
                str(status): int(count)
                for status, count in coordination.connection.execute(
                    "SELECT status, COUNT(*) FROM claims GROUP BY status"
                )
            }
            progress = database_status["progress"]
            return {
                "source_total": self.total_rows,
                "workers": list(self.workers),
                "progress": {
                    worker: progress.get(worker)
                    for worker in self.workers
                },
                "issued_this_run": self.issued,
                "success_this_run": self.successes,
                "deferred_this_run": self.deferred_results,
                "terminal_this_run": self.terminal_results,
                "outstanding": len(self.outstanding),
                "completed_known": len(self.completed),
                "deferred_known": len(self.deferred),
                "deferred_attempted_this_run": len(self.deferred_attempted),
                "terminal_known": len(self.terminal),
                "claim_status_counts": status_counts,
                "duplicate_claims": database_status["duplicate_claims"],
                "limit": self.limit,
            }


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        bridge: LiepinExtensionBridge,
        token: str,
    ) -> None:
        if server_address[0] != LOOPBACK_HOST:
            raise ValueError("bridge must bind to 127.0.0.1")
        self.bridge = bridge
        self.token = token
        super().__init__(server_address, BridgeRequestHandler)


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def log_message(self, _format: str, *_args: object) -> None:
        # Request logs can accidentally retain authentication headers in wrappers.
        return

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Bridge-Token",
        )
        self.send_header("Access-Control-Max-Age", "600")

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _authenticated(self) -> bool:
        supplied = self.headers.get("X-Bridge-Token", "")
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied = authorization[7:]
        return bool(supplied) and secrets.compare_digest(supplied, self.server.token)

    def _require_authentication(self) -> bool:
        if self._authenticated():
            return True
        self.close_connection = True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._require_authentication():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/status":
            self._send_json(HTTPStatus.OK, self.server.bridge.status())
            return
        if parsed.path != "/next":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        worker_values = parse_qs(parsed.query, keep_blank_values=True).get("worker", [])
        if len(worker_values) != 1:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "worker_required"})
            return
        try:
            assignment, reason = self.server.bridge.next_assignment(worker_values[0])
        except BridgeInputError as exc:
            self._send_json(exc.status, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "task": assignment.as_dict() if assignment else None,
                "reason": reason,
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._require_authentication():
            return
        if urlparse(self.path).path != "/result":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "chunked_body_not_supported"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "content_length_required"})
            return
        if content_length < 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return
        if content_length > MAX_BODY_BYTES:
            self.close_connection = True
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large"})
            return
        try:
            raw = self.rfile.read(content_length)
        except (TimeoutError, socket.timeout):
            self._send_json(HTTPStatus.REQUEST_TIMEOUT, {"error": "request_timeout"})
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "json_object_required"})
            return
        try:
            result = self.server.bridge.accept_result(payload)
        except BridgeInputError as exc:
            self._send_json(exc.status, {"error": str(exc)})
            return
        except OSError:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "output_write_failed"})
            return
        self._send_json(HTTPStatus.OK, result)


def create_runtime_token(path: Path) -> str:
    """Create a fresh per-process token without exposing it on stdout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="ascii", newline="\n") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return token


def write_extension_runtime_config(path: Path, port: int, token: str) -> None:
    """Atomically provision the local unpacked extension without printing secrets."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bridge_origin": f"http://{LOOPBACK_HOST}:{port}",
        "token": token,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_workers(value: str) -> tuple[str, ...]:
    workers = tuple(item.strip() for item in value.split(",") if item.strip())
    if not workers or len(set(workers)) != len(workers):
        raise argparse.ArgumentTypeError("workers must be a unique comma-separated list")
    unknown = set(workers).difference(WORKER_CONFIGS)
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown workers: {sorted(unknown)}")
    return workers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--coord-db", type=Path, default=COORD_DB_DEFAULT)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE_DEFAULT)
    parser.add_argument(
        "--extension-runtime-config",
        type=Path,
        default=EXTENSION_RUNTIME_CONFIG_DEFAULT,
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--workers",
        type=parse_workers,
        default=tuple(WORKER_CONFIGS),
        help="comma-separated worker names",
    )
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or args.limit < 0:
        parser.error("port and limit must be non-negative and bounded")

    token = create_runtime_token(args.token_file)
    bridge = LiepinExtensionBridge(
        source=args.source,
        coord_db=args.coord_db,
        workers=args.workers,
        limit=args.limit,
    )
    server = BridgeHTTPServer((LOOPBACK_HOST, args.port), bridge, token)
    write_extension_runtime_config(
        args.extension_runtime_config,
        server.server_port,
        token,
    )
    print(
        json.dumps(
            {
                "ready": True,
                "host": LOOPBACK_HOST,
                "port": server.server_port,
                "workers": list(args.workers),
                "token_file": str(args.token_file),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
