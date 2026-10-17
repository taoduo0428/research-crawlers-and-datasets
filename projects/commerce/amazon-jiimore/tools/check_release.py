#!/usr/bin/env python3
"""Validate the public Amazon source distribution without contacting websites.

Only files staged/tracked by Git count for the privacy allowlist; local users
may keep gitignored config files or Edge profiles without failing the test.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[2]
PREFIX = "projects/commerce/amazon-jiimore/source/"
ALLOWED = {
    "README.md", "amazon.py", "amazon_extract.py", "jiimore.py",
    "sellersprite.py", "project_config.py", "requirements.txt",
    "config.example.json", "sellersprite_cookies.example.json",
    "data/README.md", "data/ASIN.txt",
    "tests/README.md", "tests/fixtures/评论详情页.html",
    "tests/test_amazon_extract.py", "tests/test_amazon_runtime.py",
    "tests/test_jiimore.py", "tests/test_project_config.py",
    "tests/test_sellersprite.py",
}
REQUIRED_HISTORICAL = [
    "amazon/products.csv", "amazon/tags.csv", "jiimore/asin.csv",
    "jiimore/weekly_clicks.csv", "sellersprite/monthly_sales.csv",
    "sellersprite/monthly_reviews.csv", "sellersprite/keepa_history.csv",
]
CHECK_READMES = [
    REPO/"README.md", PROJECT/"README.md",
    PROJECT/"source"/"README.md", PROJECT/"source"/"data"/"README.md",
    PROJECT/"source"/"tests"/"README.md", PROJECT/"data"/"README.md",
    PROJECT/"tools"/"README.md", PROJECT/"tests"/"README.md",
    PROJECT/"docs"/"README.md",
]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def staged_and_tracked() -> set[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO,
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout
    return {s.decode("utf-8") for s in output.split(b"\x00") if s}


def main() -> int:
    issues: list[str] = []
    if REPO.name != "research-crawlers-and-datasets":
        raise RuntimeError("Unexpected repository root")
    tracked = staged_and_tracked()
    actual = {path.removeprefix(PREFIX) for path in tracked if path.startswith(PREFIX)}
    missing = ALLOWED - actual
    unexpected = actual - ALLOWED
    for filename in sorted(missing):
        issues.append("missing tracked source: " + filename)
    for filename in sorted(unexpected):
        issues.append("unexpected tracked source: " + filename)
    forbidden = ("config.local.json", "sellersprite_cookies.json", ".edge-amazon-profile",
                 "__pycache__", "/.venv/", "/.ai-bridge/")
    for filename in sorted(tracked):
        if filename.startswith(PREFIX) and any(x in filename for x in forbidden):
            issues.append("forbidden tracked file: " + filename)
    template = json.loads((PROJECT/"source"/"config.example.json").read_text(encoding="utf-8"))
    if template.get("jiimore", {}).get("phone_num") or template.get("jiimore", {}).get("password"):
        issues.append("real credentials in config.example.json")
    cookie_example = json.loads(
        (PROJECT/"source"/"sellersprite_cookies.example.json").read_text(encoding="utf-8"))
    if not cookie_example or any(not str(v).strip().startswith(("填写", "如有")) for v in cookie_example.values()):
        issues.append("cookie example may contain actual session material")
    sample = (PROJECT/"source"/"tests"/"fixtures"/"评论详情页.html").read_text(encoding="utf-8")
    if "SYNTHETIC" not in sample and "synthetic" not in sample.lower():
        issues.append("review fixture is not clearly synthetic")
    for name in REQUIRED_HISTORICAL:
        if not (PROJECT/"data"/name).is_file():
            issues.append("missing historical research table: " + name)
    asin_txt = (PROJECT/"source"/"data"/"ASIN.txt").read_text(encoding="utf-8-sig")
    asins = [value.strip() for value in asin_txt.splitlines() if value.strip()]
    if not asins or any(not re.fullmatch(r"[A-Z0-9]{10}", asin) for asin in asins):
        issues.append("ASIN.txt contains an invalid line")
    if len(set(asins)) != len(asins):
        issues.append("ASIN.txt contains duplicate ASINs")
    with (PROJECT/"data"/"jiimore"/"asin.csv").open(encoding="utf-8-sig", newline="") as handle:
        historical_asins = {str(row.get("ASIN") or "").strip().upper()
                            for row in csv.DictReader(handle) if row.get("ASIN")}
    unique_asins = set(asins)
    new_asins = unique_asins - historical_asins
    if not historical_asins.issubset(unique_asins):
        issues.append("Published ASIN.txt unexpectedly dropped older history ASINs")
    checked_links = 0
    for markdown in CHECK_READMES:
        if not markdown.is_file():
            issues.append("missing README: " + str(markdown.relative_to(REPO)))
            continue
        raw_text = markdown.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(raw_text):
            raw = match.group(1).strip().split()[0].strip("<>")
            if not raw or raw.startswith(("#", "http://", "https://", "mailto:", "data:")):
                continue
            file_target = unquote(raw.split("#", 1)[0].split("?",1)[0])
            target = (markdown.parent / file_target).resolve()
            checked_links += 1
            if not target.is_relative_to(REPO) or not target.exists():
                issues.append("broken link in " + str(markdown.relative_to(REPO)) +
                              ": " + file_target)
    print(f"amazon_release_checked: tracked_source={len(actual)} "
          f"expected_source={len(ALLOWED)} existing_asins={len(asins)} "
          f"historical_asins={len(historical_asins)} "
          f"new_vs_historical={len(new_asins)} "
          f"historical_tables={len(REQUIRED_HISTORICAL)} readme_links={checked_links} "
          f"issues={len(issues)}")
    for issue in issues:
        print("CHECK_FAILED:", issue)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
