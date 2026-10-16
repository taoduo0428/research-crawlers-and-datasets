import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("boss_jd_backfill.py")


def load_module():
    spec = importlib.util.spec_from_file_location("boss_jd_backfill", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BossJdBackfillTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.row = {
            "record_id": "row-1",
            "encrypt_job_id": "encrypted-id",
            "detail_url": "https://example.invalid/job_detail/encrypted-id.html",
        }

    def test_build_card_url(self):
        self.assertEqual(
            self.module.build_card_url(self.row),
            "https://www.zhipin.com/wapi/zpgeek/view/job/card.json?jid=encrypted-id&lid=&type=3",
        )

    def test_challenge_redirect_is_detected(self):
        self.assertTrue(
            self.module.is_list_challenge(
                "https://www.zhipin.com/web/passport/zp/verify.html?code=35",
                "",
            )
        )
        self.assertFalse(self.module.is_list_challenge("https://www.zhipin.com/web/geek/job", "职位列表"))

    def test_mode_defaults_from_config(self):
        parser = self.module.build_parser(
            {
                "mode": "direct",
                "proxy": "",
                "workers": 1,
                "max_inflight": 1,
            }
        )
        args = parser.parse_args(["crawl"])
        self.assertEqual(args.mode, "direct")
        self.assertEqual(args.workers, 1)
        self.assertEqual(args.max_inflight, 1)

    def test_browser_failures_remain_retryable(self):
        self.assertNotIn("browser_timeout", self.module.TERMINAL_STATUSES)
        self.assertNotIn("missing_list_url", self.module.TERMINAL_STATUSES)

    def test_zero_block_threshold_disables_circuit_breaker(self):
        self.assertFalse(self.module.block_threshold_reached(10_000, 0))
        self.assertTrue(self.module.block_threshold_reached(4, 4))

    def test_camoufox_mode_requires_single_worker(self):
        parser = self.module.build_parser({"mode": "camoufox-list-hover"})
        args = parser.parse_args(["crawl", "--workers", "2", "--max-inflight", "2"])
        with self.assertRaisesRegex(ValueError, "exactly one worker"):
            self.module.run_crawl(args)

    def test_extract_jd_preserves_breaks(self):
        raw = '<div class="detail-bottom-text">职责一<br/>职责二<br>薪资 4000</div>'
        self.assertEqual(self.module.extract_jd(raw), "职责一\n职责二\n薪资 4000")

    def test_parse_success(self):
        payload = {"code": 0, "zpData": {"html": '<div class="detail-bottom-text">完整 JD</div>'}}
        result = self.module.parse_response(
            self.row,
            http_status=200,
            payload=payload,
            elapsed_ms=12,
            attempt=1,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["jd"], "完整 JD")

    def test_code_35_is_blocked_not_expired(self):
        result = self.module.parse_response(
            self.row,
            http_status=200,
            payload={"code": 35, "message": "异常访问", "zpData": {}},
            elapsed_ms=8,
            attempt=1,
        )
        self.assertEqual(result["status"], "blocked_code_35")
        self.assertEqual(result["api_message"], "异常访问")
        self.assertNotIn(result["status"], self.module.TERMINAL_STATUSES)

    def test_expired_http_is_terminal(self):
        result = self.module.parse_response(
            self.row,
            http_status=404,
            payload=None,
            elapsed_ms=8,
            attempt=1,
        )
        self.assertEqual(result["status"], "expired_http_404")
        self.assertIn(result["status"], self.module.TERMINAL_STATUSES)

    def test_completed_loader_allows_blocked_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "results.jsonl"
            rows = [
                {"record_id": "a", "status": "blocked_code_35", "jd": ""},
                {"record_id": "b", "status": "api_code_31", "jd": ""},
                {"record_id": "c", "status": "expired_http_404", "jd": ""},
                {"record_id": "d", "status": "success", "jd": "text"},
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            success, terminal = self.module.load_completed(path)
            self.assertEqual(success, {"d"})
            self.assertEqual(terminal, {"c"})
            self.assertNotIn("a", terminal)
            self.assertNotIn("b", terminal)


if __name__ == "__main__":
    unittest.main()
