from __future__ import annotations

import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import facebook_visible_emulator as visible


class VisibleEmulatorConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sdk = self.root / "Android SDK"
        self.avd = self.root / "Android AVD"
        (self.sdk / "emulator").mkdir(parents=True)
        (self.sdk / "platform-tools").mkdir(parents=True)
        (self.avd / "fb-custom-worker-02.avd").mkdir(parents=True)
        (self.root / "facebook_visible_emulator.py").write_text("# test adapter\n", encoding="utf-8")
        (self.sdk / "emulator" / "emulator.exe").write_bytes(b"test emulator")
        (self.sdk / "platform-tools" / "adb.exe").write_bytes(b"test adb")
        (self.root / "facebook.local.json").write_text(
            json.dumps(
                {
                    "sdk_root": str(self.sdk),
                    "avd_home": str(self.avd),
                    "port": 8765,
                    "open_browser": False,
                    "network": {"mode": "direct", "upstream_url": ""},
                }
            ),
            encoding="utf-8",
        )
        (self.root / "facebook_workers.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "workers": [
                        {
                            "worker_id": "worker-01",
                            "serial": "emulator-5554",
                            "avd_name": "fb-custom-worker-01",
                            "capture_port": 8081,
                            "native_port": 18766,
                            "enabled": True,
                        },
                        {
                            "worker_id": "worker-02",
                            "serial": "emulator-5556",
                            "avd_name": "fb-custom-worker-02",
                            "capture_port": 18082,
                            "native_port": 18767,
                            "enabled": True,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resolve_worker_uses_local_environment_and_validated_registry(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            spec = visible.resolve_worker(self.root, "worker-02")

        self.assertEqual(spec.worker_id, "worker-02")
        self.assertEqual(spec.serial, "emulator-5556")
        self.assertEqual(spec.avd_name, "fb-custom-worker-02")
        self.assertEqual(spec.console_port, 5556)
        self.assertEqual(spec.capture_port, 18082)
        self.assertEqual(spec.native_port, 18767)
        self.assertEqual(spec.sdk_root, self.sdk.resolve())
        self.assertEqual(spec.avd_home, self.avd.resolve())
        self.assertEqual(spec.emulator_exe, (self.sdk / "emulator" / "emulator.exe").resolve())

    def test_unknown_worker_fails_with_known_worker_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown worker_id 'worker-99'.*worker-01.*worker-02"):
            visible.resolve_worker(self.root, "worker-99")

    def test_disabled_worker_is_not_started(self) -> None:
        payload = json.loads((self.root / "facebook_workers.json").read_text(encoding="utf-8"))
        payload["workers"][1]["enabled"] = False
        (self.root / "facebook_workers.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "worker-02 is disabled"):
            visible.resolve_worker(self.root, "worker-02")

    def test_emulator_command_has_no_transport_or_destructive_flags(self) -> None:
        spec = visible.resolve_worker(self.root, "worker-02")
        command = visible.build_emulator_command(spec)
        self.assertEqual(command[:5], [str(spec.emulator_exe), "-avd", "fb-custom-worker-02", "-port", "5556"])
        self.assertNotIn("-wipe-data", command)
        self.assertNotIn("-http-proxy", command)
        self.assertNotIn("facebook_transport.ps1", " ".join(command))
        self.assertNotIn("D:\\Android", " ".join(command))

    def test_windows_command_line_uses_safe_argument_quoting(self) -> None:
        args = [
            r"C:\Program Files\Python\python.exe",
            r"C:\Project Root\facebook_visible_emulator.py",
            "--worker-id",
            "worker & 2",
        ]
        self.assertEqual(visible.windows_command_line(args), subprocess.list2cmdline(args))
        self.assertIn('"C:\\Program Files\\Python\\python.exe"', visible.windows_command_line(args))

    def test_print_spec_is_configuration_only_and_does_not_run_emulator(self) -> None:
        output = StringIO()
        with patch.object(visible, "run_emulator") as run_emulator, redirect_stdout(output):
            exit_code = visible.main(
                ["--root", str(self.root), "--worker-id", "worker-02", "--print-spec"]
            )
        self.assertEqual(exit_code, 0)
        run_emulator.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["worker_id"], "worker-02")
        self.assertEqual(payload["serial"], "emulator-5556")
        self.assertEqual(payload["capture_port"], 18082)
        self.assertIn("facebook_visible_emulator.py", " ".join(payload["launcher_command"]))

    def test_matching_configured_qemu_is_reused_without_starting_a_second_process(self) -> None:
        spec = visible.resolve_worker(self.root, "worker-02")
        matching = [{"ProcessId": 4321, "SessionId": 7}]
        with patch.object(visible, "_current_session_id", return_value=7), patch.object(
            visible, "find_matching_processes", return_value=matching
        ), patch.object(visible.subprocess, "Popen") as popen:
            result = visible.run_emulator(spec)
        popen.assert_not_called()
        self.assertEqual(result["action"], "reused")
        self.assertEqual(result["pid"], 4321)
        self.assertEqual(result["session_id"], 7)

    def test_session_zero_current_process_is_rejected_before_process_query_or_start(self) -> None:
        spec = visible.resolve_worker(self.root, "worker-02")
        with patch.object(visible, "_current_session_id", return_value=0), patch.object(
            visible, "find_matching_processes"
        ) as find_processes, patch.object(visible.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "Session 0.*launch_visible_in_user_session.ps1"):
                visible.run_emulator(spec)
        find_processes.assert_not_called()
        popen.assert_not_called()

    def test_session_zero_matching_process_is_not_reused(self) -> None:
        spec = visible.resolve_worker(self.root, "worker-02")
        with patch.object(visible, "_current_session_id", return_value=7), patch.object(
            visible, "find_matching_processes", return_value=[{"ProcessId": 4321, "SessionId": 0}]
        ), patch.object(visible.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "matching emulator process.*Session 0"):
                visible.run_emulator(spec)
        popen.assert_not_called()

    def test_process_query_failure_is_not_treated_as_no_match(self) -> None:
        spec = visible.resolve_worker(self.root, "worker-02")
        failed = subprocess.CompletedProcess(
            args=["powershell.exe"], returncode=1, stdout="", stderr="CIM unavailable"
        )
        with patch.object(visible, "_powershell_executable", return_value="powershell.exe"), patch.object(
            visible.subprocess, "run", return_value=failed
        ) as run, patch.object(visible.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "could not inspect existing emulator processes"):
                visible.find_matching_processes(spec)
        run.assert_called_once()
        popen.assert_not_called()


class LauncherContractTests(unittest.TestCase):
    def test_launcher_delegates_to_generic_entrypoint_and_keeps_identity_guards(self) -> None:
        launcher = (Path(__file__).resolve().parents[1] / "launch_visible_in_user_session.ps1")
        content = launcher.read_text(encoding="utf-8-sig")
        self.assertIn("facebook_visible_emulator.py", content)
        self.assertIn("--print-spec", content)
        self.assertIn("launcher_command", content)
        self.assertIn("Get-FacebookCaptureMarker", content)
        self.assertIn("Session 0 emulator identity changed before cleanup", content)
        self.assertIn("Get-CimInstance Win32_Process -ErrorAction Stop", content)
        self.assertIn("Could not inspect existing emulator processes", content)
        self.assertNotIn("visible_emulator_only.cmd", content)
        self.assertNotIn("visible_emulator_worker02.cmd", content)
        self.assertNotIn("D:\\Android\\SdkManual", content)
        self.assertNotIn("C:\\Users\\lenovo", content)


if __name__ == "__main__":
    unittest.main()
