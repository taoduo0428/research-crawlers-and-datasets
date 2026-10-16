import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "tools" / "run_boss_public_details.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_boss_public_details", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class BossPublicDetailsTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_select_pending_rows_skips_success_duplicates_and_missing_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jsonl"
            completed = root / "completed.jsonl"
            write_jsonl(source, [
                {"record_id": "done", "detail_url": "https://example/done"},
                {"record_id": "next", "detail_url": "https://example/next"},
                {"record_id": "next", "detail_url": "https://example/next"},
                {"record_id": "missing"},
                {"record_id": "last", "detail_url": "https://example/last"},
            ])
            write_jsonl(completed, [
                {"record_id": "done", "jd": "complete description"},
            ])

            rows = self.module.select_pending_rows(source, [completed], limit=2)

            self.assertEqual([row["record_id"] for row in rows], ["next", "last"])

    def test_runtime_errors_are_classified_for_fail_closed_behavior(self):
        cases = {
            "BOSS detail login expired": "blocked_login_required",
            "access verification required": "blocked_verification_required",
            "CDP endpoint unavailable": "transport_error",
            "unexpected parser drift": "runtime_error",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(
                    self.module.classify_runtime_error(RuntimeError(message)),
                    expected,
                )

    def test_zero_valid_jds_is_not_reported_as_success(self):
        self.assertEqual(self.module.final_status("ok", 0), "no_public_full_jd")
        self.assertEqual(self.module.final_status("ok", 1), "ok")
        self.assertEqual(
            self.module.final_status("blocked_login_required", 0),
            "blocked_login_required",
        )


if __name__ == "__main__":
    unittest.main()
