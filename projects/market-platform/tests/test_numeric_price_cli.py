from __future__ import annotations

import os
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from drv_crawler.cli import build_parser, run_collection
from drv_crawler.models import SiteResult
from drv_crawler.publication import PublicationFilter
from drv_crawler.runner import run_sites


class FakeConfig:
    def section(self, name: str) -> dict[str, object]:
        if name == "export":
            return {"directory": "outputs"}
        return {}


class FakeClient:
    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class NumericPriceCliTests(unittest.TestCase):
    def test_collect_with_publication_filter_enables_complete_window(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "collect",
                "--site",
                "zbj",
                "--limit",
                "50",
                "--recent-months",
                "3",
                "--as-of",
                "2026-07-21",
            ]
        )
        result = SiteResult(site_name="zbj", requested_limit=50, status="success")

        with patch("drv_crawler.cli.select_sites", return_value=["zbj"]), patch(
            "drv_crawler.cli.run_sites",
            return_value=(Path("outputs/test"), [result], {}),
        ) as mocked_run_sites, patch("builtins.print"):
            exit_code = run_collection(args, FakeConfig(), mode="collect")  # type: ignore[arg-type]

        self.assertEqual(0, exit_code)
        self.assertTrue(mocked_run_sites.call_args.kwargs["complete_publication_window"])
        publication_filter = mocked_run_sites.call_args.kwargs["publication_filter"]
        self.assertEqual("2026-07-21", publication_filter.describe()["as_of"])
        self.assertEqual("2026-04-21", publication_filter.describe()["cutoff"])

    def test_sample_with_publication_filter_keeps_record_limit_mode(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["sample", "--site", "zbj", "--limit", "10", "--recent-months", "3"]
        )
        result = SiteResult(site_name="zbj", requested_limit=10, status="success")

        with patch("drv_crawler.cli.select_sites", return_value=["zbj"]), patch(
            "drv_crawler.cli.run_sites",
            return_value=(Path("outputs/test"), [result], {}),
        ) as mocked_run_sites, patch("builtins.print"):
            exit_code = run_collection(args, FakeConfig(), mode="sample")  # type: ignore[arg-type]

        self.assertEqual(0, exit_code)
        self.assertFalse(mocked_run_sites.call_args.kwargs["complete_publication_window"])

    def test_sample_and_collect_accept_publication_window_options(self) -> None:
        parser = build_parser()
        try:
            recent = parser.parse_args(
                ["sample", "--site", "epwk", "--recent-months", "3"]
            )
            cutoff = parser.parse_args(
                [
                    "collect",
                    "--site",
                    "zbj",
                    "--limit",
                    "50",
                    "--published-since",
                    "2026-04-21",
                ]
            )
        except SystemExit as exc:
            self.fail(f"publication window CLI options are missing: {exc}")

        self.assertEqual(3, recent.recent_months)
        self.assertEqual("2026-04-21", cutoff.published_since)
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "sample",
                    "--site",
                    "epwk",
                    "--recent-months",
                    "3",
                    "--published-since",
                    "2026-04-21",
                ]
            )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["sample", "--site", "epwk", "--recent-months", "0"]
            )

    def test_sample_and_collect_accept_numeric_price_only(self) -> None:
        parser = build_parser()
        sample = parser.parse_args(
            ["sample", "--site", "zbj", "--limit", "50", "--numeric-price-only"]
        )
        collect = parser.parse_args(
            [
                "collect",
                "--site",
                "zbj",
                "--limit",
                "100",
                "--approved",
                "--numeric-price-only",
            ]
        )
        default_sample = parser.parse_args(["sample", "--site", "zbj"])

        self.assertTrue(sample.numeric_price_only)
        self.assertTrue(collect.numeric_price_only)
        self.assertFalse(default_sample.numeric_price_only)

    def test_runner_passes_numeric_price_only_to_adapter(self) -> None:
        calls: list[bool] = []
        config = FakeConfig()

        class FakeAdapter:
            adapter_name = "fake"
            difficulty = 1

            def __init__(self, *_: object) -> None:
                pass

            def crawl(
                self,
                limit: int,
                *,
                with_details: bool = True,
                numeric_price_only: bool = False,
            ) -> SiteResult:
                calls.append(numeric_price_only)
                result = SiteResult(site_name="demo", requested_limit=limit)
                result.finish()
                return result

        with TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"CRAWLER_OUTPUT_DIR": temp_dir}
        ), patch.dict("drv_crawler.runner.ADAPTERS", {"demo": FakeAdapter}, clear=True), patch(
            "drv_crawler.runner.ResilientHttpClient", return_value=FakeClient()
        ) as client_factory, patch("drv_crawler.runner.export_site_result"), patch(
            "drv_crawler.runner.export_run_report", return_value={}
        ), patch("drv_crawler.runner.CheckpointManager.save"):
            run_dir, _, _ = run_sites(
                config,  # type: ignore[arg-type]
                site_names=["demo"],
                limit=1,
                with_details=False,
                mode="sample",
                numeric_price_only=True,
            )

        self.assertEqual([True], calls)
        self.assertEqual(Path(temp_dir), run_dir.parent)
        client_factory.assert_called_once_with(config, site_name="demo")

    def test_runner_sets_publication_filter_on_adapter(self) -> None:
        calls: list[dict[str, object] | None] = []

        class FakeAdapter:
            adapter_name = "fake"
            difficulty = 1

            def __init__(self, *_: object) -> None:
                self.publication_filter = None

            def crawl(
                self,
                limit: int,
                *,
                with_details: bool = True,
                numeric_price_only: bool = False,
            ) -> SiteResult:
                calls.append(
                    self.publication_filter.describe()
                    if self.publication_filter is not None
                    else None
                )
                result = SiteResult(site_name="demo", requested_limit=limit)
                result.finish()
                return result

        publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        try:
            with TemporaryDirectory() as temp_dir, patch.dict(
                os.environ, {"CRAWLER_OUTPUT_DIR": temp_dir}
            ), patch.dict(
                "drv_crawler.runner.ADAPTERS", {"demo": FakeAdapter}, clear=True
            ), patch(
                "drv_crawler.runner.ResilientHttpClient", return_value=FakeClient()
            ), patch("drv_crawler.runner.export_site_result"), patch(
                "drv_crawler.runner.export_run_report", return_value={}
            ), patch("drv_crawler.runner.CheckpointManager.save"):
                run_sites(
                    FakeConfig(),  # type: ignore[arg-type]
                    site_names=["demo"],
                    limit=1,
                    with_details=False,
                    mode="sample",
                    publication_filter=publication_filter,
                )
        except TypeError as exc:
            self.fail(f"runner does not accept publication_filter: {exc}")

        self.assertEqual("2026-04-21", calls[0]["cutoff"] if calls[0] else None)

    def test_runner_enables_complete_publication_window_on_adapter(self) -> None:
        calls: list[bool] = []

        class FakeAdapter:
            adapter_name = "fake"
            difficulty = 1

            def __init__(self, *_: object) -> None:
                self.publication_filter = None
                self.complete_publication_window = False

            def crawl(self, limit: int, **_: object) -> SiteResult:
                calls.append(self.complete_publication_window)
                result = SiteResult(site_name="demo", requested_limit=limit)
                result.finish()
                return result

        publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        with TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"CRAWLER_OUTPUT_DIR": temp_dir}
        ), patch.dict(
            "drv_crawler.runner.ADAPTERS", {"demo": FakeAdapter}, clear=True
        ), patch(
            "drv_crawler.runner.ResilientHttpClient", return_value=FakeClient()
        ), patch("drv_crawler.runner.export_site_result"), patch(
            "drv_crawler.runner.export_run_report", return_value={}
        ), patch("drv_crawler.runner.CheckpointManager.save"):
            run_sites(
                FakeConfig(),  # type: ignore[arg-type]
                site_names=["demo"],
                limit=100,
                with_details=False,
                mode="collect",
                publication_filter=publication_filter,
                complete_publication_window=True,
            )

        self.assertEqual([True], calls)

    def test_runner_failure_keeps_publication_filter_metadata(self) -> None:
        class FailingAdapter:
            adapter_name = "failing"
            difficulty = 1

            def __init__(self, *_: object) -> None:
                self.publication_filter = None

            def crawl(self, *_: object, **__: object) -> SiteResult:
                raise RuntimeError("simulated adapter failure")

        publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        with TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"CRAWLER_OUTPUT_DIR": temp_dir}
        ), patch.dict(
            "drv_crawler.runner.ADAPTERS", {"demo": FailingAdapter}, clear=True
        ), patch(
            "drv_crawler.runner.ResilientHttpClient", return_value=FakeClient()
        ), patch("drv_crawler.runner.export_site_result"), patch(
            "drv_crawler.runner.export_run_report", return_value={}
        ), patch("drv_crawler.runner.CheckpointManager.save"):
            _, results, _ = run_sites(
                FakeConfig(),  # type: ignore[arg-type]
                site_names=["demo"],
                limit=1,
                with_details=False,
                mode="sample",
                publication_filter=publication_filter,
            )

        self.assertEqual("2026-04-21", results[0].publication_filter["cutoff"])

    def test_runner_window_failure_is_reported_as_incomplete(self) -> None:
        class FailingAdapter:
            adapter_name = "failing"
            difficulty = 1

            def __init__(self, *_: object) -> None:
                self.publication_filter = None

            def crawl(self, *_: object, **__: object) -> SiteResult:
                raise RuntimeError("simulated adapter failure")

        publication_filter = PublicationFilter(
            recent_months=3, as_of=date(2026, 7, 21)
        )
        with TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"CRAWLER_OUTPUT_DIR": temp_dir}
        ), patch.dict(
            "drv_crawler.runner.ADAPTERS", {"demo": FailingAdapter}, clear=True
        ), patch(
            "drv_crawler.runner.ResilientHttpClient", return_value=FakeClient()
        ), patch("drv_crawler.runner.export_site_result"), patch(
            "drv_crawler.runner.export_run_report", return_value={}
        ), patch("drv_crawler.runner.CheckpointManager.save"):
            _, results, _ = run_sites(
                FakeConfig(),  # type: ignore[arg-type]
                site_names=["demo"],
                limit=1,
                with_details=False,
                mode="collect",
                publication_filter=publication_filter,
                complete_publication_window=True,
            )

        self.assertEqual("publication_window", results[0].completion_mode)
        self.assertFalse(results[0].window_complete)
        self.assertEqual("site_error", results[0].stop_reason)
        self.assertEqual("failed", results[0].status)


if __name__ == "__main__":
    unittest.main()
