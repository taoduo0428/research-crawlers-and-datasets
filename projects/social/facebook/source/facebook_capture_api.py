from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from contextvars import ContextVar
from typing import Any, Callable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from facebook_share_contract import ShareValidationError, validate_share_result
from facebook_http_security import body_length, request_error, REQUEST_TIMEOUT_SECONDS

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_SERIAL = "emulator-5554"
DEFAULT_AVD_NAME = "fb-x86_64-api35"
DEFAULT_CAPTURE_PORT = 8081
DEFAULT_NATIVE_PORT = 18766
WORKER_CONFIG_NAME = "facebook_workers.json"
TRANSPORT_CLEANUP_RESTORED = "restored"
TRANSPORT_CLEANUP_NOT_REQUIRED = "not-required"
TRANSPORT_CLEANUP_RUNNER_OWNED = "runner-owned"
TRANSPORT_CLEANUP_PENDING = "pending"
TRANSPORT_CLEANUP_FAILED = "failed"
MAX_REQUEST_BYTES = 64 * 1024
MAX_BATCH_POSTS = 100
REACTION_SCROLL_LIMIT_MIN = 1
REACTION_SCROLL_LIMIT_MAX = 300
# ``None`` means the API caller did not request a bounded sample.  The
# capture command then keeps the existing unlimited/terminal pagination path.
REACTION_SCROLL_LIMIT_DEFAULT = None
BOUNDED_SAMPLE_COMPLETION_SCOPE = "bounded-sample"
TERMINAL_COMPLETION_SCOPE = "terminal"
BOUNDED_SAMPLE_STOP_REASONS = frozenset({"absolute-cap"})
GRAPH_API_VERSION = "v26.0"
REACTION_DEFAULTS = {
    # Keep the established adaptive pagination budget and unlimited cap for
    # requests without an explicit bounded-sample limit.
    "ReactionScrollCount": 60,
    "ReactionAbsoluteCap": 0,
    "ReactionExtensionChunk": 12,
    "ReactionTabLimit": 8,
    "ReactionScreenScrollCount": 2,
    "ReactionProgressPollIntervalMs": 500,
    "ReactionProgressPollTimeoutMs": 2200,
    "ReactionLiveProgressPollIntervalMs": 150,
    "ReactionLiveProgressPollTimeoutMs": 1200,
    "ReactionSteadyNoGrowthPollTimeoutMs": 500,
    "ReactionLongSwipeStartY": 1700,
    "ReactionLongSwipeEndY": 250,
    "ReactionLongSwipeDurationMs": 300,
    "ReactionPipelineBurstSize": 2,
    "ReactionPipelineSwipeGapMs": 150,
    "ReactionNativeScrollEnabled": 1,
    "ReactionNativeScrollTimeoutMs": 2500,
    "ReactionUiDumpEveryN": 64,
    "ReactionMinCycleMs": 700,
    "ReactionInitialPaginationCooldownSeconds": 4,
    "ReactionNoGrowthUiDumpEveryN": 1,
    "MaxFlows": 0,
}

STATIC_ASSETS = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}

ARTIFACT_SPECS = (
    ("raw-network.pcap", "原始网络 PCAP", "TLS 解密前", "raw-network.pcap"),
    ("raw-network-capture.json", "PCAP 校验信息", "完整性", "raw-network-capture.json"),
    ("raw-facebook-http.mitm", "完整 MITM flow", "实时解密", "raw-facebook-http.mitm"),
    ("raw-facebook-http-index.jsonl", "安全 flow 索引", "实时解密", "raw-facebook-http-index.jsonl"),
    ("safe-facebook-http-capture.jsonl", "脱敏 GraphQL 记录", "安全分析", "safe-facebook-http-capture.jsonl"),
    ("facebook-live-progress.json", "分页实时状态", "抓取进度", "facebook-live-progress.json"),
    ("raw-facebook-http-summary.json", "MITM 校验摘要", "完整性", "raw-facebook-http-summary.json"),
    ("facebook-sample-patterns.json", "解析与完整性报告", "解析结果", "analysis/facebook-sample-patterns.json"),
    ("facebook-reactor-users.csv", "Reaction 用户结果", "解析结果", "analysis/facebook-reactor-users.csv"),
)

ARTIFACT_PATHS = {name: Path(relative) for name, _label, _layer, relative in ARTIFACT_SPECS}
INLINE_ARTIFACT_SUFFIXES = {".csv", ".json", ".jsonl", ".log", ".txt"}
OPTIONAL_ARTIFACT_GROUPS = {
    "pcap": {"raw-network.pcap", "raw-network-capture.json"},
    "mitm": {"raw-facebook-http.mitm", "raw-facebook-http-index.jsonl", "raw-facebook-http-summary.json"},
    "safe_capture": {"safe-facebook-http-capture.jsonl"},
    "progress": {"facebook-live-progress.json"},
}
# Default to the lightweight evidence needed for live parsing and progress.
# PCAP and full MITM archives remain opt-in diagnostics because they add heavy
# disk I/O without being required for the final Reaction-user result.
DEFAULT_SAVE_ARTIFACTS = ("safe_capture", "progress")
REQUIRED_ARTIFACTS = {"facebook-sample-patterns.json", "facebook-reactor-users.csv"}
ARTIFACT_GROUP_BY_NAME = {
    name: group
    for group, names in OPTIONAL_ARTIFACT_GROUPS.items()
    for name in names
}
BATCH_ARTIFACT_SPECS = (
    ("batch-summary.json", "批次汇总", "批次结果"),
    ("batch-posts.csv", "帖子任务明细", "批次结果"),
    ("batch-reactor-users.csv", "帖子-用户记录", "批次结果"),
    ("batch-user-post-relations.csv", "用户与帖子关系", "批次结果"),
)


class ApiError(RuntimeError):
    pass


class SchedulerStartError(RuntimeError):
    """Native scheduler launch failed; state must be inspected before retry."""


class WorkerUnavailableError(ApiError):
    """The selected worker could not provide a usable Android device."""


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: str
    serial: str
    avd_name: str
    capture_port: int
    native_port: int
    enabled: bool
    cooldown_seconds: int = 900
    frida_ssl_unpin: bool = False


def _default_worker(serial: str = DEFAULT_SERIAL) -> WorkerConfig:
    return WorkerConfig(
        worker_id="worker-01",
        serial=serial,
        avd_name=DEFAULT_AVD_NAME,
        capture_port=DEFAULT_CAPTURE_PORT,
        native_port=DEFAULT_NATIVE_PORT,
        enabled=True,
    )


def validate_worker_config(workers: list[WorkerConfig]) -> list[WorkerConfig]:
    if not workers:
        raise ApiError("worker configuration must contain at least one worker")
    if not any(worker.enabled for worker in workers):
        raise ApiError("worker configuration must contain at least one enabled worker")
    unique_fields = ("worker_id", "serial", "avd_name")
    for field in unique_fields:
        values = [getattr(worker, field) for worker in workers]
        if len(values) != len(set(values)):
            raise ApiError(f"worker configuration has duplicate {field}")
    for worker in workers:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", worker.worker_id):
            raise ApiError(f"invalid worker_id: {worker.worker_id!r}")
        if not worker.serial.strip() or not worker.avd_name.strip():
            raise ApiError(f"worker {worker.worker_id} requires serial and avd_name")
        for name, port in (("capture_port", worker.capture_port), ("native_port", worker.native_port)):
            if not 1024 <= port <= 65535:
                raise ApiError(f"worker {worker.worker_id} has invalid {name}: {port}")
        if worker.capture_port == worker.native_port:
            raise ApiError(f"worker {worker.worker_id} capture_port and native_port must differ")
        if worker.cooldown_seconds < 0:
            raise ApiError(f"worker {worker.worker_id} cooldown_seconds cannot be negative")
    all_ports = [port for worker in workers for port in (worker.capture_port, worker.native_port)]
    if len(all_ports) != len(set(all_ports)):
        raise ApiError("worker capture and native ports must be globally unique")
    return workers


