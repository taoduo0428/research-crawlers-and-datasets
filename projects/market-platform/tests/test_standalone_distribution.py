from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StandaloneDistributionTests(unittest.TestCase):
    def test_pyproject_declares_cli_and_runtime_dependencies(self) -> None:
        pyproject_path = ROOT / "pyproject.toml"
        self.assertTrue(pyproject_path.exists(), "pyproject.toml is required")
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]

        self.assertEqual(project["scripts"]["drv-crawler"], "drv_crawler.cli:main")
        self.assertEqual(project["requires-python"], ">=3.11")
        dependencies = "\n".join(project["dependencies"]).lower()
        for package in (
            "requests",
            "beautifulsoup4",
            "playwright",
            "pyyaml",
            "openpyxl",
            "websocket-client",
        ):
            self.assertIn(package, dependencies)

    def test_runtime_has_no_ai_or_codex_imports(self) -> None:
        runtime_files = [ROOT / "run.py", *(ROOT / "drv_crawler").glob("*.py")]
        forbidden_roots = {"codex", "mcp", "openai", "anthropic"}

        for path in runtime_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported_roots: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots.add(node.module.split(".", 1)[0])
            self.assertTrue(
                imported_roots.isdisjoint(forbidden_roots),
                f"{path.name} imports an interactive AI/Codex dependency",
            )

    def test_setup_script_bootstraps_without_uv_or_codex(self) -> None:
        setup_path = ROOT / "setup.ps1"
        self.assertTrue(setup_path.exists(), "setup.ps1 is required")
        content = setup_path.read_text(encoding="utf-8").lower()
        self.assertIn("-m venv", content)
        self.assertIn("-m pip", content)
        self.assertIn("run.py doctor", content)
        self.assertNotIn("uv pip", content)
        self.assertNotIn(".codex", content)

    def test_boss_cdp_source_is_colocated_with_crawler_package(self) -> None:
        boss_root = ROOT / "drv_crawler" / "boss_cdp"
        self.assertTrue((boss_root / "scripts" / "boss_cdp_raw.py").is_file())
        self.assertTrue((boss_root / "data" / "city_codes.json").is_file())
        self.assertTrue((boss_root / "LICENSE").is_file())

    def test_readme_documents_standalone_runtime_only(self) -> None:
        content = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertNotIn(".codex", content)
        self.assertNotIn("mcp", content)
        self.assertIn("不依赖 codex", content)


if __name__ == "__main__":
    unittest.main()
