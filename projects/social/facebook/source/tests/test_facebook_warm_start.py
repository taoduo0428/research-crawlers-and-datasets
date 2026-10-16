from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_facebook_capture.ps1").read_text(encoding="utf-8-sig")
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _extract_function(source: str, name: str, next_name: str) -> str:
    start = source.index(f"function {name}")
    end = source.index(f"\nfunction {next_name}", start)
    return source[start:end]


MAIN_PID_FUNCTION = _extract_function(RUN, "Get-FacebookMainProcessId", "Start-FacebookWithFridaSslUnpin")
FRIDA_FUNCTION = _extract_function(RUN, "Start-FacebookWithFridaSslUnpin", "Get-LatestDiagnosticAction")


def _powershell_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _harness(scenario: str, base: Path, event_log: Path, fake_frida: Path) -> str:
    cold_start = "$true" if scenario == "cold" else "$false"
    return f"""\
$ErrorActionPreference = 'Stop'
$Scenario = {_powershell_literal(scenario)}
$Base = {_powershell_literal(base)}
$Serial = 'fake-serial'
$FridaLog = {_powershell_literal(base / 'frida.log')}
$FacebookReadyTimeoutSeconds = 20
$ColdStartFacebook = {cold_start}
$EventLog = {_powershell_literal(event_log)}
$script:FacebookPidQueryCount = 0

function Start-Sleep {{
    param([int]$Milliseconds, [int]$Seconds)
}}

function Wait-FacebookUiReady([int]$TimeoutSeconds) {{
    Add-Content -LiteralPath $EventLog -Value 'ui-ready'
    return $true
}}

function Get-Command {{
    [CmdletBinding()]
    param([Parameter(Position = 0)][string]$Name)
    if ($Name -eq 'frida.exe') {{
        return [pscustomobject]@{{ Source = {_powershell_literal(fake_frida)} }}
    }}
    throw "unexpected fake Get-Command request: $Name"
}}

function Invoke-FacebookAdbCommand {{
    param(
        [string]$Serial,
        [string[]]$Arguments,
        [int]$TimeoutSeconds
    )
    $joined = @($Arguments) -join ' '
    Add-Content -LiteralPath $EventLog -Value ("adb:" + $joined)
    if ($joined -eq 'shell /data/local/tmp/fb-fs17 --version') {{
        return [pscustomobject]@{{ Success = $true; Stdout = 'Frida 17'; Stderr = '' }}
    }}
    if ($joined -eq 'shell pidof fb-fs17') {{
        return [pscustomobject]@{{ Success = $true; Stdout = '4242'; Stderr = '' }}
    }}
    if ($joined -eq 'shell am force-stop com.facebook.katana') {{
        return [pscustomobject]@{{ Success = $true; Stdout = ''; Stderr = '' }}
    }}
    if ($joined -like 'shell am start*') {{
        return [pscustomobject]@{{ Success = $true; Stdout = 'Starting: Intent'; Stderr = '' }}
    }}
    if ($joined -eq 'shell pidof com.facebook.katana') {{
        $script:FacebookPidQueryCount++
        if ($Scenario -eq 'missing' -and $script:FacebookPidQueryCount -eq 1) {{
            return [pscustomobject]@{{ Success = $true; Stdout = ''; Stderr = '' }}
        }}
        $fakeFacebookPid = switch ($Scenario) {{
            'existing' {{ '111' }}
            'missing' {{ '222' }}
            'cold' {{ '333' }}
        }}
        return [pscustomobject]@{{ Success = $true; Stdout = $fakeFacebookPid; Stderr = '' }}
    }}
    if ($joined -like 'shell ps -p *') {{
        return [pscustomobject]@{{ Success = $true; Stdout = 'com.facebook.katana'; Stderr = '' }}
    }}
    throw "unexpected fake ADB command: $joined"
}}

{MAIN_PID_FUNCTION}
{FRIDA_FUNCTION}
Start-FacebookWithFridaSslUnpin
"""


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell is required for the extracted-function warm-start tests")
class FacebookWarmStartPowerShellTests(unittest.TestCase):
    def _run_scenario(self, scenario: str) -> list[str]:
        with tempfile.TemporaryDirectory(prefix="facebook-warm-start-") as temp_dir:
            base = Path(temp_dir)
            event_log = base / "events.log"
            fake_frida = base / ("frida.cmd" if os.name == "nt" else "frida")
            if os.name == "nt":
                fake_frida.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii", newline="")
            else:
                fake_frida.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
                fake_frida.chmod(0o700)
            (base / "facebook_ssl_unpin.js").write_text("// fake Frida source\n", encoding="utf-8")
            harness = base / "harness.ps1"
            harness.write_text(_harness(scenario, base, event_log, fake_frida), encoding="utf-8")

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

    def test_existing_main_pid_reuses_facebook_without_stop_or_launch(self):
        events = self._run_scenario("existing")
        adb_events = [event for event in events if event.startswith("adb:")]
        self.assertFalse(any("force-stop" in event for event in adb_events), events)
        self.assertFalse(any("am start" in event for event in adb_events), events)

    def test_missing_main_pid_launches_facebook_without_force_stop(self):
        events = self._run_scenario("missing")
        adb_events = [event for event in events if event.startswith("adb:")]
        self.assertFalse(any("force-stop" in event for event in adb_events), events)
        self.assertEqual(
            sum("shell am start -n com.facebook.katana/.LoginActivity" == event[4:] for event in adb_events),
            1,
            events,
        )

    def test_explicit_cold_start_force_stops_then_launches_facebook(self):
        events = self._run_scenario("cold")
        adb_events = [event for event in events if event.startswith("adb:")]
        self.assertEqual(sum("shell am force-stop com.facebook.katana" == event[4:] for event in adb_events), 1, events)
        self.assertEqual(
            sum("shell am start -n com.facebook.katana/.LoginActivity" == event[4:] for event in adb_events),
            1,
            events,
        )


if __name__ == "__main__":
    unittest.main()
