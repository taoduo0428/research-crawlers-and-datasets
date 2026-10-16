import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "tools" / "run_boss_detail_workers.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_boss_detail_workers", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class BossDetailParallelTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def make_accounts(self, root: Path):
        accounts = []
        for index in range(5):
            filename = f"account-{index}.json"
            (root / filename).write_text(json.dumps([{
                "name": "session",
                "value": f"secret-{index}",
                "domain": ".zhipin.com",
                "path": "/",
            }]), encoding="utf-8")
            accounts.append({
                "file": filename,
                "fingerprint": f"fp-{index}",
                "active": True,
                "quarantined": False,
            })
        (root / "accounts-manifest.json").write_text(
            json.dumps({"accounts": accounts}), encoding="utf-8"
        )

    def test_prepare_creates_disjoint_stable_shards_and_skips_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "jobs.jsonl"
            prior = root / "details.jsonl"
            accounts = root / "accounts"
            state = root / "state"
            accounts.mkdir()
            self.make_accounts(accounts)
            records = [
                {"record_id": f"boss-{index}", "job_id": str(index), "detail_url": f"https://example/{index}"}
                for index in range(12)
            ]
            records.append(dict(records[4]))
            write_jsonl(source, records)
            write_jsonl(prior, [{**records[2], "jd": "real description"}])

            plan = self.module.prepare(
                source, prior, accounts, state, workers=3, port_base=9300, inject_cookies=False
            )

            self.assertEqual(plan["source_count"], 13)
            self.assertEqual(plan["unique_source_count"], 12)
            self.assertEqual(plan["duplicate_source_count"], 1)
            self.assertEqual(plan["pending_count"], 11)
            self.assertFalse(plan["inject_cookies"])
            keys = []
            for worker in range(1, 4):
                shard = list(self.module.iter_jsonl(state / "shards" / f"worker-{worker}.jsonl"))
                keys.extend(item["record_id"] for item in shard)
                for item in shard:
                    self.assertEqual(self.module.worker_index(item["record_id"], 3), worker - 1)
            self.assertEqual(len(keys), len(set(keys)))
            self.assertNotIn("boss-2", keys)

    def test_prepare_low_risk_defaults_single_worker_without_cookie_inject(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "jobs.jsonl"
            prior = root / "details.jsonl"
            accounts = root / "accounts"
            state = root / "state"
            accounts.mkdir()
            write_jsonl(source, [
                {"record_id": "boss-1", "job_id": "1", "detail_url": "https://example/1"},
                {"record_id": "boss-2", "job_id": "2", "detail_url": "https://example/2"},
            ])
            write_jsonl(prior, [])

            plan = self.module.prepare(source, prior, accounts, state)

            self.assertEqual(plan["workers"], 1)
            self.assertFalse(plan["inject_cookies"])
            self.assertGreaterEqual(plan["gap_min"], 8.0)
            self.assertGreaterEqual(plan["gap_max"], plan["gap_min"])
            self.assertLessEqual(plan["batch_size"], 50)
            self.assertFalse(plan["rotation_policy"]["enabled"])
            self.assertEqual(plan["worker_specs"][0]["account_file"], "profile-login-only")

    def test_cooldown_blocks_until_expiry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self.module.write_cooldown(state, seconds=120, reason="login_required")
            active = self.module.read_cooldown_remaining(state)
            self.assertIsNotNone(active)
            self.assertEqual(active["reason"], "login_required")
            self.module.clear_cooldown(state)
            self.assertIsNone(self.module.read_cooldown_remaining(state))

    def test_soft_fail_streak_counts_trailing_failures(self):
        self.assertEqual(
            self.module.consecutive_soft_fail_streak(["success", "failed", "failed"]),
            2,
        )
        self.assertEqual(
            self.module.consecutive_soft_fail_streak(["failed", "success"]),
            0,
        )

    def test_checkpoint_statuses_are_scoped_to_current_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoint.jsonl"
            write_jsonl(path, [{"status": "verification_required"}])
            offset = path.stat().st_size
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"status": "success"}) + "\n")
            self.assertEqual(self.module.checkpoint_statuses_since(path, offset), ["success"])

    def test_shard_remaining_only_counts_successful_jd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shard = root / "shard.jsonl"
            output = root / "output.jsonl"
            write_jsonl(shard, [{"record_id": "a"}, {"record_id": "b"}])
            write_jsonl(output, [
                {"record_id": "a", "jd": "description"},
                {"record_id": "b", "jd": ""},
            ])
            self.assertEqual(self.module.shard_remaining(shard, output), 1)

    def test_shard_remaining_excludes_only_repeated_terminal_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shard = root / "shard.jsonl"
            output = root / "output.jsonl"
            checkpoint = root / "checkpoint.jsonl"
            write_jsonl(shard, [
                {"record_id": "retry-once"},
                {"record_id": "terminal"},
                {"record_id": "verification"},
            ])
            write_jsonl(output, [])
            write_jsonl(checkpoint, [
                {"record_id": "retry-once", "status": "failed"},
                {"record_id": "terminal", "status": "failed"},
                {"record_id": "terminal", "status": "failed"},
                {"record_id": "verification", "status": "verification_required"},
            ])

            self.assertEqual(
                self.module.shard_remaining(shard, output, checkpoint),
                2,
            )


if __name__ == "__main__":
    unittest.main()
