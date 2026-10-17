"""Regression guards for source-only test relocation and CI entrypoints."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestLayoutTests(unittest.TestCase):
    def test_transport_utf8_bom_is_preserved_for_windows_powershell51(self):
        for name in ("facebook_transport.ps1", "run_facebook_capture.ps1", "open_facebook_post_readonly.ps1"):
            with self.subTest(source=name):
                data = (ROOT / name).read_bytes()
                self.assertTrue(data.startswith(b"\xef\xbb\xbf"),
                                "Windows PowerShell 5.1 must not parse UTF-8 non-ASCII source as ANSI")
                data.decode("utf-8-sig")

    def test_python_tests_are_packaged_not_left_at_project_root(self):
        self.assertFalse(list(ROOT.glob("test_*.py")))
        self.assertTrue((ROOT / "tests" / "__init__.py").is_file())
        self.assertTrue((ROOT / "tests" / "test_facebook_capture_api.py").is_file())

    def test_release_manifest_includes_all_python_and_manual_tests(self):
        manifest = set((ROOT / "release-files.txt").read_text(encoding="utf-8").splitlines())
        required = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")}
        required.update({"tests/__init__.py", "tests/README.md"})
        required.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests" / "manual").glob("test_*.ps1"))
        self.assertFalse(required - manifest)
        # A listed but absent file makes the CI source-export fail. This
        # curated repo intentionally excludes old one-click Mac launchers
        # and retired tests, so only publish files that actually exist.
        missing = sorted(name for name in manifest if name.strip() and not name.startswith('#')
                         and not (ROOT / name).is_file())
        self.assertFalse(missing, f'Release manifest lists missing files: {missing}')
        self.assertFalse(any(name.startswith("test_") for name in manifest))

    def test_engineering_and_frontend_guards_are_in_both_ci_commands(self):
        source = (ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8-sig")
        for runner, interpreter in (("Windows", ".venv/Scripts/python.exe"), ("macOS", ".venv/bin/python")):
            with self.subTest(platform=runner):
                targets = set()
                platform = None
                for line in source.splitlines():
                    if "- if: runner.os ==" in line:
                        platform = line.split("==", 1)[1].strip().strip("'\"")
                    elif "run:" in line:
                        if platform == runner and f"{interpreter} -m unittest " in line:
                            targets.update(line.split("-m unittest ", 1)[1].split())
                        platform = None
                required = {"tests.test_facebook_http_security", "tests.test_frontend_security", "tests.test_maintenance_gate"}
                self.assertFalse(required - targets, f"CI omitted regression modules: {required - targets}")

    def test_ci_unittest_targets_resolve_from_package(self):
        source = (ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8")
        targets = []
        for line in source.splitlines():
            if "-m unittest " in line:
                targets.extend(line.split("-m unittest ", 1)[1].split())
        self.assertTrue(targets)
        self.assertTrue(all(name.startswith("tests.test_") for name in targets))
        loader = unittest.TestLoader()
        loader.loadTestsFromNames(sorted(set(targets)))
        self.assertEqual(loader.errors, [])


if __name__ == "__main__":
    unittest.main()