def load_worker_config(path: Path | None = None, fallback_serial: str = DEFAULT_SERIAL) -> list[WorkerConfig]:
    config_path = path or (BASE_DIR / WORKER_CONFIG_NAME)
    if not config_path.is_file():
        return [_default_worker(fallback_serial)]
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(f"worker configuration could not be read: {config_path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ApiError("worker configuration version must be 1")
    raw_workers = payload.get("workers")
    if not isinstance(raw_workers, list):
        raise ApiError("worker configuration workers must be a list")
    workers: list[WorkerConfig] = []
    try:
        for raw in raw_workers:
            if not isinstance(raw, dict):
                raise TypeError("worker entry must be an object")
            enabled = raw["enabled"]
            if not isinstance(enabled, bool):
                raise TypeError("worker enabled must be boolean")
            frida_ssl_unpin = raw.get("frida_ssl_unpin", False)
            if not isinstance(frida_ssl_unpin, bool):
                raise TypeError("worker frida_ssl_unpin must be boolean")
            workers.append(
                WorkerConfig(
                    worker_id=str(raw["worker_id"]).strip(),
                    serial=str(raw["serial"]).strip(),
                    avd_name=str(raw["avd_name"]).strip(),
                    capture_port=int(raw["capture_port"]),
                    native_port=int(raw["native_port"]),
                    enabled=enabled,
                    cooldown_seconds=int(raw.get("cooldown_seconds", 900)),
                    frida_ssl_unpin=frida_ssl_unpin,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("worker configuration contains an invalid worker entry") from exc
    return validate_worker_config(workers)


def write_worker_config(path: Path, workers: list[WorkerConfig]) -> None:
    validated = validate_worker_config(workers)
    _atomic_write_json(path, {"version": 1, "workers": [asdict(worker) for worker in validated]})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _job_manifest_path(job_id: str) -> Path:
    return BASE_DIR / "api-jobs" / f"{job_id}.job.json"


def _batch_manifest_path(batch_id: str) -> Path:
    return BASE_DIR / "api-batches" / f"{batch_id}.batch.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def normalize_post_url(value: str) -> str:
    value = str(value or "").strip().replace("\\u0026", "&")
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ApiError("invalid post_url") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise ApiError("post_url must be an absolute http(s) URL")
    if (host != "facebook.com" and not host.endswith(".facebook.com")):
        raise ApiError("post_url must be a Facebook URL")
    if (parsed.username is not None or parsed.password is not None
            or port not in {None, 80 if parsed.scheme == "http" else 443}
            or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ApiError("post_url contains unsupported authority or characters")
    return value


def normalize_collect(value: Any) -> list[str]:
    if value is None:
        return ["reactions"]
    if not isinstance(value, list):
        raise ApiError("collect must be a list")
    allowed = {"reactions", "shares"}
    result: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name not in allowed:
            raise ApiError(f"unsupported collect item: {name or item}")
        if name not in result:
            result.append(name)
    if not result:
        raise ApiError("collect cannot be empty")
    return result


def normalize_save_artifacts(value: Any) -> list[str]:
    if value is None:
        return list(DEFAULT_SAVE_ARTIFACTS)
    if not isinstance(value, list):
        raise ApiError("save_artifacts must be a list")
    result: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name not in OPTIONAL_ARTIFACT_GROUPS:
            raise ApiError(f"unsupported artifact group: {name or item}")
        if name not in result:
            result.append(name)
    return result


def normalize_reaction_scroll_limit(value: Any) -> int | None:
    if value is None:
        return REACTION_SCROLL_LIMIT_DEFAULT
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(
            "reaction_scroll_limit must be an integer between "
            f"{REACTION_SCROLL_LIMIT_MIN} and {REACTION_SCROLL_LIMIT_MAX}"
        )
    if not REACTION_SCROLL_LIMIT_MIN <= value <= REACTION_SCROLL_LIMIT_MAX:
        raise ApiError(
            "reaction_scroll_limit must be an integer between "
            f"{REACTION_SCROLL_LIMIT_MIN} and {REACTION_SCROLL_LIMIT_MAX}"
        )
    return value


def normalize_persisted_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a job manifest while preserving the old unlimited default.

    The first pagination-limit manifests did not persist an explicitness bit,
    so a stored numeric value may only be a materialized default.  Treat those
    manifests as unlimited; only a manifest with an explicit true marker can
    restore a bounded sample.
    """
    if not isinstance(payload, dict):
        raise ApiError("persisted request must be an object")
    normalized_payload = dict(payload)
    if normalized_payload.get("reaction_scroll_limit_explicit") is not True:
        normalized_payload.pop("reaction_scroll_limit", None)
        normalized_payload.pop("reaction_scroll_limit_explicit", None)
    request = normalize_request(normalized_payload)
    if normalized_payload.get("reaction_scroll_limit_explicit") is not True:
        request["reaction_scroll_limit"] = REACTION_SCROLL_LIMIT_DEFAULT
        request["reaction_scroll_limit_explicit"] = False
    return request


def _reaction_limit_is_explicit(request: dict[str, Any]) -> bool:
    raw_limit = request.get("reaction_scroll_limit")
    if "reaction_scroll_limit_explicit" in request:
        return bool(request.get("reaction_scroll_limit_explicit")) and raw_limit not in (None, 0)
    # Keep direct callers that already provide a numeric limit working while
    # treating the historical zero sentinel as unlimited.
    return raw_limit not in (None, 0)


def _reaction_command_limits(request: dict[str, Any]) -> tuple[int, int]:
    """Resolve the runner budgets without turning the API default into a cap."""
    explicit = _reaction_limit_is_explicit(request)
    if explicit:
        limit = normalize_reaction_scroll_limit(request.get("reaction_scroll_limit"))
        if limit is not None:
            return limit, limit
    return (
        int(REACTION_DEFAULTS["ReactionScrollCount"]),
        int(REACTION_DEFAULTS["ReactionAbsoluteCap"]),
    )


def normalize_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError("JSON body must be an object")
    post_url = normalize_post_url(payload.get("post_url", ""))
    post_id = str(payload.get("post_id") or "").strip()
    if not post_url and not post_id:
        raise ApiError("post_url or post_id is required")
    if post_id and not re.fullmatch(r"[A-Za-z0-9_.:-]+", post_id):
        raise ApiError("post_id contains unsupported characters")
    reaction_scroll_limit = normalize_reaction_scroll_limit(payload.get("reaction_scroll_limit"))
    return {
        "post_url": post_url,
        "post_id": post_id,
        "collect": normalize_collect(payload.get("collect")),
        "save_artifacts": normalize_save_artifacts(payload.get("save_artifacts")),
        "reaction_scroll_limit": reaction_scroll_limit,
        "reaction_scroll_limit_explicit": (
            "reaction_scroll_limit" in payload and reaction_scroll_limit is not None
        ),
    }


def normalize_batch_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError("JSON body must be an object")
    values = payload.get("post_urls")
    if not isinstance(values, list):
        raise ApiError("post_urls must be a list")
    post_urls: list[str] = []
    for value in values:
        url = normalize_post_url(str(value or ""))
        if url and url not in post_urls:
            post_urls.append(url)
    if not post_urls:
        raise ApiError("post_urls cannot be empty")
    if len(post_urls) > MAX_BATCH_POSTS:
        raise ApiError(f"a batch can contain at most {MAX_BATCH_POSTS} unique posts")
    reaction_scroll_limit = normalize_reaction_scroll_limit(payload.get("reaction_scroll_limit"))
    return {
        "post_urls": post_urls,
        "collect": normalize_collect(payload.get("collect")),
        "save_artifacts": normalize_save_artifacts(payload.get("save_artifacts")),
        "reaction_scroll_limit": reaction_scroll_limit,
        "reaction_scroll_limit_explicit": (
            "reaction_scroll_limit" in payload and reaction_scroll_limit is not None
        ),
    }


def _run_quiet(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {}
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation:
        kwargs["creationflags"] = creation
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **kwargs,
    )


_health_adb_state_scope: ContextVar[dict[str, Any] | None] = ContextVar("facebook_health_adb_states", default=None)


def _health_adb_inventory(adb: str, timeout: float) -> dict[str, str] | None:
    """Validated state list for one health request; invalid data uses old probes."""
    try:
        result = _run_quiet([adb, "devices"], timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not isinstance(result.stdout, str):
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines or lines[0] != "List of devices attached":
        return None
    states: dict[str, str] = {}
    allowed = {"device", "offline", "unauthorized", "no", "recovery", "sideload", "bootloader", "authorizing"}
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 2 or fields[0] in states or fields[1] not in allowed:
            return None
        states[fields[0]] = fields[1]
    return states


def adb_snapshot(serial: str = DEFAULT_SERIAL, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    adb = shutil.which("adb")
    if not adb:
        return {"state": "adb-not-found", "boot_completed": False, "proxy": ""}
    timeout = max(0.1, float(timeout_seconds))
    scope = _health_adb_state_scope.get()
    inventory = None
    if scope is not None:
        if not scope["queried"]:
            scope["queried"] = True
            scope["states"] = _health_adb_inventory(adb, timeout)
        inventory = scope["states"]
    if inventory is not None:
        if inventory.get(serial) != "device":
            return {"state": "offline", "boot_completed": False, "proxy": ""}
    else:
        try:
            state = _run_quiet([adb, "-s", serial, "get-state"], timeout)
        except (OSError, subprocess.SubprocessError):
            return {"state": "offline", "boot_completed": False, "proxy": ""}
        if state.returncode != 0 or state.stdout.strip() != "device":
            return {"state": "offline", "boot_completed": False, "proxy": ""}
    try:
        boot = _run_quiet([adb, "-s", serial, "shell", "getprop", "sys.boot_completed"], timeout)
    except (OSError, subprocess.SubprocessError):
        return {"state": "offline", "boot_completed": False, "proxy": ""}
    if boot.returncode != 0:
        return {"state": "offline", "boot_completed": False, "proxy": ""}
    snapshot = {
        "state": "device",
        "boot_completed": boot.stdout.strip() == "1",
        "proxy": "",
    }
    if not snapshot["boot_completed"]:
        return snapshot
    try:
        proxy = _run_quiet([adb, "-s", serial, "shell", "settings", "get", "global", "http_proxy"], timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        snapshot["proxy_error"] = type(exc).__name__
        return snapshot
    if proxy.returncode != 0:
        snapshot["proxy_error"] = f"exit:{proxy.returncode}"
        return snapshot
    snapshot["proxy"] = proxy.stdout.strip()
    return snapshot


def ensure_emulator(
    serial: str = DEFAULT_SERIAL,
    timeout_seconds: int = 180,
    *,
    worker: WorkerConfig | None = None,
    avd_name: str | None = None,
    capture_port: int = DEFAULT_CAPTURE_PORT,
    native_port: int = DEFAULT_NATIVE_PORT,
    process_callback: Callable[[subprocess.Popen[Any] | None], None] | None = None,
) -> dict[str, Any]:
    if worker is not None:
        serial = worker.serial
        avd_name = worker.avd_name
        capture_port = worker.capture_port
        native_port = worker.native_port
    avd_name = avd_name or DEFAULT_AVD_NAME
    snapshot = adb_snapshot(serial)
    if snapshot["state"] == "device" and snapshot["boot_completed"]:
        return snapshot
    if sys.platform == 'darwin':
        from facebook_macos_runtime import start_emulator
        start_emulator(serial, avd_name, timeout_seconds)
        return adb_snapshot(serial)
    script = BASE_DIR / "start_facebook_stable.ps1"
    if not script.exists():
        raise ApiError(f"emulator bootstrap script not found: {script}")
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise WorkerUnavailableError("PowerShell is required to start the current Windows Facebook worker")
    kwargs: dict[str, Any] = {}
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation:
        kwargs["creationflags"] = creation
    bootstrap_log = BASE_DIR / "api-jobs" / "emulator-bootstrap.log"
    bootstrap_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = bootstrap_log.open("a", encoding="utf-8")
    bootstrap_process: subprocess.Popen[Any] | None = None
    try:
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Serial",
            serial,
            "-AvdName",
            avd_name,
            "-CapturePort",
            str(capture_port),
            "-NativePort",
            str(native_port),
        ]
        if worker is not None and worker.frida_ssl_unpin:
            command.append("-RestoreMitmCertificate")
        bootstrap_process = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            **kwargs,
        )
        if process_callback is not None:
            process_callback(bootstrap_process)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            snapshot = adb_snapshot(serial)
            if snapshot["state"] == "device" and snapshot["boot_completed"]:
                if bootstrap_process is not None and bootstrap_process.poll() is None:
                    remaining = max(0.1, deadline - time.monotonic())
                    try:
                        bootstrap_process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired as exc:
                        raise WorkerUnavailableError(
                            f"Android emulator bootstrap did not finish within {timeout_seconds}s"
                        ) from exc
                return_code = getattr(bootstrap_process, "returncode", None)
                if isinstance(return_code, int) and return_code != 0:
                    raise WorkerUnavailableError(
                        f"Android emulator bootstrap failed with exit {return_code}"
                    )
                if process_callback is not None:
                    process_callback(None)
                return snapshot
            time.sleep(2)
        raise WorkerUnavailableError(f"Android emulator did not become ready within {timeout_seconds}s")
    except Exception as exc:
        if bootstrap_process is not None and bootstrap_process.poll() is None:
            stopped = _terminate_tree(bootstrap_process)
            if process_callback is not None:
                process_callback(None)
            if not stopped:
                raise WorkerUnavailableError(
                    f"Android emulator bootstrap did not stop after failure; refusing worker takeover: {exc}"
                ) from exc
        raise
    finally:
        log_handle.close()


def build_capture_command(
    request: dict[str, Any],
    serial: str = DEFAULT_SERIAL,
    run_dir: Path | None = None,
    *,
    proxy_port: int = DEFAULT_CAPTURE_PORT,
    native_port: int = DEFAULT_NATIVE_PORT,
    frida_ssl_unpin: bool = False,
) -> list[str]:
    reaction_scroll_count, reaction_absolute_cap = _reaction_command_limits(request)
    if sys.platform == 'darwin':
        args = [sys.executable, str(BASE_DIR / 'facebook_android_capture.py'),
                '--serial', serial, '--capture-port', str(proxy_port),
                '--native-port', str(native_port),
                '--post-url', request.get('post_url') or '',
                '--collect', ','.join(request.get('collect') or ['reactions']),
                '--api-log',
                '--save-artifacts', ','.join(request.get('save_artifacts', DEFAULT_SAVE_ARTIFACTS))]
        if _reaction_limit_is_explicit(request):
            args += ['--max-scrolls', str(reaction_scroll_count)]
        if request.get('post_id'):
            args += ['--post-id', str(request['post_id'])]
        if run_dir is not None:
            args += ['--output-dir', str(run_dir)]
        if frida_ssl_unpin:
            args.append('--frida-ssl-unpin')
        return args
    powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    script = BASE_DIR / "run_facebook_capture.ps1"
    args = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Serial",
        serial,
        "-ProxyPort",
        str(proxy_port),
        "-DurationSeconds",
        "0",
        "-ScrollCount",
        "0",
        "-ScrollDelaySeconds",
        "0",
        "-ReadOnlyWaitSeconds",
        "3",
        "-AutoScreenThenDeep",
        "-ReactionScrollCount",
        str(reaction_scroll_count),
        "-ReactionAbsoluteCap",
        str(reaction_absolute_cap),
        "-ReactionExtensionChunk",
        str(REACTION_DEFAULTS["ReactionExtensionChunk"]),
        "-ReactionTabLimit",
        str(REACTION_DEFAULTS["ReactionTabLimit"]),
        "-ReactionScreenScrollCount",
        str(REACTION_DEFAULTS["ReactionScreenScrollCount"]),
        "-ReactionProgressPollIntervalMs",
        str(REACTION_DEFAULTS["ReactionProgressPollIntervalMs"]),
        "-ReactionProgressPollTimeoutMs",
        str(REACTION_DEFAULTS["ReactionProgressPollTimeoutMs"]),
        "-ReactionLiveProgressPollIntervalMs",
        str(REACTION_DEFAULTS["ReactionLiveProgressPollIntervalMs"]),
        "-ReactionLiveProgressPollTimeoutMs",
        str(REACTION_DEFAULTS["ReactionLiveProgressPollTimeoutMs"]),
        "-ReactionSteadyNoGrowthPollTimeoutMs",
        str(REACTION_DEFAULTS["ReactionSteadyNoGrowthPollTimeoutMs"]),
        "-ReactionLongSwipeStartY",
        str(REACTION_DEFAULTS["ReactionLongSwipeStartY"]),
        "-ReactionLongSwipeEndY",
        str(REACTION_DEFAULTS["ReactionLongSwipeEndY"]),
        "-ReactionLongSwipeDurationMs",
        str(REACTION_DEFAULTS["ReactionLongSwipeDurationMs"]),
        "-ReactionPipelineBurstSize",
        str(REACTION_DEFAULTS["ReactionPipelineBurstSize"]),
        "-ReactionPipelineSwipeGapMs",
        str(REACTION_DEFAULTS["ReactionPipelineSwipeGapMs"]),
        "-ReactionNativeScrollEnabled",
        str(REACTION_DEFAULTS["ReactionNativeScrollEnabled"]),
        "-ReactionNativeScrollTimeoutMs",
        str(REACTION_DEFAULTS["ReactionNativeScrollTimeoutMs"]),
        "-ReactionNativeScrollPort",
        str(native_port),
        "-ReactionUiDumpEveryN",
        str(REACTION_DEFAULTS["ReactionUiDumpEveryN"]),
        "-ReactionMinCycleMs",
        str(REACTION_DEFAULTS["ReactionMinCycleMs"]),
        "-ReactionInitialPaginationCooldownSeconds",
        str(REACTION_DEFAULTS["ReactionInitialPaginationCooldownSeconds"]),
        "-ReactionNoGrowthUiDumpEveryN",
        str(REACTION_DEFAULTS["ReactionNoGrowthUiDumpEveryN"]),
        "-MaxFlows",
        str(REACTION_DEFAULTS["MaxFlows"]),
    ]
    selected_artifacts = set(request.get("save_artifacts", DEFAULT_SAVE_ARTIFACTS))
    if "pcap" in selected_artifacts:
        args.append("-EnableRawNetworkCapture")
    if "mitm" not in selected_artifacts:
        args.append("-DisableRawHttpCapture")
    if frida_ssl_unpin:
        args.append("-EnableFridaSslUnpin")
    if run_dir is not None:
        args += ["-OutputDir", str(run_dir)]
    if request.get("post_url"):
        args += ["-PostUrl", request["post_url"]]
    if request.get("post_id"):
        args += ["-PostId", request["post_id"]]
    collect = set(request["collect"])
    if "reactions" in collect:
        args.append("-OpenReactionList")
    if "shares" in collect:
        # Share target navigation is sensitive to a stale Facebook activity
        # and to a cached Frida attachment.  Start a fresh app process for
        # share jobs only; reaction-only requests keep the warm-start path and
        # their existing timing/behavior unchanged.  This does not clear
        # account data, cookies, or app storage.
        args.append("-ColdStartFacebook")
        args.append("-OpenShareList")
        args += ["-ShareScrollCount", "0"]
    return args


def _cleanup_transport_after_capture(
    worker: WorkerConfig,
    *,
    timeout_seconds: int = 45,
) -> dict[str, Any]:
    """Run the bounded Windows transport cleanup and return its JSON result."""
    if sys.platform != "win32":
        # The native macOS runner owns its own signal-safe finally cleanup.
        return {
            "transport_cleanup_state": TRANSPORT_CLEANUP_NOT_REQUIRED,
            "transport_cleanup_error": None,
        }

    script = BASE_DIR / "facebook_cancel_transport_cleanup.ps1"
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return {
            "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
            "transport_cleanup_error": "PowerShell cleanup runner is unavailable",
        }
    if not script.is_file():
        return {
            "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
            "transport_cleanup_error": f"transport cleanup helper is missing: {script}",
        }

    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-BasePath",
        str(BASE_DIR),
        "-Serial",
        worker.serial,
        "-CapturePort",
        str(worker.capture_port),
    ]
    try:
        completed = _run_quiet(command, timeout=max(1, int(timeout_seconds)))
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
            "transport_cleanup_error": f"transport cleanup runner failed: {type(exc).__name__}: {exc}",
        }

    payload: dict[str, Any] = {}
    for line in reversed((completed.stdout or "").splitlines()):
        try:
            candidate = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    state = str(payload.get("transport_cleanup_state") or "").strip()
    error = str(
        payload.get("transport_cleanup_error")
        or payload.get("error")
        or (completed.stderr or "").strip()
        or f"cleanup helper exited with code {completed.returncode}"
    ).strip()
    conflicting_error = False
    for key in ("transport_cleanup_error", "error"):
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or value.strip()):
            conflicting_error = True
            break
    if completed.returncode == 0 and state == TRANSPORT_CLEANUP_RESTORED and not conflicting_error:
        payload["transport_cleanup_state"] = TRANSPORT_CLEANUP_RESTORED
        payload["transport_cleanup_error"] = None
        return payload
    if state == TRANSPORT_CLEANUP_RESTORED and conflicting_error:
        state = TRANSPORT_CLEANUP_FAILED
    payload["transport_cleanup_state"] = state or TRANSPORT_CLEANUP_FAILED
    payload["transport_cleanup_error"] = error
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_csv(path: Path, *, required_columns: set[str] | None = None) -> list[dict[str, str]]:
    if not path.exists():
        if required_columns is not None:
            raise ApiError(f"required CSV missing: {path}")
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if required_columns is not None and not required_columns.issubset(set(reader.fieldnames or [])):
                raise ApiError(f"CSV missing required columns {sorted(required_columns)}: {path}")
            return [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        if required_columns is not None:
            raise ApiError(f"required CSV unreadable or invalid: {path}") from exc
        raise


def resolve_static_asset(request_path: str) -> Path | None:
    name = STATIC_ASSETS.get(urlparse(request_path).path)
    return WEB_DIR / name if name else None


def _checked_artifact_path(root: Path, relative: Path, *, unavailable: str = "artifact is not available yet") -> Path:
    """Constrain artifacts below a trusted root without changing logical names.

    The root may be relocated. Child symbolic links and escaping directory
    junctions are not artifact files. This is not a hostile-local-writer sandbox.
    """
    relative = Path(relative)
    if relative.anchor or ".." in relative.parts:
        raise ApiError("artifact path is outside its run directory")
    candidate = root / relative
    try:
        resolved_root = root.resolve(strict=True)
        current = root
        linked = False
        for part in relative.parts:
            current = current / part
            linked = linked or current.is_symlink()
        resolved_candidate = candidate.resolve(strict=True)
        confined = resolved_candidate.is_relative_to(resolved_root)
    except (OSError, RuntimeError) as exc:
        raise ApiError(unavailable) from exc
    if linked or not confined:
        raise ApiError("artifact path is outside its run directory or linked")
    try:
        nonempty_file = candidate.is_file() and candidate.stat().st_size > 0
    except OSError as exc:
        raise ApiError(unavailable) from exc
    if not nonempty_file:
        raise ApiError(unavailable)
    return candidate


def _artifact_metadata(
    run_dir: Path,
    job_status: str,
    save_artifacts: list[str] | None = None,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    selected_groups = set(save_artifacts if save_artifacts is not None else DEFAULT_SAVE_ARTIFACTS)
    for name, label, layer, relative in ARTIFACT_SPECS:
        path = run_dir / Path(relative)
        group = ARTIFACT_GROUP_BY_NAME.get(name)
        selected = name in REQUIRED_ARTIFACTS or group in selected_groups
        try:
            checked = _checked_artifact_path(run_dir, Path(relative))
            size = checked.stat().st_size
        except (ApiError, OSError):
            size = 0
        if not selected:
            state = "discarding" if job_status in {"queued", "running", "cancelling"} else "not_saved"
            size = 0
        elif size > 0:
            state = "ready" if job_status in {"completed", "failed", "cancelled"} else "writing"
        else:
            state = "missing" if job_status == "completed" else "waiting"
        artifacts.append(
            {
                "name": name,
                "label": label,
                "layer": layer,
                "path": str(path),
                "size_bytes": size,
                "state": state,
                "viewable": path.suffix.lower() in INLINE_ARTIFACT_SUFFIXES,
                "url": f"/v1/jobs/{{job_id}}/artifacts/{name}",
                "required": name in REQUIRED_ARTIFACTS,
                "selected": selected,
            }
        )
    return artifacts


def _valid_reactor_user_count(run_dir: Path) -> int:
    for path in (
        run_dir / "analysis" / "facebook-reactor-users.csv",
        run_dir / "checkpoint" / "facebook-reactor-users.csv",
    ):
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return sum(1 for row in csv.DictReader(handle) if str(row.get("user_id") or "").strip())
        except OSError:
            continue
    return 0


def _completion_scope_for_jobs(jobs: list[dict[str, Any]]) -> str | None:
    completed = [job for job in jobs if str(job.get("status") or "") == "completed"]
    if not completed:
        return None
    scopes = [str(job.get("completion_scope") or "").strip() for job in completed]
    known = {scope for scope in scopes if scope}
    if not known:
        return None
    if len(known) != 1 or any(not scope for scope in scopes):
        return "mixed"
    return next(iter(known))


def _batch_post_identity(job: dict[str, Any], post_index: int) -> str:
    job_id = str(job.get("job_id") or "").strip()
    if job_id:
        return f"job:{job_id}"
    return f"index:{post_index}"


def _read_batch_reactor_rows(job: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for evidence_dir in _job_evidence_dirs(job):
        if evidence_dir.name.casefold() == "checkpoint":
            path = evidence_dir / "facebook-reactor-users.csv"
        else:
            path = evidence_dir / "analysis" / "facebook-reactor-users.csv"
        path_key = os.path.normcase(os.path.abspath(str(path)))
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        rows.extend(_read_reactor_rows_file(path))
    return rows


def write_batch_outputs(
    batch_dir: Path,
    batch_id: str,
    jobs: list[dict[str, Any]],
    *,
    persist: bool = True,
) -> dict[str, Any]:
    if persist:
        batch_dir.mkdir(parents=True, exist_ok=True)
    post_rows: list[dict[str, Any]] = []
    user_rows_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    relation_rows_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    cross_post_user_ids: set[str] = set()
    per_post_valid_sum = 0
    raw_post_user_occurrences = 0
    status_counts = {name: 0 for name in ("queued", "running", "cancelling", "completed", "failed", "cancelled")}

    for index, job in enumerate(jobs, start=1):
        status = str(job.get("status") or "queued")
        if status in status_counts:
            status_counts[status] += 1
        request = job.get("request") or {}
        post_url = str(request.get("post_url") or "")
        run_dir_text = str(job.get("run_dir") or "")
        run_dir = Path(run_dir_text) if run_dir_text else Path()
        evidence_dirs = _job_evidence_dirs(job)
        raw_rows = _read_batch_reactor_rows(job)
        raw_post_user_occurrences += sum(
            bool(str(row.get("user_id") or "").strip()) for row in raw_rows
        )
        rows = _dedupe_reactor_rows(raw_rows)
        post_user_keys: set[tuple[str, str]] = set()
        job_id = str(job.get("job_id") or "")
        post_identity = _batch_post_identity(job, index)
        for row in rows:
            user_id = str(row.get("user_id") or "").strip()
            if not user_id:
                continue
            cross_post_user_ids.add(user_id)
            pair_key = (post_identity, _reactor_row_key(row)[0])
            post_user_keys.add(pair_key)
            if pair_key not in user_rows_by_pair:
                pair_row = {
                    "user_id": user_id,
                    "user_name": str(row.get("user_name") or ""),
                    "avatar_url": str(row.get("avatar_url") or ""),
                    "post_index": index,
                    "job_id": job_id,
                    "post_url": post_url,
                    "source_post_id": str(row.get("source_post_id") or ""),
                    "reaction_id": str(row.get("reaction_id") or ""),
                }
                for field, value in row.items():
                    if field not in pair_row:
                        pair_row[field] = str(value or "")
                user_rows_by_pair[pair_key] = pair_row
                relation = {
                    "user_id": user_id,
                    "job_id": job_id,
                    "post_index": index,
                    "post_url": post_url,
                    "source_post_id": str(row.get("source_post_id") or ""),
                    "reaction_id": str(row.get("reaction_id") or ""),
                }
                for field, value in row.items():
                    if field not in relation:
                        relation[field] = str(value or "")
                relation_rows_by_pair[pair_key] = relation
        valid_users = len(post_user_keys)
        per_post_valid_sum += valid_users
        metrics_dir = evidence_dirs[0] if evidence_dirs else run_dir
        metrics = _merge_reaction_progress(evidence_dirs) if evidence_dirs else {}
        outcome = _job_result_outcome(job, evidence_dirs, valid_users) or {}
        post_rows.append(
            {
                "post_index": index,
                "job_id": str(job.get("job_id") or ""),
                "post_url": post_url,
                "status": status,
                "completion_scope": str(job.get("completion_scope") or "") or None,
                "valid_users": valid_users,
                "result_code": outcome.get("code", ""),
                "result_reason": outcome.get("reason", ""),
                "result_label": outcome.get("label", ""),
                "reaction_pages": int(metrics.get("reaction_pages") or 0),
                "run_dir": run_dir_text,
            }
        )

    post_counts: dict[str, int] = {}
    for row in user_rows_by_pair.values():
        user_id = str(row.get("user_id") or "")
        post_counts[user_id] = post_counts.get(user_id, 0) + 1
    for row in user_rows_by_pair.values():
        row["post_count"] = post_counts.get(str(row.get("user_id") or ""), 0)
    cross_post_duplicate_occurrences = sum(max(0, count - 1) for count in post_counts.values())
    user_rows = sorted(
        user_rows_by_pair.values(),
        key=lambda row: (int(row["post_index"]), str(row["user_id"])),
    )
    relation_rows = sorted(
        relation_rows_by_pair.values(),
        key=lambda row: (int(row["post_index"]), str(row["user_id"])),
    )
    terminal_count = status_counts["completed"] + status_counts["failed"] + status_counts["cancelled"]
    if status_counts["running"] or status_counts["cancelling"]:
        batch_status = "running"
    elif status_counts["queued"]:
        batch_status = "queued"
    elif terminal_count == len(jobs) and (status_counts["failed"] or status_counts["cancelled"]):
        batch_status = "completed_with_errors"
    elif terminal_count == len(jobs):
        batch_status = "completed"
    else:
        batch_status = "queued"
    summary = {
        "batch_id": batch_id,
        "status": batch_status,
        "updated_at": utc_now(),
        "total_posts": len(jobs),
        "status_counts": status_counts,
        "completion_scope": _completion_scope_for_jobs(jobs),
        "per_post_valid_sum": per_post_valid_sum,
        "post_user_records": len(user_rows),
        "within_post_duplicate_occurrences": max(0, raw_post_user_occurrences - len(user_rows)),
        # Keep the earlier names for clients that have not migrated yet.
        "post_user_record_count": len(user_rows),
        "same_post_duplicate_occurrences": max(0, raw_post_user_occurrences - len(user_rows)),
        "cross_post_unique_users": len(cross_post_user_ids),
        "cross_post_duplicate_occurrences": cross_post_duplicate_occurrences,
    }
    if persist:
        _atomic_write_csv(
            batch_dir / "batch-posts.csv",
            [
                "post_index",
                "job_id",
                "post_url",
                "status",
                "completion_scope",
                "valid_users",
                "reaction_pages",
                "run_dir",
                "result_code",
                "result_reason",
                "result_label",
            ],
            post_rows,
        )
        user_fieldnames = [
            "user_id",
            "user_name",
            "avatar_url",
            "post_index",
            "job_id",
            "post_url",
            "source_post_id",
            "reaction_id",
            "post_count",
        ]
        user_fields_seen = set(user_fieldnames)
        for row in user_rows:
            for field in row:
                if field not in user_fields_seen:
                    user_fieldnames.append(field)
                    user_fields_seen.add(field)
        _atomic_write_csv(batch_dir / "batch-reactor-users.csv", user_fieldnames, user_rows)
        relation_fieldnames = [
            "user_id",
            "job_id",
            "post_index",
            "post_url",
            "source_post_id",
            "reaction_id",
        ]
        relation_fields_seen = set(relation_fieldnames)
        for row in relation_rows:
            for field in row:
                if field not in relation_fields_seen:
                    relation_fieldnames.append(field)
                    relation_fields_seen.add(field)
        _atomic_write_csv(
            batch_dir / "batch-user-post-relations.csv",
            relation_fieldnames,
            relation_rows,
        )
        _atomic_write_json(batch_dir / "batch-summary.json", summary)
    return summary


def _reaction_progress(run_dir: Path) -> dict[str, Any]:
    live = _read_json(run_dir / "facebook-live-progress.json")
    sources = (((live.get("families") or {}).get("reaction") or {}).get("sources") or {})
    candidates = [value for value in sources.values() if isinstance(value, dict)] if isinstance(sources, dict) else []
    source = max(candidates, key=lambda item: int(item.get("user_count") or 0), default={})
    session = live.get("session") or {}
    diagnostics = _read_json(run_dir / "readonly-ui" / "exploration-diagnostics.json")
    pagination_runs = [item for item in diagnostics.get("pagination_runs") or [] if isinstance(item, dict)]
    pagination = pagination_runs[-1] if pagination_runs else {}
    report = _read_json(run_dir / "analysis" / "facebook-sample-patterns.json")
    integrity = report.get("reaction_integrity") or {}
    enumeration = ((report.get("enumeration_status") or {}).get("reactions") or {})
    return {
        "reaction_users": int(source.get("user_count") or enumeration.get("enumerated_identity_count") or integrity.get("unique_reactor_identities") or 0),
        "reaction_pages": int(source.get("page_count") or integrity.get("successful_page_count") or 0),
        "query_records": int(source.get("query_records") or 0),
        "terminal": bool(source.get("terminal_page_seen") or pagination.get("terminal_seen") or integrity.get("terminal_page_seen")),
        "rate_limits": int(source.get("rate_limit_count") or 0),
        "latest_graphql_error_code": source.get("latest_graphql_error_code"),
        "latest_api_error_code": source.get("latest_api_error_code"),
        "temporary_feature_blocks": int(session.get("temporary_feature_block_count") or 0),
        "session_restricted": bool(
            source.get("session_restricted")
            or session.get("temporary_feature_block_count")
            or session.get("latest_temporary_feature_block_graphql_code")
            or session.get("latest_temporary_feature_block_api_code")
        ),
        "record_lines": int(live.get("record_line_count") or 0),
        "attempts": int(pagination.get("attempts") or 0),
        "growth_events": int(pagination.get("growth_events") or 0),
        "elapsed_ms": int(pagination.get("elapsed_ms") or 0),
        "stop_reason": str(pagination.get("stop_reason") or ""),
        "updated_at": live.get("updated_at"),
    }


def _share_progress(run_dir: Path) -> dict[str, Any]:
    """Read live Share counters without treating them as accepted results."""
    live = _read_json(run_dir / "facebook-live-progress.json")
    sources = (((live.get("families") or {}).get("share") or {}).get("sources") or {})
    candidates = [value for value in sources.values() if isinstance(value, dict)] if isinstance(sources, dict) else []
    source = max(candidates, key=lambda item: int(item.get("user_count") or 0), default={})
    return {
        "share_users": int(source.get("user_count") or 0),
        "share_pages": int(source.get("page_count") or 0),
        "share_query_records": int(source.get("query_records") or 0),
        "share_terminal": bool(source.get("terminal_page_seen")),
        "share_open_page_seen": bool(source.get("open_page_seen")),
        "share_source_post_id": str(source.get("source_post_id") or ""),
        "share_updated_at": live.get("updated_at"),
    }


def _merge_share_progress(run_dirs: list[Path]) -> dict[str, Any]:
    reports = [_share_progress(run_dir) for run_dir in run_dirs]
    if not reports:
        return {}
    current = reports[0]
    return {
        "share_users": max(int(report.get("share_users") or 0) for report in reports),
        "share_pages": max(int(report.get("share_pages") or 0) for report in reports),
        "share_query_records": max(int(report.get("share_query_records") or 0) for report in reports),
        "share_terminal": bool(current.get("share_terminal")),
        "share_open_page_seen": bool(current.get("share_open_page_seen")),
        "share_source_post_id": str(current.get("share_source_post_id") or ""),
        "share_updated_at": current.get("share_updated_at"),
    }


def _merge_reaction_progress(run_dirs: list[Path]) -> dict[str, Any]:
    reports = [_reaction_progress(run_dir) for run_dir in run_dirs]
    if not reports:
        return {}
    merged = dict(reports[0])
    for key in ("reaction_users", "reaction_pages", "query_records", "record_lines", "attempts", "growth_events", "elapsed_ms"):
        merged[key] = max(int(report.get(key) or 0) for report in reports)
    merged["rate_limits"] = max(int(report.get("rate_limits") or 0) for report in reports)
    # The first directory is the current attempt. Historical attempts supply
    # counts, while terminal/open state belongs to the current attempt.
    merged["terminal"] = bool(reports[0].get("terminal"))
    merged["stop_reason"] = str(reports[0].get("stop_reason") or "")
    merged["latest_graphql_error_code"] = reports[0].get("latest_graphql_error_code")
    merged["latest_api_error_code"] = reports[0].get("latest_api_error_code")
    merged["temporary_feature_blocks"] = max(
        int(report.get("temporary_feature_blocks") or 0) for report in reports
    )
    merged["session_restricted"] = bool(reports[0].get("session_restricted"))
    merged["updated_at"] = reports[0].get("updated_at")
    return merged


def _count_reactor_users(run_dirs: list[Path]) -> int:
    rows: list[dict[str, str]] = []
    for run_dir in run_dirs:
        rows.extend(_read_reactor_rows(run_dir))
        rows.extend(_read_reactor_rows_file(run_dir / "facebook-reactor-users.csv"))
        rows.extend(_read_reactor_rows_file(run_dir / "checkpoint" / "facebook-reactor-users.csv"))
    return len(
        {
            str(row.get("user_id") or "").strip()
            for row in _dedupe_reactor_rows(rows)
            if str(row.get("user_id") or "").strip()
        }
    )


def _job_evidence_dirs(job: dict[str, Any]) -> list[Path]:
    values = [job.get("capture_run_dir"), *(job.get("attempt_dirs") or []), job.get("run_dir")]
    paths: list[Path] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        path = Path(text)
        if not path.is_absolute():
            path = BASE_DIR / path
        if path not in paths:
            paths.append(path)
    root_text = str(job.get("run_dir") or "").strip()
    if root_text:
        root = Path(root_text)
        if not root.is_absolute():
            root = BASE_DIR / root
        checkpoint = root / "checkpoint"
        if checkpoint not in paths:
            paths.append(checkpoint)
    return paths


def build_capture_checkpoint(run_dir: Path, *, worker_id: str | None = None, serial: str | None = None) -> dict[str, Any]:
    """Build a local, secret-free checkpoint from evidence already on disk.

    The Facebook UI does not expose a cursor that can be safely transferred
    between logged-in workers.  The checkpoint therefore records observed
    progress and the recovery/merge policy, rather than pretending that a
    second account can continue from the first account's cursor.
    """
    metrics = _reaction_progress(run_dir)
    return {
        "version": 1,
        "captured_at": utc_now(),
        "worker_id": worker_id,
        "serial": serial,
        "record_lines": int(metrics.get("record_lines") or 0),
        "query_records": int(metrics.get("query_records") or 0),
        "reaction_pages": int(metrics.get("reaction_pages") or 0),
        "reaction_users": int(metrics.get("reaction_users") or 0),
        "valid_reactor_users": int(_valid_reactor_user_count(run_dir)),
        "terminal": bool(metrics.get("terminal")),
        "rate_limits": int(metrics.get("rate_limits") or 0),
        "stop_reason": str(metrics.get("stop_reason") or ""),
        "source_updated_at": metrics.get("updated_at"),
        "resume_policy": "revalidate-and-dedupe-persisted-evidence",
        "cross_worker_cursor": "unsupported",
        "cursor_material": "not-persisted",
    }


def _stage(stage_id: str, label: str, state: str, detail: str) -> dict[str, str]:
    return {"id": stage_id, "label": label, "state": state, "detail": detail}


def _select_batch_current_stage(snapshot: dict[str, Any]) -> str:
    """Choose a batch label from persisted capture evidence, without probing ADB."""
    stages = {
        str(stage.get("id") or ""): stage
        for stage in snapshot.get("stages") or []
        if isinstance(stage, dict)
    }
    metrics = snapshot.get("metrics") or {}
    target = stages.get("target") or {}
    pagination = stages.get("pagination") or {}
    decrypt = stages.get("decrypt") or {}
    analysis = stages.get("analysis") or {}

    def label(stage: dict[str, Any], fallback: str) -> str:
        return str(stage.get("label") or fallback)

    # A batch poll deliberately has no live Android snapshot.  Do not let the
    # default environment stage (active while a job runs) hide capture evidence.
    android = snapshot.get("android") or {}
    android_known = android.get("state") not in (None, "", "unknown")
    if android_known and (stages.get("environment") or {}).get("state") == "active":
        return label(stages["environment"], "模拟器与网络")

    reaction_evidence = any(
        int(metrics.get(key) or 0) > 0
        for key in ("reaction_users", "reaction_pages", "query_records")
    ) or bool(metrics.get("terminal"))
    if (target.get("state") or "") == "active":
        return label(target, "目标帖子校验")
    if not reaction_evidence and (target.get("state") or "") != "complete":
        return "准备抓取"
    if bool(metrics.get("terminal")):
        if (decrypt.get("state") or "") == "active":
            return label(decrypt, "解密 flow 校验")
        if (analysis.get("state") or "") != "complete":
            return label(analysis, "解析与去重")
    if (pagination.get("state") or "") == "active":
        return label(pagination, "Reaction 分页")
    for stage_id, fallback in (
        ("target", "目标帖子校验"),
        ("pagination", "Reaction 分页"),
        ("decrypt", "解密 flow 校验"),
        ("analysis", "解析与去重"),
    ):
        stage = stages.get(stage_id) or {}
        if (stage.get("state") or "") in {"waiting", "active"}:
            return label(stage, fallback)
    return "抓取中"


def _reaction_result_outcome(report: dict[str, Any], status: str, users: int) -> dict[str, str] | None:
    """Describe a verified empty result separately from process completion."""
    if status != "completed" or users != 0:
        return None
    if (report.get("target_verification") or {}).get("status") != "matched":
        return None
    integrity = _reaction_completeness(report)
    if not integrity.get("terminal_page_seen") or any(
        integrity.get(key) for key in ("rate_limited_records", "temporary_feature_block_records")
    ) or report.get("line_parse_errors"):
        return None
    enumeration = (report.get("enumeration_status") or {}).get("reactions") or {}
    restricted = (
        enumeration.get("status") == "query-triggered-friends-only-visibility"
        and enumeration.get("ui_visibility_hint") == "friends-only-names"
        and int(enumeration.get("query_records") or 0) > 0
    )
    if restricted:
        return {"code": "empty-lists", "reason": "friends-only-names",
                "label": "Empty lists（名单不可见）",
                "detail": "Facebook 仅展示好友的用户名单，本次返回 0 人；不代表帖子没有互动。"}
    if integrity.get("completion_claim_allowed") and integrity.get("reported_total_count") == 0:
        return {"code": "empty-lists", "reason": "zero-reactions",
                "label": "Empty lists（空名单）",
                "detail": "已确认目标及列表末页，Facebook 报告互动数量为 0。"}
    return None


def _job_result_outcome(job: dict[str, Any], evidence_dirs: list[Path], users: int) -> dict[str, str] | None:
    if job.get("status") != "completed" or users:
        return None
    selected = _select_evidence_dir(Path(job.get("run_dir") or ""), evidence_dirs)
    report = _read_json(selected / "analysis" / "facebook-sample-patterns.json") if selected else {}
    return _reaction_result_outcome(report, str(job.get("status")), users)


def build_progress_snapshot(
    run_dir: Path,
    job_status: str,
    android: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    evidence_dirs: list[Path] | None = None,
    completion_scope: str | None = None,
) -> dict[str, Any]:
    android = android or {}
    request = request or {}
    progress_dirs = evidence_dirs or [run_dir]
    metrics = _merge_reaction_progress(progress_dirs) if evidence_dirs else _reaction_progress(run_dir)
    metrics.update(_merge_share_progress(progress_dirs) if evidence_dirs else _share_progress(run_dir))
    valid_reactor_users = _count_reactor_users(progress_dirs) if evidence_dirs else _valid_reactor_user_count(run_dir)
    metrics["valid_reactor_users"] = valid_reactor_users
    metrics["reaction_users"] = max(int(metrics.get("reaction_users") or 0), valid_reactor_users)
    artifacts = _artifact_metadata(run_dir, job_status, request.get("save_artifacts"))
    ready = {item["name"]: item["size_bytes"] > 0 for item in artifacts}
    selected_groups = set(request.get("save_artifacts", DEFAULT_SAVE_ARTIFACTS))
    target = _read_json(run_dir / "target-open" / "target-open-diagnostics.json")
    report = _read_json(run_dir / "analysis" / "facebook-sample-patterns.json")
    result_outcome = _reaction_result_outcome(report, job_status, metrics["reaction_users"])
    target_status = str((report.get("target_verification") or {}).get("status") or "")
    target_verified = bool(target.get("verified") or target_status == "matched")
    terminal_job = job_status in {"completed", "failed", "cancelled"}

    environment_state = "complete" if android.get("state") == "device" and android.get("boot_completed") else ("error" if terminal_job else "active")
    active_job = job_status in {"running", "cancelling"}
    pcap_state = "complete" if "pcap" not in selected_groups or (ready.get("raw-network.pcap") and terminal_job) else ("active" if active_job else "waiting")
    proxy_state = "complete" if "mitm" not in selected_groups or (terminal_job and ready.get("raw-facebook-http.mitm")) else ("active" if active_job else "waiting")
    target_state = "complete" if target_verified else ("active" if active_job and ready.get("safe-facebook-http-capture.jsonl") else ("error" if terminal_job else "waiting"))
    reaction_state = (
        "complete"
        if metrics["terminal"] or completion_scope == BOUNDED_SAMPLE_COMPLETION_SCOPE
        else ("active" if metrics["reaction_users"] > 0 or active_job else ("error" if job_status == "failed" else "waiting"))
    )
    decrypt_state = "complete" if "mitm" not in selected_groups or ready.get("raw-facebook-http-summary.json") else ("active" if ready.get("raw-facebook-http.mitm") and active_job else ("error" if job_status == "failed" else "waiting"))
    analysis_state = "complete" if ready.get("facebook-sample-patterns.json") else ("error" if job_status == "failed" else "waiting")
    selected_names = REQUIRED_ARTIFACTS | {
        name for group in selected_groups for name in OPTIONAL_ARTIFACT_GROUPS[group]
    }
    required_artifacts_ready = all(ready.get(name) for name in REQUIRED_ARTIFACTS)
    selected_artifacts_ready = all(ready.get(name) for name in selected_names)
    selected_mitm_artifacts_ready = all(
        ready.get(name) for name in OPTIONAL_ARTIFACT_GROUPS["mitm"]
    )
    if job_status == "completed" and all(ready.get(name) for name in REQUIRED_ARTIFACTS):
        final_state = (
            "warning"
            if completion_scope == BOUNDED_SAMPLE_COMPLETION_SCOPE
            else "complete" if all(ready.get(name) for name in selected_names) else "warning"
        )
    elif job_status == "failed":
        final_state = "error"
    elif job_status == "cancelled":
        final_state = "warning"
    elif active_job:
        final_state = "waiting"
    else:
        final_state = "waiting"

    if job_status == "completed":
        if not required_artifacts_ready:
            complete_detail = (
                "必留解析结果未全部就绪，结果未形成非全量样本"
                if completion_scope == BOUNDED_SAMPLE_COMPLETION_SCOPE
                else "必留解析结果未全部就绪"
            )
        elif completion_scope == BOUNDED_SAMPLE_COMPLETION_SCOPE:
            if selected_artifacts_ready and selected_groups:
                complete_detail = "Reaction 样本及所选证据均已落盘，结果为非全量"
            elif selected_artifacts_ready:
                complete_detail = "Reaction 样本及必留解析结果已落盘，本次未选择可选证据，结果为非全量"
            else:
                complete_detail = "Reaction 样本已形成，但所选证据未全部就绪，结果为非全量"
        elif selected_artifacts_ready and selected_groups:
            complete_detail = "必留解析结果和所选证据均已落盘"
        elif selected_artifacts_ready:
            complete_detail = "必留解析结果已落盘，本次未选择可选证据"
        else:
            complete_detail = "必留解析结果已落盘，但所选证据未全部就绪"
    else:
        complete_detail = {
            "failed": "任务失败，证据链未完成",
            "cancelled": "任务已取消，证据链未完成",
            "queued": "任务尚未开始，证据链等待生成",
            "running": "任务进行中，证据链尚未完成",
            "cancelling": "任务停止中，等待进程终止确认",
        }.get(job_status, "任务尚未完成，证据链未完成")

    if "mitm" not in selected_groups:
        decrypt_detail = "本次未选择保存 MITM 归档，仅用于实时处理"
    elif job_status == "completed" and selected_mitm_artifacts_ready:
        decrypt_detail = "MITM archive 与安全索引完成校验"
    elif job_status == "completed":
        decrypt_detail = "MITM 归档与索引未全部就绪"
    elif active_job:
        decrypt_detail = "MITM archive 与安全索引校验进行中"
    elif job_status == "queued":
        decrypt_detail = "MITM archive 与安全索引等待校验"
    elif job_status == "failed":
        decrypt_detail = "MITM 归档与索引校验未完成，任务失败"
    elif job_status == "cancelled":
        decrypt_detail = "MITM 归档与索引校验未完成，任务已取消"
    else:
        decrypt_detail = "MITM 归档与索引校验未完成"

    if result_outcome:
        final_state = "warning"
        complete_detail = result_outcome["detail"]

    stages = [
        _stage("environment", "模拟器与网络", environment_state, "ADB 设备和 Android 网络就绪"),
        _stage("pcap", "原始 PCAP", pcap_state, "tcpdump 保存 TLS 解密前流量" if "pcap" in selected_groups else "本次未选择保存原始 PCAP"),
        _stage("proxy", "代理与 MITM", proxy_state, "ADB reverse 与 mitmproxy 实时接收流量" if "mitm" in selected_groups else "MITM 仅用于处理，本次不保留证据文件"),
        _stage("target", "目标帖子校验", target_state, "只允许匹配目标帖的后续 Reaction 抓取"),
        _stage(
            "pagination",
            "Reaction 分页",
            reaction_state,
            "达到配置上限，已完成非全量样本"
            if completion_scope == BOUNDED_SAMPLE_COMPLETION_SCOPE
            else "ALL connection 持续增长直到 terminal",
        ),
        _stage("decrypt", "解密 flow 校验", decrypt_state, decrypt_detail),
        _stage("analysis", "解析与去重", analysis_state, "GraphQL 节点标准化并输出 CSV"),
        _stage(
            "complete",
            result_outcome["label"] if result_outcome else "证据链完成",
            final_state,
            complete_detail,
        ),
    ]
    return {
        "job_status": job_status,
        "result_outcome": result_outcome,
        "completion_scope": completion_scope,
        "storage_root": str(run_dir),
        "android": {
            "state": android.get("state", "unknown"),
            "boot_completed": bool(android.get("boot_completed")),
            "proxy": android.get("proxy", ""),
        },
        "metrics": metrics,
        "stages": stages,
        "artifacts": artifacts,
    }


def resolve_avatar_url(user_id: str, timeout_seconds: int = 15) -> str:
    """Resolve a fresh, signed Facebook CDN URL without downloading the image."""
    token = os.environ.get("FACEBOOK_GRAPH_ACCESS_TOKEN", "").strip()
    if not token:
        raise ApiError("FACEBOOK_GRAPH_ACCESS_TOKEN is not configured")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", user_id):
        raise ApiError("invalid user id")
    query = urlencode({"type": "large", "redirect": "false", "access_token": token})
    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{user_id}/picture?{query}"
    try:
        request = Request(endpoint, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ApiError("Facebook avatar URL resolution failed") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ApiError("Facebook did not return an avatar URL")
    if data.get("is_silhouette"):
        raise ApiError("Facebook returned a silhouette avatar")
    return url


def _enumeration_summary(report: dict[str, Any], family: str) -> dict[str, Any]:
    item = ((report.get("enumeration_status") or {}).get(family) or {})
    pagination = item.get("pagination") or {}
    groups = pagination.get("groups") or {}
    all_group = groups.get("__ALL__") or {}
    ui_total = item.get("ui_total_count")
    reported_total = item.get("reported_total_count")
    if reported_total is None:
        reported_total = ui_total
    enumerated = item.get("enumerated_identity_count")
    if enumerated is None:
        enumerated = 0
    gap = item.get("identity_count_gap")
    count_difference = gap is not None and gap > 0
    visibility_hint = item.get("ui_visibility_hint") or item.get("visibility_hint")
    limited = bool(count_difference or visibility_hint or str(item.get("status") or "").endswith("-subset"))
    terminal = (
        bool(_reaction_completeness(report).get("terminal_page_seen"))
        if family == "reactions"
        else bool(pagination.get("all_observed_groups_terminal") or item.get("terminal_page_seen"))
    )
    integrity_key = "reaction_integrity" if family == "reactions" else f"{family.rstrip('s')}_integrity"
    integrity = report.get(integrity_key) or {}
    if not isinstance(integrity, dict):
        integrity = {}
    integrity_status = str(integrity.get("integrity_status") or "").strip().lower()
    capture_error = bool(
        integrity_status
        and integrity_status not in {"complete", "complete-terminal", "complete-terminal-visible-subset"}
    )
    for key in (
        "rate_limited_records",
        "temporary_feature_block_records",
        "parse_errors",
        "line_parse_errors",
    ):
        try:
            capture_error = capture_error or int(integrity.get(key) or 0) > 0
        except (TypeError, ValueError):
            capture_error = True
    capture_error = capture_error or bool(
        integrity.get("latest_rate_limited")
        or integrity.get("session_restricted")
        or integrity.get("latest_graphql_error_code")
        or integrity.get("latest_api_error_code")
    )
    if family == "reactions":
        try:
            capture_error = capture_error or int(report.get("line_parse_errors") or 0) > 0
        except (TypeError, ValueError):
            capture_error = True
    if terminal and integrity.get("completion_claim_allowed") is False:
        capture_error = True
    count_difference_note = None
    if count_difference:
        if not terminal:
            count_difference_note = (
                "Facebook 报告的互动总数大于当前已返回的身份数，但分页尚未到终页；结果未采完，"
                "该差额不能归因于平台可见性。"
            )
        elif capture_error:
            count_difference_note = (
                "Facebook 报告的互动总数大于当前已返回的身份数，但存在抓取错误证据；该差额不能归因于"
                "平台可见性。"
            )
        else:
            count_difference_note = (
                "Facebook reported a larger interaction count than the identities returned by the "
                "current account's terminal visible list; treat this as a platform visibility/counting "
                "difference, not a capture failure."
            )
    return {
        "status": item.get("status"),
        "classification": item.get("classification"),
        "decision_reason": item.get("decision_reason"),
        "ui_total": ui_total,
        "reported_total": reported_total,
        "reported_total_source": item.get("reported_total_source"),
        "enumerated_count": enumerated,
        "identity_gap": gap,
        "count_difference_observed": count_difference,
        "limited": limited,
        "count_difference_note": count_difference_note,
        "visibility_hint": visibility_hint,
        "terminal": terminal,
        "pagination": pagination,
    }


def _attempt_pagination_diagnostics(
    run_dir: Path,
    evidence_dirs: list[Path] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = evidence_dirs or [run_dir]
    for candidate in candidates:
        root = candidate.parent if candidate.name in {"readonly-ui", "readonly-ui-fallback"} else candidate
        for path in (root / "readonly-ui", root / "readonly-ui-fallback"):
            diagnostics = _read_json(path / "exploration-diagnostics.json")
            pagination_runs = [
                item for item in diagnostics.get("pagination_runs") or [] if isinstance(item, dict)
            ]
            if not pagination_runs:
                continue
            all_runs = [item for item in pagination_runs if str(item.get("reaction_key") or "") == "__ALL__"]
            return diagnostics, (all_runs[-1] if all_runs else pagination_runs[-1])
    return {}, {}


def _has_error_code(value: Any) -> bool:
    return value not in (None, "", 0, "0", False)


def _diagnostic_count_is_positive(value: Any) -> bool:
    if value in (None, "", 0, "0", False):
        return False
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return True


def _diagnostic_flag_is_set(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "0.0", "false", "no", "none", "null", "off"}
    return bool(value)


def _exploration_diagnostics_paths(evidence_dir: Path) -> list[Path]:
    root = Path(evidence_dir)
    if root.name in {"readonly-ui", "readonly-ui-fallback"}:
        root = root.parent
    return [
        root / "readonly-ui" / "exploration-diagnostics.json",
        root / "readonly-ui-fallback" / "exploration-diagnostics.json",
    ]


def _read_exploration_diagnostics(evidence_dir: Path) -> list[dict[str, Any]] | None:
    diagnostics: list[dict[str, Any]] = []
    found = False
    for path in _exploration_diagnostics_paths(evidence_dir):
        try:
            if not path.is_file():
                continue
            found = True
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        diagnostics.append(value)
    return diagnostics if found else None


def _diagnostics_have_reaction_errors(diagnostics: dict[str, Any]) -> bool:
    if _diagnostic_count_is_positive(diagnostics.get("parse_errors")):
        return True
    if _diagnostic_flag_is_set(diagnostics.get("session_restricted")):
        return True
    for value in (
        diagnostics.get("latest_graphql_error_code"),
        diagnostics.get("latest_api_error_code"),
    ):
        if _has_error_code(value):
            return True

    pagination_runs = diagnostics.get("pagination_runs")
    if pagination_runs is None:
        pagination_runs = []
    if not isinstance(pagination_runs, list):
        return True
    for pagination in pagination_runs:
        if not isinstance(pagination, dict):
            return True
        if any(
            _diagnostic_count_is_positive(pagination.get(key))
            for key in ("parse_errors", "rate_limit_count", "temporary_feature_block_count")
        ):
            return True
        if _diagnostic_flag_is_set(pagination.get("latest_rate_limited")) or _diagnostic_flag_is_set(
            pagination.get("session_restricted")
        ):
            return True
        for value in (
            pagination.get("latest_graphql_error_code"),
            pagination.get("latest_api_error_code"),
            pagination.get("session_restriction_graphql_code"),
            pagination.get("session_restriction_api_error_code"),
        ):
            if _has_error_code(value):
                return True

    reaction_screening = diagnostics.get("reaction_screening")
    if reaction_screening is not None:
        if not isinstance(reaction_screening, dict):
            return True
        if _diagnostic_count_is_positive(reaction_screening.get("parse_errors")):
            return True
        if _diagnostic_flag_is_set(reaction_screening.get("latest_rate_limited")) or _diagnostic_flag_is_set(
            reaction_screening.get("session_restricted")
        ):
            return True
        for value in (
            reaction_screening.get("latest_graphql_error_code"),
            reaction_screening.get("latest_api_error_code"),
            reaction_screening.get("session_restriction_graphql_code"),
            reaction_screening.get("session_restriction_api_error_code"),
        ):
            if _has_error_code(value):
                return True
    return False


def _required_artifacts_ready(run_dir: Path) -> bool:
    for name in REQUIRED_ARTIFACTS:
        path = run_dir / ARTIFACT_PATHS[name]
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                return False
        except OSError:
            return False
    return True


def _evidence_dir_candidates(run_dir: Path, evidence_dirs: list[Path] | None = None) -> list[Path]:
    values = evidence_dirs if evidence_dirs else [run_dir]
    candidates: list[Path] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = BASE_DIR / path
        if path not in candidates:
            candidates.append(path)
    return candidates


def _select_evidence_dir(run_dir: Path, evidence_dirs: list[Path] | None = None) -> Path | None:
    for candidate in _evidence_dir_candidates(run_dir, evidence_dirs):
        if _required_artifacts_ready(candidate):
            return candidate
    return None


def _effective_matched_post_id(
    target: dict[str, Any],
    report: dict[str, Any],
    reactor_rows: list[dict[str, str]],
) -> str:
    if str(target.get("status") or "") != "matched":
        return ""
    matched_post_id = str(target.get("matched_post_id") or "").strip()
    if matched_post_id:
        return matched_post_id

    matched_token = str(target.get("matched_token") or "").strip()
    expected_url_tokens = {
        str(value).strip()
        for value in (target.get("expected_url_tokens") or [])
        if str(value).strip()
    }
    observed_sources = {
        str(value).strip()
        for value in ((report.get("source_post_ids_by_family") or {}).get("reactions") or [])
        if str(value).strip()
    }
    row_sources = {
        str(row.get("source_post_id") or "").strip()
        for row in reactor_rows
        if str(row.get("source_post_id") or "").strip()
    }
    if (
        matched_token
        and matched_token in expected_url_tokens
        and observed_sources == {matched_token}
        and row_sources == {matched_token}
    ):
        return matched_token
    return ""


def _reel_source_discovery_matched_post_id(
    evidence_dir: Path,
    request: dict[str, Any],
    report: dict[str, Any],
    reactor_rows: list[dict[str, str]],
) -> str:
    post_url = str(request.get("post_url") or "").strip()
    if not re.fullmatch(r"https://(?:www\.|m\.)?facebook\.com/(?:reel/[0-9]{1,32}|share/r/[A-Za-z0-9]+)/?", post_url, re.I):
        return ""

    target_open = _read_json(evidence_dir / "target-open" / "target-open-diagnostics.json")
    resolved_url = str(target_open.get("resolved_url") or "").strip()
    if (
        target_open.get("verified") is not True
        or target_open.get("reason") != "reel-ui-source-discovery-required"
        or not re.fullmatch(r"https://(?:www\.|m\.)?facebook\.com/reel/[0-9]{1,32}/?", resolved_url, re.I)
    ):
        return ""

    diagnostics = _read_json(evidence_dir / "readonly-ui" / "exploration-diagnostics.json")
    discovery = diagnostics.get("source_discovery") or {}
    guard = diagnostics.get("target_guard") or {}
    source_post_id = str(discovery.get("source_post_id") or "").strip()
    discovered_sources = {
        str(value).strip() for value in discovery.get("observed_source_post_ids") or [] if str(value).strip()
    }
    report_sources = {
        str(value).strip()
        for value in ((report.get("source_post_ids_by_family") or {}).get("reactions") or [])
        if str(value).strip()
    }
    row_sources = {
        str(row.get("source_post_id") or "").strip()
        for row in reactor_rows
        if str(row.get("source_post_id") or "").strip()
    }
    if (
        not re.fullmatch(r"[0-9]{1,32}", source_post_id)
        or discovery.get("status") != "matched"
        or discovered_sources != {source_post_id}
        or int(discovery.get("query_records") or 0) <= 0
        or guard.get("status") != "matched"
        or str(guard.get("expected_post_id") or "").strip() != source_post_id
        or report_sources != {source_post_id}
        or row_sources != {source_post_id}
    ):
        return ""
    return source_post_id


def _bounded_sample_evidence_allowed(
    run_dir: Path,
    request: dict[str, Any],
    report: dict[str, Any],
    target: dict[str, Any],
    reactor_rows: list[dict[str, str]],
    evidence_dirs: list[Path] | None = None,
) -> bool:
    if not bool(request.get("reaction_scroll_limit_explicit")):
        return False

    diagnostics, pagination = _attempt_pagination_diagnostics(run_dir, evidence_dirs)
    stop_reason = str(pagination.get("stop_reason") or "").strip().lower()
    if stop_reason not in BOUNDED_SAMPLE_STOP_REASONS:
        return False
    if bool(pagination.get("unlimited")):
        return False
    if bool(pagination.get("latest_rate_limited")) or bool(pagination.get("session_restricted")):
        return False
    configured_limit = normalize_reaction_scroll_limit(request.get("reaction_scroll_limit"))
    if configured_limit is None:
        return False
    absolute_cap = pagination.get("absolute_cap")
    if absolute_cap not in (None, ""):
        try:
            if int(absolute_cap) != configured_limit:
                return False
        except (TypeError, ValueError):
            return False

    completeness = _reaction_completeness(report)
    if int(completeness.get("rate_limited_records") or 0) > 0:
        return False
    if int(completeness.get("temporary_feature_block_records") or 0) > 0:
        return False
    reaction_screening = diagnostics.get("reaction_screening") or {}
    try:
        if int(report.get("line_parse_errors") or 0) > 0:
            return False
    except (TypeError, ValueError):
        return False

    for value in (
        diagnostics.get("parse_errors"),
        pagination.get("parse_errors"),
        reaction_screening.get("parse_errors") if isinstance(diagnostics.get("reaction_screening"), dict) else None,
    ):
        try:
            if int(value or 0) > 0:
                return False
        except (TypeError, ValueError):
            return False

    if bool(reaction_screening.get("session_restricted")) or bool(reaction_screening.get("latest_rate_limited")):
        return False
    for value in (
        pagination.get("latest_graphql_error_code"),
        pagination.get("latest_api_error_code"),
        reaction_screening.get("latest_graphql_error_code"),
        reaction_screening.get("session_restriction_graphql_code"),
        reaction_screening.get("session_restriction_api_error_code"),
    ):
        if _has_error_code(value):
            return False

    progress_dirs = evidence_dirs or [run_dir]
    metrics = _merge_reaction_progress(progress_dirs)
    if bool(metrics.get("session_restricted")):
        return False
    if int(metrics.get("temporary_feature_blocks") or 0) > 0:
        return False
    for value in (metrics.get("latest_graphql_error_code"), metrics.get("latest_api_error_code")):
        if _has_error_code(value):
            return False

    matched_post_id = _effective_matched_post_id(target, report, reactor_rows)
    if not matched_post_id:
        return False
    observed_sources = {
        str(value).strip()
        for value in ((report.get("source_post_ids_by_family") or {}).get("reactions") or [])
        if str(value).strip()
    }
    row_sources = {str(row.get("source_post_id") or "").strip() for row in reactor_rows}
    if observed_sources != {matched_post_id} or (row_sources and row_sources != {matched_post_id}):
        return False
    valid_users = {
        str(row.get("user_id") or "").strip()
        for row in reactor_rows
        if str(row.get("user_id") or "").strip()
    }
    if valid_users:
        return _required_artifacts_ready(run_dir)

    # A bounded page can be valid even when Facebook exposes only the total
    # count and friends' names.  All transport and diagnostic checks above
    # still apply; this branch only changes the visible-user cardinality rule.
    if reactor_rows:
        return False
    enumeration = ((report.get("enumeration_status") or {}).get("reactions") or {})
    if (
        enumeration.get("status") != "query-triggered-friends-only-visibility"
        or enumeration.get("ui_visibility_hint") != "friends-only-names"
        or enumeration.get("reported_total_source") != "target-post-feedback"
    ):
        return False
    statuses = report.get("statuses") or {}
    if not isinstance(statuses, dict) or not statuses:
        return False
    try:
        if int(enumeration.get("query_records") or 0) <= 0:
            return False
        if int(enumeration.get("reported_total_count") or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    return _required_artifacts_ready(run_dir)


def _reaction_completeness(report: dict[str, Any]) -> dict[str, Any]:
    integrity = report.get("reaction_integrity") or {}
    item = ((report.get("enumeration_status") or {}).get("reactions") or {})
    pagination = item.get("pagination") or {}
    groups = pagination.get("groups") or {}
    all_group = groups.get("__ALL__") or {}
    terminal = bool(
        integrity.get("terminal_page_seen")
        or all_group.get("terminal_page_seen")
        or item.get("terminal_page_seen")
    )
    open_tail = bool(
        integrity.get("open_tail_without_terminal")
        or (all_group.get("open_page_seen") and not terminal)
    )
    completion_claim_allowed = (
        bool(integrity.get("completion_claim_allowed"))
        if "completion_claim_allowed" in integrity
        else bool(terminal and not open_tail)
    )
    integrity_status = integrity.get("integrity_status")
    if not integrity_status:
        integrity_status = (
            "complete-terminal"
            if completion_claim_allowed
            else "partial-open-tail"
            if open_tail
            else "incomplete"
        )
    return {
        "integrity_status": integrity_status,
        "completion_claim_allowed": completion_claim_allowed,
        "terminal_page_seen": terminal,
        "open_tail_without_terminal": open_tail,
        "successful_page_count": integrity.get("successful_page_count", len(report.get("reactor_pages") or [])),
        "raw_reactor_rows": integrity.get("raw_reactor_rows"),
        "unique_reactor_identities": integrity.get("unique_reactor_identities", item.get("enumerated_identity_count", 0)),
        "duplicate_reactor_rows": integrity.get("duplicate_reactor_rows", integrity.get("duplicate_rows")),
        "reported_total_count": integrity.get("reported_total_count", item.get("reported_total_count", item.get("ui_total_count"))),
        "reported_total_unique_gap": integrity.get("reported_total_unique_gap", item.get("identity_count_gap")),
        "rate_limited_records": integrity.get("rate_limited_records", 0),
        "temporary_feature_block_records": integrity.get("temporary_feature_block_records", 0),
    }


def _empty_reaction_visibility_allowed(
    report: dict[str, Any],
    reaction_completeness: dict[str, Any],
    reactor_rows: list[dict[str, str]],
    evidence_dir: Path,
) -> bool:
    """Allow a verified target to complete when Facebook exposes no identities."""
    if reactor_rows:
        return False

    enumeration = ((report.get("enumeration_status") or {}).get("reactions") or {})
    status = str(enumeration.get("status") or "").strip()
    try:
        query_records = int(reaction_completeness.get("query_records") or enumeration.get("query_records") or 0)
        reported_total = int(
            reaction_completeness.get("reported_total_count")
            or enumeration.get("reported_total_count")
            or 0
        )
    except (TypeError, ValueError):
        return False
    if reported_total <= 0 or enumeration.get("reported_total_source") != "target-post-feedback":
        return False

    if int(report.get("line_parse_errors") or 0) > 0:
        return False
    if int(reaction_completeness.get("rate_limited_records") or 0) > 0:
        return False
    if int(reaction_completeness.get("temporary_feature_block_records") or 0) > 0:
        return False
    diagnostics = _read_exploration_diagnostics(evidence_dir)
    if diagnostics is None or any(_diagnostics_have_reaction_errors(item) for item in diagnostics):
        return False

    # A visibility-only result must have a clean transport record.  A missing
    # or non-2xx status keeps the original failure contract in place.
    statuses = report.get("statuses") or {}
    if not isinstance(statuses, dict) or not statuses:
        return False
    for value, count in statuses.items():
        try:
            if int(count or 0) <= 0:
                continue
            status_code = int(value)
        except (TypeError, ValueError):
            return False
        if status_code < 200 or status_code >= 300:
            return False

    if status == "query-triggered-friends-only-visibility":
        return query_records > 0 and enumeration.get("ui_visibility_hint") == "friends-only-names" and bool(
            reaction_completeness.get("terminal_page_seen")
        )
    # A verified post that reports reactions but never emits a Reaction query is
    # an entry/query failure, not a terminal empty result.  Fail closed so the
    # API cannot claim success with zero identities for a visibly non-empty post.
    if status == "query-not-triggered":
        return False
    return False


def build_api_result(
    run_dir: Path,
    request: dict[str, Any],
    *,
    evidence_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    collect = set(request.get("collect") or [])
    selected_evidence_dir = _select_evidence_dir(run_dir, evidence_dirs)
    evidence_dir = selected_evidence_dir or run_dir
    analysis = evidence_dir / "analysis"
    report = _read_json(analysis / "facebook-sample-patterns.json")
    if not report:
        raise ApiError(f"analysis report missing or invalid: {analysis / 'facebook-sample-patterns.json'}")
    target = report.get("target_verification") or {}
    reactor_rows = _read_csv(analysis / "facebook-reactor-users.csv")
    if (request.get("post_url") or request.get("post_id")) and target.get("status") != "matched":
        discovered_reel_post_id = _reel_source_discovery_matched_post_id(
            evidence_dir, request, report, reactor_rows
        )
        if not discovered_reel_post_id:
            raise ApiError(f"target verification failed: {target.get('status')}")
        target = {
            **target,
            "status": "matched",
            "matched_by": "reel-ui-source-discovery",
            "matched_post_id": discovered_reel_post_id,
        }
        report = {**report, "target_verification": target}
    share_rows = _read_csv(
        analysis / "facebook-share-users.csv",
        required_columns={"actor_id", "source_post_id", "share_story_id"}
        if "shares" in collect else None,
    )
    reaction_completeness = _reaction_completeness(report)
    empty_reaction_visibility_allowed = _empty_reaction_visibility_allowed(
        report,
        reaction_completeness,
        reactor_rows,
        evidence_dir,
    )
    bounded_sample_evidence_allowed = False
    completion_scope: str | None = None
    if "reactions" in collect:
        if not reaction_completeness.get("completion_claim_allowed") and not empty_reaction_visibility_allowed:
            bounded_sample_evidence_allowed = _bounded_sample_evidence_allowed(
                evidence_dir,
                request,
                report,
                target,
                reactor_rows,
                [evidence_dir],
            )
            if not bounded_sample_evidence_allowed:
                raise ApiError(
                    "reaction capture is incomplete: "
                    f"{reaction_completeness.get('integrity_status') or 'unknown'}"
                )
            completion_scope = BOUNDED_SAMPLE_COMPLETION_SCOPE
        else:
            completion_scope = TERMINAL_COMPLETION_SCOPE
        matched_post_id = _effective_matched_post_id(target, report, reactor_rows)
        if not matched_post_id:
            raise ApiError("reaction capture lacks a verified matched_post_id")
        observed_sources = sorted(
            {
                str(value).strip()
                for value in ((report.get("source_post_ids_by_family") or {}).get("reactions") or [])
                if str(value).strip()
            }
        )
        if observed_sources != [matched_post_id] and (
            observed_sources or not empty_reaction_visibility_allowed
        ):
            raise ApiError(
                "reaction capture source mismatch: "
                f"expected={matched_post_id} observed={observed_sources}"
            )
        row_sources = {str(row.get("source_post_id") or "").strip() for row in reactor_rows}
        if row_sources and row_sources != {matched_post_id}:
            raise ApiError(
                "reaction user rows contain a different source_post_id: "
                f"expected={matched_post_id} observed={sorted(row_sources)}"
            )
        reported_total = reaction_completeness.get("reported_total_count")
        if (
            reported_total not in (None, "")
            and int(reported_total) > 0
            and not reactor_rows
            and not empty_reaction_visibility_allowed
            and not bounded_sample_evidence_allowed
        ):
            raise ApiError("reaction capture completed without the visible reactor users reported by Facebook")
    share_completeness = None
    if "shares" in collect:
        try:
            share_completeness = validate_share_result(report, share_rows)
        except ShareValidationError as exc:
            raise ApiError(f"share capture rejected ({exc.code}): {exc}") from exc
        if completion_scope is None:
            completion_scope = TERMINAL_COMPLETION_SCOPE
    reactions = [
        {
            "id": row.get("user_id") or None,
            "name": row.get("user_name") or None,
            "avatar_url": row.get("avatar_url") or None,
            # Facebook CDN URLs are useful source evidence but are not stable
            # public assets. Keep that distinction explicit in the API.
            "avatar_status": "refreshable" if os.environ.get("FACEBOOK_GRAPH_ACCESS_TOKEN", "").strip() else ("source-only" if row.get("avatar_url") else "unavailable"),
            "avatar_proxy_url": f"/v1/avatars/{row.get('user_id')}" if row.get("user_id") else None,
            "reaction_id": row.get("reaction_id") or None,
            "source_field": row.get("source_field") or None,
            "source_post_id": row.get("source_post_id") or None,
        }
        for row in reactor_rows
    ]
    shares = [
        {
            "id": row.get("actor_id") or None,
            "name": row.get("actor_name") or None,
            "source_post_id": row.get("source_post_id") or None,
            "share_story_id": row.get("share_story_id") or None,
            **({
                "target_input_url": row.get("target_input_url") or request.get("post_url") or None,
                "canonical_url": row.get("canonical_url") or None,
                "post_type": row.get("post_type") or None,
                "post_id": row.get("post_id") or row.get("source_post_id") or None,
                "owner_id": row.get("owner_id") or None,
                "feedback_id": row.get("feedback_id") or None,
                "share_actor_id": row.get("actor_id") or None,
                "share_actor_name": row.get("actor_name") or None,
                "share_actor_url": row.get("share_actor_url") or None,
                "page_marker": row.get("page_marker") or None,
                "source": row.get("source") or None,
                "captured_at": row.get("captured_at") or None,
            } if "shares" in collect else {}),
        }
        for row in share_rows
    ]
    statuses = report.get("statuses") or {}
    completeness = {"reactions": reaction_completeness}
    result_outcome = _reaction_result_outcome(report, "completed", len(reactions))
    if share_completeness is not None:
        completeness["shares"] = share_completeness
        if shares:
            result_outcome = None
        if collect == {"shares"}:
            result_outcome = None
            if not shares:
                result_outcome = {"code": "empty-lists", "reason": "no-visible-share-users",
                                  "label": "Empty lists（无可见转发用户）",
                                  "detail": "已确认目标及转发列表末页；当前账号没有可见转发记录，不代表分享总数为零。"}
    return {
        "run_dir": str(run_dir),
        "result_outcome": result_outcome,
        "completion_scope": completion_scope,
        "target_verification": target,
        "completeness": completeness,
        "visibility": {
            "reactions": _enumeration_summary(report, "reactions"),
            "shares": _enumeration_summary(report, "shares"),
        },
        "reaction_users": reactions,
        "share_users": shares,
        "transport_observation": {
            "http_statuses": statuses,
            "rate_limit_http_seen": bool(statuses.get("429") or statuses.get(429)),
            "auth_http_error_seen": bool(statuses.get("401") or statuses.get(401) or statuses.get("403") or statuses.get(403)),
        },
    }


def _process_creation_time(pid: int) -> float | None:
    if pid <= 0 or os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return value / 10_000_000 - 11_644_473_600
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _process_identity_matches(pid: int | None, creation_time: float | None) -> bool:
    return _process_identity_state(pid, creation_time) == "match"


def _process_presence(pid: int | None) -> bool | None:
    """Return whether a PID exists, or None when the OS cannot tell us."""
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        return True
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        error = ctypes.get_last_error()
        if error in {5}:  # ERROR_ACCESS_DENIED still proves that a process exists.
            return True
        if error in {87, 1168}:  # invalid parameter / not found
            return False
        return None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _process_identity_state(pid: int | None, creation_time: float | None) -> str:
    if not pid or pid <= 0:
        return "unknown"
    actual = _process_creation_time(int(pid))
    if actual is not None:
        if creation_time is None:
            return "unknown"
        return "match" if abs(actual - float(creation_time)) <= 2.0 else "mismatch"
    return "absent" if _process_presence(pid) is False else "unknown"


def _coerce_process_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _coerce_process_creation_time(value: Any) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def _terminate_tree(process: subprocess.Popen[Any]) -> bool:
    try:
        if process.poll() is not None:
            return True
        pid = int(process.pid)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    except (AttributeError, TypeError, ValueError):
        return False
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
    else:
        # Capture runners start a new session, so signal the whole process
        # group on POSIX.  Fall back to the handle when a test/foreign caller
        # did not create a session leader; never signal an unrelated group.
        try:
            process_group = os.getpgid(pid)
        except (AttributeError, OSError, ProcessLookupError):
            process_group = None
        if process_group == pid:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                try:
                    if process.poll() is not None:
                        return True
                except ProcessLookupError:
                    return True
                except OSError:
                    return False
                try:
                    process.terminate()
                except ProcessLookupError:
                    return True
                except OSError:
                    return False
            except (AttributeError, OSError):
                try:
                    process.terminate()
                except ProcessLookupError:
                    return True
                except OSError:
                    return False
        else:
            try:
                process.terminate()
            except ProcessLookupError:
                return True
            except OSError:
                return False
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            if process.poll() is not None:
                return True
        except ProcessLookupError:
            return True
        except OSError:
            return False
        time.sleep(0.05)
    try:
        return process.poll() is not None
    except ProcessLookupError:
        return True
    except OSError:
        return False


def _terminate_known_pid(pid: int | None, creation_time: float | None) -> bool:
    try:
        state = _process_identity_state(pid, creation_time)
    except (OSError, TypeError, ValueError):
        return False
    if state == "absent":
        return True
    if state != "match":
        return False
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
    else:
        try:
            process_group = os.getpgid(pid)
        except (AttributeError, OSError, ProcessLookupError):
            process_group = None
        try:
            if process_group == pid:
                os.killpg(process_group, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except OSError:
            return False
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            state = _process_identity_state(pid, creation_time)
        except (OSError, TypeError, ValueError):
            return False
        if state in {"absent", "mismatch"}:
            return True
        if state == "unknown":
            time.sleep(0.05)
            continue
        time.sleep(0.05)
    try:
        return _process_identity_state(pid, creation_time) in {"absent", "mismatch"}
    except (OSError, TypeError, ValueError):
        return False


def _api_run_dir(job_id: str) -> Path:
    return BASE_DIR / "runs" / f"api-{job_id}"


REACTOR_USER_FIELDS = (
    "user_id",
    "user_name",
    "avatar_url",
    "source_post_id",
    "reaction_id",
)


def _reactor_row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    user_id = str(row.get("user_id") or "").strip()
    if user_id:
        identity = f"id:{user_id}"
    else:
        identity = "name:" + "|".join(
            str(row.get(field) or "").strip()
            for field in ("user_name", "avatar_url")
        )
    return (
        identity,
        str(row.get("source_post_id") or "").strip(),
        str(row.get("reaction_id") or "").strip(),
    )


def _read_reactor_rows(run_dir: Path) -> list[dict[str, str]]:
    return _normalize_reactor_rows(_read_csv(run_dir / "analysis" / "facebook-reactor-users.csv"))


def _read_reactor_rows_file(path: Path) -> list[dict[str, str]]:
    return _normalize_reactor_rows(_read_csv(path))


def _normalize_reactor_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for source in rows:
        row: dict[str, str] = {}
        for field, value in source.items():
            if field is None:
                continue
            name = str(field)
            if not name:
                continue
            row[name] = str(value or "")
        if str(row.get("user_id") or row.get("user_name") or "").strip():
            normalized.append(row)
    return normalized


def _reactor_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames = list(REACTOR_USER_FIELDS)
    seen = set(fieldnames)
    for row in rows:
        for field in row:
            name = str(field)
            if name and name not in seen:
                fieldnames.append(name)
                seen.add(name)
    return fieldnames


def _dedupe_reactor_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = _reactor_row_key(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(row))
    return unique


def _persist_checkpoint_users(root: Path, attempt_dirs: list[Path]) -> list[dict[str, str]]:
    checkpoint_dir = root / "checkpoint"
    checkpoint_path = checkpoint_dir / "facebook-reactor-users.csv"
    rows = _read_reactor_rows_file(checkpoint_path)
    for attempt_dir in attempt_dirs:
        rows.extend(_read_reactor_rows(attempt_dir))
    rows = _dedupe_reactor_rows(rows)
    if rows:
        _atomic_write_csv(checkpoint_path, _reactor_fieldnames(rows), rows)
    return rows


def _atomic_write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text("".join(lines), encoding="utf-8")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _merge_safe_capture_records(root: Path, attempt_dirs: list[Path]) -> None:
    lines: list[str] = []
    seen: set[str] = set()
    for attempt_dir in attempt_dirs:
        path = attempt_dir / "safe-facebook-http-capture.jsonl"
        try:
            current = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            continue
        for line in current:
            normalized = line.strip()
            if not normalized:
                continue
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            lines.append(line if line.endswith("\n") else line + "\n")
    if lines:
        _atomic_write_lines(root / "safe-facebook-http-capture.jsonl", lines)


def materialize_capture_attempts(root: Path, attempt_dirs: list[Path]) -> Path:
    """Create the stable job result directory from isolated capture attempts."""
    existing = [path for path in attempt_dirs if path.is_dir()]
    if not existing:
        return root
    root.mkdir(parents=True, exist_ok=True)
    latest = existing[-1]
    _merge_safe_capture_records(root, existing)
    for relative in ARTIFACT_PATHS.values():
        if relative == ARTIFACT_PATHS["safe-facebook-http-capture.jsonl"]:
            continue
        source = latest / relative
        if not source.is_file():
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except OSError:
            # The capture result remains usable when an optional artifact is
            # unavailable; the original attempt directory is retained.
            pass
    _persist_checkpoint_users(root, existing)
    checkpoint_rows = _read_reactor_rows_file(root / "checkpoint" / "facebook-reactor-users.csv")
    latest_rows = _read_reactor_rows(latest)
    merged_rows = _dedupe_reactor_rows(checkpoint_rows + latest_rows)
    if merged_rows:
        _atomic_write_csv(root / "analysis" / "facebook-reactor-users.csv", _reactor_fieldnames(merged_rows), merged_rows)
    return root


def _prune_optional_artifacts(run_dir: Path, request: dict[str, Any]) -> None:
    selected_groups = set(request.get("save_artifacts", DEFAULT_SAVE_ARTIFACTS))
    for group, names in OPTIONAL_ARTIFACT_GROUPS.items():
        if group in selected_groups:
            continue
        for name in names:
            path = run_dir / ARTIFACT_PATHS[name]
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Cleanup failure must not hide an otherwise usable final result.
                pass


def run_capture_job(
    request: dict[str, Any],
    job_id: str,
    serial: str = DEFAULT_SERIAL,
    timeout_seconds: int | None = None,
    process_callback: Callable[[subprocess.Popen[Any] | None], None] | None = None,
    capture_process_callback: Callable[[], None] | None = None,
    run_dir: Path | None = None,
    worker: WorkerConfig | None = None,
) -> dict[str, Any]:
    worker = worker or _default_worker(serial)
    serial = worker.serial
    run_dir = run_dir or _api_run_dir(job_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    if process_callback is None:
        ensure_emulator(worker=worker)
    else:
        ensure_emulator(worker=worker, process_callback=process_callback)
    command = build_capture_command(
        request,
        serial,
        run_dir,
        proxy_port=worker.capture_port,
        native_port=worker.native_port,
        frida_ssl_unpin=worker.frida_ssl_unpin,
    )
    # Each retry owns its attempt directory, so its log must not overwrite a
    # previous attempt for the same job.
    log_path = run_dir / "capture.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation:
        kwargs["creationflags"] = creation
    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(BASE_DIR),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                **kwargs,
            )
            if capture_process_callback is not None:
                capture_process_callback()
            if process_callback is not None:
                process_callback(process)
            try:
                if timeout_seconds is None or timeout_seconds <= 0:
                    process.wait()
                else:
                    try:
                        process.wait(timeout=timeout_seconds)
                    except subprocess.TimeoutExpired as exc:
                        if not _terminate_tree(process):
                            raise WorkerUnavailableError(
                                f"capture process did not stop after timeout; refusing worker takeover: {run_dir}"
                            ) from exc
                        raise ApiError(f"capture timed out after {timeout_seconds}s") from exc
            finally:
                if process_callback is not None:
                    process_callback(None)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if process.returncode != 0:
            tail = "\n".join(log_text.splitlines()[-20:])
            raise ApiError(f"capture command failed with exit {process.returncode}; run_dir={run_dir}; tail={tail[-3000:]}")
        result = build_api_result(run_dir, request, evidence_dirs=[run_dir])
        result["job_log"] = str(log_path)
        return result
    finally:
        _prune_optional_artifacts(run_dir, request)


class JobManager:
    _UNVERIFIED_PROCESS_STATES = frozenset(
        {
            "running",
            "stop-unverified",
            "restart-process-still-running",
            "restart-unverified",
        }
    )

    def __init__(
        self,
        serial: str = DEFAULT_SERIAL,
        *,
        workers: list[WorkerConfig] | None = None,
        worker_config_path: Path | None = None,
    ):
        self.worker_config_path = worker_config_path or (BASE_DIR / WORKER_CONFIG_NAME)
        configured = validate_worker_config(list(workers)) if workers is not None else load_worker_config(
            self.worker_config_path, fallback_serial=serial
        )
        self.workers: dict[str, WorkerConfig] = {worker.worker_id: worker for worker in configured}
        self._worker_order = [worker.worker_id for worker in configured]
        self.serial = configured[0].serial
        self._queue: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, dict[str, Any]] = {}
        self._running_processes: dict[str, subprocess.Popen[Any]] = {}
        self._worker_runtime: dict[str, dict[str, Any]] = {
            worker.worker_id: {
                "current_job_id": None,
                "cooldown_until": 0.0,
                "last_failure_at": None,
                "last_failure": None,
            }
            for worker in configured
        }
        self._workers: list[threading.Thread] = []
        self._started_worker_ids: set[str] = set()
        self._active_worker_id: str | None = next(
            (worker.worker_id for worker in configured if worker.enabled),
            None,
        )
        self._active_job_id: str | None = None
        self._failed_worker_ids: set[str] = set()
        self._orphaned_worker_ids: set[str] = set()
        self._capture_lease_uncertain = False
        self._capture_lease_job_id: str | None = None
        self._capture_lease_reason: str | None = None
        self._persistence_blocked_jobs: dict[str, str] = {}
        self._transport_cleanup_inflight: dict[str, threading.Event] = {}
        self._last_failover: dict[str, Any] | None = None
        self._wake_event = threading.Event()
        self._lock = threading.RLock()
        self._load_persisted_jobs()
        self._load_persisted_batches()
        if self._capture_lease_uncertain:
            self._active_worker_id = None
        elif self._active_worker_id in self._orphaned_worker_ids:
            self._active_worker_id = next(
                (
                    worker.worker_id
                    for worker in configured
                    if worker.enabled and worker.worker_id not in self._orphaned_worker_ids
                ),
                None,
            )
        self._worker: threading.Thread | None = None
        if self._active_worker_id is not None:
            self._worker = self._start_worker(self._active_worker_id)

    def _block_capture_lease_locked(self, job_id: str | None, reason: str, *, persistence: bool = False) -> None:
        if persistence and job_id:
            # Stopped-process/transport callbacks cannot acknowledge metadata
            # durability. Keep this reason separate until explicit reconciliation.
            self._persistence_blocked_jobs[job_id] = reason
        self._capture_lease_uncertain = True
        if job_id:
            self._capture_lease_job_id = job_id
        self._capture_lease_reason = reason
        self._wake_event.set()

    def _start_worker(self, worker_id: str) -> threading.Thread:
        with self._lock:
            if worker_id not in self.workers:
                raise KeyError(worker_id)
            # A single scheduler owns the queue and the selected emulator.
            # Standby workers are switched in by the scheduler after a
            # confirmed primary failure; they never get a second queue thread.
            if self._worker is not None:
                return self._worker
            thread = threading.Thread(
                target=self._worker_loop,
                name="facebook-capture-primary-standby",
                daemon=True,
            )
            previous_started_ids = set(self._started_worker_ids)
            self._workers.append(thread)
            self._started_worker_ids.add(worker_id)
            self._worker = thread
            try:
                # Keep publication/start serialized: another starter must not
                # receive this handle before a failed native start is rolled back.
                thread.start()
            except Exception as exc:
                # Never discard a thread that may already own capture work.
                # Only a confirmed unstarted handle is eligible for rollback.
                if thread.ident is None and self._worker is thread:
                    self._worker = None
                    self._workers.remove(thread)
                    self._started_worker_ids = previous_started_ids
                if isinstance(exc, RuntimeError):
                    # Preserve the cause but expose a narrowly typed operational
                    # error, not every RuntimeError from unrelated Manager code.
                    raise SchedulerStartError("capture scheduler startup failed") from exc
                raise
            return thread

    @staticmethod
    def _manifest_payload(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "cancel_requested_at": job.get("cancel_requested_at"),
            "request": job.get("request"),
            "completion_scope": job.get("completion_scope"),
            "error": job.get("error"),
            "run_dir": job.get("run_dir"),
            "capture_run_dir": job.get("capture_run_dir"),
            "attempt_dirs": list(job.get("attempt_dirs") or []),
            "batch_id": job.get("batch_id"),
            "batch_index": job.get("batch_index"),
            "worker_id": job.get("worker_id"),
            "serial": job.get("serial"),
            "attempts": int(job.get("attempts") or 0),
            "failover_count": int(job.get("failover_count") or 0),
            "last_worker_id": job.get("last_worker_id"),
            "last_serial": job.get("last_serial"),
            "checkpoint": job.get("checkpoint"),
            "recovery_state": job.get("recovery_state"),
            "process_pid": job.get("process_pid"),
            "process_creation_time": job.get("process_creation_time"),
            "process_started_at": job.get("process_started_at"),
            "process_state": job.get("process_state"),
            "transport_cleanup_state": job.get("transport_cleanup_state"),
            "transport_cleanup_error": job.get("transport_cleanup_error"),
        }

    @staticmethod
    def _batch_manifest_payload(batch: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "batch_id": batch.get("batch_id"),
            "created_at": batch.get("created_at"),
            "job_ids": list(batch.get("job_ids") or []),
            "run_dir": batch.get("run_dir"),
            "completion_scope": batch.get("completion_scope"),
        }

    def _persist_job(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise ApiError("cannot persist job without job_id")
        _atomic_write_json(_job_manifest_path(job_id), self._manifest_payload(job))

    def _persist_batch(self, batch: dict[str, Any]) -> None:
        batch_id = str(batch.get("batch_id") or "").strip()
        if not batch_id:
            raise ApiError("cannot persist batch without batch_id")
        _atomic_write_json(_batch_manifest_path(batch_id), self._batch_manifest_payload(batch))

    @staticmethod
    def _batch_child_is_committed(job: dict[str, Any]) -> bool:
        batch_id = job.get("batch_id")
        if batch_id in (None, ""):
            return True
        # Use the existing version-1 batch manifest as the commit marker, while
        # keeping path selection confined to the manifest directory.
        if not isinstance(batch_id, str) or not re.fullmatch(r"batch_[A-Za-z0-9_-]+", batch_id):
            return False
        batch = _read_json(_batch_manifest_path(batch_id))
        job_ids = batch.get("job_ids")
        return bool(
            batch.get("version") == 1
            and batch.get("batch_id") == batch_id
            and isinstance(job_ids, list)
            and job.get("job_id") in job_ids
        )

    def _load_persisted_jobs(self) -> None:
        job_dir = BASE_DIR / "api-jobs"
        if not job_dir.exists():
            return
        allowed_statuses = {"queued", "running", "cancelling", "completed", "failed", "cancelled"}
        for path in sorted(job_dir.glob("fb_*.job.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(raw, dict) or raw.get("version") != 1:
                    continue
                job_id = str(raw.get("job_id") or "").strip()
                expected_job_id = path.name.removesuffix(".job.json")
                if not job_id or job_id != expected_job_id:
                    continue
                status = str(raw.get("status") or "").strip()
                if status not in allowed_statuses:
                    continue
                raw_request = raw.get("request") or {}
                request = normalize_persisted_request(raw_request)
                run_dir = raw.get("run_dir") or None
                capture_run_dir = raw.get("capture_run_dir") or run_dir
                attempt_dirs = [str(value) for value in raw.get("attempt_dirs") or [] if str(value).strip()]
                if capture_run_dir and not attempt_dirs:
                    attempt_dirs = [str(capture_run_dir)]
                checkpoint = raw.get("checkpoint") if isinstance(raw.get("checkpoint"), dict) else None
                if capture_run_dir:
                    checkpoint_path = Path(str(capture_run_dir))
                    if not checkpoint_path.is_absolute():
                        checkpoint_path = BASE_DIR / checkpoint_path
                    checkpoint = build_capture_checkpoint(
                        checkpoint_path,
                        worker_id=str(raw.get("worker_id") or raw.get("last_worker_id") or "") or None,
                        serial=str(raw.get("serial") or raw.get("last_serial") or "") or None,
                    )
                process_pid = _coerce_process_pid(raw.get("process_pid"))
                process_creation_time = _coerce_process_creation_time(raw.get("process_creation_time"))
                process_state = str(raw.get("process_state") or "") or None
                transport_cleanup_state = str(raw.get("transport_cleanup_state") or "") or None
                transport_cleanup_error = str(raw.get("transport_cleanup_error") or "") or None
                persisted_worker_id = str(raw.get("worker_id") or raw.get("last_worker_id") or "").strip()
                process_evidence = bool(
                    (status in {"running", "cancelling"} and process_state != "stopped")
                    or process_pid is not None
                    or process_state in self._UNVERIFIED_PROCESS_STATES
                )
                original_process_evidence = process_evidence
                identity_state = _process_identity_state(process_pid, process_creation_time)
                identity_matches = identity_state == "match"
                if process_evidence and process_pid is not None and identity_matches:
                    # The PID belongs to the old capture process.  It is safe
                    # to terminate that known tree before allowing a worker
                    # to acquire the global capture lease again.
                    if _terminate_known_pid(process_pid, process_creation_time):
                        process_pid = None
                        process_creation_time = None
                        process_state = "stopped"
                        process_evidence = False
                    else:
                        process_state = "restart-process-still-running"
                elif process_evidence and process_pid is not None and identity_state == "absent":
                    # The operating system has confirmed that the persisted
                    # PID no longer exists, so the old task cannot retain the
                    # capture lease.
                    process_pid = None
                    process_creation_time = None
                    process_state = "stopped"
                    process_evidence = False
                elif process_evidence:
                    # A persisted PID without a matching creation time cannot
                    # be killed safely: it may have been reused by another
                    # process.  Keep every worker blocked until this is
                    # resolved by an operator or a later verified cleanup.
                    process_state = "restart-unverified"
                reopened_cancelled = status == "cancelled" and process_evidence
                if reopened_cancelled:
                    # Older versions could persist a terminal cancellation
                    # before proving that its capture process had stopped.
                    # Reopen that record so the normal cancel path can retry
                    # cleanup instead of treating the stale terminal state as
                    # proof that the process is gone.
                    status = "cancelling"
                job = {
                    "job_id": job_id,
                    "status": status,
                    "created_at": raw.get("created_at") or utc_now(),
                    "started_at": raw.get("started_at"),
                    "finished_at": None if reopened_cancelled else raw.get("finished_at"),
                    "cancel_requested_at": (
                        raw.get("cancel_requested_at")
                        or raw.get("finished_at")
                        or utc_now()
                    ) if reopened_cancelled else raw.get("cancel_requested_at"),
                    "request": request,
                    "completion_scope": raw.get("completion_scope"),
                    "result": None,
                    "error": raw.get("error"),
                    "run_dir": run_dir,
                    "capture_run_dir": capture_run_dir,
                    "attempt_dirs": attempt_dirs,
                    "batch_id": raw.get("batch_id") or None,
                    "batch_index": raw.get("batch_index"),
                    "worker_id": raw.get("worker_id") or None,
                    "serial": raw.get("serial") or None,
                    "attempts": int(raw.get("attempts") or 0),
                    "failover_count": int(raw.get("failover_count") or 0),
                    "last_worker_id": raw.get("last_worker_id") or raw.get("worker_id") or None,
                    "last_serial": raw.get("last_serial") or raw.get("serial") or None,
                    "checkpoint": checkpoint,
                    "recovery_state": raw.get("recovery_state") or ("checkpointed" if checkpoint else None),
                    "process_pid": process_pid,
                    "process_creation_time": process_creation_time,
                    "process_started_at": raw.get("process_started_at"),
                    "process_state": process_state,
                    "transport_cleanup_state": transport_cleanup_state,
                    "transport_cleanup_error": transport_cleanup_error,
                }
                transport_cleanup_pending = transport_cleanup_state not in {
                    None,
                    TRANSPORT_CLEANUP_RESTORED,
                    TRANSPORT_CLEANUP_NOT_REQUIRED,
                    TRANSPORT_CLEANUP_RUNNER_OWNED,
                }
                if transport_cleanup_pending:
                    self._block_capture_lease_locked(
                        job_id,
                        transport_cleanup_error
                        or "capture transport cleanup is incomplete; worker takeover is blocked",
                    )
                    if persisted_worker_id:
                        self._orphaned_worker_ids.add(persisted_worker_id)
                if process_evidence:
                    self._block_capture_lease_locked(
                        job_id,
                        (
                            "persisted capture process is still running; worker takeover is blocked"
                            if identity_matches
                            else "persisted capture process identity could not be verified; worker takeover is blocked"
                        ),
                    )
                    if persisted_worker_id:
                        self._orphaned_worker_ids.add(persisted_worker_id)
                if job.get("recovery_state") == "failed-result-persistence":
                    # A stopped process/clean transport does not acknowledge a
                    # persisted result-storage failure. Rebuild the separate
                    # reason registry before any scheduler can start.
                    self._block_capture_lease_locked(
                        job_id,
                        "persisted capture result failure requires storage reconciliation; worker takeover is blocked",
                        persistence=True,
                    )
                    if persisted_worker_id:
                        self._orphaned_worker_ids.add(persisted_worker_id)
                if status == "cancelling" and transport_cleanup_pending and not process_evidence:
                    # A stopped runner still needs transport cleanup.  Keep the
                    # cancellation visible and blocked until an explicit retry
                    # can perform the bounded helper operation.
                    job["recovery_state"] = "cancelling-transport-cleanup-pending"
                    self._persist_job(job)
                elif status == "cancelling":
                    if process_evidence:
                        # A cancellation request survives service restart, but
                        # an unverified process still owns the serial lease.
                        job["recovery_state"] = "cancelling-after-restart"
                        self._persist_job(job)
                    else:
                        # Only an explicitly stopped/absent process is enough
                        # to make a pending cancellation terminal.
                        job["status"] = "cancelled"
                        job["finished_at"] = raw.get("finished_at") or utc_now()
                        job["process_state"] = "stopped"
                        job["recovery_state"] = "cancelled-after-restart"
                        self._persist_job(job)
                elif status in {"queued", "running"}:
                    if transport_cleanup_pending:
                        job["status"] = "failed"
                        job["error"] = (
                            str(job.get("error") or "").strip()
                            or "capture transport cleanup is incomplete; worker takeover is blocked"
                        )
                        job["finished_at"] = raw.get("finished_at") or utc_now()
                        job["recovery_state"] = "restart-transport-cleanup-pending"
                        self._persist_job(job)
                    elif status == "queued" and not self._batch_child_is_committed(job):
                        # A child manifest alone is not submission acceptance.
                        # Keep any process/lease evidence above intact, but do
                        # not auto-run a partially written, uncommitted batch.
                        job["status"] = "failed"
                        job["finished_at"] = raw.get("finished_at") or utc_now()
                        job["recovery_state"] = "restart-uncommitted-batch"
                        job["error"] = "queued batch child has no matching committed batch manifest; automatic recovery is blocked"
                        self._persist_job(job)
                    elif not process_evidence:
                        # Only jobs with a verified-clear capture lease are
                        # eligible for restart recovery.  Preserve prior
                        # attempts/checkpoints, but clear live ownership so the
                        # normal scheduler creates a fresh attempt directory.
                        if persisted_worker_id:
                            job["last_worker_id"] = persisted_worker_id
                        persisted_serial = str(raw.get("serial") or raw.get("last_serial") or "").strip()
                        if persisted_serial:
                            job["last_serial"] = persisted_serial
                        job["status"] = "queued"
                        job["started_at"] = None
                        job["finished_at"] = None
                        job["worker_id"] = None
                        job["serial"] = None
                        job["process_pid"] = None
                        job["process_creation_time"] = None
                        job["process_started_at"] = None
                        if status == "running":
                            job["error"] = "interrupted by API service restart; safely requeued"
                            job["recovery_state"] = (
                                "checkpointed-requeued-after-restart" if checkpoint else "requeued-after-restart"
                            )
                        else:
                            job["recovery_state"] = raw.get("recovery_state") or "queued-after-restart"
                        self._persist_job(job)
                        self._queue.put(job_id)
                    else:
                        job["status"] = "failed"
                        job["error"] = "interrupted by API service restart; capture was not automatically resumed"
                        job["finished_at"] = utc_now()
                        if identity_matches:
                            job["recovery_state"] = "restart-process-still-running-no-auto-resume"
                            job["error"] += "; persisted capture process is still running and worker takeover is blocked"
                        else:
                            job["recovery_state"] = "restart-process-identity-unverified-no-auto-resume"
                            job["error"] += "; persisted capture process identity could not be verified"
                        self._persist_job(job)
                elif process_evidence:
                    # Process uncertainty must not replace a durable storage
                    # diagnostic needed to rebuild its independent block.
                    if job.get("recovery_state") != "failed-result-persistence":
                        job["recovery_state"] = "restart-process-identity-unverified-no-auto-resume"
                    job["error"] = (
                        str(job.get("error") or "").strip()
                        or "persisted capture process identity could not be verified"
                    )
                    self._persist_job(job)
                elif original_process_evidence and process_state == "stopped":
                    # Persist the cleared PID after a verified startup cleanup
                    # so a later restart does not rediscover the old process.
                    self._persist_job(job)
                self._jobs[job_id] = job
            except (OSError, json.JSONDecodeError, ApiError, TypeError, ValueError):
                # Ignore malformed/stale manifests rather than preventing API startup.
                continue

    def _load_persisted_batches(self) -> None:
        batch_dir = BASE_DIR / "api-batches"
        if not batch_dir.exists():
            return
        for path in sorted(batch_dir.glob("batch_*.batch.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                batch_id = str(raw.get("batch_id") or "").strip()
                job_ids = [str(value) for value in raw.get("job_ids") or []]
                if raw.get("version") != 1 or path.name != f"{batch_id}.batch.json":
                    continue
                if not job_ids or any(job_id not in self._jobs for job_id in job_ids):
                    continue
                self._batches[batch_id] = {
                    "batch_id": batch_id,
                    "created_at": raw.get("created_at") or utc_now(),
                    "job_ids": job_ids,
                    "run_dir": raw.get("run_dir") or str(BASE_DIR / "runs" / batch_id),
                    "completion_scope": raw.get("completion_scope"),
                }
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_request(payload)
        job_id = f"fb_{uuid.uuid4().hex[:16]}"
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "cancel_requested_at": None,
            "request": request,
            "completion_scope": None,
            "result": None,
            "error": None,
            "run_dir": None,
            "capture_run_dir": None,
            "attempt_dirs": [],
            "batch_id": None,
            "batch_index": None,
            "worker_id": None,
            "serial": None,
            "attempts": 0,
            "failover_count": 0,
            "last_worker_id": None,
            "last_serial": None,
            "checkpoint": None,
            "recovery_state": None,
            "process_pid": None,
            "process_creation_time": None,
            "process_started_at": None,
            "process_state": None,
            "transport_cleanup_state": None,
            "transport_cleanup_error": None,
        }
        with self._lock:
            # A rejected write must not leave an in-memory queued ghost.
            self._persist_job(job)
            self._jobs[job_id] = job
        self._queue.put(job_id)
        return self.public_job(job_id)

    def submit_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_batch_request(payload)
        batch_id = f"batch_{uuid.uuid4().hex[:16]}"
        batch_run_dir = BASE_DIR / "runs" / batch_id
        created_at = utc_now()
        jobs: list[dict[str, Any]] = []
        for index, post_url in enumerate(request["post_urls"], start=1):
            job_id = f"fb_{uuid.uuid4().hex[:16]}"
            jobs.append(
                {
                    "job_id": job_id,
                    "status": "queued",
                    "created_at": created_at,
                    "started_at": None,
                    "finished_at": None,
                    "cancel_requested_at": None,
                    "request": {
                        "post_url": post_url,
                        "post_id": "",
                        "collect": list(request["collect"]),
                        "save_artifacts": list(request["save_artifacts"]),
                        "reaction_scroll_limit": request["reaction_scroll_limit"],
                        "reaction_scroll_limit_explicit": request["reaction_scroll_limit_explicit"],
                    },
                    "completion_scope": None,
                    "result": None,
                    "error": None,
                    "run_dir": str(batch_run_dir / f"post-{index:03d}"),
                    "capture_run_dir": None,
                    "attempt_dirs": [],
                    "batch_id": batch_id,
                    "batch_index": index,
                    "worker_id": None,
                    "serial": None,
                    "attempts": 0,
                    "failover_count": 0,
                    "last_worker_id": None,
                    "last_serial": None,
                    "checkpoint": None,
                    "recovery_state": None,
                    "process_pid": None,
                    "process_creation_time": None,
                    "process_started_at": None,
                    "process_state": None,
                    "transport_cleanup_state": None,
                    "transport_cleanup_error": None,
                }
            )
        batch = {
            "batch_id": batch_id,
            "created_at": created_at,
            "job_ids": [job["job_id"] for job in jobs],
            "run_dir": str(batch_run_dir),
            "completion_scope": None,
        }
        # Prepare derived outputs before recording a committed batch. Unfinished
        # child writes stay private and cannot be scheduled on restart.
        write_batch_outputs(batch_run_dir, batch_id, jobs)
        with self._lock:
            for job in jobs:
                self._persist_job(job)
            self._persist_batch(batch)
            self._batches[batch_id] = batch
            self._jobs.update({job["job_id"]: job for job in jobs})
        for job in jobs:
            self._queue.put(job["job_id"])
        return self.batch_progress(batch_id)

    def _batch_jobs(self, batch_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self._lock:
            batch = self._batches.get(batch_id)
            if not batch:
                raise KeyError(batch_id)
            batch_copy = dict(batch)
            jobs = [dict(self._jobs[job_id]) for job_id in batch["job_ids"] if job_id in self._jobs]
        return batch_copy, jobs

    def _refresh_batch_outputs(self, batch_id: str) -> dict[str, Any]:
        batch, jobs = self._batch_jobs(batch_id)
        summary = write_batch_outputs(Path(batch["run_dir"]), batch_id, jobs)
        with self._lock:
            current = self._batches.get(batch_id)
            if current is not None:
                current["completion_scope"] = summary.get("completion_scope")
                self._persist_batch(current)
        return summary

    def batch_progress(self, batch_id: str) -> dict[str, Any]:
        batch, jobs = self._batch_jobs(batch_id)
        batch_dir = Path(batch["run_dir"])
        persisted_summary = _read_json(batch_dir / "batch-summary.json")
        summary = write_batch_outputs(batch_dir, batch_id, jobs, persist=False)
        if persisted_summary.get("updated_at"):
            summary["updated_at"] = persisted_summary["updated_at"]
        post_summaries: list[dict[str, Any]] = []
        for index, job in enumerate(jobs, start=1):
            status = str(job.get("status") or "queued")
            evidence_dirs = _job_evidence_dirs(job)
            progress_run_dir = str(job.get("capture_run_dir") or job.get("run_dir") or "")
            run_dir = evidence_dirs[0] if evidence_dirs else Path()
            metrics = _merge_reaction_progress(evidence_dirs) if evidence_dirs else {}
            metrics.update(_merge_share_progress(evidence_dirs) if evidence_dirs else {})
            collect = set((job.get("request") or {}).get("collect") or ["reactions"])
            valid_users = (
                int(metrics.get("share_users") or 0)
                if collect == {"shares"}
                else _count_reactor_users(evidence_dirs) if evidence_dirs else 0
            )
            result_outcome = _job_result_outcome(job, evidence_dirs, valid_users)
            if status == "running":
                snapshot = build_progress_snapshot(
                    run_dir,
                    status,
                    request=job.get("request") or {},
                    evidence_dirs=evidence_dirs,
                )
                active = _select_batch_current_stage(snapshot)
            elif status == "cancelling":
                active = "停止中"
            elif status == "completed":
                active = (
                    "样本完成（非全量）"
                    if str(job.get("completion_scope") or "") == BOUNDED_SAMPLE_COMPLETION_SCOPE
                    else result_outcome["detail"] if result_outcome else "证据链完成"
                )
            elif status == "failed":
                if metrics.get("stop_reason") == "rate-limited" and int(metrics.get("reaction_users") or 0) > 0:
                    active = "限流，已保留部分结果"
                else:
                    active = "失败"
            elif status == "cancelled":
                active = "已停止"
            else:
                active = "等待队列"
            post_summaries.append(
                {
                    "index": int(job.get("batch_index") or index),
                    "job_id": job["job_id"],
                    "post_url": str((job.get("request") or {}).get("post_url") or ""),
                    "status": status,
                    "completion_scope": str(job.get("completion_scope") or "") or None,
                    "current_stage": active,
                    "result_outcome": result_outcome,
                    "valid_users": valid_users or int(metrics.get("reaction_users") or 0),
                    "reaction_pages": int(metrics.get("reaction_pages") or 0),
                    "run_dir": str(job.get("run_dir") or ""),
                    "worker_id": job.get("worker_id"),
                    "serial": job.get("serial"),
                }
            )
        status_counts = {
            name: sum(1 for job in jobs if job.get("status") == name)
            for name in ("queued", "running", "cancelling", "completed", "failed", "cancelled")
        }
        terminal_count = status_counts["completed"] + status_counts["failed"] + status_counts["cancelled"]
        if status_counts["running"] or status_counts["cancelling"]:
            live_status = "running"
        elif status_counts["queued"]:
            live_status = "queued"
        elif terminal_count == len(jobs) and (status_counts["failed"] or status_counts["cancelled"]):
            live_status = "completed_with_errors"
        elif terminal_count == len(jobs):
            live_status = "completed"
        else:
            live_status = "queued"
        summary.update(
            {
                "status": live_status,
                "total_posts": len(jobs),
                "status_counts": status_counts,
                "completion_scope": _completion_scope_for_jobs(jobs),
                "per_post_valid_sum": sum(int(post["valid_users"]) for post in post_summaries),
            }
        )
        with self._lock:
            current = self._batches.get(batch_id)
            if current is not None and current.get("completion_scope") != summary.get("completion_scope"):
                current["completion_scope"] = summary.get("completion_scope")
                self._persist_batch(current)
        artifacts: list[dict[str, Any]] = []
        for name, label, layer in BATCH_ARTIFACT_SPECS:
            path = batch_dir / name
            try:
                checked = _checked_artifact_path(batch_dir, Path(name), unavailable="batch artifact is not available yet")
                size = checked.stat().st_size
            except (ApiError, OSError):
                size = 0
            artifacts.append(
                {
                    "name": name,
                    "label": label,
                    "layer": layer,
                    "path": str(path),
                    "size_bytes": size,
                    "state": "ready" if size else "waiting",
                    "viewable": True,
                    "url": f"/v1/batches/{batch_id}/artifacts/{name}",
                }
            )
        return {
            **summary,
            "created_at": batch.get("created_at"),
            "run_dir": batch["run_dir"],
            "posts": post_summaries,
            "artifacts": artifacts,
        }

    def latest_batch(self) -> dict[str, Any]:
        with self._lock:
            if not self._batches:
                raise KeyError("latest")
            batch_id = max(self._batches, key=lambda value: str(self._batches[value].get("created_at") or ""))
        return self.batch_progress(batch_id)

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        _batch, jobs = self._batch_jobs(batch_id)
        cancelled = 0
        for job in jobs:
            response = self.cancel(job["job_id"])
            cancelled += int(bool(response["cancelled"]))
        self._refresh_batch_outputs(batch_id)
        return {"cancelled_jobs": cancelled, "batch": self.batch_progress(batch_id)}

    def batch_artifact(self, batch_id: str, artifact_name: str) -> Path:
        allowed = {name for name, _label, _layer in BATCH_ARTIFACT_SPECS}
        if artifact_name not in allowed:
            raise ApiError("unknown batch artifact")
        batch, _jobs = self._batch_jobs(batch_id)
        return _checked_artifact_path(
            Path(batch["run_dir"]), Path(artifact_name), unavailable="batch artifact is not available yet"
        )

    def _release_capture_lease_locked(self, job_id: str) -> None:
        """Release only the lease that this job has positively relinquished."""
        if self._capture_lease_job_id != job_id:
            return
        if job_id in self._persistence_blocked_jobs:
            self._block_capture_lease_locked(job_id, self._persistence_blocked_jobs[job_id])
            return
        other_job_id = next(
            (
                other_id
                for other_id, other_job in self._jobs.items()
                if other_id != job_id and (
                    self._job_has_uncertain_process_locked(other_job, other_id)
                    or self._transport_cleanup_pending_locked(other_job)
                    or other_id in self._persistence_blocked_jobs
                )
            ),
            None,
        )
        if other_job_id is not None:
            self._capture_lease_job_id = other_job_id
            self._capture_lease_reason = self._persistence_blocked_jobs.get(other_job_id) or (
                "another capture process or transport cleanup remains unverified; worker takeover is blocked"
            )
            self._capture_lease_uncertain = True
            self._wake_event.set()
            return
        self._capture_lease_uncertain = False
        self._capture_lease_job_id = None
        self._capture_lease_reason = None
        self._wake_event.set()

    def _job_has_uncertain_process_locked(self, job: dict[str, Any], job_id: str | None = None) -> bool:
        process_state = str(job.get("process_state") or "")
        return bool(
            (
                job.get("status") in {"running", "cancelling"}
                and process_state != "stopped"
            )
            or job.get("process_pid") is not None
            or process_state in self._UNVERIFIED_PROCESS_STATES
            or (job_id is not None and job_id in self._running_processes)
        )

    @staticmethod
    def _transport_cleanup_pending_locked(job: dict[str, Any]) -> bool:
        return str(job.get("transport_cleanup_state") or "") not in {
            "",
            TRANSPORT_CLEANUP_RESTORED,
            TRANSPORT_CLEANUP_NOT_REQUIRED,
            TRANSPORT_CLEANUP_RUNNER_OWNED,
        }

    def _mark_capture_runner_started(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["transport_cleanup_state"] = TRANSPORT_CLEANUP_PENDING
            job["transport_cleanup_error"] = None
            self._persist_job(job)

    @staticmethod
    def _macos_runner_cleanup_result(job: dict[str, Any]) -> dict[str, Any]:
        """Verify that the native runner reached its signal-safe finally block."""
        raw = str(job.get("capture_run_dir") or "").strip()
        if not raw:
            attempt_dirs = [str(value).strip() for value in job.get("attempt_dirs") or [] if str(value).strip()]
            raw = attempt_dirs[-1] if attempt_dirs else str(job.get("run_dir") or "").strip()
        if not raw:
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status is missing; finally block was not verified",
            }
        run_dir = Path(raw)
        if not run_dir.is_absolute():
            run_dir = BASE_DIR / run_dir
        status_path = run_dir / "android-capture-status.json"
        status = _read_json(status_path)
        if not status:
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status is missing; finally block was not verified",
            }
        required_fields = (
            "run_id",
            "exit_code",
            "stop_reason",
            "pagination_outcome",
            "completion_scope",
            "source_post_id",
            "attempts",
            "completion_claim_allowed",
            "cleanup_errors",
            "native_scroll",
            "save_artifacts",
            "partial_evidence_retained",
        )
        missing_fields = [field for field in required_fields if field not in status]
        if missing_fields:
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": (
                    "native runner cleanup status is incomplete; missing "
                    + ", ".join(missing_fields)
                ),
            }
        run_id = status.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status run_id is invalid",
            }
        if not isinstance(status.get("exit_code"), int) or isinstance(status.get("exit_code"), bool):
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status exit_code is invalid",
            }
        stop_reason = status.get("stop_reason")
        if not isinstance(stop_reason, str) or not stop_reason.strip():
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status stop_reason is invalid",
            }
        for field in ("pagination_outcome", "completion_scope", "source_post_id"):
            value = status.get(field)
            if value is not None and not isinstance(value, str):
                return {
                    "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                    "transport_cleanup_error": f"native runner cleanup status {field} is invalid",
                }
        attempts = status.get("attempts")
        if (
            not isinstance(attempts, int)
            or isinstance(attempts, bool)
            or attempts < 0
        ):
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status attempts is invalid",
            }
        if not isinstance(status.get("completion_claim_allowed"), bool):
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status completion_claim_allowed is invalid",
            }
        if not isinstance(status.get("native_scroll"), str) or not status["native_scroll"].strip():
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status native_scroll is invalid",
            }
        save_artifacts = status.get("save_artifacts")
        if not isinstance(save_artifacts, list) or any(
            not isinstance(value, str) for value in save_artifacts
        ):
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status save_artifacts is invalid",
            }
        if not isinstance(status.get("partial_evidence_retained"), bool):
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status partial_evidence_retained is invalid",
            }

        # The API records the capture subprocess start time.  The runner's
        # status file must be newer than that attempt, otherwise a prior
        # attempt could be mistaken for signal-safe cleanup.
        started_raw = str(
            job.get("process_started_at") or job.get("started_at") or ""
        ).strip()
        if not started_raw:
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status timestamp is missing",
            }
        try:
            started_timestamp = datetime.fromisoformat(
                started_raw.replace("Z", "+00:00")
            )
            if started_timestamp.tzinfo is None:
                raise ValueError("timezone is missing")
            status_mtime = status_path.stat().st_mtime
            if status_mtime + 1.0 < started_timestamp.timestamp():
                return {
                    "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                    "transport_cleanup_error": "native runner cleanup status is stale; finally block was not verified",
                }
        except (OSError, TypeError, ValueError):
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status timestamp is invalid",
            }

        # Newer native runners may include explicit timestamps.  Validate them
        # when present while keeping compatibility with the current writer,
        # whose file mtime is the completion timestamp.
        status_times: dict[str, float] = {}
        for field in ("started_at", "finished_at", "updated_at"):
            if field not in status:
                continue
            value = status.get(field)
            if not isinstance(value, str) or not value.strip():
                return {
                    "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                    "transport_cleanup_error": f"native runner cleanup status {field} timestamp is invalid",
                }
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone is missing")
                status_times[field] = parsed.timestamp()
            except (TypeError, ValueError):
                return {
                    "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                    "transport_cleanup_error": f"native runner cleanup status {field} timestamp is invalid",
                }
        if (
            "started_at" in status_times
            and "finished_at" in status_times
            and status_times["finished_at"] < status_times["started_at"]
        ):
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status timestamps are inconsistent",
            }

        owner_marker = run_dir / ".android-capture-owner"
        if owner_marker.is_file():
            try:
                marker_run_id = owner_marker.read_text(encoding="ascii").strip()
            except (OSError, UnicodeError):
                return {
                    "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                    "transport_cleanup_error": "native runner cleanup status run_id marker is unreadable",
                }
            if not marker_run_id or marker_run_id != run_id.strip():
                return {
                    "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                    "transport_cleanup_error": "native runner cleanup status run_id does not match current attempt",
                }

        errors = status.get("cleanup_errors")
        if not isinstance(errors, list):
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup status cleanup_errors is invalid",
            }
        if errors:
            return {
                "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                "transport_cleanup_error": "native runner cleanup incomplete: " + ", ".join(str(item) for item in errors),
            }
        return {
            "transport_cleanup_state": TRANSPORT_CLEANUP_RUNNER_OWNED,
            "transport_cleanup_error": None,
        }

    def _cleanup_job_transport(self, job_id: str) -> bool:
        owner = False
        wait_for: threading.Event | None = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or not self._transport_cleanup_pending_locked(job):
                return True
            wait_for = self._transport_cleanup_inflight.get(job_id)
            if wait_for is None:
                wait_for = threading.Event()
                self._transport_cleanup_inflight[job_id] = wait_for
                owner = True
            if not owner:
                # The first caller performs the only ADB mutation.  Wait for
                # its bounded operation so a concurrent HTTP cancel/final
                # callback cannot launch a second watchdog or proxy restore.
                pass
            worker_id = str(job.get("worker_id") or job.get("last_worker_id") or "").strip()
            worker = self.workers.get(worker_id) if worker_id else None

        if not owner:
            wait_for.wait(timeout=60.0)
            with self._lock:
                current = self._jobs.get(job_id)
                return bool(
                    current
                    and not self._transport_cleanup_pending_locked(current)
                )

        try:
            try:
                if sys.platform == "darwin":
                    result = self._macos_runner_cleanup_result(job)
                elif sys.platform != "win32":
                    result = {
                        "transport_cleanup_state": TRANSPORT_CLEANUP_NOT_REQUIRED,
                        "transport_cleanup_error": None,
                    }
                elif worker is None:
                    result = {
                        "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                        "transport_cleanup_error": f"worker configuration is unavailable for transport cleanup: {worker_id}",
                    }
                else:
                    result = _cleanup_transport_after_capture(worker)
            except Exception as exc:
                # Cleanup is a safety boundary: an unexpected helper failure
                # must become persisted blocked state, not escape cancellation
                # while leaving the transport marker pending.
                result = {
                    "transport_cleanup_state": TRANSPORT_CLEANUP_FAILED,
                    "transport_cleanup_error": f"{type(exc).__name__}: {exc}",
                }

            if not isinstance(result, dict):
                state = TRANSPORT_CLEANUP_FAILED
                error = "transport cleanup helper returned no result"
            else:
                raw_state = result.get("transport_cleanup_state")
                state = raw_state.strip() if isinstance(raw_state, str) else ""
                error_values: list[str] = []
                malformed_error = False
                for key in ("transport_cleanup_error", "error"):
                    if key not in result or result.get(key) is None:
                        continue
                    value = result.get(key)
                    if not isinstance(value, str):
                        malformed_error = True
                        continue
                    if value.strip():
                        error_values.append(value.strip())
                error = error_values[0] if error_values else ""
                platform_success_state = {
                    "win32": {TRANSPORT_CLEANUP_RESTORED},
                    "darwin": {TRANSPORT_CLEANUP_RUNNER_OWNED},
                }.get(sys.platform, {TRANSPORT_CLEANUP_NOT_REQUIRED})
                success_state = state in platform_success_state
                if malformed_error:
                    state = TRANSPORT_CLEANUP_FAILED
                    error = error or "transport cleanup helper returned an invalid error"
                elif success_state and error:
                    # A success marker paired with an error is contradictory;
                    # keep the lease blocked until a clean result is observed.
                    state = TRANSPORT_CLEANUP_FAILED
                elif state in {
                    TRANSPORT_CLEANUP_RESTORED,
                    TRANSPORT_CLEANUP_NOT_REQUIRED,
                    TRANSPORT_CLEANUP_RUNNER_OWNED,
                } and state not in platform_success_state:
                    invalid_state = state
                    state = TRANSPORT_CLEANUP_FAILED
                    error = f"transport cleanup helper returned invalid state for {sys.platform}: {invalid_state}"
                elif not success_state and state not in {
                    TRANSPORT_CLEANUP_FAILED,
                    "blocked-external-proxy",
                }:
                    state = TRANSPORT_CLEANUP_FAILED
                    error = error or "transport cleanup helper returned an invalid state"
                if not error:
                    error = "transport cleanup helper returned no result"
            cleanup_satisfied = state in {
                TRANSPORT_CLEANUP_RESTORED,
                TRANSPORT_CLEANUP_NOT_REQUIRED,
                TRANSPORT_CLEANUP_RUNNER_OWNED,
            }
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None:
                    return cleanup_satisfied
                if cleanup_satisfied:
                    current["transport_cleanup_state"] = (
                        state
                    )
                    current["transport_cleanup_error"] = None
                    self._persist_job(current)
                    if not self._job_has_uncertain_process_locked(current):
                        self._release_capture_lease_locked(job_id)
                        if current.get("status") == "failed":
                            self._restore_worker_after_capture_cleanup_locked(job_id)
                    return True

                current["transport_cleanup_state"] = state or TRANSPORT_CLEANUP_FAILED
                current["transport_cleanup_error"] = error
                # A failed terminal job keeps its original failure reason;
                # cleanup retry state is exposed separately in the manifest.
                if current.get("status") != "failed":
                    existing_error = str(current.get("error") or "").strip()
                    cleanup_error = f"transport cleanup failed: {error}"
                    if cleanup_error not in existing_error:
                        current["error"] = f"{existing_error}; {cleanup_error}" if existing_error else cleanup_error
                if current.get("status") == "cancelling":
                    current["status"] = "cancelled"
                    current["finished_at"] = current.get("finished_at") or utc_now()
                    current["recovery_state"] = "cancelled-transport-cleanup-failed"
                self._block_capture_lease_locked(
                    job_id,
                    "transport cleanup failed; worker takeover is blocked",
                )
                orphan_worker_id = str(
                    current.get("worker_id") or current.get("last_worker_id") or ""
                ).strip()
                if orphan_worker_id:
                    self._orphaned_worker_ids.add(orphan_worker_id)
                self._persist_job(current)
            return False
        finally:
            with self._lock:
                event = self._transport_cleanup_inflight.pop(job_id, None)
                if event is not None:
                    event.set()

    def _mark_process_stopped_locked(
        self,
        job_id: str,
        *,
        process: subprocess.Popen[Any] | None = None,
        finalize_cancel: bool | None = None,
    ) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return True
        registered = self._running_processes.get(job_id)
        if process is not None and registered is not None and registered is not process:
            return False
        if finalize_cancel is None:
            # A local runner still owns the job when the active job id or a
            # process handle is present; its final callback must close the
            # cancellation.  A restored job has neither and can finalize as
            # soon as its persisted process identity is confirmed stopped.
            finalize_cancel = self._active_job_id != job_id and process is None
        self._running_processes.pop(job_id, None)
        job["process_pid"] = None
        job["process_creation_time"] = None
        job["process_started_at"] = None
        job["process_state"] = "stopped"
        if job.get("status") == "cancelling" and finalize_cancel:
            job["status"] = "cancelled"
            job["finished_at"] = job.get("finished_at") or utc_now()
            job["error"] = None
            job["recovery_state"] = "cancelled"
        cleanup_pending = self._transport_cleanup_pending_locked(job)
        if cleanup_pending:
            self._block_capture_lease_locked(
                job_id,
                str(job.get("transport_cleanup_error") or "transport cleanup is pending; worker takeover is blocked"),
            )
        elif job.get("status") == "cancelling" and not finalize_cancel:
            self._block_capture_lease_locked(
                job_id,
                "capture process stopped, waiting for worker finalization before releasing cancellation lease",
            )
        elif not cleanup_pending:
            self._release_capture_lease_locked(job_id)
        self._persist_job(job)
        if job.get("status") == "cancelled" and not cleanup_pending:
            self._restore_worker_after_capture_cleanup_locked(job_id)
        return True

    def reconcile_result_persistence(self, job_id: str, *, acknowledge: bool = False) -> dict[str, Any]:
        """Explicit internal acknowledgment of a reviewed, terminal result failure.

        Does not validate/revive results, recapture, kill processes, clean transport
        or acknowledge arbitrary persistence faults. Only the explicit guarded
        result acknowledgment route invokes this; no ordinary action does.
        """
        if acknowledge is not True:
            raise ApiError("explicit acknowledgment of reviewed failed output is required")
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if (
                job_id not in self._persistence_blocked_jobs
                or job.get("status") != "failed"
                or job.get("recovery_state") != "failed-result-persistence"
            ):
                raise ApiError("job has no eligible failed-result-persistence acknowledgment")
            if self._active_job_id is not None:
                raise ApiError("cannot reconcile persistence while a capture job is active")
            if (
                job.get("process_state") != "stopped"
                or self._job_has_uncertain_process_locked(job)
                or job_id in self._running_processes
            ):
                raise ApiError("capture process stop must be confirmed before persistence acknowledgment")
            allowed_cleanup = {
                "win32": {TRANSPORT_CLEANUP_RESTORED, TRANSPORT_CLEANUP_RUNNER_OWNED},
                "darwin": {TRANSPORT_CLEANUP_RUNNER_OWNED},
            }.get(sys.platform, {TRANSPORT_CLEANUP_NOT_REQUIRED})
            if (
                job.get("transport_cleanup_state") not in allowed_cleanup
                or self._transport_cleanup_pending_locked(job)
                or job.get("transport_cleanup_error") not in (None, "")
            ):
                raise ApiError("transport cleanup must be explicitly confirmed before persistence acknowledgment")
            worker_id = str(job.get("worker_id") or job.get("last_worker_id") or "").strip()
            if worker_id in self._failed_worker_ids:
                raise ApiError("worker device failure must be resolved separately")
            candidate = dict(job)
            candidate["recovery_state"] = "failed-result-persistence-reconciled"
            self._persist_job(candidate)
            job.update(candidate)
            self._persistence_blocked_jobs.pop(job_id)
            self._release_capture_lease_locked(job_id)
            active_worker_id = self._restore_worker_after_capture_cleanup_locked(job_id)
        if active_worker_id:
            self._start_worker(active_worker_id)
        return {"reconciled": True, "job": self.public_job(job_id)}

    def _restore_worker_after_capture_cleanup_locked(self, job_id: str) -> str | None:
        """Restore dispatch after this job's persisted process is cleared."""
        if job_id in self._persistence_blocked_jobs:
            self._block_capture_lease_locked(job_id, self._persistence_blocked_jobs[job_id])
            return None
        job = self._jobs.get(job_id)
        if job is None:
            return None
        orphan_worker_id = str(job.get("worker_id") or job.get("last_worker_id") or "").strip()
        other_same_worker_uncertain = bool(
            orphan_worker_id
            and any(
                other_id != job_id
                and str(other_job.get("worker_id") or other_job.get("last_worker_id") or "").strip()
                == orphan_worker_id
                and (
                    self._job_has_uncertain_process_locked(other_job, other_id)
                    or self._transport_cleanup_pending_locked(other_job)
                    or other_id in self._persistence_blocked_jobs
                )
                for other_id, other_job in self._jobs.items()
            )
        )
        if orphan_worker_id and not other_same_worker_uncertain:
            # Only clear the worker recorded on the job whose process was
            # positively stopped and whose transport was restored; unrelated
            # worker failures and orphan markers remain intact.
            self._orphaned_worker_ids.discard(orphan_worker_id)
            if job.get("status") == "failed":
                self._failed_worker_ids.discard(orphan_worker_id)
        if self._capture_lease_uncertain:
            return None
        active = self.workers.get(self._active_worker_id) if self._active_worker_id else None
        if (
            active
            and active.enabled
            and active.worker_id not in self._failed_worker_ids
            and active.worker_id not in self._orphaned_worker_ids
        ):
            return active.worker_id
        worker_id = next(
            (
                candidate_id
                for candidate_id in self._worker_order
                if (
                    (candidate := self.workers.get(candidate_id)) is not None
                    and candidate.enabled
                    and candidate_id not in self._failed_worker_ids
                    and candidate_id not in self._orphaned_worker_ids
                )
            ),
            None,
        )
        self._active_worker_id = worker_id
        if worker_id:
            self._started_worker_ids = {worker_id}
            self._wake_event.set()
        return worker_id

    def _mark_process_unverified_locked(self, job_id: str, reason: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        # Keep the process handle and persisted identity available for a later
        # cancellation retry.  Discarding it is not proof of termination.
        job["process_state"] = "stop-unverified"
        self._block_capture_lease_locked(job_id, reason)
        self._persist_job(job)
        return False

    @staticmethod
    def _process_is_alive(process: subprocess.Popen[Any]) -> bool:
        try:
            return process.poll() is None
        except (OSError, AttributeError):
            return True

    def _cleanup_job_process(self, job_id: str) -> bool:
        """Stop the known capture tree before releasing the global lease."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return True
            process = self._running_processes.get(job_id)
            pid = _coerce_process_pid(job.get("process_pid"))
            creation_time = _coerce_process_creation_time(job.get("process_creation_time"))
            process_state = str(job.get("process_state") or "")
            transport_pending = self._transport_cleanup_pending_locked(job)

        stopped = True
        if process is not None:
            if self._process_is_alive(process):
                # A persisted creation time protects against killing a PID
                # that has already been recycled by another process.
                if creation_time is not None and not _process_identity_matches(pid, creation_time):
                    stopped = False
                else:
                    stopped = bool(_terminate_tree(process))
        elif pid is not None or process_state in self._UNVERIFIED_PROCESS_STATES or (
            transport_pending and process_state != "stopped"
        ):
            if pid is not None and creation_time is not None:
                stopped = _terminate_known_pid(pid, creation_time)
            else:
                stopped = False

        if stopped and transport_pending:
            self._cleanup_job_transport(job_id)

        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                return stopped
            if stopped:
                return self._mark_process_stopped_locked(job_id, process=process)
            return self._mark_process_unverified_locked(
                job_id,
                "capture process did not stop or its identity could not be verified; worker takeover is blocked",
            )

    def _set_running_process(
        self,
        job_id: str,
        process: subprocess.Popen[Any] | None,
        *,
        final: bool = False,
    ) -> None:
        terminate_now = False
        previous_finished: subprocess.Popen[Any] | None = None
        finalize_without_process = False
        with self._lock:
            job = self._jobs.get(job_id)
            if process is None:
                previous = self._running_processes.get(job_id)
                if previous is not None:
                    if self._process_is_alive(previous):
                        self._mark_process_unverified_locked(
                            job_id,
                            "capture process stop could not be verified; worker takeover is blocked",
                        )
                        return
                    else:
                        previous_finished = previous
                if job is None:
                    return
                if previous_finished is None and job.get("status") == "cancelling":
                    # The callback can be emitted between emulator bootstrap
                    # and capture process creation.  Keep cancellation
                    # pending until the worker's final callback proves that
                    # no later process can be registered.
                    if final:
                        state = str(job.get("process_state") or "")
                        if (
                            job.get("process_pid") is not None
                            or state in self._UNVERIFIED_PROCESS_STATES
                            or (
                                self._transport_cleanup_pending_locked(job)
                                and state != "stopped"
                            )
                        ):
                            self._mark_process_unverified_locked(
                                job_id,
                                "capture process handle was discarded without termination proof; worker takeover is blocked",
                            )
                            return
                        else:
                            finalize_without_process = True
                    else:
                        job["process_state"] = "cancelling"
                        self._block_capture_lease_locked(
                            job_id,
                            "cancellation requested before capture process stop was confirmed; worker takeover is blocked",
                        )
                        self._persist_job(job)
                        return
                if (
                    previous_finished is None
                    and self._capture_lease_uncertain
                    and self._capture_lease_job_id == job_id
                    and not finalize_without_process
                ):
                    # A stopped process is already independently proven gone.
                    # Keep that proof while transport cleanup remains pending
                    # so cancel() can retry cleanup without converting the job
                    # into stop-unverified and losing the retry path.
                    if (
                        str(job.get("process_state") or "") == "stopped"
                        and self._transport_cleanup_pending_locked(job)
                    ):
                        self._persist_job(job)
                        return
                    if (
                        job.get("status") not in {"cancelling", "cancelled"}
                        and str(job.get("process_state") or "") == "stopped"
                        and not self._transport_cleanup_pending_locked(job)
                    ):
                        # The runner has already reported a terminal result
                        # after its stopped callback.  The pending marker was
                        # resolved by the runner-owned success path, so this
                        # final callback must release the lease rather than
                        # reinterpret a clean completion as an orphan.
                        self._release_capture_lease_locked(job_id)
                        self._persist_job(job)
                        return
                    self._mark_process_unverified_locked(
                        job_id,
                        "capture process handle was discarded without termination proof; worker takeover is blocked",
                    )
                    return
                if previous_finished is None and not finalize_without_process:
                    self._mark_process_stopped_locked(job_id)
                    return

            if process is not None and job is None:
                return
            if process is not None:
                self._running_processes[job_id] = process
                pid = _coerce_process_pid(getattr(process, "pid", None))
                job["process_pid"] = pid
                job["process_creation_time"] = _process_creation_time(pid) if pid else None
                job["process_started_at"] = utc_now()
                if job.get("status") in {"cancelling", "cancelled"}:
                    if job.get("status") == "cancelled":
                        # A late bootstrap/capture callback proves that the
                        # cancellation was observed before this process existed.
                        # Reopen it until this newly registered process is also
                        # confirmed stopped.
                        job["status"] = "cancelling"
                        job["finished_at"] = None
                        job["recovery_state"] = "cancellation-process-registration-race"
                    job["process_state"] = "cancelling"
                    terminate_now = True
                else:
                    job["process_state"] = "running"
                self._persist_job(job)

        if previous_finished is not None:
            with self._lock:
                current = self._jobs.get(job_id)
                should_cleanup = bool(
                    current
                    and current.get("status") in {"cancelling", "cancelled"}
                    and self._transport_cleanup_pending_locked(current)
                )
            if should_cleanup:
                self._cleanup_job_transport(job_id)
            with self._lock:
                self._mark_process_stopped_locked(
                    job_id,
                    process=previous_finished,
                    finalize_cancel=final,
                )
            return

        if finalize_without_process:
            with self._lock:
                should_cleanup = self._transport_cleanup_pending_locked(self._jobs[job_id])
            if should_cleanup:
                self._cleanup_job_transport(job_id)
            with self._lock:
                self._mark_process_stopped_locked(job_id, finalize_cancel=True)
            return

        if terminate_now:
            stopped = bool(_terminate_tree(process))
            if stopped:
                self._cleanup_job_transport(job_id)
            with self._lock:
                if stopped:
                    self._mark_process_stopped_locked(
                        job_id,
                        process=process,
                        finalize_cancel=False,
                    )
                else:
                    self._mark_process_unverified_locked(
                        job_id,
                        "cancelled capture process stop could not be verified; worker takeover is blocked",
                    )

    def cancel(self, job_id: str) -> dict[str, Any]:
        process: subprocess.Popen[Any] | None = None
        process_pid: int | None = None
        process_creation_time: float | None = None
        stopped_without_handle = False
        retry_failed_cleanup = False
        cancelled = False
        reason = ""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            status = str(job.get("status") or "")
            if status == "queued":
                job["status"] = "cancelled"
                job["finished_at"] = utc_now()
                job["error"] = None
                job["recovery_state"] = "cancelled"
                self._persist_job(job)
                cancelled = True
                reason = "queued job cancelled"
            elif status in {"running", "cancelling"}:
                if status == "running":
                    job["status"] = "cancelling"
                    job["cancel_requested_at"] = utc_now()
                    job["finished_at"] = None
                    job["error"] = None
                    job["recovery_state"] = "cancellation-requested"
                    if not job.get("process_state"):
                        job["process_state"] = "cancelling"
                    self._block_capture_lease_locked(
                        job_id,
                        "cancellation requested; worker takeover is blocked until process stop is confirmed",
                    )
                    cancelled = True
                    reason = "running job cancellation requested"
                else:
                    reason = "job cancellation is already in progress"
                process = self._running_processes.get(job_id)
                process_pid = _coerce_process_pid(job.get("process_pid"))
                process_creation_time = _coerce_process_creation_time(job.get("process_creation_time"))
                stopped_without_handle = bool(
                    process is None
                    and process_pid is None
                    and str(job.get("process_state") or "") == "stopped"
                    and self._transport_cleanup_pending_locked(job)
                )
                self._persist_job(job)
            elif status == "cancelled":
                reason = "job is already cancelled"
                job_process_state = str(job.get("process_state") or "")
                process_pid = _coerce_process_pid(job.get("process_pid"))
                stopped_without_handle = bool(
                    process_pid is None
                    and job_process_state == "stopped"
                    and self._transport_cleanup_pending_locked(job)
                )
            elif status == "failed":
                reason = "job is already failed"
                if self._transport_cleanup_pending_locked(job):
                    # A terminal failure may still carry a failed transport
                    # restore.  Retry only after independently proving that
                    # its capture process is no longer running.
                    retry_failed_cleanup = True
                    process = self._running_processes.get(job_id)
                    process_pid = _coerce_process_pid(job.get("process_pid"))
                    process_creation_time = _coerce_process_creation_time(job.get("process_creation_time"))
                    stopped_without_handle = bool(
                        process is None
                        and process_pid is None
                        and str(job.get("process_state") or "") == "stopped"
                    )
                    reason += "; retrying pending transport cleanup"
            else:
                reason = f"job is already {status}"

        stopped: bool | None = True if stopped_without_handle else None
        if process is not None:
            stopped = bool(_terminate_tree(process))
        elif process_pid is not None and process_creation_time is not None:
            stopped = _terminate_known_pid(process_pid, process_creation_time)
        elif retry_failed_cleanup and not stopped_without_handle:
            # Missing process identity is uncertainty, not proof that the
            # capture process has gone away.
            stopped = False
        if stopped is not None:
            if stopped and retry_failed_cleanup:
                # Validate native cleanup while the attempt start marker is
                # still present.  Only then clear the persisted process proof.
                self._cleanup_job_transport(job_id)
                with self._lock:
                    marked = self._mark_process_stopped_locked(
                        job_id,
                        process=process,
                        finalize_cancel=False,
                    )
                if not marked:
                    stopped = False
            else:
                if stopped:
                    self._cleanup_job_transport(job_id)
                with self._lock:
                    if stopped:
                        self._mark_process_stopped_locked(
                            job_id,
                            process=process,
                        )
                    elif not retry_failed_cleanup:
                        self._mark_process_unverified_locked(
                            job_id,
                            "cancelled capture process stop could not be verified; worker takeover is blocked",
                        )
            if not stopped and retry_failed_cleanup:
                with self._lock:
                    self._mark_process_unverified_locked(
                        job_id,
                        "failed job capture process stop could not be verified; worker takeover is blocked",
                    )
        active_worker_id = None
        with self._lock:
            active_worker_id = self._active_worker_id
        if active_worker_id:
            self._start_worker(active_worker_id)
        return {
            "cancelled": cancelled,
            "reason": reason,
            "job": self.public_job(job_id),
        }

    def public_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            payload = {k: v for k, v in job.items() if k != "result"}
        run_dir = str(payload.get("run_dir") or "").strip()
        partial_evidence_available = False
        if run_dir:
            run_paths = [
                Path(str(value))
                for value in payload.get("attempt_dirs") or []
                if str(value).strip()
            ]
            run_paths += [Path(str(payload.get("capture_run_dir") or "")), Path(run_dir)]
            for run_path in run_paths:
                if not run_path.is_absolute():
                    run_path = BASE_DIR / run_path
                capture = run_path / "safe-facebook-http-capture.jsonl"
                try:
                    if capture.exists() and capture.stat().st_size > 0:
                        partial_evidence_available = True
                        break
                except OSError:
                    continue
        payload["partial_evidence_available"] = partial_evidence_available
        return payload

    def latest_job(self) -> dict[str, Any]:
        with self._lock:
            if not self._jobs:
                raise KeyError("latest")
            job_id = max(
                self._jobs,
                key=lambda value: str(self._jobs[value].get("created_at") or ""),
            )
        return self.public_job(job_id)

    def result(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job["status"] != "completed":
                raise ApiError(f"job is {job['status']}")
            if job.get("result") is not None:
                return job["result"]
            run_dir = str(job.get("run_dir") or "").strip()
            request = dict(job.get("request") or {})
        if not run_dir:
            raise ApiError("completed job is missing persisted run_dir")
        run_path = Path(run_dir)
        if not run_path.is_absolute():
            run_path = BASE_DIR / run_path
        if not run_path.exists():
            raise ApiError(f"completed job run directory is missing: {run_path}")
        attempt_dirs = _job_evidence_dirs(job)
        result = build_api_result(run_path, request, evidence_dirs=attempt_dirs)
        for attempt_dir in attempt_dirs:
            candidate = attempt_dir / "capture.log"
            if candidate.is_file():
                result["job_log"] = str(candidate)
                break
        with self._lock:
            current = self._jobs.get(job_id)
            if current and current.get("status") == "completed":
                current["result"] = result
        return result

    def artifact(self, job_id: str, artifact_name: str) -> Path:
        relative = ARTIFACT_PATHS.get(artifact_name)
        if relative is None:
            raise ApiError("unknown artifact")
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            run_dir = str(job.get("run_dir") or "").strip()
            request = dict(job.get("request") or {})
        group = ARTIFACT_GROUP_BY_NAME.get(artifact_name)
        if group and group not in set(request.get("save_artifacts", DEFAULT_SAVE_ARTIFACTS)):
            raise ApiError("artifact was not selected for saving")
        if not run_dir:
            raise ApiError("job has no artifact directory yet")
        run_path = Path(run_dir)
        if not run_path.is_absolute():
            run_path = BASE_DIR / run_path
        return _checked_artifact_path(run_path, relative)

    def _checkpoint_job_locked(self, job: dict[str, Any], *, worker_id: str | None = None, serial: str | None = None) -> dict[str, Any] | None:
        root_text = str(job.get("run_dir") or "").strip()
        if not root_text:
            return None
        root = Path(root_text)
        if not root.is_absolute():
            root = BASE_DIR / root
        capture_text = str(job.get("capture_run_dir") or "").strip()
        capture_dir = Path(capture_text) if capture_text else root
        if not capture_dir.is_absolute():
            capture_dir = BASE_DIR / capture_dir
        attempt_texts = [str(value) for value in job.get("attempt_dirs") or [] if str(value).strip()]
        attempt_dirs = []
        for value in attempt_texts:
            path = Path(value)
            attempt_dirs.append(path if path.is_absolute() else BASE_DIR / path)
        if capture_dir not in attempt_dirs:
            attempt_dirs.append(capture_dir)
        preserved_rows = _persist_checkpoint_users(root, attempt_dirs)
        checkpoint = build_capture_checkpoint(capture_dir, worker_id=worker_id, serial=serial)
        checkpoint.update(
            {
                "attempt": int(job.get("attempts") or 0),
                "attempt_count": len(attempt_dirs),
                "preserved_user_count": len(preserved_rows),
                "dedupe_key": "user_id+source_post_id+reaction_id",
                "checkpoint_users_path": str(root / "checkpoint" / "facebook-reactor-users.csv")
                if preserved_rows
                else None,
            }
        )
        job["checkpoint"] = checkpoint
        return checkpoint

    def _worker_failure_confirmed(self, worker: WorkerConfig, error: Exception) -> bool:
        del error
        for attempt in range(3):
            snapshot = adb_snapshot(worker.serial, timeout_seconds=1.0)
            if snapshot.get("state") == "device" and bool(snapshot.get("boot_completed")):
                return False
            if attempt < 2:
                time.sleep(0.25)
        return True

    def _activate_next_worker_locked(self, failed_worker_id: str) -> WorkerConfig | None:
        if self._capture_lease_uncertain:
            self._active_worker_id = None
            self._wake_event.set()
            return None
        try:
            start_index = self._worker_order.index(failed_worker_id) + 1
        except ValueError:
            start_index = 0
        for worker_id in self._worker_order[start_index:]:
            worker = self.workers.get(worker_id)
            if (
                not worker
                or not worker.enabled
                or worker_id in self._failed_worker_ids
                or worker_id in self._orphaned_worker_ids
            ):
                continue
            self._active_worker_id = worker_id
            self._started_worker_ids = {worker_id}
            self._last_failover = {
                "from_worker_id": failed_worker_id,
                "to_worker_id": worker_id,
                "at": utc_now(),
            }
            self._wake_event.set()
            return worker
        self._active_worker_id = None
        self._wake_event.set()
        return None

    def _recover_after_worker_failure(self, job_id: str, worker: WorkerConfig, error: Exception) -> bool | None:
        if not self._worker_failure_confirmed(worker, error):
            return None
        with self._lock:
            self._failed_worker_ids.add(worker.worker_id)
            runtime = self._worker_runtime.get(worker.worker_id)
            if runtime is not None:
                runtime["last_failure_at"] = utc_now()
                runtime["last_failure"] = str(error)
            # Do not let a confirmed device failure receive another job while
            # its capture process is being checked and stopped.
            self._active_worker_id = None

        process_stopped = self._cleanup_job_process(job_id)
        with self._lock:
            job = self._jobs.get(job_id)
            cancellation_pending = bool(job and job.get("status") in {"cancelling", "cancelled"})
            if cancellation_pending:
                # A cancellation is terminal work, never a failover retry.
                # Keep standby dispatch blocked until the local runner's final
                # callback releases the lease when stop proof is deferred.
                if not process_stopped or self._capture_lease_uncertain:
                    return None
                self._activate_next_worker_locked(worker.worker_id)
                return None
            if not job:
                return None
            if not process_stopped:
                job["last_worker_id"] = worker.worker_id
                job["last_serial"] = worker.serial
                job["failover_count"] = int(job.get("failover_count") or 0) + 1
                job["worker_id"] = None
                job["serial"] = None
                job["status"] = "failed"
                job["finished_at"] = utc_now()
                job["recovery_state"] = "checkpointed-process-stop-unverified"
                job["error"] = (
                    f"worker {worker.worker_id} failed, but capture process stop could not be verified; "
                    f"worker takeover is blocked: {error}"
                )
                self._persist_job(job)
                return False
            self._checkpoint_job_locked(job, worker_id=worker.worker_id, serial=worker.serial)
            standby = self._activate_next_worker_locked(worker.worker_id)
            job["last_worker_id"] = worker.worker_id
            job["last_serial"] = worker.serial
            job["failover_count"] = int(job.get("failover_count") or 0) + 1
            job["worker_id"] = None
            job["serial"] = None
            if standby is None:
                job["status"] = "failed"
                job["finished_at"] = utc_now()
                job["recovery_state"] = "checkpointed-no-standby"
                job["error"] = f"worker {worker.worker_id} failed and no standby worker is available: {error}"
                self._persist_job(job)
                return False
            job["status"] = "queued"
            job["recovery_state"] = "checkpointed-queued-for-standby"
            job["error"] = f"worker {worker.worker_id} failed; queued for {standby.worker_id} from persisted checkpoint: {error}"
            self._persist_job(job)
            return True

    def _assign_job_to_worker(self, job_id: str, worker: WorkerConfig) -> bool:
        with self._lock:
            current = self.workers.get(worker.worker_id)
            job = self._jobs.get(job_id)
            runtime = self._worker_runtime.get(worker.worker_id)
            if not current or not current.enabled or not job or not runtime:
                return False
            if worker.worker_id != self._active_worker_id or self._active_job_id is not None:
                return False
            if (
                self._capture_lease_uncertain
                or worker.worker_id in self._failed_worker_ids
                or worker.worker_id in self._orphaned_worker_ids
            ):
                return False
            if job.get("status") != "queued" or float(runtime.get("cooldown_until") or 0) > time.time():
                return False
            run_dir = Path(str(job.get("run_dir") or _api_run_dir(job_id)))
            if not run_dir.is_absolute():
                run_dir = BASE_DIR / run_dir
            attempt = int(job.get("attempts") or 0) + 1
            capture_run_dir = run_dir / f"attempt-{attempt:03d}"
            attempt_dirs = [str(value) for value in job.get("attempt_dirs") or [] if str(value).strip()]
            if str(capture_run_dir) not in attempt_dirs:
                attempt_dirs.append(str(capture_run_dir))
            candidate = dict(job)
            candidate["status"] = "running"
            candidate["started_at"] = utc_now()
            candidate["run_dir"] = str(run_dir)
            candidate["capture_run_dir"] = str(capture_run_dir)
            candidate["attempt_dirs"] = attempt_dirs
            candidate["worker_id"] = current.worker_id
            candidate["serial"] = current.serial
            candidate["attempts"] = attempt
            # A queued recovery/failover error describes the previous attempt,
            # not the newly assigned one. Clear it only when this attempt
            # actually acquires the worker so queued audit context remains
            # visible until execution resumes.
            candidate["error"] = None
            candidate["recovery_state"] = "running"
            candidate["process_pid"] = None
            candidate["process_creation_time"] = None
            candidate["process_started_at"] = None
            candidate["process_state"] = "not-started"
            candidate["transport_cleanup_state"] = None
            candidate["transport_cleanup_error"] = None
            self._persist_job(candidate)
            job.update(candidate)
            runtime["current_job_id"] = job_id
            self._active_job_id = job_id
            return True

    def set_worker_enabled(self, worker_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            current = self.workers.get(worker_id)
            if not current:
                raise KeyError(worker_id)
            if not enabled and worker_id == self._active_worker_id and self._active_job_id is not None:
                raise ApiError("cannot disable the active worker while a job is running")
            updated = replace(current, enabled=enabled)
            candidate = [updated if worker.worker_id == worker_id else worker for worker in self.workers.values()]
            validate_worker_config(candidate)
            # Persist the validated candidate before changing live dispatch state.
            # A failed atomic write must leave the running configuration intact.
            write_worker_config(self.worker_config_path, candidate)
            self.workers = {worker.worker_id: worker for worker in candidate}
            if enabled:
                self._failed_worker_ids.discard(worker_id)
                if self._active_worker_id is None:
                    self._active_worker_id = next(
                        (
                            candidate_worker.worker_id
                            for candidate_worker in candidate
                            if candidate_worker.enabled and candidate_worker.worker_id not in self._orphaned_worker_ids
                        ),
                        None,
                    )
                    if self._active_worker_id:
                        self._started_worker_ids = {self._active_worker_id}
            elif worker_id == self._active_worker_id:
                self._active_worker_id = next(
                    (
                        candidate_worker.worker_id
                        for candidate_worker in candidate
                        if candidate_worker.enabled and candidate_worker.worker_id != worker_id
                    ),
                    None,
                )
                if self._active_worker_id:
                    self._started_worker_ids = {self._active_worker_id}
            self._wake_event.set()
            active_worker_id = self._active_worker_id
        if active_worker_id:
            self._start_worker(active_worker_id)
        return self.worker_health(worker_id)

    def reload_workers(self) -> dict[str, Any]:
        start_worker_ids: list[str] = []
        with self._lock:
            # Serialize read/validate/publish with enable/disable persistence.
            # Otherwise a reload snapshot can overwrite a setter's newer state.
            configured = load_worker_config(self.worker_config_path, fallback_serial=self.serial)
            configured_by_id = {worker.worker_id: worker for worker in configured}
            current_ids = list(self.workers)
            configured_ids = [worker.worker_id for worker in configured]
            removed = [worker_id for worker_id in current_ids if worker_id not in configured_by_id]
            if removed:
                raise ApiError(f"worker reload cannot remove loaded workers: {', '.join(removed)}")
            if configured_ids[: len(current_ids)] != current_ids:
                raise ApiError("worker reload cannot reorder loaded workers; new workers must be appended")

            updated_ids: list[str] = []
            for worker_id in current_ids:
                current = self.workers[worker_id]
                replacement = configured_by_id[worker_id]
                current_identity = (
                    current.serial,
                    current.avd_name,
                    current.capture_port,
                    current.native_port,
                )
                replacement_identity = (
                    replacement.serial,
                    replacement.avd_name,
                    replacement.capture_port,
                    replacement.native_port,
                )
                if current_identity != replacement_identity:
                    raise ApiError(f"worker reload cannot change immutable identity for {worker_id}")
                if (
                    not replacement.enabled
                    and worker_id == self._active_worker_id
                    and self._active_job_id is not None
                ):
                    raise ApiError("cannot disable the active worker while a job is running")
                if replacement != current:
                    updated_ids.append(worker_id)
                    if replacement.enabled and not current.enabled:
                        start_worker_ids.append(worker_id)

            added = [worker for worker in configured if worker.worker_id not in self.workers]
            enabled_additions = [worker.worker_id for worker in added if worker.enabled]
            if enabled_additions:
                raise ApiError(
                    "new workers must be loaded disabled before explicit enable: "
                    + ", ".join(enabled_additions)
                )

            self.workers = dict(configured_by_id)
            self._worker_order = configured_ids
            for worker in added:
                self._worker_runtime[worker.worker_id] = {
                    "current_job_id": None,
                    "cooldown_until": 0.0,
                    "last_failure_at": None,
                    "last_failure": None,
                }
            self.serial = configured[0].serial
            active_worker = self.workers.get(self._active_worker_id) if self._active_worker_id else None
            if active_worker is None or not active_worker.enabled:
                self._active_worker_id = next(
                    (
                        worker.worker_id
                        for worker in configured
                        if (
                            worker.enabled
                            and worker.worker_id not in self._failed_worker_ids
                            and worker.worker_id not in self._orphaned_worker_ids
                        )
                    ),
                    None,
                )
                if self._active_worker_id:
                    self._started_worker_ids = {self._active_worker_id}
            # Configuration may already be enabled after a prior native start
            # failure. Retry only a confirmed missing scheduler, never replace a
            # registered thread or bypass lease/device blockers.
            active_id = self._active_worker_id
            if (
                self._worker is None
                and active_id is not None
                and self.workers[active_id].enabled
                and not self._capture_lease_uncertain
                and active_id not in self._failed_worker_ids
                and active_id not in self._orphaned_worker_ids
                and active_id not in start_worker_ids
            ):
                start_worker_ids.append(active_id)
            self._wake_event.set()

        for worker_id in start_worker_ids:
            self._start_worker(worker_id)
        return {
            "added": [worker.worker_id for worker in added],
            "updated": updated_ids,
            "worker_ids": [worker.worker_id for worker in configured],
        }

    def worker_health(self, worker_id: str) -> dict[str, Any]:
        with self._lock:
            worker = self.workers.get(worker_id)
            if not worker:
                raise KeyError(worker_id)
            runtime = dict(self._worker_runtime[worker_id])
            active_worker_id = self._active_worker_id
            capture_lease_uncertain = self._capture_lease_uncertain
        failed = worker_id in self._failed_worker_ids or worker_id in self._orphaned_worker_ids
        # Health is polled by the local UI; a wedged shell must not stall it.
        snapshot = adb_snapshot(worker.serial, timeout_seconds=1.0)
        now = time.time()
        cooldown_until = float(runtime.get("cooldown_until") or 0)
        if not worker.enabled:
            state = "disabled"
        elif failed:
            state = "failed"
        elif capture_lease_uncertain:
            state = "blocked"
        elif worker_id != active_worker_id:
            state = "standby"
        elif runtime.get("current_job_id"):
            state = "busy"
        elif cooldown_until > now:
            state = "cooling"
        elif snapshot.get("state") != "device" or not snapshot.get("boot_completed"):
            state = "offline"
        else:
            state = "idle"
        return {
            "worker_id": worker.worker_id,
            "serial": worker.serial,
            "avd_name": worker.avd_name,
            "capture_port": worker.capture_port,
            "native_port": worker.native_port,
            "enabled": worker.enabled,
            "role": "active" if worker_id == active_worker_id else "standby",
            "active": worker_id == active_worker_id,
            "state": state,
            "current_job_id": runtime.get("current_job_id"),
            "last_failure_at": runtime.get("last_failure_at"),
            "cooldown_until": (
                datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat().replace("+00:00", "Z")
                if cooldown_until > now
                else None
            ),
            "android": snapshot,
        }

    def health(self) -> dict[str, Any]:
        with self._lock:
            queued = sum(1 for job in self._jobs.values() if job["status"] == "queued")
            running = sum(1 for job in self._jobs.values() if job["status"] == "running")
            cancelling = sum(1 for job in self._jobs.values() if job["status"] == "cancelling")
            worker_ids = list(self.workers)
            active_worker_id = self._active_worker_id
            capture_lease_uncertain = self._capture_lease_uncertain
            capture_lease_job_id = self._capture_lease_job_id
            capture_lease_reason = self._capture_lease_reason
            last_failover = dict(self._last_failover) if self._last_failover else None
            scheduler = {"state": "not-started", "alive": False}
            if self._worker is not None:
                try:
                    alive = self._worker.is_alive()
                    scheduler = {
                        "state": "running" if alive else ("stopped" if self._worker.ident is not None else "not-started"),
                        "alive": bool(alive),
                    }
                except (RuntimeError, OSError):
                    # Observation is not termination proof or recovery authority.
                    scheduler = {"state": "unknown", "alive": None}
        token = _health_adb_state_scope.set({"queried": False, "states": None})
        try:
            worker_health = [self.worker_health(worker_id) for worker_id in worker_ids]
        finally:
            _health_adb_state_scope.reset(token)
        active = next((item for item in worker_health if item["worker_id"] == active_worker_id), None)
        return {
            "service": "ready",
            "mode": "primary-standby-serial",
            "scheduler": scheduler,
            "worker": "blocked" if capture_lease_uncertain else ("busy" if running or cancelling else "idle"),
            "cancelling_jobs": cancelling,
            "queued_jobs": queued,
            "android": active["android"] if active else {
                "state": "capture-lease-uncertain",
                "boot_completed": False,
                "proxy": "",
            },
            "serial": active["serial"] if active else None,
            "active_worker_id": active_worker_id,
            "last_failover": last_failover,
            "capture_lease": {
                "state": "uncertain" if capture_lease_uncertain else "available",
                "job_id": capture_lease_job_id,
                "reason": capture_lease_reason,
            },
            "workers": worker_health,
        }

    def progress(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            status = str(job.get("status") or "queued")
            evidence_dirs = _job_evidence_dirs(job)
            run_dir = str(job.get("capture_run_dir") or job.get("run_dir") or "").strip()
            request = dict(job.get("request") or {})
            completion_scope = job.get("completion_scope")
            serial = str(job.get("serial") or self.serial)
        run_path = evidence_dirs[0] if evidence_dirs else _api_run_dir(job_id)
        return build_progress_snapshot(
            run_path,
            status,
            adb_snapshot(serial),
            request,
            evidence_dirs=evidence_dirs or None,
            completion_scope=completion_scope,
        )

    def _worker_loop(self) -> None:
        while True:
            with self._lock:
                worker = self.workers.get(self._active_worker_id) if self._active_worker_id else None
                runtime = self._worker_runtime.get(worker.worker_id) if worker else None
                available = bool(
                    worker
                    and runtime
                    and worker.enabled
                    and worker.worker_id not in self._failed_worker_ids
                    and worker.worker_id not in self._orphaned_worker_ids
                    and not self._capture_lease_uncertain
                    and self._active_job_id is None
                    and float(runtime.get("cooldown_until") or 0) <= time.time()
                )
            if not available:
                self._wake_event.wait(0.5)
                self._wake_event.clear()
                continue
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            requeue_after_failover = False
            assignment_committed = False
            recovery_storage_failed = False
            completion_storage_failed = False
            capture_returned = False
            try:
                try:
                    assignment_committed = self._assign_job_to_worker(job_id, worker)
                except OSError:
                    # No capture process has started. Keep the queued job and
                    # retry dispatch only after a bounded pause; disk failure
                    # is not device failure or authorization to run cleanup.
                    with self._lock:
                        if (self._jobs.get(job_id) or {}).get("status") == "queued":
                            self._queue.put(job_id)
                    self._wake_event.wait(0.5)
                    self._wake_event.clear()
                    continue
                if not assignment_committed:
                    with self._lock:
                        if (self._jobs.get(job_id) or {}).get("status") == "queued":
                            self._queue.put(job_id)
                    time.sleep(0.1)
                    continue
                with self._lock:
                    job = self._jobs[job_id]
                    request = dict(job["request"])
                    run_dir = Path(str(job["capture_run_dir"] or job["run_dir"]))
                result = run_capture_job(
                    request,
                    job_id,
                    worker.serial,
                    process_callback=lambda process: self._set_running_process(job_id, process),
                    capture_process_callback=lambda: self._mark_capture_runner_started(job_id),
                    run_dir=run_dir,
                    worker=worker,
                )
                capture_returned = True
                with self._lock:
                    job = self._jobs[job_id]
                    if job.get("status") in {"cancelling", "cancelled"}:
                        continue
                    root = Path(str(job.get("run_dir") or run_dir))
                    if not root.is_absolute():
                        root = BASE_DIR / root
                    attempts = []
                    for value in job.get("attempt_dirs") or []:
                        path = Path(str(value))
                        attempts.append(path if path.is_absolute() else BASE_DIR / path)
                    materialize_capture_attempts(root, attempts)
                    result = build_api_result(root, request, evidence_dirs=attempts)
                    result["job_log"] = str(result.get("job_log") or (run_dir / "capture.log"))
                    candidate = dict(job)
                    candidate["status"] = "completed"
                    candidate["result"] = result
                    candidate["completion_scope"] = result.get("completion_scope")
                    candidate["run_dir"] = str(root)
                    candidate["capture_run_dir"] = str(run_dir)
                    candidate["finished_at"] = utc_now()
                    candidate["recovery_state"] = "completed-with-deduplicated-attempts"
                    candidate["transport_cleanup_state"] = TRANSPORT_CLEANUP_RUNNER_OWNED
                    candidate["transport_cleanup_error"] = None
                    self._checkpoint_job_locked(candidate, worker_id=worker.worker_id, serial=worker.serial)
                    self._persist_job(candidate)
                    job.update(candidate)
            except Exception as exc:  # worker must remain available for the next job
                if capture_returned and isinstance(exc, OSError):
                    # Local result output/commit failure after capture returned
                    # is not a device outage or permission to recapture.
                    completion_storage_failed = True
                    with self._lock:
                        job = self._jobs.get(job_id)
                        if job and job.get("status") not in {"cancelling", "cancelled"}:
                            failed = dict(job)
                            failed.update(
                                status="failed", result=None, completion_scope=None,
                                finished_at=utc_now(), recovery_state="failed-result-persistence",
                                error="capture result persistence failed due to a local I/O error; dispatch is blocked",
                            )
                            try:
                                self._persist_job(failed)
                            except OSError:
                                # Keep the uncommitted completion private. The
                                # global block below exposes remaining uncertainty.
                                pass
                            else:
                                job.update(failed)
                else:
                    try:
                        recovery = self._recover_after_worker_failure(job_id, worker, exc)
                        requeue_after_failover = recovery is True
                        if recovery is None:
                            with self._lock:
                                cancellation_pending = (self._jobs.get(job_id) or {}).get("status") in {"cancelling", "cancelled"}
                            if not cancellation_pending:
                                process_stopped = self._cleanup_job_process(job_id)
                                with self._lock:
                                    job = self._jobs.get(job_id)
                                    if job and job.get("status") not in {"cancelling", "cancelled"}:
                                        self._checkpoint_job_locked(job, worker_id=worker.worker_id, serial=worker.serial)
                                        job["status"] = "failed"
                                        job["error"] = str(exc)
                                        job["finished_at"] = utc_now()
                                        if process_stopped:
                                            job["recovery_state"] = "failed-after-persisted-checkpoint" if job.get("checkpoint") else "failed"
                                        else:
                                            job["recovery_state"] = "failed-process-stop-unverified"
                                            job["error"] += "; capture process stop could not be verified; worker takeover is blocked"
                                            self._active_worker_id = None
                                        self._persist_job(job)
                                    if "rate-limit" in str(exc).lower():
                                        self._worker_runtime[worker.worker_id]["cooldown_until"] = time.time() + worker.cooldown_seconds
                    except OSError:
                        # Recovery/checkpoint persistence itself can fail. This is
                        # not permission to resume capture or lose the scheduler.
                        recovery_storage_failed = True
                        requeue_after_failover = False
            finally:
                if assignment_committed:
                    try:
                        self._set_running_process(job_id, None, final=True)
                    except OSError:
                        # Finalization metadata is not durably confirmed. Keep
                        # dispatch blocked without losing queue accounting or
                        # misclassifying storage failure as device failure.
                        with self._lock:
                            self._block_capture_lease_locked(
                                job_id,
                                "capture finalization persistence is unverified; worker takeover is blocked",
                                persistence=True,
                            )
                            self._orphaned_worker_ids.add(worker.worker_id)
                with self._lock:
                    if recovery_storage_failed or completion_storage_failed:
                        # A stopped callback cannot prove result/recovery output
                        # durability. Retain the block after it has run.
                        reason = (
                            "capture result persistence is unverified; worker takeover is blocked"
                            if completion_storage_failed
                            else "capture recovery persistence is unverified; worker takeover is blocked"
                        )
                        self._block_capture_lease_locked(job_id, reason, persistence=True)
                        self._orphaned_worker_ids.add(worker.worker_id)
                    batch_id = str((self._jobs.get(job_id) or {}).get("batch_id") or "")
                    self._worker_runtime[worker.worker_id]["current_job_id"] = None
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                if batch_id and assignment_committed:
                    try:
                        self._refresh_batch_outputs(batch_id)
                    except (OSError, ApiError, KeyError):
                        pass
                self._queue.task_done()
                if requeue_after_failover:
                    self._queue.put(job_id)
                    self._wake_event.set()


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "FacebookCaptureAPI/0.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def _trusted_request(self) -> bool:
        error = request_error(self)
        if error:
            self._json(error[0], {"error": error[1]})
            return False
        return True

    @property
    def manager(self) -> JobManager:
        return self.server.manager  # type: ignore[attr-defined]

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._json(404, {"error": "asset not found"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _artifact(self, path: Path, download: bool) -> None:
        content_type = "text/csv; charset=utf-8" if path.suffix.lower() == ".csv" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        disposition = "attachment" if download or path.suffix.lower() not in INLINE_ARTIFACT_SUFFIXES else "inline"
        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError:
            self._json(404, {"error": "artifact not found"})
            return
        with handle:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'{disposition}; filename="{path.name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            while chunk := handle.read(1024 * 1024):
                self.wfile.write(chunk)

    def _segments(self) -> list[str]:
        return [part for part in urlparse(self.path).path.split("/") if part]

    def do_GET(self) -> None:
        if not self._trusted_request():
            return
        segments = self._segments()
        if segments == ['v1', 'setup']:
            try:
                from facebook_local import doctor
                self._json(200, doctor())
            except (OSError, ValueError, RuntimeError) as exc:
                self._json(400, {'error': str(exc)})
            return
        if segments == ['v1', 'capabilities']:
            self._json(200, {'platform': sys.platform, 'capture_pcap_supported': sys.platform in {'win32', 'darwin'}})
            return
        if segments == ["guide"]:
            body = (Path(__file__).resolve().parent / "README.md").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        static = resolve_static_asset(self.path)
        if static is not None:
            self._static(static)
            return
        if segments == ["health"]:
            self._json(200, self.manager.health())
            return
        if segments == ["v1", "jobs", "latest"]:
            try:
                self._json(200, self.manager.latest_job())
            except KeyError:
                self._json(404, {"error": "no jobs found"})
            return
        if segments == ["v1", "batches", "latest"]:
            try:
                self._json(200, self.manager.latest_batch())
            except KeyError:
                self._json(404, {"error": "no batches found"})
            return
        if len(segments) == 5 and segments[:2] == ["v1", "batches"] and segments[3] == "artifacts":
            try:
                artifact = self.manager.batch_artifact(segments[2], segments[4])
                download = urlparse(self.path).query == "download=1"
                self._artifact(artifact, download)
            except KeyError:
                self._json(404, {"error": "batch not found"})
            except ApiError as exc:
                self._json(404, {"error": str(exc)})
            return
        if len(segments) in {3, 4} and segments[:2] == ["v1", "batches"]:
            try:
                if len(segments) == 4 and segments[3] == "progress":
                    self._json(200, self.manager.batch_progress(segments[2]))
                elif len(segments) == 3:
                    self._json(200, self.manager.batch_progress(segments[2]))
                else:
                    self._json(404, {"error": "not found"})
            except KeyError:
                self._json(404, {"error": "batch not found"})
            return
        if len(segments) == 3 and segments[:2] == ["v1", "avatars"]:
            try:
                avatar_url = resolve_avatar_url(segments[2])
                self.send_response(302)
                self.send_header("Location", avatar_url)
                self.send_header("Cache-Control", "private, max-age=300")
                self.end_headers()
            except ApiError as exc:
                self._json(503, {"error": str(exc)})
            return
        if len(segments) == 5 and segments[:2] == ["v1", "jobs"] and segments[3] == "artifacts":
            try:
                artifact = self.manager.artifact(segments[2], segments[4])
                download = urlparse(self.path).query == "download=1"
                self._artifact(artifact, download)
            except KeyError:
                self._json(404, {"error": "job not found"})
            except ApiError as exc:
                self._json(404, {"error": str(exc)})
            return
        if len(segments) in {3, 4} and segments[:2] == ["v1", "jobs"]:
            job_id = segments[2]
            try:
                if len(segments) == 4 and segments[3] == "result":
                    self._json(200, self.manager.result(job_id))
                elif len(segments) == 4 and segments[3] == "progress":
                    self._json(200, self.manager.progress(job_id))
                elif len(segments) == 3:
                    self._json(200, self.manager.public_job(job_id))
                else:
                    self._json(404, {"error": "not found"})
            except KeyError:
                self._json(404, {"error": "job not found"})
            except ApiError as exc:
                self._json(409, {"error": str(exc)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._trusted_request():
            return
        try:
            length = body_length(self.headers)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        segments = self._segments()
        if segments == ["v1", "workers", "reload"]:
            try:
                result = self.manager.reload_workers()
            except ApiError as exc:
                self._json(409, {"error": str(exc)})
            except SchedulerStartError:
                self._json(503, {"error": "worker reload could not start the scheduler; inspect state before retrying"})
            except OSError:
                # Do not expose paths or local exception details to callers.
                self._json(503, {"error": "worker reload failed due to a local I/O error"})
            else:
                self._json(200, result)
            return
        if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] in {"enable", "disable"}:
            try:
                result = self.manager.set_worker_enabled(segments[2], segments[3] == "enable")
            except KeyError:
                self._json(404, {"error": "worker not found"})
            except ApiError as exc:
                self._json(409, {"error": str(exc)})
            except SchedulerStartError:
                self._json(503, {"error": "worker update could not start the scheduler; inspect state before retrying"})
            except OSError:
                self._json(503, {"error": "worker update failed due to a local I/O error"})
            else:
                self._json(200, result)
            return
        if len(segments) == 4 and segments[:2] == ["v1", "jobs"] and segments[3] == "reconcile-result-persistence":
            # A review acknowledgment is deliberately explicit, not inferred
            # from a GET, Worker enable, repeated cleanup or permissive coercion.
            try:
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if length <= 0:
                    raise ApiError("explicit JSON acknowledgment is required")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ApiError("incomplete acknowledgment body")
                if content_type != "application/json":
                    raise ApiError("explicit JSON acknowledgment is required")
                def unique_object(pairs):
                    value = {}
                    for key, item in pairs:
                        if key in value:
                            raise ApiError("duplicate acknowledgment keys are not allowed")
                        value[key] = item
                    return value
                payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
                if not isinstance(payload, dict) or set(payload) != {"acknowledge"} or payload["acknowledge"] is not True:
                    raise ApiError("exactly one acknowledge:true field is required")
            except (ApiError, json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                self._json(400, {"error": "invalid explicit acknowledgment body"})
            except TimeoutError:
                self._json(408, {"error": "acknowledgment body timed out"})
            except OSError:
                self._json(503, {"error": "acknowledgment request failed due to a local I/O error"})
            else:
                try:
                    result = self.manager.reconcile_result_persistence(segments[2], acknowledge=True)
                except KeyError:
                    self._json(404, {"error": "job not found"})
                except ApiError as exc:
                    self._json(409, {"error": str(exc)})
                except SchedulerStartError:
                    self._json(503, {"error": "result reconciliation could not start the scheduler; inspect state before retrying"})
                except OSError:
                    self._json(503, {"error": "result reconciliation failed due to a local I/O error; inspect state before retrying"})
                else:
                    self._json(200, result)
            return
        if len(segments) == 4 and segments[:2] == ["v1", "batches"] and segments[3] == "cancel":
            try:
                self._json(200, self.manager.cancel_batch(segments[2]))
            except KeyError:
                self._json(404, {"error": "batch not found"})
            return
        if len(segments) == 4 and segments[:2] == ["v1", "jobs"] and segments[3] == "cancel":
            try:
                self._json(200, self.manager.cancel(segments[2]))
            except KeyError:
                self._json(404, {"error": "job not found"})
            return
        if segments not in (["v1", "jobs"], ["v1", "batches"]):
            self._json(404, {"error": "not found"})
            return
        try:
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ApiError("invalid request body size")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ApiError("incomplete request body")
            payload = json.loads(raw.decode("utf-8"))
        except (ApiError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except TimeoutError:
            self._json(408, {"error": "request body timed out"})
        except OSError:
            self._json(503, {"error": "capture submission failed due to a local I/O error; inspect state before retrying"})
        else:
            try:
                result = self.manager.submit_batch(payload) if segments == ["v1", "batches"] else self.manager.submit(payload)
            except (ApiError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except OSError:
                # Storage timeout is not a request-body timeout. No automatic
                # POST retry: a client must inspect current state first.
                self._json(503, {"error": "capture submission failed due to a local I/O error; inspect state before retrying"})
            else:
                self._json(202, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[api] {self.address_string()} {fmt % args}")


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], manager: JobManager):
        super().__init__(address, ApiHandler)
        self.manager = manager


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Facebook Android capture API")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    args = parser.parse_args()
    if (BASE_DIR / 'facebook.local.json').is_file():
        from facebook_local import configuration, environment
        environment(configuration(BASE_DIR), BASE_DIR)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("refusing non-loopback bind; expose this service only through an authenticated private tunnel")
    manager = JobManager(args.serial)
    server = CaptureServer((args.host, args.port), manager)
    print(f"Facebook Capture API ready: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
