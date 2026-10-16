#!/usr/bin/env python3
"""Open the persistent Liepin Camoufox profile for one manual login."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from camoufox.sync_api import Camoufox
from playwright.sync_api import Error as PlaywrightError


def run(profile: Path, ready_file: Path, url: str) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    ready_file.parent.mkdir(parents=True, exist_ok=True)
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
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        print("Camoufox 登录窗口已打开；完成登录后等待退出信号。", flush=True)
        while True:
            if ready_file.is_file():
                print("收到登录完成信号，保存 Profile 并关闭窗口。", flush=True)
                return 0
            try:
                if page.is_closed():
                    return 0
            except PlaywrightError:
                return 0
            time.sleep(1)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--url", default="https://www.liepin.com/")
    args = parser.parse_args()
    profile = args.profile if args.profile.is_absolute() else project_root / args.profile
    ready_file = args.ready_file if args.ready_file.is_absolute() else project_root / args.ready_file
    return run(profile, ready_file, args.url)


if __name__ == "__main__":
    raise SystemExit(main())
