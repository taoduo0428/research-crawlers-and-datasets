"""Regression tests for truthful maintenance-gate result semantics."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import maintenance_gate as gate


class MaintenanceGateTests(unittest.TestCase):
    def test_skipped_checks_are_partial_not_pass(self):
        report = gate.summarize([{"ok": True}, {"ok": None, "skipped": "tool absent"}])
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["ok"])
        self.assertEqual(gate.exit_code(report), 2)

    def test_failure_takes_priority_over_skips(self):
        report = gate.summarize([{"ok": False}, {"ok": None}])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(gate.exit_code(report), 1)

    def test_empty_run_is_not_success(self):
        self.assertEqual(gate.summarize([])["status"], "partial")

    def test_all_checks_successful(self):
        report = gate.summarize([{"ok": True}])
        self.assertTrue(report["ok"])
        self.assertEqual(gate.exit_code(report), 0)

    def test_missing_required_file_fails_and_is_not_filtered_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(gate.require_files(root, ["missing.py"])["ok"])

    def test_python_syntax_check_does_not_create_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "module.py").write_text("answer = 42\n", encoding="utf-8")
            self.assertTrue(gate.python_syntax(root, ["module.py"])["ok"])
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["module.py"])
            (root / "module.py").write_text("def broken(\n", encoding="utf-8")
            self.assertFalse(gate.python_syntax(root, ["module.py"])["ok"])

    def test_release_list_rejects_missing_duplicate_and_external_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valid.py").write_text("pass\n", encoding="utf-8")
            manifest = root / "release-files.txt"
            manifest.write_text("valid.py\n", encoding="utf-8")
            self.assertTrue(gate.release_paths(root)["ok"])
            for content in ("valid.py\nvalid.py\n", "missing.py\n", "../outside.txt\n", "private.key\n"):
                manifest.write_text(content, encoding="utf-8")
                self.assertFalse(gate.release_paths(root)["ok"], content)


    def test_project_venv_is_preferred_without_installing_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(gate.os, "name", "nt"):
                candidate = root / ".venv" / "Scripts" / "python.exe"
                candidate.parent.mkdir(parents=True)
                candidate.write_bytes(b"placeholder")
                self.assertEqual(gate.project_python(root), str(candidate))
            with patch.object(gate.os, "name", "posix"):
                candidate = root / ".venv" / "bin" / "python"
                candidate.parent.mkdir(parents=True)
                candidate.write_bytes(b"placeholder")
                self.assertEqual(gate.project_python(root), str(candidate))


    def test_private_log_directory_is_external_new_and_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            external = Path(tmp) / "audit" / "run-1"
            self.assertEqual(gate.prepare_log_dir(root, external), external.resolve())
            with self.assertRaises(ValueError):
                gate.prepare_log_dir(root, external)
            with self.assertRaises(ValueError):
                gate.prepare_log_dir(root, root / "logs")
            with self.assertRaises(ValueError):
                gate.prepare_log_dir(root, root.parent)

    def test_full_streams_retained_when_summary_is_truncated(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = gate.prepare_log_dir(root / "project", root / "audit")
            output = "x" * 12000 + "END"
            result = gate.run([sys.executable, "-c", "import sys;print('x'*12000+'END');print('diagnostic',file=sys.stderr)"],
                              cwd=root, log_dir=log_dir)
            self.assertTrue(result["ok"])
            self.assertTrue(result["output_truncated"])
            self.assertEqual(Path(result["stdout_file"]).read_text(encoding="utf-8").strip(), output)
            self.assertEqual(Path(result["stderr_file"]).read_text(encoding="utf-8").strip(), "diagnostic")
            self.assertLessEqual(len(result["stdout_tail"]), 4000)
            self.assertTrue(result["stdout_tail"].rstrip().endswith("END"))

    def test_log_setup_failure_prevents_command_execution(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "unexpected"
            result = gate.run([sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"],
                              cwd=root, log_dir=root / "missing")
            self.assertFalse(result["ok"])
            self.assertFalse(marker.exists())

    def test_timeout_retains_output_and_is_failed(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = gate.prepare_log_dir(root / "project", root / "audit")
            # Windows venv python.exe is a redirector that spawns another process.
            # This fixture tests the documented direct-child timeout, not tree cleanup.
            interpreter = getattr(sys, "_base_executable", sys.executable) or sys.executable
            result = gate.run([interpreter, "-u", "-c", "import time;print('before-timeout',flush=True);time.sleep(30)"],
                              cwd=root, timeout=1, log_dir=log_dir)
            self.assertFalse(result["ok"])
            self.assertTrue(result["timed_out"])
            self.assertIn("before-timeout", Path(result["stdout_file"]).read_text(encoding="utf-8"))

    def test_repeated_commands_have_distinct_logs(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = gate.prepare_log_dir(root / "project", root / "audit")
            argv = [sys.executable, "-c", "print('dummy')"]
            a = gate.run(argv, cwd=root, log_dir=log_dir)
            b = gate.run(argv, cwd=root, log_dir=log_dir)
            self.assertNotEqual(a["stdout_file"], b["stdout_file"])
            self.assertTrue(Path(a["stdout_file"]).is_file())
            self.assertTrue(Path(b["stdout_file"]).is_file())

    def test_nonzero_exit_and_start_failure_are_not_lost(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = gate.prepare_log_dir(root / "project", root / "audit")
            result = gate.run([sys.executable, "-c", "import sys;print('dummy-error',file=sys.stderr);sys.exit(7)"],
                              cwd=root, log_dir=log_dir)
            self.assertFalse(result["ok"])
            self.assertEqual(result["returncode"], 7)
            self.assertEqual(Path(result["stderr_file"]).read_text(encoding="utf-8").strip(), "dummy-error")
            missing = gate.run([str(root / "not-installed-executable")], cwd=root, log_dir=log_dir)
            self.assertFalse(missing["ok"])
            self.assertIsNone(missing["returncode"])
            self.assertFalse(missing["command_started"])
            self.assertFalse(missing["full_output_retained"])
            self.assertTrue(Path(missing["stdout_file"]).is_file())
            self.assertTrue(Path(missing["stderr_file"]).is_file())

    def test_log_read_error_is_failed_even_when_command_succeeds(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = gate.prepare_log_dir(root / "project", root / "audit")
            with patch.object(gate, "_log_tail", side_effect=OSError("dummy evidence read error")):
                result = gate.run([sys.executable, "-c", "print('dummy')"], cwd=root, log_dir=log_dir)
            self.assertFalse(result["ok"])
            self.assertEqual(result["returncode"], 0)
            self.assertFalse(result["full_output_retained"])
            self.assertIn("evidence_error", result)

    def test_log_capture_preserves_raw_bytes_and_decodes_summary_only(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = gate.prepare_log_dir(root / "project", root / "audit")
            result = gate.run([sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'\\xffRAW')"],
                              cwd=root, log_dir=log_dir)
            self.assertTrue(result["ok"])
            self.assertEqual(Path(result["stdout_file"]).read_bytes(), b"\xffRAW")
            self.assertIn("RAW", result["stdout_tail"])

    def test_report_save_failure_cannot_report_pass(self):
        import io
        import json
        import sys
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            report = {"root": str(gate.ROOT), "log_dir": str(log_dir), **gate.summarize([{"ok": True}])}
            output = io.StringIO()
            with patch.object(sys, "argv", ["maintenance_gate", "--json", "--log-dir", str(log_dir)]), \
                 patch.object(gate, "prepare_log_dir", return_value=log_dir), \
                 patch.object(gate, "collect", return_value=report), \
                 patch.object(Path, "open", side_effect=OSError("dummy report write failure")), \
                 redirect_stdout(output):
                code = gate.main()
            actual = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertFalse(actual["ok"])
            self.assertEqual(actual["status"], "failed")
            self.assertEqual(actual["results"][-1]["name"], "evidence-report")

    def test_cli_rejects_existing_directory_before_collecting(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sys, "argv", ["maintenance_gate", "--log-dir", tmp]), \
                 patch.object(gate, "collect") as collect, \
                 patch.object(sys, "stderr"):
                with self.assertRaises(SystemExit) as raised:
                    gate.main()
            self.assertEqual(raised.exception.code, 2)
            collect.assert_not_called()

    def test_collect_routes_every_command_to_selected_log_dir(self):
        selected = Path("dummy-external-run")
        with patch.object(gate, "require_files", return_value={"ok": True}), \
             patch.object(gate, "python_syntax", return_value={"ok": True}), \
             patch.object(gate, "release_paths", return_value={"ok": True}), \
             patch.object(gate, "run", return_value={"ok": True}) as run, \
             patch.object(gate.shutil, "which", return_value="fake"):
            report = gate.collect(log_dir=selected)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(all(call.kwargs.get("log_dir") == selected for call in run.call_args_list))
        self.assertTrue(any("unittest" in call.args[0] for call in run.call_args_list))

    def test_dual_stream_large_raw_bytes_retained_exactly(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = gate.prepare_log_dir(root / "project", root / "audit")
            result = gate.run([sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'A'*25000+b'\\xffOUT');sys.stderr.buffer.write(b'B'*22000+b'\\xfeERR')"], cwd=root, log_dir=log_dir)
            self.assertTrue(result["command_started"])
            self.assertTrue(result["full_output_retained"])
            self.assertEqual(Path(result["stdout_file"]).read_bytes(), b"A" * 25000 + b"\xffOUT")
            self.assertEqual(Path(result["stderr_file"]).read_bytes(), b"B" * 22000 + b"\xfeERR")
            self.assertTrue(result["output_truncated"])

    def test_second_log_open_failure_prevents_command_start(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = gate.prepare_log_dir(root / "project", root / "audit")
            original_open = Path.open
            def open_path(path, *args, **kwargs):
                if str(path).endswith(".stderr.log"):
                    raise OSError("dummy second open failure")
                return original_open(path, *args, **kwargs)
            with patch.object(Path, "open", new=open_path), patch.object(gate.subprocess, "run") as execute:
                result = gate.run([sys.executable, "-c", "print('dummy')"], cwd=root, log_dir=log_dir)
            execute.assert_not_called()
            self.assertFalse(result["ok"])
            self.assertFalse(result["command_started"])

    def test_report_publication_is_complete_and_exclusive(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = {"status": "passed", "dummy": "content"}
            gate.save_report(root, report)
            original = (root / "report.json").read_bytes()
            self.assertEqual(json.loads(original), report)
            with self.assertRaises(OSError):
                gate.save_report(root, {"status": "failed"})
            self.assertEqual((root / "report.json").read_bytes(), original)
            self.assertFalse(list(root.glob("*.tmp")))

    def test_report_write_sync_or_publish_failure_leaves_no_final_report(self):
        for phase in ("write", "sync", "publish"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = gate.json if phase == "write" else gate.os
                symbol = "dump" if phase == "write" else "fsync" if phase == "sync" else "link"
                with patch.object(target, symbol, side_effect=OSError("dummy evidence failure")):
                    with self.assertRaises(OSError):
                        gate.save_report(root, {"status": "passed"})
                self.assertFalse((root / "report.json").exists())
                self.assertFalse(list(root.glob("*.tmp")))

    def test_report_close_failure_leaves_no_final_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_open = Path.open
            class FailedClose:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self.stream
                def __exit__(self, *args):
                    self.stream.close()
                    raise OSError("dummy close failure")
            def open_path(path, *args, **kwargs):
                return FailedClose(original_open(path, *args, **kwargs))
            with patch.object(Path, "open", new=open_path):
                with self.assertRaises(OSError):
                    gate.save_report(root, {"status": "passed"})
            self.assertFalse((root / "report.json").exists())
            self.assertFalse(list(root.glob("*.tmp")))

    def test_existing_intermediate_is_not_removed_on_exclusive_open_failure(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            intermediate = root / "existing.report.tmp"
            intermediate.write_bytes(b"prior-evidence")
            with patch.object(gate.uuid, "uuid4", return_value=SimpleNamespace(hex="existing")):
                with self.assertRaises(OSError):
                    gate.save_report(root, {"status": "passed"})
            self.assertEqual(intermediate.read_bytes(), b"prior-evidence")
            self.assertFalse((root / "report.json").exists())

    def test_skip_tests_never_reports_unittest_as_passed(self):
        with patch.object(gate, "require_files", return_value={"ok": True}), \
             patch.object(gate, "python_syntax", return_value={"ok": True}), \
             patch.object(gate, "release_paths", return_value={"ok": True}), \
             patch.object(gate, "run", return_value={"ok": True}), \
             patch.object(gate.shutil, "which", return_value="fake"):
            report = gate.collect(skip_tests=True)
        self.assertEqual(report["status"], "partial")
        self.assertTrue(any(r.get("name") == "unittest" and r.get("ok") is None for r in report["results"]))


if __name__ == "__main__":
    unittest.main()
