from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from drv_crawler.core import BlockedResponseError
from drv_crawler.models import CrawlRecord, PriceEvidence
from drv_crawler.publication import PublicationFilter
from tools.recover_epwk_failures import (
    RecoveryError,
    load_recovery_plan,
    recover_epwk_failures,
)


class FakeConfig:
    def __init__(self, path: Path, state_path: Path) -> None:
        self.path = path
        self.data = {
            "retry": {"retry_times": 2},
            "sites": {
                "epwk": {
                    "http_transport": "curl_cffi",
                    "storage_state": str(state_path),
                    "start_url": "https://task.epwk.com/?o=7",
                }
            },
        }

    def site(self, name: str) -> dict[str, object]:
        return self.data["sites"][name]

    def section(self, name: str) -> dict[str, object]:
        return self.data.setdefault(name, {})


class FakeClient:
    def __init__(self, *_: object, **__: object) -> None:
        self.loaded = False

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def load_storage_state(self, *_: object, **__: object) -> int:
        self.loaded = True
        return 1


class FakeBootstrapAdapter:
    bootstrap_calls = 0

    def __init__(self, *_: object) -> None:
        self._publication_renderer = None
        self.bootstrapped = False

    def _load_publication_sitemap_inventory(
        self, *_: object
    ) -> SimpleNamespace:
        type(self).bootstrap_calls += 1
        self.bootstrapped = True
        return SimpleNamespace(
            configured_files=1,
            loaded_files=1,
            complete=True,
        )

    def assert_bootstrapped(self) -> None:
        if not self.bootstrapped:
            raise AssertionError("detail requested before sitemap bootstrap")


def make_record(task_id: str, published_at: str) -> CrawlRecord:
    record = CrawlRecord(
        site_name="epwk",
        content_category="outsourcing_task",
        title_raw=f"task {task_id}",
        list_url="https://task.epwk.com/?o=7",
        detail_url=f"https://task.epwk.com/{task_id}/",
        page_no=0,
        list_position=1,
        published_at_raw=published_at,
    )
    record.add_price(
        PriceEvidence(
            raw_text="1000元",
            price_type="fixed_amount",
            price_source="detail_summary",
            evidence_url=record.detail_url or record.list_url,
        )
    )
    return record


