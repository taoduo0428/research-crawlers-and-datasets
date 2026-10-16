from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

from facebook_capture_api import WorkerConfig, load_worker_config, validate_worker_config, write_worker_config


WORKER_ID_PATTERN = re.compile(r"^worker-(\d+)$")
EMULATOR_SERIAL_PATTERN = re.compile(r"^emulator-(\d+)$")
STATE_STATUSES = {"reserved", "provisioning", "awaiting-login", "ready", "failed"}
_PROCESS_LOCK = threading.Lock()


class BootstrapError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_workers(raw_workers: Iterable[WorkerConfig | dict]) -> list[WorkerConfig]:
    workers: list[WorkerConfig] = []
    try:
        for raw in raw_workers:
            if isinstance(raw, WorkerConfig):
                workers.append(raw)
                continue
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
        return validate_worker_config(workers)
    except (KeyError, TypeError, ValueError) as exc:
        raise BootstrapError("worker registry contains an invalid entry") from exc
    except Exception as exc:
        raise BootstrapError(str(exc)) from exc


def port_is_available(port: int) -> bool:
    if not 1024 <= int(port) <= 65535:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _next_available_port(
    start: int,
    used: set[int],
    port_available: Callable[[int], bool],
    *,
    stop: int = 65535,
) -> int:
    for port in range(max(1024, start), stop + 1):
        if port not in used and port_available(port):
            return port
    raise BootstrapError(f"no free TCP port is available from {start} through {stop}")


def allocate_worker(
    raw_workers: Iterable[WorkerConfig | dict],
    *,
    avd_home: Path,
    occupied_serials: Iterable[str] = (),
    port_available: Callable[[int], bool] = port_is_available,
    path_exists: Callable[[Path], bool] = Path.exists,
) -> WorkerConfig:
    workers = _coerce_workers(raw_workers)
    used_ids = {worker.worker_id for worker in workers}
    used_avds = {worker.avd_name for worker in workers}
    numeric_ids = [
        int(match.group(1))
        for worker in workers
        if (match := WORKER_ID_PATTERN.fullmatch(worker.worker_id))
    ]
    worker_number = max(numeric_ids or [0]) + 1
    while True:
        worker_id = f"worker-{worker_number:02d}"
        avd_name = f"fb-x86_64-api35-worker-{worker_number:02d}"
        avd_path = avd_home / f"{avd_name}.avd"
        avd_ini = avd_home / f"{avd_name}.ini"
        if (
            worker_id not in used_ids
            and avd_name not in used_avds
            and not path_exists(avd_path)
            and not path_exists(avd_ini)
        ):
            break
        worker_number += 1

    used_serials = {worker.serial for worker in workers}
    used_serials.update(str(value).strip() for value in occupied_serials if str(value).strip())
    serial = ""
    for emulator_port in range(5554, 5683, 2):
        candidate = f"emulator-{emulator_port}"
        if candidate in used_serials or not port_available(emulator_port):
            continue
        serial = candidate
        break
    if not serial:
        raise BootstrapError("no free even Android emulator port is available from 5554 through 5682")

    used_ports = {
        port
        for worker in workers
        for port in (int(worker.capture_port), int(worker.native_port))
    }
    capture_port = _next_available_port(18080 + worker_number, used_ports, port_available)
    used_ports.add(capture_port)
    native_port = _next_available_port(18765 + worker_number, used_ports, port_available)
    candidate = WorkerConfig(
        worker_id=worker_id,
        serial=serial,
        avd_name=avd_name,
        capture_port=capture_port,
        native_port=native_port,
        enabled=False,
        cooldown_seconds=900,
        frida_ssl_unpin=True,
    )
    validate_worker_config([*workers, candidate])
    return candidate


@contextmanager
def exclusive_file_lock(path: Path, timeout_seconds: float = 15.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    with _PROCESS_LOCK, path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise BootstrapError(f"timed out waiting for worker registry lock: {path}") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _registry_replace_retryable(error: Exception) -> bool:
    # Only native Windows replacement/share errors; not arbitrary ApiError,
    # JSON validation, disk-full or POSIX permission failures.
    return os.name == "nt" and isinstance(error, OSError) and getattr(error, "winerror", None) in {5, 32, 33}


def reserve_worker(
    config_path: Path,
    *,
    avd_home: Path,
    state_dir: Path,
    occupied_serials: Iterable[str] = (),
    port_available: Callable[[int], bool] = port_is_available,
    lock_timeout_seconds: float = 15.0,
) -> dict:
    config_path = Path(config_path).resolve()
    state_dir = Path(state_dir).resolve()
    lock_path = config_path.with_name(f".{config_path.name}.lock")
    with exclusive_file_lock(lock_path, timeout_seconds=lock_timeout_seconds):
        try:
            workers = load_worker_config(config_path)
        except Exception as exc:
            raise BootstrapError(str(exc)) from exc
        selected = allocate_worker(
            workers,
            avd_home=Path(avd_home),
            occupied_serials=occupied_serials,
            port_available=port_available,
        )
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = state_dir / "backups" / f"facebook_workers.{timestamp}.json"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, backup_path)
        candidate = [*workers, selected]
        for attempt in range(4):
            try:
                write_worker_config(config_path, candidate)
                break
            except Exception as exc:
                if attempt == 3 or not _registry_replace_retryable(exc):
                    raise BootstrapError(f"failed to reserve {selected.worker_id}: {exc}") from exc
                # Retain the registry lock and the exact allocation candidate.
                # Do not reread/reallocate or publish state before replace succeeds.
                time.sleep(0.05 * (attempt + 1))
        state_path = state_dir / f"{selected.worker_id}.json"
        state = {
            "version": 1,
            "worker_id": selected.worker_id,
            "worker": asdict(selected),
            "status": "reserved",
            "completed_stages": ["reserved"],
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "failed_stage": None,
            "error": None,
            "source_worker_id": None,
            "certificate_hash": None,
        }
        _atomic_write_json(state_path, state)
    return {
        "worker": asdict(selected),
        "state_path": str(state_path),
        "backup_path": str(backup_path),
    }


