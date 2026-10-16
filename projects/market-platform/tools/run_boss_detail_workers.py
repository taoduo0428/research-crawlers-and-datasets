#!/usr/bin/env python3
"""Prepare and run low-risk BOSS detail workers (default: one headed browser).

The source catalog is never modified. ``prepare`` creates deterministic
record-id shards; ``run`` starts a headed Chrome profile and invokes the
existing single-worker extractor in small batches.

Default risk posture (per operator policy):
- one worker / one account / one browser profile
- no Cookie injection (login manually in the headed Chrome)
- slow detail gaps, small batches, long batch cooldowns
- login/verification/soft-fail stops the run and writes a cooldown
- account rotation is off unless explicitly re-enabled
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DEFAULT = PROJECT_ROOT / "outputs" / "招聘--完整数据" / "BOSS" / "岗位数据.jsonl"
DETAILS_DEFAULT = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "BOSS" / "详情补充.jsonl"
ACCOUNTS_DEFAULT = PROJECT_ROOT / ".crawler-state" / "boss-accounts"
STATE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "boss-detail-parallel"
DETAIL_OUTPUT_ROOT = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "BOSS" / "workers"
WORKER_COUNT_DEFAULT = 1
PORT_BASE_DEFAULT = 9231
# Conservative defaults: prefer survival over throughput.
GAP_MIN_DEFAULT = 8.0
GAP_MAX_DEFAULT = 15.0
BATCH_SIZE_DEFAULT = 20
BATCH_COOLDOWN_DEFAULT = 900.0
COOLDOWN_SECONDS_DEFAULT = 3600.0
SOFT_FAIL_STREAK_DEFAULT = 3
INJECT_COOKIES_DEFAULT = False
ROTATION_DEFAULT = False
RESTRICTED_STATUSES = frozenset({"login_required", "verification_required"})
SOFT_FAIL_STATUSES = frozenset({"failed", "login_required", "verification_required"})


def atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_jsonl_write(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return count


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL 第 {line_number} 行无效: {path}") from exc
            if isinstance(value, dict):
                yield value


def record_key(record: dict[str, Any]) -> str:
    """Use record_id first; fallbacks are only for legacy rows."""
    for field in ("record_id", "job_id", "detail_url", "job_link"):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return ""


def worker_index(key: str, workers: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % workers


def load_success_keys(paths: Iterable[Path]) -> set[str]:
    successful: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for record in iter_jsonl(path):
            key = record_key(record)
            if key and str(record.get("jd") or "").strip():
                successful.add(key)
    return successful


def load_terminal_failure_keys(path: Path, max_failed_events: int = 2) -> set[str]:
    """Return records that exhausted bounded detail attempts across runs."""
    if not path.exists():
        return set()
    failures: dict[str, int] = {}
    for event in iter_jsonl(path):
        if event.get("status") != "failed":
            continue
        key = record_key(event)
        if key:
            failures[key] = failures.get(key, 0) + 1
    return {key for key, count in failures.items() if count >= max_failed_events}


def manual_profile_account() -> dict[str, Any]:
    """Placeholder when the operator logs in inside the headed profile."""
    return {
        "file": "profile-login-only",
        "path": "",
        "fingerprint": "manual-profile",
        "cookie_count": 0,
    }


def load_active_accounts(
    accounts_dir: Path,
    *,
    minimum: int = 1,
    required: bool = False,
) -> list[dict[str, Any]]:
    manifest_path = accounts_dir / "accounts-manifest.json"
    if not manifest_path.is_file():
        if required:
            raise FileNotFoundError(f"missing accounts manifest: {manifest_path}")
        return []
    with manifest_path.open("r", encoding="utf-8-sig") as handle:
        manifest = json.load(handle)
    active: list[dict[str, Any]] = []
    for item in manifest.get("accounts", []):
        if not item.get("active") or item.get("quarantined"):
            continue
        filename = str(item.get("file") or "").strip()
        path = accounts_dir / filename
        if not filename or not path.is_file():
            raise FileNotFoundError(f"active account file missing: {path}")
        with path.open("r", encoding="utf-8-sig") as handle:
            cookies = json.load(handle)
        if not isinstance(cookies, list) or not cookies:
            raise ValueError(f"active account cookie file is empty or invalid: {path}")
        active.append({
            "file": filename,
            "path": str(path),
            "fingerprint": str(item.get("fingerprint") or ""),
            "cookie_count": len(cookies),
        })
    if len(active) < minimum:
        if required:
            raise ValueError(f"至少需要 {minimum} 个 active BOSS 账号，当前 {len(active)} 个")
        return active
    return active


def resolve_accounts_for_plan(
    accounts_dir: Path,
    *,
    workers: int,
    inject_cookies: bool,
) -> list[dict[str, Any]]:
    if inject_cookies:
        accounts = load_active_accounts(accounts_dir, minimum=workers, required=True)
        return accounts[:workers]
    accounts = load_active_accounts(accounts_dir, minimum=0, required=False)
    if not accounts:
        return [manual_profile_account() for _ in range(workers)]
    if len(accounts) < workers:
        raise ValueError(
            f"plan 需要 {workers} 个账号标签，但 active 只有 {len(accounts)} 个；"
            "低风险模式请使用 --workers 1"
        )
    return accounts[:workers]


def cooldown_path(state_root: Path) -> Path:
    return state_root / "cooldown.json"


def read_cooldown_remaining(state_root: Path, *, now: float | None = None) -> dict[str, Any] | None:
    path = cooldown_path(state_root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    until = float(payload.get("cooldown_until") or 0)
    current = time.time() if now is None else now
    remaining = until - current
    if remaining <= 0:
        return None
    return {
        "remaining_seconds": remaining,
        "reason": str(payload.get("reason") or "cooldown"),
        "cooldown_until": until,
    }


def write_cooldown(state_root: Path, *, seconds: float, reason: str) -> dict[str, Any]:
    seconds = max(60.0, float(seconds))
    payload = {
        "cooldown_until": time.time() + seconds,
        "seconds": seconds,
        "reason": reason,
        "updated_at": time.time(),
    }
    atomic_json_write(cooldown_path(state_root), payload)
    return payload


def clear_cooldown(state_root: Path) -> None:
    path = cooldown_path(state_root)
    path.unlink(missing_ok=True)


def consecutive_soft_fail_streak(statuses: list[str]) -> int:
    """Count trailing soft-fail statuses in the current batch checkpoint."""
    streak = 0
    for status in reversed(statuses):
        if status in SOFT_FAIL_STATUSES:
            streak += 1
            continue
        break
    return streak


def worker_paths(state_root: Path, worker: int) -> dict[str, Path]:
    name = f"worker-{worker}"
    return {
        "shard": state_root / "shards" / f"{name}.jsonl",
        "profile": state_root / "profiles" / name,
        "output": DETAIL_OUTPUT_ROOT / f"{name}.jsonl",
        "checkpoint": DETAIL_OUTPUT_ROOT / f"{name}_checkpoint.jsonl",
        "log": state_root / "logs" / f"{name}.log",
    }


def prepare(
    source: Path = SOURCE_DEFAULT,
    prior_details: Path = DETAILS_DEFAULT,
    accounts_dir: Path = ACCOUNTS_DEFAULT,
    state_root: Path = STATE_DEFAULT,
    workers: int = WORKER_COUNT_DEFAULT,
    port_base: int = PORT_BASE_DEFAULT,
    *,
    gap_min: float = GAP_MIN_DEFAULT,
    gap_max: float = GAP_MAX_DEFAULT,
    batch_size: int = BATCH_SIZE_DEFAULT,
    batch_cooldown_seconds: float = BATCH_COOLDOWN_DEFAULT,
    cooldown_seconds: float = COOLDOWN_SECONDS_DEFAULT,
    soft_fail_streak: int = SOFT_FAIL_STREAK_DEFAULT,
    inject_cookies: bool = INJECT_COOKIES_DEFAULT,
    rotation_enabled: bool = ROTATION_DEFAULT,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be positive")
    if gap_min < 0 or gap_max < gap_min:
        raise ValueError("gap must satisfy 0 <= gap_min <= gap_max")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if batch_cooldown_seconds < 0 or cooldown_seconds < 0:
        raise ValueError("cooldown values must be non-negative")
    if soft_fail_streak < 1:
        raise ValueError("soft_fail_streak must be positive")
    if not source.is_file():
        raise FileNotFoundError(source)
    accounts = resolve_accounts_for_plan(
        accounts_dir, workers=workers, inject_cookies=inject_cookies
    )
    # All historical worker outputs remain authoritative when worker count changes.
    existing_paths = [prior_details, *sorted(DETAIL_OUTPUT_ROOT.glob("worker-*.jsonl"))]
    completed = load_success_keys(existing_paths)
    shards: list[list[dict[str, Any]]] = [[] for _ in range(workers)]
    seen: set[str] = set()
    source_count = 0
    duplicate_count = 0
    skipped_completed = 0
    for record in iter_jsonl(source):
        source_count += 1
        key = record_key(record)
        if not key:
            raise ValueError(f"source row {source_count} has no record_id/job_id/detail URL")
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        if key in completed:
            skipped_completed += 1
            continue
        shards[worker_index(key, workers)].append(record)
    state_root.mkdir(parents=True, exist_ok=True)
    for index, records in enumerate(shards, 1):
        atomic_jsonl_write(worker_paths(state_root, index)["shard"], records)
    worker_specs = []
    for index in range(1, workers + 1):
        paths = worker_paths(state_root, index)
        worker_specs.append({
            "worker": index,
            "port": port_base + index - 1,
            "account_file": accounts[index - 1]["file"],
            "account_fingerprint": accounts[index - 1]["fingerprint"],
            "shard": str(paths["shard"]),
            "profile": str(paths["profile"]),
            "output": str(paths["output"]),
            "checkpoint": str(paths["checkpoint"]),
            "log": str(paths["log"]),
            "records": len(shards[index - 1]),
        })
    plan = {
        "schema_version": 2,
        "site": "boss",
        "status": "prepared_not_started",
        "risk_profile": "low",
        "source": str(source),
        "prior_details": str(prior_details),
        "accounts_dir": str(accounts_dir),
        "workers": workers,
        "port_base": port_base,
        "gap_min": float(gap_min),
        "gap_max": float(gap_max),
        "batch_size": int(batch_size),
        "batch_cooldown_seconds": float(batch_cooldown_seconds),
        "cooldown_seconds": float(cooldown_seconds),
        "soft_fail_streak": int(soft_fail_streak),
        "inject_cookies": bool(inject_cookies),
        "source_count": source_count,
        "unique_source_count": len(seen),
        "already_successful_count": len(completed),
        "skipped_completed_count": skipped_completed,
        "duplicate_source_count": duplicate_count,
        "pending_count": sum(map(len, shards)),
        "active_accounts": [
            {"file": item["file"], "fingerprint": item["fingerprint"], "cookie_count": item["cookie_count"]}
            for item in accounts
        ],
        "quarantined_excluded": True,
        "partition_policy": "sha256(record_id) mod workers",
        "no_duplicate_policy": "disjoint record-id shards plus output-success resume",
        "session_policy": {
            "login": "manual_headed_profile",
            "inject_cookies": bool(inject_cookies),
            "note": "默认不注入 Cookie；请在 headed Chrome 中人工登录后 run",
        },
        "rotation_policy": {
            "enabled": bool(rotation_enabled),
            "trigger": ["login_required", "verification_required"],
            "never_replay_successful": True,
            "replacement_pool": "remaining active accounts",
        },
        "worker_specs": worker_specs,
    }
    atomic_json_write(state_root / "plan.json", plan)
    atomic_json_write(state_root / "runtime-status.json", {
        "status": "prepared_not_started",
        "updated_at": time.time(),
        "workers": {str(i): {"status": "pending"} for i in range(1, workers + 1)},
    })
    return plan


def import_boss_module():
    scripts_dir = PROJECT_ROOT / "drv_crawler" / "boss_cdp" / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import boss_cdp_raw  # type: ignore
    if not boss_cdp_raw.require_runtime_dependencies("requests", "websocket"):
        raise RuntimeError("BOSS CDP dependencies unavailable")
    return boss_cdp_raw


def read_cookies(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise ValueError(f"cookie file must be a JSON array: {path}")
    cookies: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name") or "value" not in item:
            continue
        cookie: dict[str, Any] = {
            "name": str(item["name"]),
            "value": str(item["value"]),
            "domain": str(item.get("domain") or ".zhipin.com"),
            "path": str(item.get("path") or "/"),
        }
        for field in ("secure", "httpOnly"):
            if field in item:
                cookie[field] = bool(item[field])
        if item.get("sameSite") in {"Strict", "Lax", "None"}:
            cookie["sameSite"] = item["sameSite"]
        expiration = item.get("expirationDate")
        if expiration:
            try:
                cookie["expires"] = float(expiration)
            except (TypeError, ValueError):
                pass
        cookies.append(cookie)
    if not cookies:
        raise ValueError(f"cookie file contains no usable cookies: {path}")
    return cookies


def inject_cookies(boss: Any, port: int, cookie_file: Path) -> None:
    cookies = read_cookies(cookie_file)
    cdp = boss.CDPSession(port)
    target_id = session_id = None
    try:
        target_id, session_id = boss.create_page_session(cdp, background=False)
        cdp.send("Network.enable", {}, session_id)
        cdp.send("Network.clearBrowserCookies", {}, session_id)
        cdp.send("Network.setCookies", {"cookies": cookies}, session_id)
    finally:
        if target_id:
            try:
                cdp.send("Target.closeTarget", {"targetId": target_id})
            except Exception:
                pass
        cdp.close()


def endpoint_ready(boss: Any, port: int) -> bool:
    # Chrome may briefly stop answering CDP between bounded batches. Give the
    # existing headed profile a short grace period before attempting a restart;
    # restarting a still-locked profile can surface as PermissionError.
    for _ in range(5):
        try:
            if bool(boss.is_cdp_ready(port)):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def ensure_headed_chrome(boss: Any, port: int, profile: Path) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    if endpoint_ready(boss, port):
        if not boss.cdp_port_uses_profile(port, str(profile)):
            raise RuntimeError(f"CDP port {port} is already occupied by another profile")
        return
    result = boss.run_setup_chrome(
        cdp_port=port,
        cdp_data_dir=str(profile),
        wait_login=False,
        reset_profile=False,
    )
    if result != 0 or not endpoint_ready(boss, port):
        raise RuntimeError(f"headed Chrome CDP did not become ready on port {port}")


def checkpoint_statuses_since(path: Path, byte_offset: int) -> list[str]:
    """Read only checkpoint events appended by the current bounded batch."""
    if not path.exists():
        return []
    statuses: list[str] = []
    with path.open("rb") as handle:
        handle.seek(byte_offset)
        for raw_line in handle:
            try:
                value = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            statuses.append(str(value.get("status") or ""))
    return statuses


def shard_remaining(shard: Path, output: Path, checkpoint: Path | None = None) -> int:
    pending = {record_key(record) for record in iter_jsonl(shard) if record_key(record)}
    if output.exists():
        pending -= load_success_keys([output])
    if checkpoint is not None:
        pending -= load_terminal_failure_keys(checkpoint)
    return len(pending)


def update_runtime_status(state_root: Path, worker: int, values: dict[str, Any], lock: threading.Lock) -> None:
    with lock:
        path = state_root / "runtime-status.json"
        current: dict[str, Any] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                current = json.load(handle)
        current.setdefault("workers", {})[str(worker)] = values
        current["updated_at"] = time.time()
        atomic_json_write(path, current)


class AccountPool:
    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self._queue = deque(accounts)
        self._used: set[str] = set()
        self._blocked: set[str] = set()
        self._lock = threading.Lock()

    def take(self, preferred: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._lock:
            if preferred and preferred["file"] not in self._used and preferred["file"] not in self._blocked:
                self._used.add(preferred["file"])
                return preferred
            for _ in range(len(self._queue)):
                item = self._queue.popleft()
                if item["file"] in self._used or item["file"] in self._blocked:
                    self._queue.append(item)
                    continue
                self._used.add(item["file"])
                return item
        return None

    def release(self, item: dict[str, Any], blocked: bool = False) -> None:
        with self._lock:
            self._used.discard(item["file"])
            if blocked:
                self._blocked.add(item["file"])
            else:
                self._queue.append(item)


def run_worker(
    spec: dict[str, Any],
    state_root: Path,
    account_pool: AccountPool,
    status_lock: threading.Lock,
    python_executable: str,
    scraper: Path,
    accounts_dir: Path,
    gap_min: float,
    gap_max: float,
    batch_size: int,
    smoke_limit: int | None,
    rotation_enabled: bool,
    inject_cookies_enabled: bool,
    batch_cooldown_seconds: float,
    cooldown_seconds: float,
    soft_fail_streak: int,
) -> None:
    worker = int(spec["worker"])
    port = int(spec["port"])
    paths = {key: Path(value) for key, value in spec.items() if key in {"shard", "profile", "output", "checkpoint", "log"}}
    account_path = accounts_dir / spec["account_file"] if spec.get("account_file") else Path()
    preferred = {
        "file": spec["account_file"],
        "path": str(account_path) if account_path.is_file() else "",
        "fingerprint": spec.get("account_fingerprint", ""),
    }
    injected_account_file: str | None = None
    attempts = 0
    while True:
        remaining = shard_remaining(paths["shard"], paths["output"], paths["checkpoint"])
        if remaining == 0:
            update_runtime_status(state_root, worker, {"status": "completed", "remaining": 0}, status_lock)
            return
        account = account_pool.take(preferred)
        preferred = None
        if account is None:
            update_runtime_status(state_root, worker, {"status": "blocked_no_account", "remaining": remaining}, status_lock)
            return
        try:
            update_runtime_status(
                state_root,
                worker,
                {
                    "status": "starting",
                    "account_file": account["file"],
                    "remaining": remaining,
                    "inject_cookies": inject_cookies_enabled,
                },
                status_lock,
            )
            boss = import_boss_module()
            ensure_headed_chrome(boss, port, paths["profile"])
            if inject_cookies_enabled:
                cookie_file = Path(account.get("path") or "")
                if not cookie_file.is_file():
                    raise FileNotFoundError(f"inject_cookies enabled but cookie file missing: {cookie_file}")
                if injected_account_file != account["file"]:
                    inject_cookies(boss, port, cookie_file)
                    injected_account_file = account["file"]
            else:
                paths["log"].parent.mkdir(parents=True, exist_ok=True)
                with paths["log"].open("a", encoding="utf-8") as log_handle:
                    log_handle.write(
                        json.dumps(
                            {
                                "event": "manual_login_required",
                                "worker": worker,
                                "profile": str(paths["profile"]),
                                "port": port,
                                "message": "未注入 Cookie；请确认 headed Chrome 已登录 BOSS 后再继续本 batch",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            command = [
                python_executable, str(scraper),
                "--input", str(paths["shard"]), "--detail",
                "--detail-output", str(paths["output"]),
                "--detail-checkpoint", str(paths["checkpoint"]),
                "--cdp-port", str(port), "--cdp-data-dir", str(paths["profile"]),
                "--detail-gap-min", str(gap_min), "--detail-gap-max", str(gap_max),
                "--max-details", str(min(batch_size, remaining)),
            ]
            paths["log"].parent.mkdir(parents=True, exist_ok=True)
            checkpoint_offset = paths["checkpoint"].stat().st_size if paths["checkpoint"].exists() else 0
            with paths["log"].open("a", encoding="utf-8") as log_handle:
                log_handle.write(json.dumps({
                    "event": "batch_start",
                    "worker": worker,
                    "account_file": account["file"],
                    "remaining": remaining,
                    "gap_min": gap_min,
                    "gap_max": gap_max,
                    "batch_size": batch_size,
                    "inject_cookies": inject_cookies_enabled,
                }, ensure_ascii=False) + "\n")
                log_handle.flush()
                result = subprocess.run(command, cwd=PROJECT_ROOT, stdout=log_handle, stderr=subprocess.STDOUT, check=False)
            statuses = checkpoint_statuses_since(paths["checkpoint"], checkpoint_offset)
            new_remaining = shard_remaining(paths["shard"], paths["output"], paths["checkpoint"])
            restricted = any(item in RESTRICTED_STATUSES for item in statuses)
            soft_streak = consecutive_soft_fail_streak(statuses)
            if restricted or soft_streak >= soft_fail_streak:
                reason = (
                    next((item for item in reversed(statuses) if item in RESTRICTED_STATUSES), None)
                    or f"soft_fail_streak_{soft_streak}"
                )
                account_pool.release(account, blocked=True)
                write_cooldown(state_root, seconds=cooldown_seconds, reason=str(reason))
                next_status = "rotating_account" if (restricted and rotation_enabled) else "stopped_restricted"
                update_runtime_status(
                    state_root,
                    worker,
                    {
                        "status": next_status,
                        "account_file": account["file"],
                        "remaining": new_remaining,
                        "reason": reason,
                        "soft_fail_streak": soft_streak,
                    },
                    status_lock,
                )
                if not (restricted and rotation_enabled):
                    return
                attempts += 1
                if attempts >= 5:
                    return
                continue
            account_pool.release(account, blocked=False)
            # Keep a healthy session bound to this worker. Rotation stays off
            # in the low-risk profile unless the operator re-enables it.
            preferred = account
            if result.returncode != 0:
                write_cooldown(state_root, seconds=cooldown_seconds, reason=f"scraper_exit_{result.returncode}")
                update_runtime_status(state_root, worker, {"status": "stopped_error", "account_file": account["file"], "remaining": new_remaining, "returncode": result.returncode}, status_lock)
                return
            if new_remaining >= remaining:
                update_runtime_status(state_root, worker, {"status": "stalled_no_progress", "account_file": account["file"], "remaining": new_remaining}, status_lock)
                return
            attempts = 0
            if smoke_limit is not None:
                update_runtime_status(state_root, worker, {"status": "smoke_complete", "remaining": new_remaining}, status_lock)
                return
            update_runtime_status(state_root, worker, {"status": "batch_complete", "remaining": new_remaining}, status_lock)
            if batch_cooldown_seconds > 0 and new_remaining > 0:
                update_runtime_status(
                    state_root,
                    worker,
                    {
                        "status": "batch_cooldown",
                        "account_file": account["file"],
                        "remaining": new_remaining,
                        "cooldown_seconds": batch_cooldown_seconds,
                    },
                    status_lock,
                )
                time.sleep(batch_cooldown_seconds)
        except PermissionError as exc:
            account_pool.release(account, blocked=False)
            attempts += 1
            update_runtime_status(
                state_root,
                worker,
                {
                    "status": "retry_permission_error",
                    "account_file": account["file"],
                    "remaining": remaining,
                    "attempt": attempts,
                    "error": str(exc)[:200],
                },
                status_lock,
            )
            if attempts < 3:
                preferred = account
                time.sleep(2 * attempts)
                continue
            update_runtime_status(
                state_root,
                worker,
                {
                    "status": "stopped_error",
                    "account_file": account["file"],
                    "remaining": remaining,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                },
                status_lock,
            )
            return
        except Exception as exc:
            account_pool.release(account, blocked=False)
            update_runtime_status(
                state_root,
                worker,
                {
                    "status": "stopped_error",
                    "account_file": account["file"],
                    "remaining": remaining,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                },
                status_lock,
            )
            return


def run(
    plan_path: Path,
    smoke_limit: int | None = None,
    *,
    ignore_cooldown: bool = False,
) -> None:
    with plan_path.open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    if plan.get("status") not in {"prepared_not_started", "running", "interrupted", "smoke_complete", "run_finished_or_stopped"}:
        raise ValueError(f"plan status does not allow run: {plan.get('status')}")
    state_root = plan_path.parent
    if not ignore_cooldown:
        active = read_cooldown_remaining(state_root)
        if active:
            minutes = max(1, int(active["remaining_seconds"] // 60) + 1)
            raise RuntimeError(
                f"BOSS 详情处于冷却中（原因: {active['reason']}，约剩余 {minutes} 分钟）。"
                "冷却是为了降低安全验证风险；确认已人工登录且要强制续跑时加 --ignore-cooldown。"
            )
    accounts_dir = Path(plan.get("accounts_dir") or ACCOUNTS_DEFAULT)
    inject_cookies_enabled = bool(plan.get("inject_cookies", INJECT_COOKIES_DEFAULT))
    # Prefer plan worker_specs account labels; fall back to manifest only when injecting.
    plan_accounts = []
    for spec in plan.get("worker_specs", []):
        plan_accounts.append({
            "file": spec.get("account_file") or "profile-login-only",
            "path": str(accounts_dir / spec["account_file"]) if spec.get("account_file") else "",
            "fingerprint": spec.get("account_fingerprint") or "",
            "cookie_count": 0,
        })
    if not plan_accounts:
        plan_accounts = resolve_accounts_for_plan(
            accounts_dir,
            workers=int(plan.get("workers") or 1),
            inject_cookies=inject_cookies_enabled,
        )
    pool = AccountPool(plan_accounts)
    lock = threading.Lock()
    if not inject_cookies_enabled:
        print(
            "低风险模式：不注入 Cookie。请先在 headed Chrome profile 中人工登录 BOSS，再继续本 run。",
            flush=True,
        )
    atomic_json_write(state_root / "runtime-status.json", {"status": "running", "updated_at": time.time(), "workers": {}})
    threads = []
    scraper = PROJECT_ROOT / "drv_crawler" / "boss_cdp" / "scripts" / "boss_cdp_raw.py"
    for spec in plan["worker_specs"]:
        effective_batch = 1 if smoke_limit is not None else int(plan.get("batch_size", BATCH_SIZE_DEFAULT))
        thread = threading.Thread(
            target=run_worker,
            args=(
                spec,
                state_root,
                pool,
                lock,
                sys.executable,
                scraper,
                accounts_dir,
                float(plan.get("gap_min", GAP_MIN_DEFAULT)),
                float(plan.get("gap_max", GAP_MAX_DEFAULT)),
                effective_batch,
                smoke_limit,
                bool(plan.get("rotation_policy", {}).get("enabled", ROTATION_DEFAULT)),
                inject_cookies_enabled,
                0.0 if smoke_limit is not None else float(plan.get("batch_cooldown_seconds", BATCH_COOLDOWN_DEFAULT)),
                float(plan.get("cooldown_seconds", COOLDOWN_SECONDS_DEFAULT)),
                int(plan.get("soft_fail_streak", SOFT_FAIL_STREAK_DEFAULT)),
            ),
            daemon=False,
        )
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()
    final_status = "smoke_complete" if smoke_limit is not None else "run_finished_or_stopped"
    plan["status"] = final_status
    atomic_json_write(plan_path, plan)
    runtime_path = state_root / "runtime-status.json"
    runtime = {}
    if runtime_path.exists():
        with runtime_path.open("r", encoding="utf-8") as handle:
            runtime = json.load(handle)
    runtime["status"] = final_status
    runtime["updated_at"] = time.time()
    atomic_json_write(runtime_path, runtime)


def check(plan_path: Path) -> int:
    with plan_path.open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    accounts_dir = Path(plan.get("accounts_dir") or ACCOUNTS_DEFAULT)
    accounts = load_active_accounts(accounts_dir, minimum=0, required=False)
    print(f"plan: {plan_path}")
    print(
        f"workers: {plan.get('workers')} | manifest active accounts: {len(accounts)} | "
        f"pending: {plan.get('pending_count')} | inject_cookies: {plan.get('inject_cookies', False)} | "
        f"gap: {plan.get('gap_min')}-{plan.get('gap_max')}s | batch: {plan.get('batch_size')}"
    )
    cooldown = read_cooldown_remaining(plan_path.parent)
    if cooldown:
        print(
            f"cooldown: active reason={cooldown['reason']} "
            f"remaining_seconds={int(cooldown['remaining_seconds'])}"
        )
    else:
        print("cooldown: none")
    for spec in plan.get("worker_specs", []):
        paths = worker_paths(plan_path.parent, int(spec["worker"]))
        print(
            f"worker-{spec['worker']}: shard={spec['records']} "
            f"remaining={shard_remaining(paths['shard'], paths['output'], paths['checkpoint'])} "
            f"port={spec['port']} profile={spec.get('profile')}"
        )
    print("cookie_values_logged: false")
    print("session: manual headed login recommended; cookie injection off by default")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BOSS low-risk detail coordinator (default: 1 worker, no cookie inject)"
    )
    parser.add_argument(
        "command",
        choices=("prepare", "check", "run", "clear-cooldown"),
    )
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--prior-details", type=Path, default=DETAILS_DEFAULT)
    parser.add_argument("--accounts-dir", type=Path, default=ACCOUNTS_DEFAULT)
    parser.add_argument("--state-root", type=Path, default=STATE_DEFAULT)
    parser.add_argument("--workers", type=int, default=WORKER_COUNT_DEFAULT)
    parser.add_argument("--port-base", type=int, default=PORT_BASE_DEFAULT)
    parser.add_argument("--gap-min", type=float, default=GAP_MIN_DEFAULT)
    parser.add_argument("--gap-max", type=float, default=GAP_MAX_DEFAULT)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT)
    parser.add_argument("--batch-cooldown-seconds", type=float, default=BATCH_COOLDOWN_DEFAULT)
    parser.add_argument("--cooldown-seconds", type=float, default=COOLDOWN_SECONDS_DEFAULT)
    parser.add_argument("--soft-fail-streak", type=int, default=SOFT_FAIL_STREAK_DEFAULT)
    parser.add_argument(
        "--inject-cookies",
        action="store_true",
        default=INJECT_COOKIES_DEFAULT,
        help="opt-in: inject Cookie files (raises verification risk; not recommended)",
    )
    parser.add_argument(
        "--enable-rotation",
        action="store_true",
        default=ROTATION_DEFAULT,
        help="opt-in: rotate accounts after login/verification (default off)",
    )
    parser.add_argument("--plan", type=Path, default=None)
    parser.add_argument(
        "--smoke-limit",
        type=int,
        default=None,
        help="run one bounded detail per worker; network starts only with run",
    )
    parser.add_argument(
        "--ignore-cooldown",
        action="store_true",
        help="allow run even when a risk cooldown is active",
    )
    args = parser.parse_args()
    if args.command == "clear-cooldown":
        clear_cooldown(args.state_root)
        print(f"cleared cooldown under {args.state_root}")
        return 0
    if args.command == "prepare":
        plan = prepare(
            args.source,
            args.prior_details,
            args.accounts_dir,
            args.state_root,
            args.workers,
            args.port_base,
            gap_min=args.gap_min,
            gap_max=args.gap_max,
            batch_size=args.batch_size,
            batch_cooldown_seconds=args.batch_cooldown_seconds,
            cooldown_seconds=args.cooldown_seconds,
            soft_fail_streak=args.soft_fail_streak,
            inject_cookies=args.inject_cookies,
            rotation_enabled=args.enable_rotation,
        )
        print(f"prepared {plan['pending_count']} pending records across {plan['workers']} shards")
        print(
            f"risk_profile=low workers={plan['workers']} inject_cookies={plan['inject_cookies']} "
            f"gap={plan['gap_min']}-{plan['gap_max']}s batch={plan['batch_size']} "
            f"batch_cooldown={plan['batch_cooldown_seconds']}s"
        )
        print(f"plan: {args.state_root / 'plan.json'}")
        print("下一步: 打开 headed Chrome profile 登录 BOSS，再执行 run")
        return 0
    plan_path = args.plan or args.state_root / "plan.json"
    if not plan_path.is_file():
        raise SystemExit(f"plan not found: {plan_path}; run prepare first")
    if args.command == "check":
        return check(plan_path)
    if args.smoke_limit is not None and args.smoke_limit < 1:
        raise SystemExit("--smoke-limit must be positive")
    run(plan_path, args.smoke_limit, ignore_cooldown=args.ignore_cooldown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
