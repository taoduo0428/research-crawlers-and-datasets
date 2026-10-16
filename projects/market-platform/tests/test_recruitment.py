from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from drv_crawler.cli import _recruitment_auth_succeeded, build_parser, validate_run
from drv_crawler.core import ProjectConfig
from drv_crawler.recruitment import (
    RecruitmentSiteOutcome,
    _adapter_proxy_policy,
    _cooldown_seconds_from_payload,
    _config_relative_path,
    _normalized_quota_summary,
    _read_jsonl,
    _record_from_dict,
    _recover_site_snapshot,
    _recruitment_scope_dir,
    _run_boss,
    _run_job51,
    _run_liepin,
    _scope_dir,
    _scope_run_lock,
    _status_from_coverage,
    enabled_recruitment_sites,
    recruitment_doctor,
    run_recruitment,
    run_recruitment_auth,
)


def write_config(
    path: Path,
    output_dir: Path,
    state_dir: Path,
    *,
    auto_resume_full_probe: bool | None = None,
    auto_resume_max_cycles: int | None = None,
    auto_resume_max_runtime_seconds: int | None = None,
) -> ProjectConfig:
    resume_lines: list[str] = []
    if auto_resume_full_probe is not None:
        resume_lines.append(
            f"    auto_resume_full_probe: {str(auto_resume_full_probe).lower()}"
        )
    if auto_resume_max_cycles is not None:
        resume_lines.append(f"    full_probe_max_cycles: {auto_resume_max_cycles}")
    if auto_resume_max_runtime_seconds is not None:
        resume_lines.append(
            f"    full_probe_max_runtime_seconds: {auto_resume_max_runtime_seconds}"
        )
    path.write_text(
        "\n".join(
            (
                "crawler:",
                "  export:",
                f"    directory: {output_dir.as_posix()}",
                "  recruitment:",
                f"    output_directory: {output_dir.as_posix()}",
                f"    state_directory: {state_dir.as_posix()}",
                "    per_category: 2",
                *resume_lines,
                "    sites:",
                "      boss:",
                "        enabled: true",
                "      job51:",
                "        enabled: true",
                "      liepin:",
                "        enabled: true",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return ProjectConfig(path)


class RecruitmentTests(unittest.TestCase):
    def test_read_jsonl_preserves_unicode_line_separator_inside_record(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            records = [
                {"record_id": "first", "description_raw": "line 1\u2028line 2"},
                {"record_id": "second", "description_raw": "plain"},
            ]
            path.write_text(
                "".join(
                    json.dumps(record, ensure_ascii=False) + "\n" for record in records
                ),
                encoding="utf-8",
            )

            self.assertEqual(records, _read_jsonl(path))

    def test_validate_run_preserves_unicode_line_separator_inside_record(self) -> None:
        with TemporaryDirectory() as directory:
            run_dir = Path(directory)
            site_dir = run_dir / "job51"
            site_dir.mkdir()
            records = [
                {
                    "title_raw": "content manager",
                    "description_raw": "line 1\u2028line 2",
                    "price_evidence": {"raw": "10-20k"},
                },
                {
                    "title_raw": "developer",
                    "description_raw": "plain",
                    "price_evidence": {"raw": "20-30k"},
                },
            ]
            (site_dir / "records.jsonl").write_text(
                "".join(
                    json.dumps(record, ensure_ascii=False) + "\n" for record in records
                ),
                encoding="utf-8",
            )
            (run_dir / "run-report.json").write_text(
                json.dumps(
                    {
                        "sites": [
                            {
                                "site_name": "job51",
                                "quality": {
                                    "record_count": 2,
                                    "quality_passed": True,
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(0, validate_run(run_dir))

    @staticmethod
    def _site_outcome(
        site: str,
        *,
        status: str = "partial",
        stop_reason: str = "rate_limit_cooldown_required",
        records: int = 1,
        added: int = 1,
        coverage_rate: float = 0.1,
    ) -> RecruitmentSiteOutcome:
        return RecruitmentSiteOutcome(
            site_name=site,
            status=status,
            records=records,
            coverage_rate=coverage_rate,
            stop_reason=stop_reason,
            summary={"records_added_this_run": added},
        )

    def test_full_probe_waits_for_job51_cooldown_and_resumes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)
            scope = _recruitment_scope_dir(
                config, state_root, "job51", per_category=2
            )
            scope.mkdir(parents=True)
            calls = 0

            def run_job51(*_args: object, **_kwargs: object) -> RecruitmentSiteOutcome:
                nonlocal calls
                calls += 1
                adapter_summary = Path(_args[1]) / "job51" / "summary.json"
                adapter_summary.parent.mkdir(parents=True, exist_ok=True)
                adapter_summary.write_text(
                    json.dumps({"records_exported": calls, "export_errors": []}),
                    encoding="utf-8",
                )
                if calls == 1:
                    (scope / "coverage.json").write_text(
                        json.dumps(
                            {
                                "rate_limit_cooldown": {"remaining_seconds": 30.0}
                            }
                        ),
                        encoding="utf-8",
                    )
                    return self._site_outcome("job51")
                (scope / "coverage.json").write_text(
                    json.dumps({"rate_limit_cooldown": None}), encoding="utf-8"
                )
                return self._site_outcome(
                    "job51",
                    status="success",
                    stop_reason="work_queue_exhausted",
                    records=2,
                    coverage_rate=1.0,
                )

            with (
                patch("drv_crawler.recruitment._run_job51", side_effect=run_job51),
                patch("drv_crawler.recruitment.time.monotonic", return_value=0.0),
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                run_dir, report = run_recruitment(config, site_name="job51")

            site = report["sites"][0]
            self.assertEqual(2, calls)
            sleep.assert_called_once_with(30.0)
            self.assertEqual("success", site["status"])
            self.assertEqual(2, site["summary"]["auto_resume_cycles"])
            self.assertEqual(2, site["summary"]["records_added_this_run"])
            self.assertTrue(report["auto_resume"]["enabled"])
            saved_summary = json.loads(
                (run_dir / "job51" / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(2, saved_summary["records_exported"])
            self.assertEqual([], saved_summary["export_errors"])
            self.assertEqual(2, saved_summary["auto_resume_cycles"])

    def test_full_probe_reads_boss_checkpoint_cooldown(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)
            scope = _recruitment_scope_dir(config, state_root, "boss", per_category=2)
            scope.mkdir(parents=True)
            calls = 0

            def run_boss(*_args: object, **_kwargs: object) -> RecruitmentSiteOutcome:
                nonlocal calls
                calls += 1
                restriction = (
                    {"cooldown_until": "1970-01-01T00:17:05+00:00"}
                    if calls == 1
                    else None
                )
                (scope / "checkpoint.json").write_text(
                    json.dumps({"site_restriction": restriction}), encoding="utf-8"
                )
                if calls == 1:
                    return self._site_outcome("boss", stop_reason="process_exit_1")
                return self._site_outcome(
                    "boss",
                    status="success",
                    stop_reason="process_completed",
                    records=2,
                    coverage_rate=1.0,
                )

            with (
                patch("drv_crawler.recruitment._run_boss", side_effect=run_boss),
                patch("drv_crawler.recruitment.time.time", return_value=1_000.0),
                patch("drv_crawler.recruitment.time.monotonic", return_value=0.0),
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(config, site_name="boss")

            self.assertEqual(2, calls)
            sleep.assert_called_once_with(25.0)
            self.assertEqual("success", report["sites"][0]["status"])

    def test_bounded_probe_never_auto_resumes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)
            scope = _recruitment_scope_dir(
                config, state_root, "job51", per_category=2
            )
            scope.mkdir(parents=True)
            (scope / "coverage.json").write_text(
                json.dumps({"rate_limit_cooldown": {"remaining_seconds": 30.0}}),
                encoding="utf-8",
            )
            outcome = self._site_outcome("job51")

            with (
                patch("drv_crawler.recruitment._run_job51", return_value=outcome) as run,
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(
                    config, site_name="job51", max_categories=1
                )

            run.assert_called_once()
            sleep.assert_not_called()
            self.assertFalse(report["auto_resume"]["enabled"])

    def test_full_probe_auto_resume_can_be_disabled(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(
                root / "config.yaml",
                root / "out",
                state_root,
                auto_resume_full_probe=False,
            )
            outcome = self._site_outcome("job51")

            with (
                patch("drv_crawler.recruitment._run_job51", return_value=outcome) as run,
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(config, site_name="job51")

            run.assert_called_once()
            sleep.assert_not_called()
            self.assertFalse(report["auto_resume"]["enabled"])

    def test_liepin_full_probe_does_not_report_auto_resume(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            outcome = self._site_outcome(
                "liepin",
                status="success",
                stop_reason="all_categories_completed",
                coverage_rate=1.0,
            )

            with patch("drv_crawler.recruitment._run_liepin", return_value=outcome):
                _run_dir, report = run_recruitment(config, site_name="liepin")

            self.assertFalse(report["auto_resume"]["enabled"])

    def test_full_probe_cycle_limit_is_explicit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(
                root / "config.yaml",
                root / "out",
                state_root,
                auto_resume_max_cycles=2,
                auto_resume_max_runtime_seconds=100,
            )
            scope = _recruitment_scope_dir(
                config, state_root, "job51", per_category=2
            )
            scope.mkdir(parents=True)

            def blocked(*_args: object, **_kwargs: object) -> RecruitmentSiteOutcome:
                (scope / "coverage.json").write_text(
                    json.dumps(
                        {"rate_limit_cooldown": {"remaining_seconds": 1.0}}
                    ),
                    encoding="utf-8",
                )
                return self._site_outcome("job51", added=0)

            with (
                patch("drv_crawler.recruitment._run_job51", side_effect=blocked) as run,
                patch("drv_crawler.recruitment.time.monotonic", return_value=0.0),
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(config, site_name="job51")

            site = report["sites"][0]
            self.assertEqual(2, run.call_count)
            sleep.assert_called_once_with(1.0)
            self.assertEqual("full_probe_cycle_limit", site["stop_reason"])
            self.assertEqual(2, site["summary"]["auto_resume_cycles"])
            self.assertTrue((scope / "coverage.json").exists())

    def test_full_probe_runtime_limit_does_not_oversleep(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(
                root / "config.yaml",
                root / "out",
                state_root,
                auto_resume_max_cycles=4,
                auto_resume_max_runtime_seconds=10,
            )
            scope = _recruitment_scope_dir(
                config, state_root, "job51", per_category=2
            )
            scope.mkdir(parents=True)

            def blocked(*_args: object, **_kwargs: object) -> RecruitmentSiteOutcome:
                (scope / "coverage.json").write_text(
                    json.dumps(
                        {"rate_limit_cooldown": {"remaining_seconds": 20.0}}
                    ),
                    encoding="utf-8",
                )
                return self._site_outcome("job51", added=0)

            with (
                patch("drv_crawler.recruitment._run_job51", side_effect=blocked) as run,
                patch("drv_crawler.recruitment.time.monotonic", return_value=0.0),
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(config, site_name="job51")

            site = report["sites"][0]
            run.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual("full_probe_runtime_limit", site["stop_reason"])
            self.assertEqual(20.0, site["summary"]["cooldown_remaining_seconds"])

    def test_full_probe_retries_job51_pending_work_without_cooldown(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)
            scope = _recruitment_scope_dir(
                config, state_root, "job51", per_category=2
            )
            scope.mkdir(parents=True)
            calls = 0

            def run_job51(*_args: object, **_kwargs: object) -> RecruitmentSiteOutcome:
                nonlocal calls
                calls += 1
                if calls == 1:
                    coverage = {
                        "rate_limit_cooldown": None,
                        "categories": [
                            {
                                "search_code": "LEAF",
                                "status": "failed",
                                "attempts": 1,
                            }
                        ],
                    }
                    outcome = self._site_outcome(
                        "job51", stop_reason="work_queue_exhausted"
                    )
                else:
                    coverage = {
                        "rate_limit_cooldown": None,
                        "categories": [
                            {
                                "search_code": "LEAF",
                                "status": "completed",
                                "attempts": 2,
                            }
                        ],
                    }
                    outcome = self._site_outcome(
                        "job51",
                        status="success",
                        stop_reason="work_queue_exhausted",
                        coverage_rate=1.0,
                    )
                (scope / "coverage.json").write_text(
                    json.dumps(coverage), encoding="utf-8"
                )
                return outcome

            with (
                patch("drv_crawler.recruitment._run_job51", side_effect=run_job51),
                patch("drv_crawler.recruitment.time.monotonic", return_value=0.0),
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(config, site_name="job51")

            self.assertEqual(2, calls)
            sleep.assert_not_called()
            self.assertEqual("success", report["sites"][0]["status"])

    def test_full_probe_retries_boss_ordinary_error_until_terminal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)
            scope = _recruitment_scope_dir(config, state_root, "boss", per_category=2)
            scope.mkdir(parents=True)
            calls = 0

            def run_boss(*_args: object, **_kwargs: object) -> RecruitmentSiteOutcome:
                nonlocal calls
                calls += 1
                terminal = calls >= 3
                checkpoint = {
                    "catalog": [{"position_code": "100101"}],
                    "completed_position_codes": [],
                    "deferred_positions": (
                        {}
                        if terminal
                        else {"100101": {"attempt_count": calls, "terminal": False}}
                    ),
                    "failed_positions": (
                        {"100101": {"attempt_count": calls, "terminal": True}}
                        if terminal
                        else {}
                    ),
                    "site_restriction": None,
                }
                (scope / "checkpoint.json").write_text(
                    json.dumps(checkpoint), encoding="utf-8"
                )
                return self._site_outcome(
                    "boss", stop_reason="process_completed", added=0
                )

            with (
                patch("drv_crawler.recruitment._run_boss", side_effect=run_boss),
                patch("drv_crawler.recruitment.time.monotonic", return_value=0.0),
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(config, site_name="boss")

            self.assertEqual(3, calls)
            sleep.assert_not_called()
            self.assertEqual("process_completed", report["sites"][0]["stop_reason"])

    def test_full_probe_does_not_retry_job51_session_failure_without_cooldown(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)
            scope = _recruitment_scope_dir(
                config, state_root, "job51", per_category=2
            )
            scope.mkdir(parents=True)
            (scope / "coverage.json").write_text(
                json.dumps(
                    {
                        "rate_limit_cooldown": None,
                        "categories": [
                            {
                                "search_code": "LEAF",
                                "status": "pending",
                                "attempts": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            outcome = self._site_outcome(
                "job51", stop_reason="SessionRequiredError", added=0
            )

            with (
                patch("drv_crawler.recruitment._run_job51", return_value=outcome) as run,
                patch("drv_crawler.recruitment.time.monotonic", return_value=0.0),
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(config, site_name="job51")

            run.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual("SessionRequiredError", report["sites"][0]["stop_reason"])

    def test_full_probe_does_not_retry_boss_exit_without_cooldown(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)
            scope = _recruitment_scope_dir(config, state_root, "boss", per_category=2)
            scope.mkdir(parents=True)
            (scope / "checkpoint.json").write_text(
                json.dumps(
                    {
                        "catalog": [{"position_code": "100101"}],
                        "completed_position_codes": [],
                        "deferred_positions": {"100101": {"attempt_count": 1}},
                        "failed_positions": {},
                        "site_restriction": None,
                    }
                ),
                encoding="utf-8",
            )
            outcome = self._site_outcome(
                "boss", stop_reason="process_exit_1", added=0
            )

            with (
                patch("drv_crawler.recruitment._run_boss", return_value=outcome) as run,
                patch("drv_crawler.recruitment.time.monotonic", return_value=0.0),
                patch("drv_crawler.recruitment.time.sleep") as sleep,
            ):
                _run_dir, report = run_recruitment(config, site_name="boss")

            run.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual("process_exit_1", report["sites"][0]["stop_reason"])

    def test_job51_runner_uses_shared_runtime_session_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)
            captured: dict[str, object] = {}

            def run_job51(crawl_config: object) -> SimpleNamespace:
                captured["config"] = crawl_config
                crawl_config.output_path.parent.mkdir(parents=True, exist_ok=True)
                crawl_config.output_path.write_text("", encoding="utf-8")
                crawl_config.coverage_path.write_text(
                    json.dumps({"coverage_rate": 0.0}), encoding="utf-8"
                )
                return SimpleNamespace(
                    to_dict=lambda: {
                        "stop_reason": "work_queue_exhausted",
                        "records_total": 0,
                    }
                )

            with patch("drv_crawler.job51.run_job51", side_effect=run_job51):
                _run_job51(
                    config,
                    root / "run",
                    state_root,
                    per_category=2,
                    max_categories=1,
                    numeric_salary_only=True,
                )

            crawl_config = captured["config"]
            self.assertEqual(
                (state_root / "job51" / "runtime_session_state.json").resolve(),
                crawl_config.runtime_session_state_path,
            )
            self.assertEqual(
                (state_root / "job51" / "browser_profile").resolve(),
                crawl_config.persistent_profile_dir,
            )
            self.assertTrue(crawl_config.numeric_salary_only)

    def test_cli_accepts_bounded_jobs_command(self) -> None:
        args = build_parser().parse_args(
            ["jobs", "--site", "all", "--per-category", "3", "--max-categories", "5"]
        )

        self.assertEqual("jobs", args.command)
        self.assertEqual(3, args.per_category)
        self.assertEqual(5, args.max_categories)

    def test_cli_accepts_per_category_50_numeric_salary_scope(self) -> None:
        args = build_parser().parse_args(
            [
                "jobs",
                "--site",
                "all",
                "--per-category",
                "50",
                "--numeric-salary-only",
            ]
        )

        self.assertEqual(50, args.per_category)
        self.assertTrue(args.numeric_salary_only)

    def test_numeric_scope_is_separate_without_moving_legacy_job51_scope(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)

            legacy = _recruitment_scope_dir(
                config, state_root, "job51", per_category=1
            )
            expected_legacy = _scope_dir(
                state_root,
                "job51",
                per_category=1,
                scope={"city_code": "000000"},
            )
            numeric = _recruitment_scope_dir(
                config,
                state_root,
                "job51",
                per_category=1,
                numeric_salary_only=True,
            )

            self.assertEqual(expected_legacy, legacy)
            self.assertNotEqual(legacy, numeric)

    def test_numeric_scope_is_separate_without_moving_legacy_liepin_scope(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)

            legacy = _recruitment_scope_dir(
                config, state_root, "liepin", per_category=1
            )
            expected_legacy = _scope_dir(
                state_root,
                "liepin",
                per_category=1,
                scope={"home_url": "https://www.liepin.com/"},
            )
            numeric = _recruitment_scope_dir(
                config,
                state_root,
                "liepin",
                per_category=1,
                numeric_salary_only=True,
            )

            self.assertEqual(expected_legacy, legacy)
            self.assertNotEqual(legacy, numeric)

    def test_boss_per_category_50_scope_uses_effective_ten_page_budget(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            config = write_config(root / "config.yaml", root / "out", state_root)

            scope = _recruitment_scope_dir(
                config,
                state_root,
                "boss",
                per_category=50,
                numeric_salary_only=True,
            )
            expected = _scope_dir(
                state_root,
                "boss",
                per_category=50,
                scope={"city": "全国", "pages": 10, "numeric": True},
            )

            self.assertEqual(expected, scope)

    def test_recruitment_rejects_more_than_50_records_per_category(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")

            with self.assertRaisesRegex(ValueError, "per_category"):
                run_recruitment(config, site_name="job51", per_category=51)

    def test_recruitment_accepts_per_category_50_numeric_scope(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            outcome = self._site_outcome(
                "job51",
                status="success",
                stop_reason="work_queue_exhausted",
                records=50,
                coverage_rate=1.0,
            )
            with patch("drv_crawler.recruitment._run_job51", return_value=outcome):
                _run_dir, report = run_recruitment(
                    config,
                    site_name="job51",
                    per_category=50,
                    max_categories=1,
                    numeric_salary_only=True,
                )

            self.assertEqual(50, report["per_category"])
            self.assertTrue(report["numeric_salary_only"]["job51"])

    def test_quota_summary_is_normalized_across_three_site_schemas(self) -> None:
        job51 = _normalized_quota_summary(
            {
                "quota_counts": {
                    "unique_search_codes_total": 10,
                    "target_per_unique_search_code": 50,
                    "numeric_salary_only": True,
                    "quota_met": 6,
                    "exhausted_below_quota": 1,
                    "empty": 1,
                    "page_limit_below_quota": 0,
                    "failed": 0,
                    "pending": 1,
                    "running": 1,
                },
                "quota_met_rate": 0.6,
            }
        )
        boss = _normalized_quota_summary(
            {
                "position_codes_total": 10,
                "position_codes_pending": 1,
                "position_codes_deferred": 1,
                "run_config": {"numeric_price_only": True},
                "quota_summary": {
                    "quota_target": 50,
                    "quota_met": 6,
                    "exhausted_below_quota": 1,
                    "empty": 1,
                    "page_limit_below_quota": 0,
                    "failed": 0,
                },
            }
        )
        liepin = _normalized_quota_summary(
            {
                "quota_target": 50,
                "numeric_salary_only": True,
                "quota_outcomes": {
                    "quota_met": 6,
                    "exhausted_below_quota": 1,
                    "empty": 1,
                    "page_limit_below_quota": 0,
                    "failed": 0,
                    "pending": 2,
                    "running": 0,
                },
                "summary": {"categories_total": 10},
            }
        )

        self.assertIsNotNone(job51)
        self.assertIsNotNone(boss)
        self.assertIsNotNone(liepin)
        for quota in (job51, boss, liepin):
            self.assertEqual(50, quota["target_per_category"])
            self.assertEqual(10, quota["categories_total"])
            self.assertEqual(6, quota["quota_met"])
            self.assertEqual(2, quota["pending"])
            self.assertEqual(0.6, quota["quota_met_rate"])
            self.assertTrue(quota["numeric_salary_only"])

    def test_expired_absolute_cooldown_ignores_stale_remaining_value(self) -> None:
        with patch("drv_crawler.recruitment.time.time", return_value=2_000.0):
            remaining = _cooldown_seconds_from_payload(
                {
                    "cooldown_until": "1970-01-01T00:16:40+00:00",
                    "remaining_seconds": 30.0,
                }
            )

        self.assertEqual(0.0, remaining)

    def test_enabled_sites_come_from_recruitment_section(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")

            self.assertEqual(
                ["boss", "job51", "liepin"], enabled_recruitment_sites(config)
            )

    def test_recruitment_doctor_requires_boss_cdp(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            with patch(
                "drv_crawler.recruitment.socket.create_connection",
                side_effect=OSError("connection refused"),
            ):
                result = recruitment_doctor(config)

            self.assertFalse(result["boss"]["cdp_ready"])
            self.assertFalse(result["boss"]["login_ready"])
            self.assertFalse(result["ok"])

    def test_recruitment_doctor_rejects_restricted_boss_session(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            with (
                patch("drv_crawler.recruitment.socket.create_connection"),
                patch(
                    "drv_crawler.recruitment.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=1,
                        stdout="BOSS 接口返回限制状态（code: 37）\n",
                    ),
                ),
            ):
                result = recruitment_doctor(config)

            self.assertTrue(result["boss"]["cdp_ready"])
            self.assertFalse(result["boss"]["login_ready"])
            self.assertEqual(1, result["boss"]["check_return_code"])
            self.assertIn("code: 37", result["boss"]["check_message"])
            self.assertFalse(result["ok"])

    def test_record_conversion_preserves_salary_and_category(self) -> None:
        value = {
            "record_id": "boss-1",
            "site_name": "boss",
            "content_category": "full_time_job",
            "title_raw": "Java Engineer",
            "list_url": "https://example.test/jobs?position=100101",
            "detail_url": "https://example.test/job/1",
            "category_level_1_raw": "技术",
            "category_level_2_raw": "后端开发",
            "category_level_3_raw": "Java",
            "category_path_raw": "技术 > 后端开发 > Java",
            "category_source": "official_position_tree",
            "category_identity_verified": True,
            "price_evidence": [
                {
                    "raw_text": "20-30K",
                    "price_type": "monthly_salary",
                    "price_source": "api_response",
                    "evidence_url": "https://example.test/job/1",
                    "period_text": "month",
                }
            ],
        }

        record = _record_from_dict(value)

        self.assertEqual("Java Engineer", record.title_raw)
        self.assertEqual("Java", record.category_level_3_raw)
        self.assertEqual("20-30K", record.price_raw_joined)
        self.assertTrue(record.category_identity_verified)

    def test_record_conversion_keeps_boss_alias_category_paths(self) -> None:
        value = {
            "site_name": "boss",
            "title_raw": "Engineer",
            "list_url": "https://example.test/jobs",
            "category_paths_raw": ["技术 > 后端开发 > Java", "技术 > 软件开发 > Java"],
            "category_matches": [{"position_code": "100101"}],
        }

        record = _record_from_dict(value)

        self.assertEqual(value["category_paths_raw"], record.metadata["category_paths_raw"])
        self.assertEqual(value["category_matches"], record.metadata["category_matches"])

    def test_proxy_disabled_spelling_is_mapped_per_adapter(self) -> None:
        self.assertEqual("off", _adapter_proxy_policy("direct", "job51"))
        self.assertEqual("off", _adapter_proxy_policy("off", "job51"))
        self.assertEqual("direct", _adapter_proxy_policy("off", "liepin"))
        self.assertEqual("required", _adapter_proxy_policy("required", "liepin"))

    def test_config_relative_state_path_uses_config_directory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            self.assertEqual(root / "relative-state", _config_relative_path(config, "relative-state"))

    def test_category_limit_is_partial_even_when_command_succeeds(self) -> None:
        self.assertEqual(
            "partial",
            _status_from_coverage(
                {"coverage_rate": 0.25}, records=4, command_ok=True
            ),
        )

    def test_page_limit_below_quota_is_partial_at_full_category_coverage(self) -> None:
        coverage = {
            "position_code_coverage_rate": 1.0,
            "position_codes_total": 1,
            "quota_summary": {
                "quota_target": 50,
                "quota_met": 0,
                "exhausted_below_quota": 0,
                "empty": 0,
                "page_limit_below_quota": 1,
                "failed": 0,
            },
        }

        self.assertEqual(
            "partial",
            _status_from_coverage(coverage, records=30, command_ok=True),
        )

    def test_boss_per_category_50_command_uses_effective_ten_pages(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            captured: dict[str, object] = {}

            def run_child(command: list[str], **_kwargs: object) -> SimpleNamespace:
                captured["command"] = command
                output_path = Path(command[command.index("--output") + 1])
                coverage_path = Path(command[command.index("--coverage-output") + 1])
                output_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")
                coverage_path.write_text(
                    json.dumps({"position_code_coverage_rate": 0.0}),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0)

            with (
                patch("drv_crawler.recruitment.subprocess.run", side_effect=run_child),
                patch("drv_crawler.recruitment._export_site_snapshot"),
            ):
                _run_boss(
                    config,
                    root / "run",
                    root / "state",
                    per_category=50,
                    max_categories=1,
                    numeric_salary_only=True,
                )

            command = captured["command"]
            self.assertEqual("10", command[command.index("--pages") + 1])
            self.assertEqual(
                "50", command[command.index("--per-position-limit") + 1]
            )
            self.assertIn("--numeric-price-only", command)

    def test_liepin_runner_receives_numeric_and_page_budget(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            captured: dict[str, object] = {}

            class FakeCrawler:
                def __init__(self, crawl_config: object) -> None:
                    captured["config"] = crawl_config

                def __enter__(self) -> "FakeCrawler":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def run(self) -> SimpleNamespace:
                    crawl_config = captured["config"]
                    crawl_config.jobs_path.parent.mkdir(parents=True, exist_ok=True)
                    crawl_config.jobs_path.write_text("", encoding="utf-8")
                    crawl_config.coverage_path.write_text(
                        json.dumps(
                            {
                                "quota_target": 50,
                                "numeric_salary_only": True,
                                "quota_outcomes": {"quota_met": 0},
                                "summary": {"categories_total": 0},
                            }
                        ),
                        encoding="utf-8",
                    )
                    return SimpleNamespace(
                        to_dict=lambda: {
                            "status": "partial",
                            "stop_reason": "category_limit_reached",
                        }
                    )

            with patch("drv_crawler.liepin.LiepinCrawler", FakeCrawler):
                _run_liepin(
                    config,
                    root / "run",
                    root / "state",
                    per_category=50,
                    max_categories=1,
                    numeric_salary_only=True,
                )

            crawl_config = captured["config"]
            self.assertTrue(crawl_config.numeric_salary_only)
            self.assertEqual(20, crawl_config.max_pages_per_category)

    def test_boss_uses_absolute_state_paths_across_subprocess_working_directory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            caller_dir = root / "caller"
            config_dir = root / "config"
            run_dir = root / "run"
            caller_dir.mkdir()
            config_dir.mkdir()
            run_dir.mkdir()
            config = write_config(
                config_dir / "config.yaml",
                root / "out",
                Path("relative-state"),
            )
            captured: dict[str, object] = {}

            def run_child(command: list[str], **kwargs: object) -> SimpleNamespace:
                captured["command"] = command
                captured["cwd"] = kwargs["cwd"]
                captured["timeout"] = kwargs["timeout"]

                def argument(name: str) -> Path:
                    return Path(command[command.index(name) + 1])

                for name in ("--output", "--checkpoint", "--coverage-output"):
                    self.assertTrue(argument(name).is_absolute())
                output_path = argument("--output")
                coverage_path = argument("--coverage-output")
                output_path.write_text(
                    json.dumps(
                        {
                            "position_codes_total": 851,
                            "positions_completed": 1,
                            "jobs": [{"record_id": "boss-1"}],
                        }
                    ),
                    encoding="utf-8",
                )
                coverage_path.write_text(
                    json.dumps({"position_code_coverage_rate": 1 / 851}),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0)

            previous = Path.cwd()
            os.chdir(caller_dir)
            try:
                with (
                    patch("drv_crawler.recruitment.subprocess.run", side_effect=run_child),
                    patch("drv_crawler.recruitment._export_site_snapshot"),
                ):
                    outcome = _run_boss(
                        config,
                        run_dir,
                        Path("relative-state"),
                        per_category=1,
                        max_categories=1,
                    )
            finally:
                os.chdir(previous)

            command = captured["command"]
            self.assertIsInstance(command, list)
            self.assertIn("--all-positions", command)
            self.assertEqual("1", command[command.index("--max-positions") + 1])
            self.assertEqual("12.0", command[command.index("--category-interval-seconds") + 1])
            self.assertEqual("8.0", command[command.index("--category-jitter-seconds") + 1])
            self.assertEqual("3600.0", command[command.index("--block-cooldown-seconds") + 1])
            self.assertEqual(config.path.resolve().parent, captured["cwd"])
            self.assertEqual(21600, captured["timeout"])
            self.assertEqual(1, outcome.records)
            self.assertEqual("partial", outcome.status)

    def test_boss_timeout_exports_existing_checkpoint_records(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            scope_dir = _recruitment_scope_dir(
                config, root / "state", "boss", per_category=1
            ).resolve()
            scope_dir.mkdir(parents=True)
            record = {
                "record_id": "boss-old",
                "site_name": "boss",
                "content_category": "full_time_job",
                "title_raw": "Engineer",
                "list_url": "https://www.zhipin.com/web/geek/jobs",
                "detail_url": "https://www.zhipin.com/job_detail/1.html",
                "price_evidence": [
                    {
                        "raw_text": "20-30K",
                        "price_type": "salary_range",
                        "price_source": "api_response",
                        "evidence_url": "https://www.zhipin.com/job_detail/1.html",
                    }
                ],
            }
            (scope_dir / "jobs.json").write_text(
                json.dumps({"jobs": [record]}), encoding="utf-8"
            )
            (scope_dir / "coverage.json").write_text(
                json.dumps({"position_code_coverage_rate": 0.5}), encoding="utf-8"
            )
            (scope_dir / "checkpoint.json").write_text("{}", encoding="utf-8")
            run_dir = root / "run"

            with patch(
                "drv_crawler.recruitment.subprocess.run",
                side_effect=subprocess.TimeoutExpired("boss", 21600),
            ) as run_process:
                outcome = _run_boss(
                    config,
                    run_dir,
                    root / "state",
                    per_category=1,
                    max_categories=1,
                )

            self.assertEqual(21600, run_process.call_args.kwargs["timeout"])
            self.assertEqual("partial", outcome.status)
            self.assertEqual("process_timeout", outcome.stop_reason)
            self.assertEqual(1, outcome.records)
            self.assertTrue(outcome.summary["checkpoint_present"])
            self.assertEqual(1, outcome.summary["records_recovered_from_checkpoint"])
            self.assertTrue((run_dir / "boss" / "records.jsonl").exists())
            self.assertTrue((run_dir / "boss" / "checkpoint.json").exists())

    def test_second_process_fails_fast_when_scope_is_locked(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            scope_dir = _recruitment_scope_dir(
                config, root / "state", "liepin", per_category=2
            )
            ready_path = root / "ready"
            code = "\n".join(
                (
                    "import sys, time",
                    "from pathlib import Path",
                    "from drv_crawler.recruitment import _scope_run_lock",
                    "with _scope_run_lock(Path(sys.argv[1])):",
                    "    Path(sys.argv[2]).write_text('ready', encoding='utf-8')",
                    "    time.sleep(30)",
                )
            )
            holder = subprocess.Popen(
                [sys.executable, "-c", code, os.fspath(scope_dir), os.fspath(ready_path)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5
                while not ready_path.exists() and time.monotonic() < deadline:
                    if holder.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertTrue(ready_path.exists(), "lock holder did not start")
                started = time.monotonic()
                with patch(
                    "drv_crawler.recruitment._run_liepin",
                    side_effect=AssertionError("busy scope must not start the adapter"),
                ):
                    _run_dir, report = run_recruitment(
                        config, site_name="liepin", output_root=root / "snapshot"
                    )
                elapsed = time.monotonic() - started
            finally:
                holder.terminate()
                try:
                    holder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    holder.kill()
                    holder.wait(timeout=5)

            self.assertLess(elapsed, 1.0)
            self.assertEqual("failed", report["sites"][0]["status"])
            self.assertEqual(
                "RecruitmentScopeBusy", report["sites"][0]["stop_reason"]
            )
            self.assertIn("already running", report["sites"][0]["summary"]["message"])

    def test_different_scopes_for_same_site_share_site_lock(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            site_dir = root / "state" / "liepin"
            ready_path = root / "ready"
            code = "\n".join(
                (
                    "import sys, time",
                    "from pathlib import Path",
                    "from drv_crawler.recruitment import _scope_run_lock",
                    "with _scope_run_lock(Path(sys.argv[1]), lock_name='.site.run.lock'):",
                    "    Path(sys.argv[2]).write_text('ready', encoding='utf-8')",
                    "    time.sleep(30)",
                )
            )
            holder = subprocess.Popen(
                [sys.executable, "-c", code, os.fspath(site_dir), os.fspath(ready_path)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5
                while not ready_path.exists() and time.monotonic() < deadline:
                    if holder.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertTrue(ready_path.exists(), "site lock holder did not start")
                with patch(
                    "drv_crawler.recruitment._run_liepin",
                    side_effect=AssertionError("busy site must not start another scope"),
                ):
                    _run_dir, report = run_recruitment(
                        config,
                        site_name="liepin",
                        per_category=50,
                        numeric_salary_only=True,
                        output_root=root / "snapshot",
                    )
            finally:
                holder.terminate()
                try:
                    holder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    holder.kill()
                    holder.wait(timeout=5)

            self.assertEqual("failed", report["sites"][0]["status"])
            self.assertEqual(
                "RecruitmentScopeBusy", report["sites"][0]["stop_reason"]
            )
            self.assertIn("already running", report["sites"][0]["summary"]["message"])

    def test_crashed_process_does_not_leave_permanent_scope_lock(self) -> None:
        with TemporaryDirectory() as directory:
            scope_dir = Path(directory) / "scope"
            code = "\n".join(
                (
                    "import os, sys",
                    "from pathlib import Path",
                    "from drv_crawler.recruitment import _scope_run_lock",
                    "with _scope_run_lock(Path(sys.argv[1])):",
                    "    os._exit(23)",
                )
            )
            crashed = subprocess.run(
                [sys.executable, "-c", code, os.fspath(scope_dir)],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
            )

            self.assertEqual(23, crashed.returncode)
            self.assertTrue((scope_dir / ".run.lock").exists())
            with _scope_run_lock(scope_dir) as lock_path:
                self.assertEqual(scope_dir / ".run.lock", lock_path)

    def test_boss_nonzero_exit_is_failed_even_when_persistent_records_exist(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            scope_dir = _scope_dir(
                root / "state",
                "boss",
                per_category=1,
                scope={"city": "全国", "pages": 1, "numeric": True},
            ).resolve()
            scope_dir.mkdir(parents=True)
            (scope_dir / "jobs.json").write_text(
                json.dumps({"jobs": [{"record_id": "boss-old"}]}),
                encoding="utf-8",
            )
            (scope_dir / "coverage.json").write_text(
                json.dumps({"position_code_coverage_rate": 0.5}),
                encoding="utf-8",
            )

            with (
                patch(
                    "drv_crawler.recruitment.subprocess.run",
                    return_value=SimpleNamespace(returncode=1),
                ),
                patch("drv_crawler.recruitment._export_site_snapshot"),
            ):
                outcome = _run_boss(
                    config,
                    root / "run",
                    root / "state",
                    per_category=1,
                    max_categories=1,
                )

            self.assertEqual("failed", outcome.status)
            self.assertEqual("process_exit_1", outcome.stop_reason)
            self.assertEqual(1, outcome.records)

    def test_boss_success_without_required_artifacts_fails_explicitly(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            with patch(
                "drv_crawler.recruitment.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ):
                with self.assertRaisesRegex(RuntimeError, "required artifacts"):
                    _run_boss(
                        config,
                        root / "run",
                        root / "state",
                        per_category=1,
                        max_categories=1,
                    )
        self.assertEqual(
            "success",
            _status_from_coverage(
                {"coverage_rate": 1.0}, records=4, command_ok=True
            ),
        )
        self.assertEqual(
            "failed",
            _status_from_coverage(
                {"coverage_rate": 0.0}, records=0, command_ok=False
            ),
        )

    def test_existing_checkpoint_records_are_exported_after_startup_failure(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_dir.mkdir()
            output_path = state_dir / "jobs.jsonl"
            coverage_path = state_dir / "coverage.json"
            record = {
                "record_id": "liepin-1",
                "site_name": "liepin",
                "content_category": "full_time_job",
                "title_raw": "Engineer",
                "list_url": "https://www.liepin.com/career/java/",
                "detail_url": "https://www.liepin.com/job/1.shtml",
                "category_level_1_raw": "IT/互联网",
                "category_level_2_raw": "后端开发",
                "category_level_3_raw": "Java",
                "price_evidence": [
                    {
                        "raw_text": "20-30k",
                        "price_type": "salary_range",
                        "price_source": "list_page",
                        "evidence_url": "https://www.liepin.com/job/1.shtml",
                    }
                ],
            }
            output_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            coverage_path.write_text(
                json.dumps({"coverage_rate": 0.25}), encoding="utf-8"
            )
            run_dir = root / "run"

            outcome = _recover_site_snapshot(
                run_dir,
                "liepin",
                output_path,
                coverage_path,
                RuntimeError("login required"),
                records_before=1,
            )

            self.assertEqual("partial", outcome.status)
            self.assertEqual(1, outcome.records)
            self.assertEqual(0, outcome.summary["records_added_this_run"])
            self.assertTrue((run_dir / "liepin" / "records.xlsx").exists())

    def test_recruitment_auth_requires_verified_state_or_nonempty_cookie_file(self) -> None:
        self.assertFalse(_recruitment_auth_succeeded({"login_verified": False, "cookie_count": 3}))
        self.assertFalse(_recruitment_auth_succeeded({"cookie_count": 0}))
        self.assertTrue(_recruitment_auth_succeeded({"login_verified": True, "cookie_count": 3}))

    def test_job51_auth_maps_direct_proxy_policy_to_off(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")
            with (
                patch(
                    "drv_crawler.recruitment._proxy_settings",
                    return_value=("direct", "http://127.0.0.1:7897"),
                ),
                patch(
                    "drv_crawler.job51.crawler.save_login_state",
                    return_value=3,
                ) as save_login_state,
            ):
                result = run_recruitment_auth(config, "job51", wait_seconds=10)

            crawl_config = save_login_state.call_args.args[0]
            self.assertEqual("off", crawl_config.proxy_policy)
            self.assertEqual(3, result["cookie_count"])

    def test_site_failure_does_not_prevent_other_sites(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = write_config(root / "config.yaml", root / "out", root / "state")

            def fail(*_args: object, **_kwargs: object) -> RecruitmentSiteOutcome:
                raise RuntimeError("login required")

            def succeed(
                _config: ProjectConfig,
                run_dir: Path,
                _state_root: Path,
                **_kwargs: object,
            ) -> RecruitmentSiteOutcome:
                site = "job51" if not (run_dir / "job51" / "summary.json").exists() else "liepin"
                (run_dir / site).mkdir(parents=True, exist_ok=True)
                (run_dir / site / "summary.json").write_text("{}", encoding="utf-8")
                return RecruitmentSiteOutcome(
                    site_name=site,
                    status="partial",
                    records=1,
                    coverage_rate=0.1,
                    stop_reason="category_limit_reached",
                    summary={},
                )

            with (
                patch("drv_crawler.recruitment._run_boss", side_effect=fail),
                patch("drv_crawler.recruitment._run_job51", side_effect=succeed),
                patch("drv_crawler.recruitment._run_liepin", side_effect=succeed),
            ):
                run_dir, report = run_recruitment(
                    config, site_name="all", max_categories=1
                )

            self.assertTrue((run_dir / "run-report.json").exists())
            self.assertEqual(1, report["totals"]["failed"])
            self.assertEqual(2, report["totals"]["partial"])
            self.assertEqual(2, report["totals"]["records"])
            saved = json.loads((run_dir / "run-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["totals"], saved["totals"])


if __name__ == "__main__":
    unittest.main()