def update_state(
    state_dir: Path,
    worker_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    failed_stage: str | None = None,
    error: str | None = None,
    source_worker_id: str | None = None,
    certificate_hash: str | None = None,
) -> dict:
    if not WORKER_ID_PATTERN.fullmatch(worker_id):
        raise BootstrapError(f"invalid worker id: {worker_id}")
    if status is not None and status not in STATE_STATUSES:
        raise BootstrapError(f"invalid bootstrap status: {status}")
    path = Path(state_dir).resolve() / f"{worker_id}.json"
    lock = path.with_suffix(".lock")
    with exclusive_file_lock(lock):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BootstrapError(f"bootstrap state could not be read: {path}") from exc
        if payload.get("version") != 1 or payload.get("worker_id") != worker_id:
            raise BootstrapError(f"invalid bootstrap state: {path}")
        completed = [str(value) for value in payload.get("completed_stages") or []]
        if stage and stage not in completed:
            completed.append(stage)
        payload["completed_stages"] = completed
        if status is not None:
            payload["status"] = status
            if status != "failed":
                payload["failed_stage"] = None
                payload["error"] = None
        if failed_stage is not None:
            payload["failed_stage"] = failed_stage or None
        if error is not None:
            payload["error"] = error or None
        if source_worker_id is not None:
            payload["source_worker_id"] = source_worker_id or None
        if certificate_hash is not None:
            payload["certificate_hash"] = certificate_hash or None
        payload["updated_at"] = utc_now()
        _atomic_write_json(path, payload)
    return payload


def discover_adb_serials(adb_path: Path) -> set[str]:
    if not Path(adb_path).is_file():
        return set()
    try:
        completed = subprocess.run(
            [str(adb_path), "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    result: set[str] = set()
    for line in completed.stdout.splitlines():
        columns = line.strip().split()
        if len(columns) >= 2 and EMULATOR_SERIAL_PATTERN.fullmatch(columns[0]):
            result.add(columns[0])
    return result


def get_worker(config_path: Path, worker_id: str) -> WorkerConfig:
    try:
        workers = load_worker_config(config_path)
    except Exception as exc:
        raise BootstrapError(str(exc)) from exc
    match = next((worker for worker in workers if worker.worker_id == worker_id), None)
    if match is None:
        raise BootstrapError(f"unknown worker: {worker_id}")
    return match


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reserve and track isolated Facebook emulator workers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    reserve = subparsers.add_parser("reserve")
    reserve.add_argument("--config", type=Path, required=True)
    reserve.add_argument("--avd-home", type=Path, required=True)
    reserve.add_argument("--state-dir", type=Path, required=True)
    reserve.add_argument("--adb", type=Path)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--config", type=Path, required=True)
    inspect.add_argument("--worker-id", required=True)

    mark = subparsers.add_parser("mark")
    mark.add_argument("--state-dir", type=Path, required=True)
    mark.add_argument("--worker-id", required=True)
    mark.add_argument("--status", choices=sorted(STATE_STATUSES))
    mark.add_argument("--stage")
    mark.add_argument("--failed-stage")
    mark.add_argument("--error")
    mark.add_argument("--source-worker-id")
    mark.add_argument("--certificate-hash")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "reserve":
            occupied = discover_adb_serials(args.adb) if args.adb else set()
            result = reserve_worker(
                args.config,
                avd_home=args.avd_home,
                state_dir=args.state_dir,
                occupied_serials=occupied,
            )
        elif args.command == "inspect":
            result = {"worker": asdict(get_worker(args.config, args.worker_id))}
        else:
            result = update_state(
                args.state_dir,
                args.worker_id,
                status=args.status,
                stage=args.stage,
                failed_stage=args.failed_stage,
                error=args.error,
                source_worker_id=args.source_worker_id,
                certificate_hash=args.certificate_hash,
            )
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except BootstrapError as exc:
        print(f"facebook worker bootstrap error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
