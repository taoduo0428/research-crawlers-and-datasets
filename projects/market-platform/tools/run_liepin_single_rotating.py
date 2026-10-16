"""DEPRECATED high-risk Liepin detail backfill with account rotation.

Prefer ``tools/run_liepin_browser_single.py`` (one headed profile, manual login).
This path injects Cookie files into requests and still tends to hit the security
center; ``run`` requires ``--i-accept-risk``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "outputs" / "招聘--完整数据" / "猎聘" / "岗位数据.jsonl"
PRIOR = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "猎聘" / "详情补充.jsonl"
OUTPUT = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "猎聘" / "workers" / "worker-1.jsonl"
WORKER_OUTPUTS = PROJECT_ROOT / ".crawler-state" / "recruitment-details" / "猎聘" / "workers"
ACCOUNTS = PROJECT_ROOT / ".crawler-state" / "liepin-accounts"
STATE = PROJECT_ROOT / ".crawler-state" / "liepin-single-rotate"


def rows(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def key(row: dict[str, Any]) -> str:
    return str(row.get("record_id") or row.get("job_id") or row.get("detail_url") or "").strip()


def has_detail(row: dict[str, Any]) -> bool:
    return bool(str(row.get("description_raw") or row.get("jd") or "").strip())


def completed_keys() -> set[str]:
    paths = [PRIOR, *sorted(WORKER_OUTPUTS.glob("worker-*.jsonl"))]
    return {key(row) for path in paths for row in rows(path) if key(row) and has_detail(row)}


def active_accounts() -> list[Path]:
    manifest = json.loads((ACCOUNTS / "accounts-manifest.json").read_text(encoding="utf-8"))
    output: list[Path] = []
    for item in manifest.get("accounts", []):
        if item.get("status") in {"quarantined", "blocked"}:
            continue
        path = ACCOUNTS / str(item.get("file") or "")
        if path.is_file():
            output.append(path)
    if not output:
        raise RuntimeError("no active Liepin accounts")
    return output


def atomic_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
    temporary.replace(path)


def prepare_pending(path: Path) -> int:
    done = completed_keys()
    seen: set[str] = set()
    pending: list[dict[str, Any]] = []
    for row in rows(SOURCE):
        record_id = key(row)
        if not record_id or record_id in seen or record_id in done:
            continue
        seen.add(record_id)
        pending.append(row)
    atomic_jsonl(path, pending)
    return len(pending)


def parse_summary(stdout: str) -> dict[str, Any]:
    for start in (index for index, char in enumerate(stdout) if char == "{"):
        try:
            value = json.loads(stdout[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "written" in value:
            return value
    return {"written": 0, "failed": 1, "parse_error": True}


def run(batch_size: int = 50, max_rounds: int | None = None) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    STATE.mkdir(parents=True, exist_ok=True)
    pending = STATE / "pending.jsonl"
    log = STATE / "run.log"
    pending_count = prepare_pending(pending)
    accounts = active_accounts()
    state: dict[str, Any] = {
        "status": "running",
        "workers": 1,
        "account_rotation": True,
        "accounts": [path.name for path in accounts],
        "pending_count": pending_count,
    }
    (STATE / "status.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    account_index = 0
    stalled_rounds = 0
    rounds = 0
    while pending_count > 0 and (max_rounds is None or rounds < max_rounds):
        account = accounts[account_index % len(accounts)]
        account_index += 1
        rounds += 1
        command = [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "backfill_liepin_details.py"),
            "--source", str(pending),
            "--output", str(OUTPUT),
            "--limit", str(batch_size),
            "--delay-min", "8",
            "--delay-max", "15",
            "--cookie-file", str(account),
            "--proxy-policy", "auto",
        ]
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        summary = parse_summary(result.stdout)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"round": rounds, "account": account.name, "returncode": result.returncode, "summary": summary}, ensure_ascii=False) + "\n")
            if result.stderr:
                handle.write(result.stderr[-4000:])
        written = int(summary.get("written") or 0)
        failed = int(summary.get("failed") or 0)
        if written == 0 and failed == 0:
            stalled_rounds += 1
        elif written == 0 and failed > 0:
            stalled_rounds += 1
        else:
            stalled_rounds = 0
        done = completed_keys()
        pending_count = sum(1 for row in rows(pending) if key(row) and key(row) not in done)
        state.update({"last_account": account.name, "last_summary": summary, "pending_count": pending_count, "rounds": rounds})
        (STATE / "status.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if stalled_rounds >= len(accounts):
            state["status"] = "stopped_no_progress"
            (STATE / "status.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 2
        time.sleep(1)
    state["status"] = "completed" if pending_count == 0 else "stopped_round_limit"
    (STATE / "status.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if pending_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument(
        "--i-accept-risk",
        action="store_true",
        help="required: acknowledge Cookie rotation detail risk",
    )
    args = parser.parse_args()
    if not args.i_accept_risk:
        raise SystemExit(
            "拒绝运行高风险猎聘 Cookie 轮换补采。请改用:\n"
            "  py tools\\run_liepin_browser_single.py --max-attempts 20\n"
            "若坚持本路径，必须显式加 --i-accept-risk。"
        )
    return run(args.batch_size, args.max_rounds)


if __name__ == "__main__":
    raise SystemExit(main())
