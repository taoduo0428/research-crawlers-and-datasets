from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import Error as PlaywrightError

from drv_crawler.browser import (
    _acquire_storage_state_from_external_edge,
    _boss_login_ready,
    _epwk_task_list_ready,
    _profile_domain_cookie_count,
    acquire_storage_state,
)
from drv_crawler.cli import build_parser, main
from drv_crawler.core import SessionRequiredError


class FakeConfig:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def site(self, name: str) -> dict[str, object]:
        if name != "boss":
            raise AssertionError(name)
        return {
            "auth_url": "https://www.zhipin.com/web/user/",
            "storage_state": str(self.state_path),
        }

    def section(self, name: str) -> dict[str, object]:
        if name == "playwright":
            return {"navigation_timeout_ms": 1_000}
        return {}


class FakeExternalConfig:
    def __init__(self, profile_dir: Path) -> None:
        self.profile_dir = profile_dir

    def site(self, name: str) -> dict[str, object]:
        if name != "boss":
            raise AssertionError(name)
        return {
            "auth_debug_port": 9333,
            "auth_profile_dir": str(self.profile_dir),
            "start_url": "https://www.zhipin.com/web/geek/jobs",
        }

    def section(self, name: str) -> dict[str, object]:
        if name == "playwright":
            return {"navigation_timeout_ms": 1_000}
        return {}


class FakeEpwkConfig:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def site(self, name: str) -> dict[str, object]:
        if name != "epwk":
            raise AssertionError(name)
        return {
            "start_url": "https://task.epwk.com/?o=7",
            "storage_state": str(self.state_path),
        }

    def section(self, name: str) -> dict[str, object]:
        if name == "playwright":
            return {"navigation_timeout_ms": 1_000}
        return {}


class FakeLocator:
    def inner_text(self, *, timeout: int) -> str:
        return "职位列表"


class FakeEpwkLocator(FakeLocator):
    def count(self) -> int:
        return 2


class FakePage:
    url = "https://www.zhipin.com/web/geek/jobs"

    def goto(self, *_: object, **__: object) -> None:
        return None

    def set_content(self, _: str) -> None:
        return None

    def evaluate(self, *_: object) -> None:
        return None

    def wait_for_load_state(self, *_: object, **__: object) -> None:
        return None

    def bring_to_front(self) -> None:
        return None

    def wait_for_timeout(self, _: int) -> None:
        return None

    def is_closed(self) -> bool:
        return False

    def locator(self, _: str) -> FakeLocator:
        return FakeLocator()


class FakeEpwkPage(FakePage):
    url = "https://task.epwk.com/?o=7"

    def goto(self, url: str, *_: object, **__: object) -> None:
        self.url = url

    def locator(self, selector: str) -> FakeLocator:
        if selector == ".content-lists .itemblock":
            return FakeEpwkLocator()
        return FakeLocator()


class FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeNavigationFailurePage(FakeEpwkPage):
    def goto(self, url: str, *_: object, **__: object) -> None:
        raise PlaywrightError("net::ERR_HTTP_RESPONSE_CODE_FAILURE")


class FakeProtectedPage(FakePage):
    def __init__(
        self, target_url: str = "https://www.zhipin.com/web/geek/jobs"
    ) -> None:
        self.url = "about:blank"
        self.target_url = target_url
        self.goto_calls: list[str] = []

    def goto(self, url: str, *_: object, **__: object) -> None:
        self.goto_calls.append(url)
        self.url = self.target_url


class FakeProtectedContext:
    def __init__(
        self, verify_url: str = "https://www.zhipin.com/web/geek/jobs"
    ) -> None:
        self.login_page = FakeProtectedPage()
        self.verify_page = FakeProtectedPage(verify_url)
        self.pages = [self.login_page]
        self.init_scripts: list[str] = []
        self.closed = False

    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def new_page(self) -> FakeProtectedPage:
        return self.verify_page

    def close(self) -> None:
        self.closed = True


class FakePageEvent:
    def __init__(self, page: FakePage) -> None:
        self.value = page

    def __enter__(self) -> FakePageEvent:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeContext:
    def __init__(self, *, fail_save: bool = False) -> None:
        self.fail_save = fail_save
        self.init_scripts: list[str] = []
        self.login_page = FakePage()

    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def new_page(self) -> FakePage:
        return FakePage()

    def expect_page(self, **_: object) -> FakePageEvent:
        return FakePageEvent(self.login_page)

    def storage_state(self, *, path: str) -> None:
        if self.fail_save:
            raise OSError("simulated storage-state write failure")
        Path(path).write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "__zp_stoken__",
                            "value": "test-secret",
                            "domain": ".zhipin.com",
                            "path": "/",
                        }
                    ],
                    "origins": [
                        {
                            "origin": "https://www.zhipin.com",
                            "localStorage": [
                                {"name": "session-token", "value": "test-token"}
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def close(self) -> None:
        return None


class FakeEpwkContext(FakeContext):
    def __init__(self) -> None:
        super().__init__()
        self.login_page = FakeEpwkPage()

    def new_page(self) -> FakePage:
        return self.login_page

    def storage_state(self, *, path: str) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "HWWAFSESID",
                            "value": "test-secret",
                            "domain": ".epwk.com",
                            "path": "/",
                        }
                    ],
                    "origins": [],
                }
            ),
            encoding="utf-8",
        )


