#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "facebook_workers.macos.json"
EXAMPLE_CONFIG = BASE_DIR / "facebook_workers.macos.example.json"
FACEBOOK_PACKAGE = "com.facebook.katana"
PROXY_PORT = 7897


class MacConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class MacWorker:
    worker_id: str
    serial: str
    avd_name: str
    capture_port: int
    native_port: int
    enabled: bool


def validate_workers(workers: list[MacWorker]) -> list[MacWorker]:
    if not workers:
        raise MacConfigError("macOS worker configuration must not be empty")
    for field in ("worker_id", "serial", "avd_name"):
        values = [getattr(worker, field) for worker in workers]
        if len(values) != len(set(values)):
            raise MacConfigError(f"macOS worker {field} values must be globally unique")
    all_ports = [
        port
        for worker in workers
        for port in (worker.capture_port, worker.native_port)
    ]
    if len(all_ports) != len(set(all_ports)):
        raise MacConfigError("macOS worker capture/native ports must be globally unique")
    for worker in workers:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", worker.worker_id):
            raise MacConfigError(f"invalid worker_id: {worker.worker_id!r}")
        match = re.fullmatch(r"emulator-(\d+)", worker.serial)
        if not match:
            raise MacConfigError(f"invalid emulator serial: {worker.serial!r}")
        emulator_port = int(match.group(1))
        if emulator_port < 5554 or emulator_port > 5682 or emulator_port % 2:
            raise MacConfigError(f"invalid emulator console port: {emulator_port}")
        if not worker.avd_name.strip():
            raise MacConfigError(f"worker {worker.worker_id} requires avd_name")
        for name, port in (
            ("capture_port", worker.capture_port),
            ("native_port", worker.native_port),
        ):
            if not 1024 <= port <= 65535:
                raise MacConfigError(f"worker {worker.worker_id} has invalid {name}: {port}")
        if worker.capture_port == worker.native_port:
            raise MacConfigError(
                f"worker {worker.worker_id} capture_port and native_port must differ"
            )
    return workers


