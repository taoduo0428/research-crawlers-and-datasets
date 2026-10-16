from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from drv_crawler.cli import doctor


class FakeConfig:
    path = Path("crawler.config.yaml")
    site_names = ["boss", "epwk", "zbj"]

    @staticmethod
    def site(_name: str) -> dict[str, object]:
        return {}

    @staticmethod
    def section(name: str) -> dict[str, object]:
        if name == "recruitment":
            return {
                "state_directory": ".crawler-state/recruitment",
                "sites": {
                    "boss": {"enabled": True, "cdp_port": 9222},
                    "job51": {"enabled": True},
                    "liepin": {"enabled": True},
                },
            }
        return {}


class FakeHttpClient:
    proxy_url = None

    def __init__(self, _config: FakeConfig) -> None:
        pass

    def __enter__(self) -> "FakeHttpClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class DoctorTests(unittest.TestCase):
    def _run_doctor(
        self, *, missing: str | None = None, recruitment_ok: bool = True
    ) -> tuple[int, dict[str, object], list[str]]:
        imports: list[str] = []

        def find_spec(name: str) -> object | None:
            imports.append(name)
            return None if name == missing else object()

        output = io.StringIO()
        with (
            patch("drv_crawler.cli.importlib.util.find_spec", side_effect=find_spec),
            patch("drv_crawler.cli.ResilientHttpClient", FakeHttpClient),
            patch(
                "drv_crawler.cli.recruitment_doctor",
                return_value={"enabled_sites": ["boss"], "ok": recruitment_ok},
            ),
            patch("sys.stdout", output),
        ):
            exit_code = doctor(FakeConfig())  # type: ignore[arg-type]
        return exit_code, json.loads(output.getvalue()), imports

    def test_doctor_requires_epwk_transport_dependencies(self) -> None:
        exit_code, report, imports = self._run_doctor()

        self.assertEqual(0, exit_code)
        dependencies = report["dependencies"]
        self.assertTrue(dependencies["curl_cffi"])
        self.assertTrue(dependencies["certifi"])
        self.assertIn("curl_cffi", imports)
        self.assertIn("certifi", imports)

    def test_missing_epwk_transport_dependency_fails_doctor(self) -> None:
        for dependency in ("curl_cffi", "certifi"):
            with self.subTest(dependency=dependency):
                exit_code, report, _ = self._run_doctor(missing=dependency)
                self.assertEqual(1, exit_code)
                self.assertFalse(report["dependencies"][dependency])
                self.assertFalse(report["ok"])

    def test_unready_recruitment_backend_fails_doctor(self) -> None:
        exit_code, report, _ = self._run_doctor(recruitment_ok=False)

        self.assertEqual(1, exit_code)
        self.assertFalse(report["recruitment"]["ok"])
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