class FakeNavigationFailureContext(FakeEpwkContext):
    def __init__(self) -> None:
        super().__init__()
        self.login_page = FakeNavigationFailurePage()
        self.closed = False
        self.storage_state_calls = 0

    def storage_state(self, *, path: str) -> None:
        self.storage_state_calls += 1
        super().storage_state(path=path)

    def close(self) -> None:
        self.closed = True


class FakePlaywrightManager:
    def __enter__(self) -> SimpleNamespace:
        return SimpleNamespace()

    def __exit__(self, *_: object) -> None:
        return None


class BossAuthTests(unittest.TestCase):
    def test_auth_parser_accepts_epwk(self) -> None:
        args = build_parser().parse_args(["auth", "--site", "epwk"])

        self.assertEqual("epwk", args.site)

    def test_epwk_ready_requires_site_domain_and_real_task_cards(self) -> None:
        self.assertTrue(_epwk_task_list_ready("https://task.epwk.com/?o=7", 1))
        self.assertFalse(_epwk_task_list_ready("https://task.epwk.com/?o=7", 0))
        self.assertFalse(_epwk_task_list_ready("https://notepwk.com/?o=7", 2))

    def test_epwk_auth_reuses_and_atomically_refreshes_storage_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "epwk-storage-state.json"
            state_path.write_text('{"cookies": []}', encoding="utf-8")
            config = FakeEpwkConfig(state_path)
            context = FakeEpwkContext()
            browser = FakeBrowser()
            with (
                patch(
                    "drv_crawler.browser.sync_playwright",
                    return_value=FakePlaywrightManager(),
                ),
                patch("drv_crawler.browser._launch_browser", return_value=browser),
                patch(
                    "drv_crawler.browser._new_context", return_value=context
                ) as new_context,
                patch("builtins.print") as print_output,
            ):
                saved_path = acquire_storage_state(
                    config,  # type: ignore[arg-type]
                    "epwk",
                    wait_seconds=10,
                )

            new_context.assert_called_once_with(
                browser,
                config,
                storage_state=state_path,
            )
            payload = json.loads(saved_path.read_text(encoding="utf-8"))
            self.assertEqual("HWWAFSESID", payload["cookies"][0]["name"])
            self.assertEqual([state_path], list(Path(temp_dir).iterdir()))
            self.assertNotIn(
                "test-secret",
                " ".join(str(call) for call in print_output.call_args_list),
            )

    def test_epwk_navigation_failure_is_handled_without_changing_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "epwk-storage-state.json"
            original = '{"cookies": [{"name": "old-session"}]}'
            state_path.write_text(original, encoding="utf-8")
            config = FakeEpwkConfig(state_path)
            context = FakeNavigationFailureContext()
            browser = FakeBrowser()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run.py",
                        "--config",
                        "unused.yaml",
                        "auth",
                        "--site",
                        "epwk",
                        "--wait-seconds",
                        "10",
                    ],
                ),
                patch("drv_crawler.cli.ProjectConfig", return_value=config),
                patch("drv_crawler.cli.configure_logging"),
                patch(
                    "drv_crawler.browser.sync_playwright",
                    return_value=FakePlaywrightManager(),
                ),
                patch("drv_crawler.browser._launch_browser", return_value=browser),
                patch("drv_crawler.browser._new_context", return_value=context),
                patch("builtins.print") as print_output,
            ):
                exit_code = main()

            output = " ".join(str(call) for call in print_output.call_args_list)
            self.assertEqual(2, exit_code)
            self.assertIn(
                "AUTH_NOT_SAVED: epwk auth page navigation failed; "
                "storage state not changed",
                output,
            )
            self.assertNotIn("Traceback", output)
            self.assertEqual(original, state_path.read_text(encoding="utf-8"))
            self.assertEqual([state_path], list(Path(temp_dir).iterdir()))
            self.assertEqual(0, context.storage_state_calls)
            self.assertTrue(context.closed)
            self.assertTrue(browser.closed)

    def test_login_ready_rejects_security_pages(self) -> None:
        self.assertFalse(
            _boss_login_ready(
                "https://www.zhipin.com/web/passport/zp/verify.html?code=35",
                "",
            )
        )
        self.assertFalse(
            _boss_login_ready("https://www.zhipin.com/web/user/", "职位列表")
        )
        self.assertFalse(
            _boss_login_ready(
                "https://www.zhipin.com/web/geek/jobs",
                "验证码登录/注册",
            )
        )
        self.assertTrue(
            _boss_login_ready("https://www.zhipin.com/web/geek/jobs", "职位列表")
        )

    def test_profile_cookie_count_reads_only_domain_row_count(self) -> None:
        with TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir)
            cookie_db = profile_dir / "Default" / "Network" / "Cookies"
            cookie_db.parent.mkdir(parents=True)
            connection = sqlite3.connect(cookie_db)
            connection.execute("CREATE TABLE cookies (host_key TEXT)")
            connection.executemany(
                "INSERT INTO cookies(host_key) VALUES (?)",
                [(".zhipin.com",), ("www.zhipin.com",), ("example.com",)],
            )
            connection.commit()
            connection.close()

            self.assertEqual(
                2,
                _profile_domain_cookie_count(
                    profile_dir, "zhipin.com", wait_seconds=1
                ),
            )

    def test_external_auth_uses_persistent_profile_without_storage_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            cookie_db = profile_dir / "Default" / "Network" / "Cookies"
            cookie_db.parent.mkdir(parents=True)
            connection = sqlite3.connect(cookie_db)
            connection.execute("CREATE TABLE cookies (host_key TEXT)")
            connection.execute(
                "INSERT INTO cookies(host_key) VALUES (?)", (".zhipin.com",)
            )
            connection.commit()
            connection.close()
            context = FakeProtectedContext()
            state_path = Path(temp_dir) / "boss-storage-state.json"

            with (
                patch(
                    "drv_crawler.browser.sync_playwright",
                    return_value=FakePlaywrightManager(),
                ),
                patch(
                    "drv_crawler.browser._launch_persistent_edge_context",
                    return_value=context,
                ),
                patch(
                    "drv_crawler.browser._wait_for_enter_or_timeout", return_value=True
                ),
            ):
                saved_path = _acquire_storage_state_from_external_edge(
                    FakeExternalConfig(profile_dir),  # type: ignore[arg-type]
                    "boss",
                    "https://www.zhipin.com/web/user/",
                    state_path,
                    wait_seconds=10,
                )

            self.assertEqual(profile_dir, saved_path)
            self.assertFalse(state_path.exists())
            self.assertTrue(context.closed)
            self.assertEqual(1, len(context.init_scripts))
            self.assertIn("window", context.init_scripts[0])
            self.assertIn("close", context.init_scripts[0])
            self.assertEqual(
                ["https://www.zhipin.com/web/geek/jobs"],
                context.verify_page.goto_calls,
            )

    def test_external_auth_rejects_stale_cookies_when_fresh_page_is_login(self) -> None:
        with TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            context = FakeProtectedContext(
                "https://www.zhipin.com/web/user/?from=passport-zp"
            )
            with (
                patch(
                    "drv_crawler.browser.sync_playwright",
                    return_value=FakePlaywrightManager(),
                ),
                patch(
                    "drv_crawler.browser._launch_persistent_edge_context",
                    return_value=context,
                ),
                patch(
                    "drv_crawler.browser._wait_for_enter_or_timeout", return_value=True
                ),
                patch(
                    "drv_crawler.browser._profile_domain_cookie_count", return_value=9
                ),
            ):
                with self.assertRaises(SessionRequiredError):
                    _acquire_storage_state_from_external_edge(
                        FakeExternalConfig(profile_dir),  # type: ignore[arg-type]
                        "boss",
                        "https://www.zhipin.com/web/user/",
                        Path(temp_dir) / "boss-storage-state.json",
                        wait_seconds=10,
                    )

            self.assertTrue(context.closed)

    def test_login_capture_saves_cookie_and_local_storage_atomically(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "boss-storage-state.json"
            context = FakeContext()
            with (
                patch(
                    "drv_crawler.browser.sync_playwright",
                    return_value=FakePlaywrightManager(),
                ),
                patch("drv_crawler.browser._launch_browser", return_value=FakeBrowser()),
                patch("drv_crawler.browser._new_context", return_value=context),
            ):
                saved_path = acquire_storage_state(
                    FakeConfig(state_path),  # type: ignore[arg-type]
                    "boss",
                    wait_seconds=10,
                )

            payload = json.loads(saved_path.read_text(encoding="utf-8"))
            self.assertEqual("__zp_stoken__", payload["cookies"][0]["name"])
            self.assertEqual("session-token", payload["origins"][0]["localStorage"][0]["name"])
            self.assertEqual(1, len(context.init_scripts))
            self.assertIn("window", context.init_scripts[0])
            self.assertIn("close", context.init_scripts[0])
            self.assertEqual([state_path], list(Path(temp_dir).iterdir()))

    def test_failed_capture_keeps_previous_state_and_removes_temp_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "boss-storage-state.json"
            original = '{"cookies": [{"name": "old-session"}]}'
            state_path.write_text(original, encoding="utf-8")
            context = FakeContext(fail_save=True)
            with (
                patch(
                    "drv_crawler.browser.sync_playwright",
                    return_value=FakePlaywrightManager(),
                ),
                patch("drv_crawler.browser._launch_browser", return_value=FakeBrowser()),
                patch("drv_crawler.browser._new_context", return_value=context),
            ):
                with self.assertRaises(OSError):
                    acquire_storage_state(
                        FakeConfig(state_path),  # type: ignore[arg-type]
                        "boss",
                        wait_seconds=10,
                    )

            self.assertEqual(original, state_path.read_text(encoding="utf-8"))
            self.assertEqual([state_path], list(Path(temp_dir).iterdir()))


if __name__ == "__main__":
    unittest.main()
