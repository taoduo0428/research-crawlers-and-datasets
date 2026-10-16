#!/usr/bin/env python3
"""Verify the published research manifests without contacting any crawler websites."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify research code and data manifests")
    parser.add_argument(
        "--manifest-only", action="store_true",
        help="Check paths without downloading Git LFS blobs",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    specs = [
        ("data", root / "metadata/data-manifest.json", "sha256"),
        ("source", root / "metadata/source-manifest.json", "published_sha256"),
    ]
    results = {}
    missing = []
    mismatched = []
    paths = set()
    duplicates = []
    for label, manifest, digest_key in specs:
        collection = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        verified = 0
        for entry in collection:
            relative = entry["path"]
            if relative in paths:
                duplicates.append(relative)
                continue
            paths.add(relative)
            path = root / relative
            if not path.is_file():
                missing.append(relative)
                continue
            if not args.manifest_only:
                actual = sha256(path)
                if actual != entry[digest_key]:
                    mismatched.append(relative)
                    continue
                if label == "data" and path.stat().st_size != entry["bytes"]:
                    mismatched.append(relative)
                    continue
            verified += 1
        results[label] = verified
    print(
        f"verified_data={results.get('data', 0)} "
        f"verified_source={results.get('source', 0)} "
        f"missing={len(missing)} mismatched={len(mismatched)} "
        f"duplicate_paths={len(duplicates)}"
    )
    for path in (missing + mismatched + duplicates)[:20]:
        print("CHECK_FAILED:", path)
    return 0 if not (missing or mismatched or duplicates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
