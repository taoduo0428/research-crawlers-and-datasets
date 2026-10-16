import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from drv_crawler.liepin.parser import classify_access_gate, looks_like_challenge


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "tools" / "run_liepin_browser_single.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_liepin_browser_single", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakePage:
    def __init__(self, closed=False):
        self.closed = closed

    def is_closed(self):
        return self.closed


class TransitPage:
    def __init__(self):
        self.states = [
            ("https://wow.liepin.com/t1012695/transit.html", "<html>transit</html>"),
            ("https://www.liepin.com/job/1.shtml", "<html>public detail</html>"),
        ]
        self.index = 0

    @property
    def url(self):
        return self.states[self.index][0]

    def content(self):
        return self.states[self.index][1]

    def wait_for_timeout(self, _milliseconds):
        self.index = min(self.index + 1, len(self.states) - 1)


class LoginThenPublicPage:
    def __init__(self):
        self.goto_count = 0

    @property
    def url(self):
        if self.goto_count == 1:
            return "https://passport.liepin.com/login/"
        return "https://www.liepin.com/job/1.shtml"

    def goto(self, _url, **_kwargs):
        self.goto_count += 1

    def content(self):
        return (
            "<html><dl class='paragraph'><dt>职位介绍</dt>"
            "<dd>public detail</dd></dl></html>"
        )

    def wait_for_timeout(self, _milliseconds):
        return None


class DynamicUnavailablePage:
    url = "https://www.liepin.com/a/1.shtml"

    def __init__(self):
        self.rendered = False

    def goto(self, _url, **_kwargs):
        return None

    def content(self):
        body = "该职位已暂停招聘" if self.rendered else "loading"
        return f"<html><body>{body}</body></html>"

    def wait_for_timeout(self, _milliseconds):
        self.rendered = True


class FakeContext:
    def __init__(self, cookies=None):
        self.clear_count = 0
        self._cookies = cookies or []

    def clear_cookies(self):
        self.clear_count += 1

    def cookies(self):
        return self._cookies


class LiepinBrowserSingleTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_target_closed_message_stops_session(self):
        exc = RuntimeError("Page.goto: Target page, context or browser has been closed")
        self.assertTrue(self.module.browser_session_closed(FakePage(False), exc))

    def test_wow_transit_page_is_a_challenge(self):
        self.assertTrue(
            looks_like_challenge(
                "<html><body>transit</body></html>",
                "https://wow.liepin.com/t1012695/4410f519.html",
            )
        )

    def test_access_gate_distinguishes_login_challenge_and_transit(self):
        self.assertEqual(
            classify_access_gate("<html></html>", "https://passport.liepin.com/login/"),
            "login_required",
        )
        self.assertEqual(
            classify_access_gate("<html></html>", "https://safe.liepin.com/verify/"),
            "security_challenge",
        )
        self.assertEqual(
            classify_access_gate("<html></html>", "https://wow.liepin.com/transit/"),
            "security_transit",
        )
        self.assertIsNone(
            classify_access_gate(
                "<html><body>公开职位详情</body></html>",
                "https://www.liepin.com/job/1.shtml",
            )
        )

    def test_waits_for_wow_transit_to_return_to_public_detail(self):
        page = TransitPage()
        self.assertIsNone(self.module.wait_out_security_transit(page, timeout_ms=1000))
        self.assertEqual(page.url, "https://www.liepin.com/job/1.shtml")

    def test_login_redirect_preserves_profile_cookies_and_returns_gate(self):
        context = FakeContext()
        page = LoginThenPublicPage()
        html, gate, retried = self.module.open_public_detail(
            context,
            page,
            "https://www.liepin.com/job/1.shtml",
            30_000,
        )
        self.assertFalse(retried)
        self.assertEqual(context.clear_count, 0)
        self.assertEqual(page.goto_count, 1)
        self.assertEqual(gate, "login_required")
        self.assertIn("public detail", html)

    def test_dynamic_unavailable_marker_is_waited_for_and_preserved(self):
        context = FakeContext()
        page = DynamicUnavailablePage()
        html, gate, retried = self.module.open_public_detail(
            context,
            page,
            page.url,
            30_000,
        )
        self.assertFalse(retried)
        self.assertIsNone(gate)
        self.assertEqual(self.module.classify_empty_detail(html), "unavailable")

    def test_static_session_reuses_edge_user_agent_and_liepin_cookies(self):
        context = FakeContext(
            cookies=[
                {"name": "public", "value": "1", "domain": ".liepin.com", "path": "/"},
                {"name": "other", "value": "2", "domain": ".example.com", "path": "/"},
            ]
        )
        page = Mock()
        page.evaluate.return_value = "Edge-UA"
        with self.module.build_static_session() as session:
            self.module.sync_static_session_from_browser(context, page, session)
            self.assertEqual(session.headers["User-Agent"], "Edge-UA")
            self.assertEqual(session.cookies.get("public"), "1")
            self.assertIsNone(session.cookies.get("other"))

    def test_regular_playwright_failure_does_not_stop_open_session(self):
        exc = RuntimeError("Page.goto: Timeout 30000ms exceeded")
        self.assertFalse(self.module.browser_session_closed(FakePage(False), exc))

    def test_closed_page_stops_session_without_specific_message(self):
        self.assertTrue(
            self.module.browser_session_closed(FakePage(True), RuntimeError("other"))
        )

    def test_load_cookies_accepts_playwright_storage_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text(json.dumps({"cookies": [{
                "name": "session",
                "value": "secret",
                "domain": ".liepin.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            }], "origins": []}), encoding="utf-8")

            cookies = self.module.load_cookies(path)

            self.assertEqual(len(cookies), 1)
            self.assertEqual(cookies[0]["name"], "session")
            self.assertNotIn("expires", cookies[0])

    def test_storage_origins_only_restore_liepin_local_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text(json.dumps({"cookies": [], "origins": [
                {
                    "origin": "https://www.liepin.com",
                    "localStorage": [{"name": "session-key", "value": "session-value"}],
                },
                {
                    "origin": "https://example.com",
                    "localStorage": [{"name": "ignored", "value": "secret"}],
                },
            ]}), encoding="utf-8")

            origins = self.module.load_storage_origins(path)
            script = self.module.storage_init_script(origins)

            self.assertEqual(origins, {
                "https://www.liepin.com": {"session-key": "session-value"}
            })
            self.assertIn("https://www.liepin.com", script)
            self.assertNotIn("example.com", script)

    def test_empty_detail_classification_marks_unavailable_pages(self):
        self.assertEqual(
            self.module.classify_empty_detail("<html>职位已下线</html>"),
            "unavailable",
        )
        self.assertEqual(
            self.module.classify_empty_detail("<html>页面暂时没有内容</html>"),
            "empty",
        )

    def test_terminal_checkpoint_ids_are_loaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoint.jsonl"
            path.write_text(
                json.dumps({"record_id": "a", "status": "unavailable"}) + "\n"
                + json.dumps({"record_id": "b", "status": "empty"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(self.module.load_terminal_ids(path), {"a"})

    def test_cooldown_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self.module.write_cooldown(state, seconds=180, reason="verification_timeout")
            active = self.module.read_cooldown_remaining(state)
            self.assertIsNotNone(active)
            self.assertEqual(active["reason"], "verification_timeout")
            self.module.clear_cooldown(state)
            self.assertIsNone(self.module.read_cooldown_remaining(state))

    def test_static_description_fallback_parses_successful_page(self):
        response = Mock()
        response.text = "<html>detail</html>"
        response.url = "https://www.liepin.com/job/1.shtml"
        response.raise_for_status.return_value = None
        with (
            patch.object(self.module.requests, "get", return_value=response),
            patch.object(self.module, "looks_like_challenge", return_value=False),
            patch.object(self.module, "parse_detail_description", return_value="JD text"),
        ):
            self.assertEqual(
                self.module.fetch_static_description(response.url),
                "JD text",
            )


if __name__ == "__main__":
    unittest.main()
