"""Prepare and run Liepin detail workers (high-risk path; prefer browser_single).

WARNING: Cookie + requests multi-worker detail fetch frequently triggers the
Liepin security center. The recommended low-risk path is
``tools/run_liepin_browser_single.py`` with one headed profile and manual login.
This coordinator refuses to run unless ``--i-accept-risk`` is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DEFAULT = PROJECT_ROOT / "outputs" / "招聘--完整数据" / "猎聘" / "岗位数据.jsonl"
DETAILS_DEFAULT = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "猎聘" / "详情补充.jsonl"
ACCOUNTS_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-accounts"
STATE_DEFAULT = PROJECT_ROOT / ".crawler-state" / "liepin-detail-parallel"
OUTPUT_ROOT = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "猎聘" / "workers"
WORKERS_DEFAULT = 3


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} 不是 JSON 对象")
            yield value


def record_key(row: dict[str, Any]) -> str:
    return str(row.get("record_id") or row.get("job_id") or row.get("detail_url") or "").strip()


def has_detail(row: dict[str, Any]) -> bool:
    return bool(str(row.get("description_raw") or row.get("jd") or "").strip())


def atomic_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return count


def atomic_json(path: Path, value: Any) -> None:
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


def worker_paths(state_root: Path, worker: int) -> dict[str, Path]:
    name = f"worker-{worker}"
    return {
        "shard": state_root / "shards" / f"{name}.jsonl",
        "output": OUTPUT_ROOT / f"{name}.jsonl",
        "log": state_root / "logs" / f"{name}.log",
    }


def active_accounts(accounts_dir: Path) -> list[Path]:
    manifest = json.loads((accounts_dir / "accounts-manifest.json").read_text(encoding="utf-8"))
    files = [str(item["file"]) for item in manifest.get("accounts", [])]
    paths = [accounts_dir / name for name in files if (accounts_dir / name).is_file()]
    if len(paths) < WORKERS_DEFAULT:
        raise ValueError(f"猎聘 active Cookie 少于 {WORKERS_DEFAULT} 份")
    return paths[:WORKERS_DEFAULT]


def successful_keys(paths: Iterable[Path]) -> set[str]:
    output: set[str] = set()
    for path in paths:
        for row in iter_jsonl(path):
            if has_detail(row) and record_key(row):
                output.add(record_key(row))
    return output


def prepare(
    source: Path = SOURCE_DEFAULT,
    prior_details: Path = DETAILS_DEFAULT,
    accounts_dir: Path = ACCOUNTS_DEFAULT,
    state_root: Path = STATE_DEFAULT,
    workers: int = WORKERS_DEFAULT,
) -> dict[str, Any]:
    if workers != WORKERS_DEFAULT:
        raise ValueError("当前猎聘账号池固定为 3 个 worker")
    accounts = active_accounts(accounts_dir)
    completed = successful_keys([prior_details] + [worker_paths(state_root, i)["output"] for i in range(1, workers + 1)])
    shards: list[list[dict[str, Any]]] = [[] for _ in range(workers)]
    seen: set[str] = set()
    source_count = 0
    skipped = 0
    for row in iter_jsonl(source):
        source_count += 1
        key = record_key(row)
        if not key:
            raise ValueError(f"source row {source_count} lacks record_id/detail_url")
        if key in seen:
            continue
        seen.add(key)
        if key in completed:
            skipped += 1
            continue
        index = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % workers
        shards[index].append(row)
    for index, rows in enumerate(shards, 1):
        atomic_jsonl(worker_paths(state_root, index)["shard"], rows)
    plan = {
        "site": "liepin",
        "status": "prepared_not_started",
        "source": str(source),
        "prior_details": str(prior_details),
        "workers": workers,
        "gap_min": 0.0,
        "gap_max": 0.3,
        "source_count": source_count,
        "unique_source_count": len(seen),
        "already_successful_count": len(completed),
        "skipped_completed_count": skipped,
        "pending_count": sum(len(rows) for rows in shards),
        "account_files": [path.name for path in accounts],
        "partition_policy": "sha256(record_id) mod 3",
        "worker_specs": [
            {
                "worker": index,
                "account_file": accounts[index - 1].name,
                "shard": str(worker_paths(state_root, index)["shard"]),
                "output": str(worker_paths(state_root, index)["output"]),
                "log": str(worker_paths(state_root, index)["log"]),
                "records": len(shards[index - 1]),
            }
            for index in range(1, workers + 1)
        ],
    }
    state_root.mkdir(parents=True, exist_ok=True)
    atomic_json(state_root / "plan.json", plan)
    return plan


def run_worker(spec: dict[str, Any], cookie_file: Path, smoke_limit: int | None) -> dict[str, Any]:
    output = Path(spec["output"])
    log = Path(spec["log"])
    log.parent.mkdir(parents=True, exist_ok=True)
    # Still slower than the old 0-0.3s defaults, but this path remains high risk.
    command = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "backfill_liepin_details.py"),
        "--source", str(spec["shard"]),
        "--output", str(output),
        "--delay-min", "8",
        "--delay-max", "15",
        "--cookie-file", str(cookie_file),
    ]
    if smoke_limit is not None:
        command.extend(["--limit", str(smoke_limit)])
    with log.open("a", encoding="utf-8") as handle:
        result = subprocess.run(command, cwd=PROJECT_ROOT, stdout=handle, stderr=subprocess.STDOUT, check=False)
    return {"worker": spec["worker"], "returncode": result.returncode, "output": str(output), "log": str(log)}


def run(plan_path: Path, smoke_limit: int | None = None) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf8"))
    if plan.get("status") not in {"prepared_not_started", "running", "smoke_complete"}:
        raise ValueError(f"plan status does not allow run: {plan.get('status')}")
    plan["status"] = "running"
    atomic_json(plan_path, plan)
    account_paths = [ACCOUNTS_DEFAULT / name for name in plan["account_files"]]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run_worker, spec, account_paths[index], smoke_limit) for index, spec in enumerate(plan["worker_specs"])]
        results = [future.result() for future in as_completed(futures)]
    plan["status"] = "smoke_complete" if smoke_limit is not None else "run_finished_or_stopped"
    plan["last_results"] = sorted(results, key=lambda item: int(item["worker"]))
    atomic_json(plan_path, plan)
    print(json.dumps(plan["last_results"], ensure_ascii=False))


def check(plan_path: Path) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf8"))
    print(json.dumps({key: plan.get(key) for key in ("site", "status", "workers", "source_count", "pending_count", "account_files")}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check", "run"))
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--prior-details", type=Path, default=DETAILS_DEFAULT)
    parser.add_argument("--accounts-dir", type=Path, default=ACCOUNTS_DEFAULT)
    parser.add_argument("--state-root", type=Path, default=STATE_DEFAULT)
    parser.add_argument("--workers", type=int, default=WORKERS_DEFAULT)
    parser.add_argument("--smoke-limit", type=int, default=None)
    parser.add_argument(
        "--i-accept-risk",
        action="store_true",
        help="required for run: acknowledge multi-account requests detail risk",
    )
    args = parser.parse_args()
    if args.command == "prepare":
        plan = prepare(args.source, args.prior_details, args.accounts_dir, args.state_root, args.workers)
        print(f"prepared {plan['pending_count']} pending records across 3 shards")
        print("WARNING: prefer tools/run_liepin_browser_single.py for low-risk JD backfill")
        return 0
    plan_path = args.state_root / "plan.json"
    if not plan_path.is_file():
        raise SystemExit("plan not found; run prepare first")
    if args.command == "check":
        return check(plan_path)
    if not args.i_accept_risk:
        raise SystemExit(
            "拒绝运行高风险猎聘并行详情补采。请改用:\n"
            "  py tools\\run_liepin_browser_single.py --max-attempts 20\n"
            "若坚持本路径，必须显式加 --i-accept-risk（仍易触发安全中心）。"
        )
    run(plan_path, args.smoke_limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
