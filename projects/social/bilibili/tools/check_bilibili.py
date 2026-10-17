#!/usr/bin/env python3
"""Read-only checks for the Bilibili research CSVs and a few public API records.

By default this tool never uses the network or modifies research data.
--online-samples N only checks N publicly accessible video metadata endpoints.
No Cookie, user session, or historic credentials are used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        if not {"BV号", "CID"}.issubset(set(header)):
            raise ValueError(f"Missing BV号 or CID header: {path}")
        return list(reader)


def key(row: dict[str, str]) -> tuple[str, str]:
    return (str(row.get("BV号", "")).strip(),
            str(row.get("CID", "")).strip())


def count_unique(rows: list[dict[str, str]]) -> int:
    return len({key(row) for row in rows})


def checked_pairs(data_dir: Path, left: str, right: str) -> dict:
    a = read_csv(data_dir / left)
    b = read_csv(data_dir / right)
    ka = Counter(key(row) for row in a)
    kb = Counter(key(row) for row in b)
    return {
        "first_rows": len(a),
        "second_rows": len(b),
        "same_bv_cid_multiset": ka == kb,
    }


def analyze(data_dir: Path) -> dict:
    first = read_csv(data_dir / "第一次字幕" / "结果.csv")
    second = read_csv(data_dir / "结果.csv")
    absent = read_csv(data_dir / "空.csv")
    keyed = defaultdict(set)
    for row in first + second:
        subtitle = (row.get("字幕") or "").strip()
        if subtitle:
            keyed[key(row)].add(subtitle)
    union = set(key(x) for x in first + second)
    has_subtitle = sum(bool(keyed.get(k)) for k in union)
    second_empty = [row for row in second if not (row.get("字幕") or "").strip()]
    results = {
        "first": {
            "rows": len(first),
            "unique_bv_cid": count_unique(first),
            "with_subtitle": sum(bool((r.get("字幕") or "").strip()) for r in first),
        },
        "second": {
            "rows": len(second),
            "unique_bv_cid": count_unique(second),
            "with_subtitle": sum(bool((r.get("字幕") or "").strip()) for r in second),
        },
        "joined": {
            "unique_bv_cid": len(union),
            "with_subtitle": has_subtitle,
            "without_subtitle": len(union) - has_subtitle,
            "different_nonempty_subtitles_same_key": sum(len(v) > 1 for v in keyed.values()),
        },
        "empty_subset": {
            "rows": len(absent),
            "derived_empty_rows": len(second_empty),
            "bv_cid_multiset_matches": Counter(map(key, absent)) == Counter(map(key, second_empty)),
            "utf8_headers_ok": bool(absent and "BV号" in absent[0]),
        },
        "linked_files": {
            "first_metadata_to_subtitle": checked_pairs(
                data_dir, "第一次字幕/merged_unique.csv", "第一次字幕/结果.csv"
            ),
            "small_metadata_to_subtitle": checked_pairs(
                data_dir, "视频基本信息.csv", "最后.csv"
            ),
            "wristband_metadata_to_subtitle": checked_pairs(
                data_dir, "视频基本信息（手环）.csv", "手环.csv"
            ),
            "keyboard_metadata_to_subtitle": checked_pairs(
                data_dir, "视频基本信息（键盘）.csv", "键盘.csv"
            ),
            "second_metadata_to_subtitle": checked_pairs(
                data_dir, "supplement/新增数据.csv", "结果.csv"
            ),
        },
    }
    return results


def public_json(endpoint: str, params: dict[str, str]) -> dict:
    url = "https://api.bilibili.com" + endpoint + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (research CSV verification)",
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=9) as response:
        payload = response.read(1_500_000)
        return json.loads(payload)


def online_samples(data_dir: Path, amount: int) -> list[dict]:
    rows = read_csv(data_dir / "视频基本信息.csv")
    results = []
    for row in rows[:amount]:
        bv, cid = key(row)
        item = {"bvid": bv, "archived_cid": cid}
        try:
            view = public_json("/x/web-interface/view", {"bvid": bv})
            item["view_code"] = view.get("code")
            if view.get("code") == 0:
                info = view.get("data") or {}
                item["bvid_matches"] = info.get("bvid") == bv
                item["cid_matches"] = str(info.get("cid")) == cid
                player = public_json("/x/player/v2", {"bvid": bv, "cid": cid})
                item["player_code"] = player.get("code")
                subs = (((player.get("data") or {}).get("subtitle") or {})
                        .get("subtitles") or [])
                item["current_subtitle_count"] = len(subs)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            item["error_type"] = type(exc).__name__
        results.append(item)
        time.sleep(.3)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check archived Bilibili CSVs; optionally verify 1-5 public API records"
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--online-samples", type=int, default=0, choices=range(6),
                        metavar="N", help="Request 0-5 sample BV/CID pairs from public Bilibili API")
    args = parser.parse_args()
    report = {"offline": analyze(args.data_root), "network_used": args.online_samples > 0}
    if args.online_samples:
        report["online"] = online_samples(args.data_root, args.online_samples)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["offline"]["empty_subset"]["bv_cid_multiset_matches"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
