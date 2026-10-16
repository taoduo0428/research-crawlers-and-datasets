#!/usr/bin/env python3
"""Validate that a Camoufox profile can render one known Liepin JD."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from camoufox.sync_api import Camoufox

from tools.run_liepin_browser_single import challenge, parse_detail_description


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    profile = args.profile if args.profile.is_absolute() else PROJECT_ROOT / args.profile
    with Camoufox(
        headless=False,
        persistent_context=True,
        user_data_dir=str(profile.resolve()),
        locale="zh-CN",
        window=(1365, 860),
        block_images=False,
        humanize=False,
    ) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(800)
        html = page.content()
        jd = parse_detail_description(html) or ""
        result = {
            "requested_url": args.url,
            "final_url": page.url,
            "jd_length": len(jd),
            "challenge": challenge(page),
            "html_length": len(html),
        }
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if jd else 2


if __name__ == "__main__":
    raise SystemExit(main())
