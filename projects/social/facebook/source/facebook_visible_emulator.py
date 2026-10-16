"""Windows-only visible Android emulator entry point.

This adapter owns only emulator process start/reuse.  API startup, ADB
readiness, transport, helper setup, and capture initialization remain in the
existing PowerShell startup chain.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SERIAL_PATTERN = re.compile(r"^emulator-(?P<port>[0-9]+)$")
EMULATOR_PROCESS_NAME = "qemu-system-x86_64.exe"


@dataclass(frozen=True)
class VisibleWorker:
    worker_id: str
    serial: str
    avd_name: str
    capture_port: int
    native_port: int
    enabled: bool
    console_port: int
    sdk_root: Path
    avd_home: Path
    emulator_exe: Path
    launcher_exe: Path
    launcher_script: Path

    def public_spec(self) -> dict[str, Any]:
        command = build_emulator_command(self)
        launcher_command = build_launcher_command(self)
        return {
            "worker_id": self.worker_id,
            "serial": self.serial,
            "avd_name": self.avd_name,
            "capture_port": self.capture_port,
            "native_port": self.native_port,
            "console_port": self.console_port,
            "sdk_root": str(self.sdk_root),
            "avd_home": str(self.avd_home),
            "emulator_exe": str(self.emulator_exe),
            "emulator_command": command,
            "launcher_exe": str(self.launcher_exe),
            "launcher_script": str(self.launcher_script),
            "launcher_command": launcher_command,
            "launcher_arguments": windows_command_line(launcher_command[1:]),
            "launcher_command_line": windows_command_line(launcher_command),
        }


def _repo_root(root: str | os.PathLike[str] | None) -> Path:
    return Path(root).expanduser().resolve() if root is not None else Path(__file__).resolve().parent


def _resolve_python_executable(root: Path) -> Path:
    candidates: list[Path] = []
    configured = str(os.environ.get("FACEBOOK_PYTHON_EXE") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(root / ".venv" / "Scripts" / "python.exe")
    if sys.executable:
        candidates.append(Path(sys.executable))
    for name in ("python.exe", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    raise ValueError("Python executable not found; run setup.ps1 or configure Python on PATH")


def _console_port(serial: str) -> int:
    match = SERIAL_PATTERN.fullmatch(serial.strip())
    if not match:
        raise ValueError(f"worker serial must use emulator-<port> format: {serial}")
    port = int(match.group("port"))
    if port < 5554 or port > 5682 or port % 2:
        raise ValueError(f"worker serial console port must be even and between 5554 and 5682: {serial}")
    return port


def _avd_exists(avd_home: Path, avd_name: str) -> bool:
    return (avd_home / f"{avd_name}.ini").is_file() or (avd_home / f"{avd_name}.avd").is_dir()


def resolve_worker(root: str | os.PathLike[str] | None = None, worker_id: str = "worker-01") -> VisibleWorker:
    """Resolve one enabled worker through the project's existing config APIs."""
    if sys.platform != "win32":
        raise ValueError("facebook_visible_emulator.py is Windows-only")
    project_root = _repo_root(root)
    worker_name = str(worker_id or "").strip()
    if not worker_name:
        raise ValueError("worker_id must be non-empty")

    import facebook_capture_api
    import facebook_local

    local_config = facebook_local.configuration(project_root)
    facebook_local.environment(local_config, project_root)
    registry = project_root / "facebook_workers.json"
    if not registry.is_file():
        raise ValueError(f"Missing facebook_workers.json: {registry}")
    try:
        workers = facebook_capture_api.load_worker_config(registry)
    except facebook_capture_api.ApiError as exc:
        raise ValueError(f"Could not load facebook_workers.json: {exc}") from exc
    by_id = {worker.worker_id: worker for worker in workers}
    worker = by_id.get(worker_name)
    if worker is None:
        known = ", ".join(sorted(by_id)) or "<none>"
        raise ValueError(f"unknown worker_id {worker_name!r}; configured workers: {known}")
    if not worker.enabled:
        raise ValueError(f"{worker_name} is disabled in facebook_workers.json")

    sdk_value = str(os.environ.get("FACEBOOK_SDK_ROOT") or local_config.get("sdk_root") or "").strip()
    avd_value = str(os.environ.get("FACEBOOK_AVD_HOME") or local_config.get("avd_home") or "").strip()
    if not sdk_value:
        raise ValueError("facebook.local.json/environment did not resolve sdk_root")
    if not avd_value:
        raise ValueError("facebook.local.json/environment did not resolve avd_home")
    sdk_root = Path(sdk_value).expanduser().resolve()
    avd_home = Path(avd_value).expanduser().resolve()
    emulator_exe = sdk_root / "emulator" / "emulator.exe"
    if not emulator_exe.is_file():
        raise ValueError(f"Missing Android emulator executable: {emulator_exe}")
    if not _avd_exists(avd_home, worker.avd_name):
        raise ValueError(f"Configured AVD does not exist: {avd_home / (worker.avd_name + '.avd')}")
    launcher_script = (project_root / "facebook_visible_emulator.py").resolve()
    if not launcher_script.is_file():
        raise ValueError(f"Missing visible emulator adapter in project root: {launcher_script}")
    launcher_exe = _resolve_python_executable(project_root)
    return VisibleWorker(
        worker_id=worker.worker_id,
        serial=worker.serial,
        avd_name=worker.avd_name,
        capture_port=worker.capture_port,
        native_port=worker.native_port,
        enabled=worker.enabled,
        console_port=_console_port(worker.serial),
        sdk_root=sdk_root,
        avd_home=avd_home,
        emulator_exe=emulator_exe.resolve(),
        launcher_exe=launcher_exe,
        launcher_script=launcher_script,
    )


