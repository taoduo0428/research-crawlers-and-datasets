#!/usr/bin/env python3
"""Merge all Liepin JD shards into one canonical, source-ordered JSONL file."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_liepin_browser_single import (
    API_OUTPUTS,
    PRIOR_DEFAULT,
    SOURCE_DEFAULT,
    WORKER_OUTPUTS,
    has_detail,
    iter_rows,
    record_key,
)


def detail_length(row: dict[str, Any]) -> int:
    return len(str(row.get("description_raw") or row.get("jd") or "").strip())


def merge_rows(
    source: Path,
    detail_paths: Iterable[Path],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    best: dict[str, dict[str, Any]] = {}
    detail_rows = 0
    for path in detail_paths:
        if not path.is_file():
            continue
        for row in iter_rows(path):
            key = record_key(row)
            if not key or not has_detail(row):
                continue
            detail_rows += 1
            current = best.get(key)
            if current is None or detail_length(row) > detail_length(current):
                best[key] = row

    merged: list[dict[str, Any]] = []
    for source_row in iter_rows(source):
        key = record_key(source_row)
        if key in best:
            merged.append(best[key])
    return merged, {
        "input_detail_rows": detail_rows,
        "unique_detail_rows": len(best),
        "merged_source_rows": len(merged),
        "duplicates_removed": detail_rows - len(best),
    }


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as handle:
        temporary = Path(handle.name)
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def default_detail_paths(output: Path) -> list[Path]:
    paths = [PRIOR_DEFAULT]
    paths.extend(sorted(WORKER_OUTPUTS.glob("worker-*.jsonl")))
    paths.extend(sorted(API_OUTPUTS.glob("*.jsonl")))
    return [path for path in paths if path.resolve() != output.resolve()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--output", type=Path, default=PRIOR_DEFAULT)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"source does not exist: {args.source}")

    detail_paths = default_detail_paths(args.output)
    if args.output.is_file():
        detail_paths.insert(0, args.output)
    merged, stats = merge_rows(args.source, detail_paths)
    atomic_write_jsonl(args.output, merged)
    print(json.dumps({**stats, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
