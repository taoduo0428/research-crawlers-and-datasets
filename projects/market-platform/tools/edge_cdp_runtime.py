#!/usr/bin/env python3
"""Ensure the project-local Edge CDP profile is running and reachable."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import psutil


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_liepin_browser_single import profile_run_lock


STATE_ROOT = PROJECT_ROOT / ".crawler-state" / "liepin-edge-cdp-local"
POINTER = STATE_ROOT / "latest-profile.json"
EDGE_DEFAULT = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / (
    "Microsoft/Edge/Application/msedge.exe"
)


def cdp_ready(endpoint: str, timeout: float = 1.0) -> bool:
    url = endpoint.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
        return bool(payload.get("webSocketDebuggerUrl"))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def load_profile(pointer: Path = POINTER) -> Path:
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    profile = Path(str(payload["profile"])).resolve()
    expected_parent = STATE_ROOT.resolve()
    if profile.parent != expected_parent or not (profile / "Default").is_dir():
        raise RuntimeError("invalid or missing project-local Edge CDP profile")
    return profile


def _is_profile_edge(process: psutil.Process, profile: Path) -> bool:
    try:
        if process.name().lower() != "msedge.exe":
            return False
        command = " ".join(process.cmdline()).casefold()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return False
    profile_text = str(profile).casefold()
    return "--type=" not in command and profile_text in command


def stop_stale_profile_edge(profile: Path) -> int:
    roots = [
        process
        for process in psutil.process_iter()
        if _is_profile_edge(process, profile)
    ]
    targets: dict[int, psutil.Process] = {}
    for root in roots:
        try:
            for child in root.children(recursive=True):
                targets[child.pid] = child
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
        targets[root.pid] = root
    for process in targets.values():
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    _gone, alive = psutil.wait_procs(list(targets.values()), timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    return len(targets)


def launch_edge(
    profile: Path,
    endpoint: str,
    edge_path: Path = EDGE_DEFAULT,
) -> int:
    parsed = urlparse(endpoint)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        raise ValueError("CDP endpoint must be a loopback URL with an explicit port")
    if not edge_path.is_file():
        raise FileNotFoundError(edge_path)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [
            str(edge_path),
            f"--remote-debugging-port={parsed.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )
    return int(process.pid)


def ensure_edge_cdp(endpoint: str, timeout: float = 30.0) -> dict[str, object]:
    if cdp_ready(endpoint):
        return {"ready": True, "launched": False, "stopped_stale": 0}
    deadline = time.monotonic() + timeout
    lock_path = STATE_ROOT / "launcher.lock"
    while time.monotonic() < deadline:
        try:
            with profile_run_lock(lock_path):
                if cdp_ready(endpoint):
                    return {"ready": True, "launched": False, "stopped_stale": 0}
                profile = load_profile()
                stopped = stop_stale_profile_edge(profile)
                pid = launch_edge(profile, endpoint)
                while time.monotonic() < deadline:
                    if cdp_ready(endpoint):
                        return {
                            "ready": True,
                            "launched": True,
                            "pid": pid,
                            "stopped_stale": stopped,
                        }
                    time.sleep(0.5)
                break
        except RuntimeError as exc:
            if "already running" not in str(exc):
                raise
            if cdp_ready(endpoint):
                return {"ready": True, "launched": False, "stopped_stale": 0}
            time.sleep(0.5)
    raise TimeoutError(f"Edge CDP did not become ready within {timeout:.1f}s: {endpoint}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:9223")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be at least one second")
    print(json.dumps(ensure_edge_cdp(args.endpoint, args.timeout), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