def build_emulator_command(spec: VisibleWorker) -> list[str]:
    """Build only emulator arguments; no proxy, transport, or helper commands."""
    return [
        str(spec.emulator_exe),
        "-avd",
        spec.avd_name,
        "-port",
        str(spec.console_port),
        "-no-snapshot",
        "-no-boot-anim",
        "-feature",
        "-WiFiPacketStream",
        "-dns-server",
        "8.8.8.8",
        "-cores",
        "4",
        "-memory",
        "3072",
    ]


def build_launcher_command(spec: VisibleWorker) -> list[str]:
    return [
        str(spec.launcher_exe),
        str(spec.launcher_script),
        "--root",
        str(spec.launcher_script.parent),
        "--worker-id",
        spec.worker_id,
    ]


def windows_command_line(arguments: Sequence[str]) -> str:
    """Quote a Windows argv using the same rules as CreateProcess consumers."""
    return subprocess.list2cmdline([str(value) for value in arguments])


def _powershell_executable() -> str:
    configured = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not configured:
        raise RuntimeError("PowerShell is required to inspect existing emulator processes")
    return configured


def _current_session_id() -> int:
    """Resolve this process's Windows session without launching a helper."""
    if sys.platform != "win32":
        raise RuntimeError("emulator process session inspection is Windows-only")
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    session_id = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        error = ctypes.get_last_error()
        raise OSError(error, f"ProcessIdToSessionId failed with error {error}")
    return int(session_id.value)


def find_matching_processes(spec: VisibleWorker) -> list[dict[str, Any]]:
    """Return qemu processes matching this worker's AVD and console port."""
    if sys.platform != "win32":
        raise RuntimeError("emulator process inspection is Windows-only")
    script = r'''
$avd = $env:FACEBOOK_VISIBLE_AVD
$port = $env:FACEBOOK_VISIBLE_PORT
$patternAvd = '(?i)(?:^|\s)-avd\s+' + [regex]::Escape($avd) + '(?:\s|$)'
$patternPort = '(?i)(?:^|\s)-port\s+' + [regex]::Escape($port) + '(?:\s|$)'
@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
    $_.Name -eq 'qemu-system-x86_64.exe' -and $_.CommandLine -and
    $_.CommandLine -match $patternAvd -and $_.CommandLine -match $patternPort
} | Select-Object ProcessId,ParentProcessId,SessionId,Name,CommandLine) | ConvertTo-Json -Compress
'''
    environment = os.environ.copy()
    environment["FACEBOOK_VISIBLE_AVD"] = spec.avd_name
    environment["FACEBOOK_VISIBLE_PORT"] = str(spec.console_port)
    completed = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=str(spec.launcher_script.parent),
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"could not inspect existing emulator processes: {detail or 'PowerShell failed'}")
    output = completed.stdout.strip()
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("could not parse existing emulator process inspection") from exc
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
        return parsed
    raise RuntimeError("existing emulator process inspection returned an invalid shape")


def run_emulator(spec: VisibleWorker) -> dict[str, Any]:
    """Reuse one matching process or start one emulator process and return metadata."""
    current_session = _current_session_id()
    if current_session == 0:
        raise RuntimeError(
            "visible emulator adapter cannot start or reuse an emulator from Session 0; "
            "use launch_visible_in_user_session.ps1"
        )
    matches = find_matching_processes(spec)
    if len(matches) > 1:
        raise RuntimeError(
            f"multiple emulator processes match {spec.worker_id} ({spec.avd_name}/{spec.serial}); refusing ambiguous reuse"
        )
    if matches:
        process = matches[0]
        raw_session_id = process.get("SessionId")
        try:
            session_id = int(raw_session_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"matching emulator process session could not be verified for {spec.worker_id}; refusing reuse"
            ) from exc
        if session_id == 0:
            raise RuntimeError(
                f"matching emulator process for {spec.worker_id} is in Session 0; "
                "refusing reuse from a non-interactive launcher"
            )
        return {
            "worker_id": spec.worker_id,
            "action": "reused",
            "pid": int(process.get("ProcessId") or 0),
            "session_id": session_id,
            "command": build_emulator_command(spec),
        }

    environment = os.environ.copy()
    environment["ANDROID_HOME"] = str(spec.sdk_root)
    environment["ANDROID_SDK_ROOT"] = str(spec.sdk_root)
    environment["FACEBOOK_SDK_ROOT"] = str(spec.sdk_root)
    environment["ANDROID_AVD_HOME"] = str(spec.avd_home)
    environment["FACEBOOK_AVD_HOME"] = str(spec.avd_home)
    environment["PATH"] = os.pathsep.join(
        [str(spec.sdk_root / "platform-tools"), str(spec.sdk_root / "emulator"), environment.get("PATH", "")]
    )
    root = spec.launcher_script.parent
    stdout_path = root / f"visible-emulator-{spec.worker_id}.stdout.log"
    stderr_path = root / f"visible-emulator-{spec.worker_id}.stderr.log"
    command = build_emulator_command(spec)
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open("a", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            env=environment,
            stdout=stdout,
            stderr=stderr,
            creationflags=creation_flags,
        )
    return {
        "worker_id": spec.worker_id,
        "action": "started",
        "pid": process.pid,
        "command": command,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--worker-id", default="worker-01")
    parser.add_argument("--print-spec", action="store_true", help="print resolved configuration without starting an emulator")
    args = parser.parse_args(argv)
    try:
        spec = resolve_worker(args.root, args.worker_id)
        if args.print_spec:
            print(json.dumps(spec.public_spec(), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(run_emulator(spec), ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
