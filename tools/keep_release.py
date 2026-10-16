"""Build a strictly allowlisted Keep publication without touching original capture files.

This is maintainer-only code. It intentionally discards media, user names, URLs,
free-form text, unknown IDs, headers and arbitrary API response fields.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

from normalize_keep import collect


def safe_scalar(value):
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= 32 and not re.search(r"(?i)(https?://|www\.|@|\\|/)", value):
            return value
    return None


def project_profile(obj, anonymous_id):
    source = (((obj.get("data") or {}).get("headInfo") or {})
              .get("userBasicInfo") or {}) if isinstance(obj, dict) else {}
    if not isinstance(source, dict):
        source = {}
    raw_id = source.get("id")
    return {
        "data": {
            "headInfo": {
                "userBasicInfo": {
                    "id": anonymous_id("keep-profile:" + str(raw_id)) if raw_id else None,
                    "gender": safe_scalar(source.get("gender")),
                    "keepValue": safe_scalar(source.get("keepValue")),
                    "kgLevel": safe_scalar(source.get("kgLevel")),
                    "memberStatus": safe_scalar(source.get("memberStatus")),
                }
            }
        }
    }


def project_activities(obj, anonymous_id):
    source = obj.get("data") if isinstance(obj, dict) else None
    output = []
    for section in source if isinstance(source, list) else []:
        info = section.get("info") if isinstance(section, dict) else {}
        entries = info.get("entries") if isinstance(info, dict) else None
        collected = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("id")
            author = entry.get("author")
            auid = author.get("_id") if isinstance(author, dict) else None
            comments = entry.get("sampleComments")
            minimized_comments = []
            for item in comments if isinstance(comments, list) else []:
                if not isinstance(item, dict):
                    continue
                raw_id = item.get("id")
                minimized_comments.append({
                    "id": anonymous_id("keep-comment:" + str(raw_id))
                    if raw_id else None
                })
            collected.append({
                "id": anonymous_id("keep-post:" + str(pid)) if pid else None,
                "author": {
                    "_id": anonymous_id("keep-profile:" + str(auid)) if auid else None
                },
                "type": safe_scalar(entry.get("type")),
                "state": safe_scalar(entry.get("state")),
                "internalShareCount": safe_scalar(entry.get("internalShareCount")),
                "sampleComments": minimized_comments,
            })
        output.append({"info": {"entries": collected}})
    return {"data": output}


def publish(keep_root: Path, output_root: Path, tracked: list,
            digest, anonymous_id) -> dict:
    dest_root = output_root / "projects/social/keep"
    phase1 = dest_root / "data/phase1_anonymized"
    counts = collections.Counter()
    for label, directory in [
        ("profiles", keep_root / "用户数据"),
        ("activities", keep_root / "动态爬取/chenggong1"),
    ]:
        for index, source in enumerate(sorted(directory.glob("*.json")), 1):
            try:
                obj = json.loads(source.read_text(encoding="utf-8-sig", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            minimized = (
                project_profile(obj, anonymous_id) if label == "profiles"
                else project_activities(obj, anonymous_id)
            )
            destination = phase1 / label / f"{index:04d}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(minimized, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            tracked.append({
                "path": destination.relative_to(output_root).as_posix(),
                "origin": "Private Keep capture; original filename withheld",
                "source_sha256": None,
                "sha256": digest(destination),
                "bytes": destination.stat().st_size,
                "records": None,
                "privacy_processing": "Whitelisted anonymized research fields only",
                "dataset": "keep-stage1-minimized",
            })
            counts[label] += 1

    stats = collect(phase1, dest_root / "data/phase2")
    for filename, kind in (
        ("profiles.csv", "keep-profiles"),
        ("posts.csv", "keep-posts"),
        ("comments.csv", "keep-comment-index"),
    ):
        destination = dest_root / "data/phase2" / filename
        tracked.append({
            "path": destination.relative_to(output_root).as_posix(),
            "origin": "Derived from minimized Keep phase-1 JSON",
            "source_sha256": None,
            "sha256": digest(destination),
            "bytes": destination.stat().st_size,
            "records": stats[filename.removesuffix(".csv")],
            "privacy_processing": "Whitelisted research features and anonymous IDs",
            "dataset": kind,
        })
    return {"phase1_files": dict(counts), **stats}