class RecoverEpwkFailuresTests(unittest.TestCase):
    def _write_run(
        self,
        root: Path,
        name: str,
        failures: list[dict[str, object]],
        *,
        as_of: date = date(2026, 7, 21),
    ) -> Path:
        run_dir = root / name
        site_dir = run_dir / "epwk"
        site_dir.mkdir(parents=True)
        report = {
            "site_name": "epwk",
            "publication_filter": PublicationFilter(
                recent_months=3, as_of=as_of
            ).describe(),
            "failures": failures,
        }
        (site_dir / "report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        return run_dir

    @staticmethod
    def _failure(task_id: str, message: str = "Blocked response 403") -> dict[str, object]:
        return {
            "site_name": "epwk",
            "stage": "publication_sitemap_detail",
            "error_type": "BlockedResponseError",
            "message": message,
            "url": f"https://task.epwk.com/{task_id}/?token=must-not-leak",
        }

    def test_load_plan_deduplicates_canonical_urls_and_rejects_window_mismatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = self._write_run(root, "run-1", [self._failure("100")])
            second = self._write_run(root, "run-2", [self._failure("100")])

            plan = load_recovery_plan([first, second])

            self.assertEqual(1, len(plan.targets))
            self.assertEqual("https://task.epwk.com/100/", plan.targets[0].detail_url)
            self.assertEqual({"input_001", "input_002"}, plan.targets[0].source_inputs)

            mismatch = self._write_run(
                root,
                "run-3",
                [self._failure("101")],
                as_of=date(2026, 7, 20),
            )
            with self.assertRaisesRegex(RecoveryError, "does not match"):
                load_recovery_plan([first, mismatch])

    def test_load_plan_accepts_previous_unresolved_manifest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "unresolved-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "site_name": "epwk",
                        "publication_filter": PublicationFilter(
                            recent_months=3,
                            as_of=date(2026, 7, 21),
                        ).describe(),
                        "unresolved": [
                            {
                                "task_id": "100",
                                "detail_url": (
                                    "https://task.epwk.com/100/"
                                    "?token=must-not-leak"
                                ),
                                "http_status": 403,
                                "error_type": "BlockedResponseError",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            plan = load_recovery_plan([manifest_path])

            self.assertEqual(1, len(plan.targets))
            self.assertEqual("https://task.epwk.com/100/", plan.targets[0].detail_url)
            self.assertEqual({403}, plan.targets[0].initial_http_statuses)

    def test_retries_then_recovers_and_writes_atomic_outputs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = self._write_run(
                root,
                "run-1",
                [self._failure("100"), self._failure("101")],
            )
            state_path = root / "state.json"
            state_path.write_text('{"cookies": []}', encoding="utf-8")
            config = FakeConfig(root / "config.yaml", state_path)

            class RetryAdapter(FakeBootstrapAdapter):
                calls = 0

                def _sitemap_detail_record(self, *_: object, **__: object) -> CrawlRecord:
                    self.assert_bootstrapped()
                    RetryAdapter.calls += 1
                    if RetryAdapter.calls == 1:
                        raise BlockedResponseError(
                            "Cookie: never log this",
                            status_code=403,
                            url="https://task.epwk.com/100/",
                        )
                    return make_record("100", "2026.07.21")

            output_dir = root / "recovered"
            sleeps: list[float] = []
            with patch(
                "tools.recover_epwk_failures.ProjectConfig", return_value=config
            ), patch(
                "tools.recover_epwk_failures.ResilientHttpClient", FakeClient
            ), patch(
                "tools.recover_epwk_failures.EpwkAdapter", RetryAdapter
            ):
                result = recover_epwk_failures(
                    [run_dir],
                    output_dir=output_dir,
                    config_path=config.path,
                    delay_seconds=2.0,
                    target_limit=1,
                    _sleep=sleeps.append,
                )

            self.assertEqual(1, RetryAdapter.bootstrap_calls)
            self.assertEqual(2, RetryAdapter.calls)
            self.assertEqual([4.0], sleeps)
            self.assertEqual(2, result.report["source_target_count"])
            self.assertEqual(1, result.report["target_count"])
            self.assertEqual(
                "formal_sitemap_inventory",
                result.report["session_bootstrap"]["mode"],
            )
            self.assertEqual(1, len(result.recovered))
            self.assertEqual([], result.unresolved)
            for name in (
                "recovered-records.jsonl",
                "unresolved-manifest.json",
                "recovery-report.json",
                "recovery-progress.json",
            ):
                self.assertTrue((output_dir / name).is_file())
            combined = "\n".join(
                path.read_text(encoding="utf-8") for path in output_dir.iterdir()
            )
            self.assertNotIn("must-not-leak", combined)
            self.assertNotIn("never log this", combined)

    def test_strictly_filters_old_detail_date_and_bounds_attempts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = self._write_run(
                root,
                "run-1",
                [self._failure("100"), self._failure("101")],
            )
            state_path = root / "state.json"
            state_path.write_text('{"cookies": []}', encoding="utf-8")
            config = FakeConfig(root / "config.yaml", state_path)

            class MixedAdapter(FakeBootstrapAdapter):
                calls: dict[str, int] = {}

                def _sitemap_detail_record(
                    self, _: object, entry: object, **__: object
                ) -> CrawlRecord:
                    self.assert_bootstrapped()
                    task_id = str(getattr(entry, "task_key"))
                    MixedAdapter.calls[task_id] = MixedAdapter.calls.get(task_id, 0) + 1
                    if task_id == "100":
                        return make_record(task_id, "2026.04.20")
                    raise ValueError("access_token=must-not-leak")

            with patch(
                "tools.recover_epwk_failures.ProjectConfig", return_value=config
            ), patch(
                "tools.recover_epwk_failures.ResilientHttpClient", FakeClient
            ), patch(
                "tools.recover_epwk_failures.EpwkAdapter", MixedAdapter
            ):
                result = recover_epwk_failures(
                    [run_dir],
                    output_dir=root / "recovered",
                    config_path=config.path,
                    delay_seconds=0,
                    _sleep=lambda _: None,
                )

            self.assertEqual(1, MixedAdapter.calls["100"])
            self.assertEqual(3, MixedAdapter.calls["101"])
            self.assertEqual(0, len(result.recovered))
            self.assertEqual(
                {"publication_too_old", "parse_error"},
                {item["status"] for item in result.unresolved},
            )
            manifest = (root / "recovered" / "unresolved-manifest.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("must-not-leak", manifest)


if __name__ == "__main__":
    unittest.main()
