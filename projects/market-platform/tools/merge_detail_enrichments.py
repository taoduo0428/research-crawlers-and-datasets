"""Atomically merge resumable recruitment detail JSONL shards by record_id."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} 不是合法 JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} 不是 JSON 对象")
            yield value


def record_key(row: dict[str, Any]) -> str:
    return str(row.get("record_id") or row.get("job_id") or "").strip()


def detail_text(row: dict[str, Any]) -> str:
    return str(row.get("jd") or row.get("description_raw") or "").strip()


def detail_url(row: dict[str, Any]) -> str:
    return str(row.get("detail_url") or row.get("job_link") or "").split("?", 1)[0].strip()


def merge_rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    seen_urls: dict[str, str] = {}
    order: list[str] = []
    for path in paths:
        for row in iter_jsonl(path):
            key = record_key(row)
            if not key:
                raise ValueError(f"{path} 中存在缺少 record_id/job_id 的记录")
            new_url = detail_url(row)
            old_url = seen_urls.get(key, "")
            if old_url and new_url and old_url != new_url:
                raise ValueError(f"{key} 对应多个详情 URL，拒绝静默合并")
            if new_url and not old_url:
                seen_urls[key] = new_url
            # Empty/challenge rows are retry evidence, not a completed detail.
            if not detail_text(row):
                continue
            current = merged.get(key)
            if current is None:
                merged[key] = row
                order.append(key)
                continue
            if detail_text(row) and not detail_text(current):
                merged[key] = row
    return [merged[key] for key in order]


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False, dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp",
    ) as handle:
        temporary = Path(handle.name)
        try:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=None)
    parser.add_argument("--shard", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inputs = ([args.base] if args.base else []) + args.shard
    rows = merge_rows(path for path in inputs if path is not None)
    count = atomic_write_jsonl(args.output, rows)
    print(json.dumps({"output": str(args.output), "records": count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
