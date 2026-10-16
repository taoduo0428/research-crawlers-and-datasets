from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from drv_crawler.liepin.auth import capture_login_state


class FakePlaywrightError(Exception):
    pass


class FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count
        self.first = self

    def count(self) -> int:
        return self._count

    def click(self, **_: object) -> None:
        return None


class FakePage:
    def __init__(self, verified: bool) -> None:
        self.verified = verified

    def goto(self, *_: object, **__: object) -> None:
        return None

    def locator(self, selector: str) -> FakeLocator:
        if selector == "#header-quick-menu-login":
            return FakeLocator(1)
        return FakeLocator(1 if self.verified else 0)

    def wait_for_timeout(self, _: int) -> None:
        return None


class FakeContext:
    def __init__(self, verified: bool, *, fail_save: bool = False) -> None:
        self.page = FakePage(verified)
        self.fail_save = fail_save
        self.cookie_calls = 0
        self.storage_state_calls = 0

    def new_page(self) -> FakePage:
        return self.page

    def cookies(self) -> list[dict[str, str]]:
        self.cookie_calls += 1
        if self.cookie_calls == 1:
            return [{"name": "anonymous", "value": "1"}]
        return [
            {"name": "anonymous", "value": "1"},
            {"name": "authenticated", "value": "2"},
        ]

    def storage_state(self, *, path: str) -> None:
        self.storage_state_calls += 1
        Path(path).write_text(
            json.dumps({"cookies": [{"name": "new", "value": "state"}]}),
            encoding="utf-8",
        )
        if self.fail_save:
            raise OSError("simulated storage-state failure")


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    def new_context(self, **_: object) -> FakeContext:
        return self.context

    def close(self) -> None:
        self.closed = True


class FakePlaywrightManager:
    def __init__(self, browser: FakeBrowser) -> None:
        self.playwright = SimpleNamespace(
            chromium=SimpleNamespace(launch=lambda **_: browser)
        )

    def __enter__(self) -> object:
        return self.playwright

    def __exit__(self, *_: object) -> None:
        return None


def fake_playwright_modules(browser: FakeBrowser) -> dict[str, ModuleType]:
    package = ModuleType("playwright")
    package.__path__ = []  # type: ignore[attr-defined]
    sync_api = ModuleType("playwright.sync_api")
    sync_api.Error = FakePlaywrightError  # type: ignore[attr-defined]
    sync_api.sync_playwright = lambda: FakePlaywrightManager(browser)  # type: ignore[attr-defined]
    return {"playwright": package, "playwright.sync_api": sync_api}


class AuthTests(unittest.TestCase):
    def capture(
        self,
        path: Path,
        context: FakeContext,
        *,
        verified: bool,
    ) -> dict[str, object]:
        browser = FakeBrowser(context)
        monotonic = [0.0, 1.0] if verified else [0.0, 11.0]
        with (
            patch.dict(sys.modules, fake_playwright_modules(browser)),
            patch("drv_crawler.liepin.auth.time.monotonic", side_effect=monotonic),
        ):
            try:
                return capture_login_state(path, wait_seconds=10)
            finally:
                self.assertTrue(browser.closed)

    def test_unverified_login_does_not_overwrite_existing_state(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "storage_state.json"
            original = '{"cookies":[{"name":"old","value":"state"}]}'
            path.write_text(original, encoding="utf-8")
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text("stale", encoding="utf-8")
            context = FakeContext(False)

            result = self.capture(path, context, verified=False)

            self.assertFalse(result["login_verified"])
            self.assertEqual(original, path.read_text(encoding="utf-8"))
            self.assertEqual(0, context.storage_state_calls)
            self.assertFalse(temporary.exists())

    def test_unverified_first_login_does_not_create_state(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "storage_state.json"
            context = FakeContext(False)

            result = self.capture(path, context, verified=False)

            self.assertFalse(result["login_verified"])
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_storage_state_failure_preserves_old_state_and_cleans_temp(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "storage_state.json"
            original = '{"cookies":[{"name":"old","value":"state"}]}'
            path.write_text(original, encoding="utf-8")
            context = FakeContext(True, fail_save=True)

            with self.assertRaisesRegex(OSError, "simulated storage-state failure"):
                self.capture(path, context, verified=True)

            self.assertEqual(original, path.read_text(encoding="utf-8"))
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_atomic_replace_failure_preserves_old_state_and_cleans_temp(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "storage_state.json"
            original = '{"cookies":[{"name":"old","value":"state"}]}'
            path.write_text(original, encoding="utf-8")
            context = FakeContext(True)
            browser = FakeBrowser(context)
            with (
                patch.dict(sys.modules, fake_playwright_modules(browser)),
                patch(
                    "drv_crawler.liepin.auth.time.monotonic",
                    side_effect=[0.0, 1.0],
                ),
                patch(
                    "drv_crawler.liepin.auth.atomic_replace",
                    side_effect=PermissionError("locked"),
                ),
            ):
                with self.assertRaises(PermissionError):
                    capture_login_state(path, wait_seconds=10)

            self.assertTrue(browser.closed)
            self.assertEqual(original, path.read_text(encoding="utf-8"))
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_verified_login_atomically_replaces_state(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "storage_state.json"
            path.write_text('{"cookies":[]}', encoding="utf-8")
            context = FakeContext(True)

            result = self.capture(path, context, verified=True)

            self.assertTrue(result["login_verified"])
            self.assertEqual(2, result["cookie_count"])
            self.assertEqual("new", json.loads(path.read_text(encoding="utf-8"))["cookies"][0]["name"])
            self.assertEqual(1, context.storage_state_calls)
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