def load_config(path: Path) -> list[MacWorker]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MacConfigError(f"could not read macOS worker configuration: {path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise MacConfigError("macOS worker configuration version must be 1")
    raw_workers = payload.get("workers")
    if not isinstance(raw_workers, list):
        raise MacConfigError("macOS worker configuration workers must be a list")
    workers: list[MacWorker] = []
    try:
        for raw in raw_workers:
            if not isinstance(raw, dict):
                raise TypeError("worker must be an object")
            enabled = raw["enabled"]
            if not isinstance(enabled, bool):
                raise TypeError("enabled must be boolean")
            workers.append(
                MacWorker(
                    worker_id=str(raw["worker_id"]).strip(),
                    serial=str(raw["serial"]).strip(),
                    avd_name=str(raw["avd_name"]).strip(),
                    capture_port=int(raw["capture_port"]),
                    native_port=int(raw["native_port"]),
                    enabled=enabled,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise MacConfigError("macOS worker configuration contains an invalid entry") from exc
    return validate_workers(workers)


def select_workers(workers: list[MacWorker], worker_id: str) -> list[MacWorker]:
    if worker_id == "all":
        selected = [worker for worker in workers if worker.enabled]
        if not selected:
            raise MacConfigError("no enabled macOS workers are configured")
        return selected
    match = next((worker for worker in workers if worker.worker_id == worker_id), None)
    if match is None:
        raise MacConfigError(f"unknown macOS worker: {worker_id}")
    if not match.enabled:
        raise MacConfigError(f"macOS worker is disabled: {worker_id}")
    return [match]


def host_abi(machine: str | None = None) -> str:
    normalized = (machine or platform.machine()).strip().lower()
    if normalized in {"arm64", "aarch64"}:
        return "arm64-v8a"
    if normalized in {"x86_64", "amd64"}:
        return "x86_64"
    raise MacConfigError(f"unsupported macOS host architecture: {normalized or 'unknown'}")


def emulator_port(worker: MacWorker) -> int:
    return int(worker.serial.rsplit("-", 1)[1])


def build_emulator_command(worker: MacWorker, emulator_path: str) -> list[str]:
    return [
        emulator_path,
        "-avd",
        worker.avd_name,
        "-port",
        str(emulator_port(worker)),
        "-no-snapshot-save",
        "-netdelay",
        "none",
        "-netspeed",
        "full",
    ]


def build_transport_commands(worker: MacWorker, adb_path: str) -> list[list[str]]:
    prefix = [adb_path, "-s", worker.serial]
    return [
        [*prefix, "reverse", f"tcp:{PROXY_PORT}", f"tcp:{PROXY_PORT}"],
        [
            *prefix,
            "reverse",
            f"tcp:{worker.capture_port}",
            f"tcp:{worker.capture_port}",
        ],
        [
            *prefix,
            "shell",
            "settings",
            "put",
            "global",
            "http_proxy",
            f"127.0.0.1:{PROXY_PORT}",
        ],
    ]


def _sdk_candidate(tool: str, environ: Mapping[str, str]) -> str | None:
    executable = "emulator" if tool == "emulator" else "adb"
    subdir = "emulator" if tool == "emulator" else "platform-tools"
    for variable in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        root = environ.get(variable)
        if root:
            candidate = Path(root).expanduser() / subdir / executable
            if candidate.is_file():
                return str(candidate)
    found = shutil.which(executable)
    return found


def discover_tools(environ: Mapping[str, str] | None = None) -> dict[str, str | None]:
    values = os.environ if environ is None else environ
    return {
        "emulator": _sdk_candidate("emulator", values),
        "adb": _sdk_candidate("adb", values),
    }


def _planned_tool(tool: str, discovered: str | None) -> str:
    if discovered:
        return discovered
    subdir = "emulator" if tool == "emulator" else "platform-tools"
    return f"$ANDROID_SDK_ROOT/{subdir}/{tool}"


def worker_plan(worker: MacWorker, emulator_path: str, adb_path: str) -> dict[str, Any]:
    return {
        **asdict(worker),
        "emulator_port": emulator_port(worker),
        "emulator_command": build_emulator_command(worker, emulator_path),
        "transport_commands": build_transport_commands(worker, adb_path),
        "facebook_package": FACEBOOK_PACKAGE,
        "deep_capture_runner": "not-implemented-for-macos",
    }


def simulate_workers(
    workers: list[MacWorker],
    *,
    emulator_path: str,
    adb_path: str,
    runner: Callable[..., Any] = subprocess.run,
    popen: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    del runner, popen
    return {
        "mode": "simulation",
        "host_system": platform.system(),
        "host_machine": platform.machine(),
        "workers": [worker_plan(worker, emulator_path, adb_path) for worker in workers],
        "executed": False,
    }


def require_macos() -> None:
    if platform.system() != "Darwin":
        raise MacConfigError("real start/transport requires macOS; use simulate on this host")


def run_checked(command: Sequence[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MacConfigError(f"command timed out after {timeout:g}s: {command[0]}") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else type(exc).__name__
        raise MacConfigError(f"command failed: {command[0]} ({detail})") from exc


def device_ready(worker: MacWorker, adb_path: str) -> bool:
    try:
        result = run_checked([adb_path, "-s", worker.serial, "get-state"], timeout=5)
    except MacConfigError:
        return False
    return result.stdout.strip() == "device"


def wait_for_boot(worker: MacWorker, adb_path: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            result = run_checked(
                [adb_path, "-s", worker.serial, "shell", "getprop", "sys.boot_completed"],
                timeout=5,
            )
            if result.stdout.strip() == "1":
                return
        except MacConfigError:
            pass
        time.sleep(2)
    raise MacConfigError(f"Android boot timed out for {worker.worker_id} ({worker.serial})")


def apply_transport(worker: MacWorker, adb_path: str) -> dict[str, Any]:
    if not device_ready(worker, adb_path):
        raise MacConfigError(f"ADB device is not ready: {worker.serial}")
    for command in build_transport_commands(worker, adb_path):
        run_checked(command)
    reverse = run_checked([adb_path, "-s", worker.serial, "reverse", "--list"]).stdout
    expected = {f"tcp:{PROXY_PORT}", f"tcp:{worker.capture_port}"}
    if not all(port in reverse for port in expected):
        raise MacConfigError(f"ADB reverse verification failed for {worker.worker_id}")
    proxy = run_checked(
        [adb_path, "-s", worker.serial, "shell", "settings", "get", "global", "http_proxy"]
    ).stdout.strip()
    if proxy != f"127.0.0.1:{PROXY_PORT}":
        raise MacConfigError(f"Android proxy verification failed for {worker.worker_id}")
    package = run_checked(
        [adb_path, "-s", worker.serial, "shell", "pm", "path", FACEBOOK_PACKAGE]
    ).stdout.strip()
    return {
        "worker_id": worker.worker_id,
        "serial": worker.serial,
        "capture_port": worker.capture_port,
        "native_port": worker.native_port,
        "proxy": proxy,
        "facebook_installed": package.startswith("package:"),
        "transport_ready": True,
    }


def start_worker(
    worker: MacWorker,
    emulator_path: str,
    adb_path: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    already_running = device_ready(worker, adb_path)
    if not already_running:
        try:
            subprocess.Popen(
                build_emulator_command(worker, emulator_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise MacConfigError(f"failed to launch emulator for {worker.worker_id}") from exc
        wait_for_boot(worker, adb_path, timeout_seconds)
    result = apply_transport(worker, adb_path)
    result["already_running"] = already_running
    return result


def doctor(simulate: bool) -> dict[str, Any]:
    tools = discover_tools()
    result: dict[str, Any] = {
        "mode": "simulation" if simulate else "real",
        "host_system": platform.system(),
        "host_machine": platform.machine(),
        "is_macos": platform.system() == "Darwin",
        "abi": None,
        "tools": tools,
        "ready_for_real_start": False,
        "executed": False,
    }
    try:
        result["abi"] = host_abi()
    except MacConfigError as exc:
        result["architecture_error"] = str(exc)
    if simulate:
        return result
    require_macos()
    missing = [name for name, path in tools.items() if not path]
    if missing:
        raise MacConfigError(f"missing Android SDK tools: {', '.join(missing)}")
    result["emulator_version"] = run_checked([str(tools["emulator"]), "-version"]).stdout.splitlines()[:2]
    result["adb_version"] = run_checked([str(tools["adb"]), "version"]).stdout.splitlines()[:2]
    result["ready_for_real_start"] = True
    result["executed"] = True
    return result


def config_path_for_cli(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else EXAMPLE_CONFIG


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated macOS Facebook emulator adapter")
    parser.add_argument("--config", help="macOS-only worker registry JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subparsers.add_parser("doctor", help="check macOS Android SDK readiness")
    doctor_parser.add_argument("--simulate", action="store_true", help="do not execute tools")
    for command in ("simulate", "start", "transport"):
        child = subparsers.add_parser(command)
        child.add_argument("--worker", default="all", help="worker id or all")
        if command == "start":
            child.add_argument("--boot-timeout", type=int, default=180)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.simulate)
        else:
            workers = select_workers(load_config(config_path_for_cli(args.config)), args.worker)
            tools = discover_tools()
            if args.command == "simulate":
                result = simulate_workers(
                    workers,
                    emulator_path=_planned_tool("emulator", None),
                    adb_path=_planned_tool("adb", None),
                )
            else:
                require_macos()
                missing = [name for name, path in tools.items() if not path]
                if missing:
                    raise MacConfigError(f"missing Android SDK tools: {', '.join(missing)}")
                if args.command == "transport":
                    result = {
                        "mode": "real",
                        "workers": [apply_transport(worker, str(tools["adb"])) for worker in workers],
                    }
                else:
                    if args.boot_timeout < 30 or args.boot_timeout > 900:
                        raise MacConfigError("boot timeout must be between 30 and 900 seconds")
                    result = {
                        "mode": "real",
                        "workers": [
                            start_worker(
                                worker,
                                str(tools["emulator"]),
                                str(tools["adb"]),
                                args.boot_timeout,
                            )
                            for worker in workers
                        ],
                    }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except MacConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
