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


def source_sha256_matches(path: Path, expected: str) -> bool:
    """Check a Git-published source with portable LF/CRLF handling.

    The manifest stores SHA-256 of *Git blob* bytes. On Windows, Git may
    check out textual sources using CRLF while Linux/macOS use LF. The
    fallback accepts only a newline-encoding difference (not other edits);
    binary / UTF-16 files containing NUL bytes stay strictly byte-for-byte.
    Historical research DATA is never normalized by this function.
    """
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() == expected:
        return True
    if b"\x00" in content or b"\r\n" not in content:
        return False
    lf_content = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(lf_content).hexdigest() == expected


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
                expected = entry[digest_key]
                if label == "source":
                    valid_digest = source_sha256_matches(path, expected)
                else:
                    # Published research data and LFS objects remain strictly
                    # byte-exact, including their line endings and file size.
                    valid_digest = sha256(path) == expected
                if not valid_digest:
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
