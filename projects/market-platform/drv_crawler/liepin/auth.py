from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

from .errors import ConfigurationError, LiepinCrawlerError
from .state import atomic_replace


def capture_login_state(
    storage_state_path: Path,
    *,
    proxy_url: str | None = None,
    wait_seconds: int = 300,
) -> dict[str, Any]:
    """Open a bounded headed login handoff and persist Playwright state."""
    if not 10 <= wait_seconds <= 900:
        raise ConfigurationError("wait_seconds must be between 10 and 900")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ConfigurationError(
            "Playwright is required for the login command"
        ) from exc

    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = storage_state_path.with_suffix(storage_state_path.suffix + ".tmp")
    verified = False
    cookie_count = 0
    browser = None
    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, Any] = {"headless": False}
            if proxy_url:
                launch_options["proxy"] = {"server": proxy_url}
            browser = playwright.chromium.launch(**launch_options)
            try:
                context_options: dict[str, Any] = {
                    "locale": "zh-CN",
                    "viewport": {"width": 1365, "height": 860},
                }
                if storage_state_path.exists():
                    try:
                        json.loads(storage_state_path.read_text(encoding="utf-8"))
                        context_options["storage_state"] = os.fspath(storage_state_path)
                    except (OSError, json.JSONDecodeError):
                        pass
                context = browser.new_context(**context_options)
                page = context.new_page()
                page.goto(
                    "https://www.liepin.com/",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                baseline_cookie_count = len(context.cookies())
                login_control = page.locator("#header-quick-menu-login")
                if login_control.count():
                    login_control.first.click(timeout=5_000)

                deadline = time.monotonic() + wait_seconds
                while time.monotonic() < deadline:
                    page.wait_for_timeout(1_000)
                    cookies = context.cookies()
                    cookie_count = len(cookies)
                    logged_in_selectors = (
                        ".header-quick-menu-login",
                        '[data-nick="user-info"]',
                        'text="我的猎聘"',
                    )
                    selector_match = any(
                        page.locator(selector).count()
                        for selector in logged_in_selectors
                    )
                    cookie_growth = cookie_count >= baseline_cookie_count + 3
                    if selector_match or cookie_growth:
                        verified = True
                        break

                if verified:
                    context.storage_state(path=os.fspath(temporary))
            finally:
                browser.close()
        if verified:
            atomic_replace(temporary, storage_state_path)
    except PlaywrightError as exc:
        raise LiepinCrawlerError(f"Liepin browser login failed: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "storage_state_path": os.fspath(storage_state_path.resolve()),
        "cookie_count": cookie_count,
        "login_verified": verified,
    }
