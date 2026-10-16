import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from macos import facebook_macos as fm


def worker(
    worker_id: str = "worker-01",
    serial: str = "emulator-5554",
    avd_name: str = "fb-macos-api35-worker-01",
    capture_port: int = 18081,
    native_port: int = 18766,
    enabled: bool = True,
) -> dict:
    return {
        "worker_id": worker_id,
        "serial": serial,
        "avd_name": avd_name,
        "capture_port": capture_port,
        "native_port": native_port,
        "enabled": enabled,
    }


class MacConfigTests(unittest.TestCase):
    def test_loads_mac_registry_without_windows_defaults(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "workers.json"
            path.write_text(
                json.dumps({"version": 1, "workers": [worker()]}),
                encoding="utf-8",
            )

            workers = fm.load_config(path)

        self.assertEqual(workers[0].avd_name, "fb-macos-api35-worker-01")
        self.assertEqual(workers[0].capture_port, 18081)

    def test_rejects_global_port_collision(self):
        workers = [
            fm.MacWorker(**worker()),
            fm.MacWorker(
                **worker(
                    "worker-02",
                    "emulator-5556",
                    "fb-macos-api35-worker-02",
                    18082,
                    18081,
                )
            ),
        ]
        with self.assertRaisesRegex(fm.MacConfigError, "globally unique"):
            fm.validate_workers(workers)

    def test_rejects_disabled_worker_selection(self):
        disabled = fm.MacWorker(**worker(enabled=False))
        with self.assertRaisesRegex(fm.MacConfigError, "disabled"):
            fm.select_workers([disabled], "worker-01")


class MacCommandTests(unittest.TestCase):
    def setUp(self):
        self.worker = fm.MacWorker(**worker())

    def test_selects_abi_for_apple_silicon_and_intel(self):
        self.assertEqual(fm.host_abi("arm64"), "arm64-v8a")
        self.assertEqual(fm.host_abi("aarch64"), "arm64-v8a")
        self.assertEqual(fm.host_abi("x86_64"), "x86_64")
        with self.assertRaisesRegex(fm.MacConfigError, "unsupported"):
            fm.host_abi("powerpc")

    def test_emulator_command_is_bound_to_worker_port_and_avd(self):
        command = fm.build_emulator_command(self.worker, "/sdk/emulator/emulator")
        self.assertEqual(command[:3], ["/sdk/emulator/emulator", "-avd", self.worker.avd_name])
        self.assertIn("5554", command)
        self.assertIn("-no-snapshot-save", command)

    def test_transport_commands_are_isolated_to_serial_and_capture_port(self):
        commands = fm.build_transport_commands(self.worker, "/sdk/platform-tools/adb")
        self.assertEqual(
            commands,
            [
                ["/sdk/platform-tools/adb", "-s", "emulator-5554", "reverse", "tcp:7897", "tcp:7897"],
                ["/sdk/platform-tools/adb", "-s", "emulator-5554", "reverse", "tcp:18081", "tcp:18081"],
                ["/sdk/platform-tools/adb", "-s", "emulator-5554", "shell", "settings", "put", "global", "http_proxy", "127.0.0.1:7897"],
            ],
        )

    def test_simulation_never_executes_processes(self):
        runner = Mock(side_effect=AssertionError("runner must not be called"))
        popen = Mock(side_effect=AssertionError("popen must not be called"))

        result = fm.simulate_workers(
            [self.worker],
            emulator_path="/sdk/emulator/emulator",
            adb_path="/sdk/platform-tools/adb",
            runner=runner,
            popen=popen,
        )

        self.assertEqual(result["mode"], "simulation")
        self.assertEqual(result["workers"][0]["worker_id"], "worker-01")
        runner.assert_not_called()
        popen.assert_not_called()

    def test_cli_simulation_does_not_emit_windows_discovered_tools(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "workers.json"
            path.write_text(
                json.dumps({"version": 1, "workers": [worker()]}),
                encoding="utf-8",
            )
            output = StringIO()
            with patch.object(
                fm,
                "discover_tools",
                return_value={"emulator": None, "adb": r"C:\sdk\adb.exe"},
            ), redirect_stdout(output):
                exit_code = fm.main(["--config", str(path), "simulate", "--worker", "all"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        command = payload["workers"][0]["transport_commands"][0]
        self.assertEqual(command[0], "$ANDROID_SDK_ROOT/platform-tools/adb")

    def test_real_execution_is_rejected_off_macos(self):
        with patch.object(fm.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(fm.MacConfigError, "requires macOS"):
                fm.require_macos()


if __name__ == "__main__":
    unittest.main()
