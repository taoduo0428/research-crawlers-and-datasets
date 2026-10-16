from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STOP = (ROOT / "stop_all_facebook.ps1").read_text(encoding="utf-8-sig")
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _extract_function(source: str, name: str, next_name: str | None = None) -> str:
    try:
        start = source.index(f"function {name}")
    except ValueError:
        return ""
    end = source.index(f"\nfunction {next_name}", start) if next_name else len(source)
    return source[start:end]


KILL_FUNCTION = _extract_function(
    STOP,
    "Invoke-VerifiedFacebookEmuKill",
    "Get-ConfiguredFacebookAvdProcesses",
)


def _powershell_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _fake_adb_script(path: Path, event_log: Path, scenario: str) -> None:
    path.write_text(
        "\n".join(
            [
                "@echo off",
                f'echo %*>>"{event_log}"',
                'if /I "%3"=="shell" if /I "%4"=="getprop" (',
                f'  if /I "{scenario}"=="exact" echo FacebookWorkerAvd',
                f'  if /I "{scenario}"=="foreign" echo ForeignAvd',
                f'  if /I "{scenario}"=="unverified" exit /b 1',
                ")",
                "exit /b 0",
            ]
        ),
        encoding="ascii",
    )


def _harness(fake_adb: Path, event_log: Path) -> str:
    return f"""\
$ErrorActionPreference = 'Stop'
$AdbPath = {_powershell_literal(fake_adb)}
$EventLog = {_powershell_literal(event_log)}
{KILL_FUNCTION}
if (-not $KILL_FUNCTION) {{
    Add-Content -LiteralPath $EventLog -Value 'missing-kill-function'
}}
$result = Invoke-VerifiedFacebookEmuKill -AdbPath $AdbPath -Serial 'emulator-5554' -ExpectedAvdName 'FacebookWorkerAvd'
Add-Content -LiteralPath $EventLog -Value ("result:" + [bool]$result)
"""


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell is required for isolated shutdown tests")
class FacebookShutdownSafetyPowerShellTests(unittest.TestCase):
    def _run_scenario(self, scenario: str) -> list[str]:
        with tempfile.TemporaryDirectory(prefix="facebook-shutdown-safety-") as temp_dir:
            temp = Path(temp_dir)
            event_log = temp / "events.log"
            fake_adb = temp / "fake-adb.cmd"
            harness = temp / "harness.ps1"
            _fake_adb_script(fake_adb, event_log, scenario)
            harness.write_text(_harness(fake_adb, event_log), encoding="utf-8")
            result = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue(event_log.is_file(), result.stdout)
            return event_log.read_text(encoding="utf-8").splitlines()

    def test_exact_configured_avd_is_killed_only_after_immediate_identity_query(self):
        events = self._run_scenario("exact")
        query = next(i for i, event in enumerate(events) if "shell getprop ro.boot.qemu.avd_name" in event)
        kill = next(i for i, event in enumerate(events) if "emu kill" in event)
        self.assertLess(query, kill, events)
        self.assertIn("result:True", events)

    def test_foreign_avd_serial_is_not_killed(self):
        events = self._run_scenario("foreign")
        self.assertTrue(any("shell getprop ro.boot.qemu.avd_name" in event for event in events), events)
        self.assertFalse(any("emu kill" in event for event in events), events)
        self.assertIn("result:False", events)

    def test_unverified_or_failed_avd_query_is_not_killed(self):
        for scenario in ("unverified", "unknown"):
            with self.subTest(scenario=scenario):
                events = self._run_scenario(scenario)
                self.assertTrue(any("shell getprop ro.boot.qemu.avd_name" in event for event in events), events)
                self.assertFalse(any("emu kill" in event for event in events), events)
                self.assertIn("result:False", events)


if __name__ == "__main__":
    unittest.main()
