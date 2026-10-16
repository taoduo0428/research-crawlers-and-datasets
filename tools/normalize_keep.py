#!/usr/bin/env python3
"""Rebuild the second Keep research stage from MINIMIZED, pseudonymous phase-1 JSON.

No network, no private seed, no raw account data required. Output is deterministic:
filenames, records, and synthetic comment IDs are stable for a fixed input tree.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
from pathlib import Path


def synthetic_comment_id(post_id: str, filename: str, group_index: int,
                         entry_index: int, comment_index: int) -> str:
    """Domain-separated, nonreversible identifier derived only from anonymous inputs."""
    raw = (
        f"keep-comment-v2|{post_id}|{filename}|{group_index}|"
        f"{entry_index}|{comment_index}"
    ).encode("utf-8")
    return base64.b32encode(hashlib.sha256(raw).digest()[:12]).decode("ascii").rstrip("=")


def _write_csv(path: Path, fields: list[str], records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in fields})


def collect(src: Path, output: Path) -> dict[str, int]:
    for phase in ("profiles", "activities"):
        if not (src / phase).is_dir():
            raise FileNotFoundError(f"Missing sanitized Keep phase: {src / phase}")

    profiles: dict[str, dict] = {}
    posts: dict[str, dict] = {}
    comments: list[dict] = []

    for file in sorted((src / "profiles").glob("*.json")):
        obj = json.loads(file.read_text(encoding="utf-8"))
        profile = (((obj.get("data") or {}).get("headInfo") or {})
                   .get("userBasicInfo") or {})
        if not isinstance(profile, dict):
            continue
        uid = profile.get("id")
        if not isinstance(uid, str) or not uid:
            continue
        profiles[uid] = {
            "profile_id": uid,
            "gender": profile.get("gender"),
            "keep_value": profile.get("keepValue"),
            "kg_level": profile.get("kgLevel"),
            "member_status": profile.get("memberStatus"),
        }

    for file in sorted((src / "activities").glob("*.json")):
        obj = json.loads(file.read_text(encoding="utf-8"))
        for group_index, group in enumerate(obj.get("data") or []):
            info = group.get("info") if isinstance(group, dict) else None
            entries = (info.get("entries") if isinstance(info, dict) else None)
            if not isinstance(entries, list):
                continue
            for entry_index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("id")
                if not isinstance(pid, str) or not pid:
                    continue
                author = entry.get("author") or {}
                candidate = {
                    "post_id": pid,
                    "author_id": author.get("_id") if isinstance(author, dict) else None,
                    "type": entry.get("type"),
                    "state": entry.get("state"),
                    "internal_share_count": entry.get("internalShareCount"),
                }
                # A post can appear in several capture files. Pick the most complete
                # version and break ties deterministically, never by filesystem order.
                old = posts.get(pid)
                def quality(row: dict) -> tuple[int, str]:
                    return (
                        sum(row.get(k) not in (None, "") for k in candidate if k != "post_id"),
                        json.dumps(row, ensure_ascii=False, sort_keys=True),
                    )
                if old is None or quality(candidate) > quality(old):
                    posts[pid] = candidate

                sampled = entry.get("sampleComments") or []
                if not isinstance(sampled, list):
                    continue
                for comment_index, comment in enumerate(sampled):
                    if not isinstance(comment, dict):
                        continue
                    comment_id = comment.get("id")
                    if not isinstance(comment_id, str) or not comment_id:
                        comment_id = synthetic_comment_id(
                            pid, file.name, group_index, entry_index, comment_index
                        )
                    comments.append({
                        "comment_id": comment_id,
                        "post_id": pid,
                        "sample_comment_index": comment_index,
                    })

    profile_rows = [profiles[k] for k in sorted(profiles)]
    post_rows = [posts[k] for k in sorted(posts)]
    comment_rows = sorted(comments, key=lambda r:
                          (r["post_id"], r["comment_id"], r["sample_comment_index"]))

    _write_csv(output / "profiles.csv",
               ["profile_id", "gender", "keep_value", "kg_level", "member_status"],
               profile_rows)
    _write_csv(output / "posts.csv",
               ["post_id", "author_id", "type", "state", "internal_share_count"],
               post_rows)
    _write_csv(output / "comments.csv",
               ["comment_id", "post_id", "sample_comment_index"],
               comment_rows)
    return {
        "profiles": len(profile_rows),
        "posts": len(post_rows),
        "comments": len(comment_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild Keep phase-2 CSV from minimized, anonymized phase-1 JSON"
    )
    base = Path(__file__).resolve().parents[1] / "projects/social/keep/data"
    parser.add_argument("--input", type=Path, default=base / "phase1_anonymized")
    parser.add_argument(
        "--output", type=Path, default=base / "phase2",
        help="Output directory; specify a temporary path to avoid overwriting tracked data",
    )
    args = parser.parse_args()
    result = collect(args.input, args.output)
    print(" ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
