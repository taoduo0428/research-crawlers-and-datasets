import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "tools" / "backfill_boss_public_card_jd.py"


def load_module():
    spec = importlib.util.spec_from_file_location("backfill_boss_public_card_jd", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BossPublicCardBackfillTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.row = {
            "record_id": "boss-test-1",
            "job_id": "job-1",
            "encrypt_job_id": "encrypted-1",
            "title_raw": "测试岗位",
            "boss_name": "测试公司",
            "salary": "10-20K",
            "detail_url": "https://www.zhipin.com/job_detail/encrypted-1.html",
            "security_id": "must-not-copy",
        }

    def test_build_url_uses_encrypt_job_id(self):
        url = self.module.build_card_url(self.row)
        self.assertIn("jid=encrypted-1", url)
        self.assertIn("lid=&type=3", url)
        self.assertNotIn("job-1", url)

    def test_extract_detail_text_preserves_breaks(self):
        html = '<div class="detail-bottom-text">岗位职责<br>负责测试<br/>任职要求</div>'
        self.assertEqual(self.module.extract_detail_text(html), "岗位职责\n负责测试\n任职要求")

    def test_success_payload_contains_jd_without_security_id(self):
        payload = {"code": 0, "zpData": {"html": '<div class="detail-bottom-text">负责研发和测试</div>'}}
        result = self.module.parse_card_payload(
            self.row,
            url=self.module.build_card_url(self.row),
            http_status=200,
            payload=payload,
            elapsed_ms=12,
            attempt=1,
        )
        self.assertEqual(result["detail_fetch_status"], "success")
        self.assertEqual(result["jd"], "负责研发和测试")
        self.assertNotIn("security_id", result)

    def test_block_response_fails_closed(self):
        result = self.module.parse_card_payload(
            self.row,
            url=self.module.build_card_url(self.row),
            http_status=200,
            payload={"code": 35, "zpData": {"html": ""}},
            elapsed_ms=12,
            attempt=1,
        )
        self.assertEqual(result["detail_fetch_status"], "blocked_code_35")
        self.assertEqual(result["jd"], "")

    def test_block_http_without_json_fails_closed(self):
        result = self.module.parse_card_payload(
            self.row,
            url=self.module.build_card_url(self.row),
            http_status=403,
            payload=None,
            elapsed_ms=12,
            attempt=1,
        )
        self.assertEqual(result["detail_fetch_status"], "blocked_http_403")

    def test_pending_rows_skip_success_and_duplicate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jsonl"
            source.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in [
                        self.row,
                        {**self.row, "record_id": "boss-test-1"},
                        {**self.row, "record_id": "boss-test-2", "encrypt_job_id": "encrypted-2"},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            rows = list(
                self.module.iter_pending_rows(
                    source,
                    completed={"boss-test-1"},
                    attempt_counts={},
                    max_attempts=2,
                    limit=None,
                )
            )
            self.assertEqual([item["record_id"] for item in rows], ["boss-test-2"])


if __name__ == "__main__":
    unittest.main()
