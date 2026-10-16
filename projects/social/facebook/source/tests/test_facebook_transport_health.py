from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = (ROOT / "facebook_transport.ps1").read_text(encoding="utf-8-sig")
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _extract_function(source: str, name: str, next_name: str | None = None) -> str:
    start = source.index(f"function {name}")
    end = source.index(f"\nfunction {next_name}", start) if next_name else len(source)
    return source[start:end]


SURFACE_FUNCTION = _extract_function(TRANSPORT, "Get-FacebookUiSurfacePackage", "Get-FacebookUiBlockingState")
BLOCKING_FUNCTION = _extract_function(TRANSPORT, "Get-FacebookUiBlockingState", "Confirm-FacebookUiOnline")
OFFLINE_FUNCTION = _extract_function(TRANSPORT, "Test-FacebookUiOffline", "Confirm-FacebookUiOnline")
CONFIRM_FUNCTION = _extract_function(TRANSPORT, "Confirm-FacebookUiOnline")


def _powershell_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _harness(scenario: str, event_log: Path) -> str:
    return f"""\
$ErrorActionPreference = 'Stop'
$Scenario = {_powershell_literal(scenario)}
$EventLog = {_powershell_literal(event_log)}
$script:SnapshotCount = 0

function Add-Event([string]$Value) {{ Add-Content -LiteralPath $EventLog -Value $Value }}

function Start-Sleep {{
    param([int]$Seconds = 0, [int]$Milliseconds = 0)
    if ($PSBoundParameters.ContainsKey('Seconds')) {{ Add-Event ("sleep-seconds:" + $Seconds) }}
    else {{ Add-Event ("sleep-ms:" + $Milliseconds) }}
}}

function Ensure-FacebookProjectTransport {{
    param([string]$Serial, [int]$CapturePort)
    Add-Event 'ensure-transport'
    return [pscustomobject]@{{}}
}}

function Start-FacebookAppBounded {{
    param([string]$Serial, [int]$TimeoutSeconds, [switch]$ForceStop)
    if ($ForceStop) {{ Add-Event 'facebook-force-stop' }}
    else {{ Add-Event 'facebook-start' }}
    return $true
}}

function Invoke-FacebookUiSnapshot {{
    param(
        [string]$Serial,
        [string]$LocalPath,
        [string]$RemotePath,
        [int]$LegacyTimeoutSeconds,
        [int]$LegacyRetryCount,
        [int]$FastTimeoutSeconds
    )
    $script:SnapshotCount++
    Add-Event ("ui-snapshot:" + $script:SnapshotCount)
    if ($Scenario -eq 'snapshot-failure-first' -and $script:SnapshotCount -eq 1) {{
        Add-Event 'ui-snapshot-failed'
        return [pscustomobject]@{{ Success = $false; Source = 'legacy'; RootPackage = ''; NodeCount = 0; Error = 'fake-snapshot-failure' }}
    }}
    $offlineText = -join @([char]0x65E0,[char]0x7F51,[char]0x7EDC,[char]0x8FDE,[char]0x63A5)
    $raw = if ($Scenario -eq 'placeholder-first' -and $script:SnapshotCount -eq 1) {{
        '<hierarchy package="com.facebook.katana"><node text="only-one-label"/></hierarchy>'
    }} elseif ($Scenario -eq 'offline-first' -and $script:SnapshotCount -eq 1) {{
        '<hierarchy package="com.facebook.katana"><node text="label-one"/><node text="label-two"/><node content-desc="label-three"/><node text="' + $offlineText + '"/></hierarchy>'
    }} elseif ($Scenario -eq 'english-offline-first' -and $script:SnapshotCount -eq 1) {{
        '<hierarchy package="com.facebook.katana"><node text="No internet connection"/><node text="Retry"/><node content-desc="Facebook"/></hierarchy>'
    }} elseif ($Scenario -eq 'english-offline-after-launch' -and $script:SnapshotCount -eq 1) {{
        '<hierarchy package="com.facebook.katana"><node text="only-one-label"/></hierarchy>'
    }} elseif ($Scenario -eq 'english-offline-after-launch' -and $script:SnapshotCount -eq 2) {{
        '<hierarchy package="com.facebook.katana"><node text="No internet connection"/><node text="Retry"/><node content-desc="Facebook"/></hierarchy>'
    }} elseif ($Scenario -eq 'healthy-retry-button') {{
        '<hierarchy package="com.facebook.katana"><node text="Retry"/><node text="Home"/><node content-desc="Facebook"/></hierarchy>'
    }} elseif ($Scenario -eq 'blocked-first' -and $script:SnapshotCount -eq 1) {{
        '<hierarchy package="com.facebook.katana"><node resource-id="android:id/aerr_wait"/><node resource-id="android:id/aerr_close"/><node text="not responding"/></hierarchy>'
    }} elseif ($Scenario -eq 'nonfacebook-first' -and $script:SnapshotCount -eq 1) {{
        '<hierarchy package="com.android.settings"><node text="label-one"/><node text="label-two"/><node content-desc="label-three"/></hierarchy>'
    }} else {{
        '<hierarchy package="com.facebook.katana"><node text="label-one"/><node text="label-two"/><node content-desc="label-three"/></hierarchy>'
    }}
    Set-Content -LiteralPath $LocalPath -Value $raw -Encoding UTF8
    return [pscustomobject]@{{ Success = $true; Source = 'legacy'; RootPackage = 'com.facebook.katana'; NodeCount = 3; Error = '' }}
}}

function Invoke-FacebookAdbQuiet {{
    param([string]$Serial, [string[]]$Arguments, [int]$TimeoutSeconds)
    Add-Event ((@($Arguments) -join ' '))
    return $true
}}

function Dismiss-FacebookAnrWait {{ return $false }}
function Reset-FacebookAndroidWifi {{ Add-Event 'reset-wifi'; return $true }}
function Ensure-FacebookReverseMappings {{ Add-Event 'ensure-reverse'; return $true }}
function Set-FacebookAndroidProxy {{ Add-Event 'set-proxy'; return '127.0.0.1:7897' }}

{SURFACE_FUNCTION}
{BLOCKING_FUNCTION}
{OFFLINE_FUNCTION}
{CONFIRM_FUNCTION}

$emptyOffline = Test-FacebookUiOffline -Raw ''
Add-Event ("empty-offline:" + [bool]$emptyOffline)
$result = Confirm-FacebookUiOnline -Serial 'fake-serial' -CapturePort 8081 -Attempts 2 -WaitSeconds 6
Add-Event ("result:" + [bool]$result)
"""


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell is required for extracted transport health tests")
class FacebookTransportHealthPowerShellTests(unittest.TestCase):
    def _run_scenario(self, scenario: str) -> list[str]:
        with tempfile.TemporaryDirectory(prefix="facebook-transport-health-") as temp_dir:
            event_log = Path(temp_dir) / "events.log"
            harness = Path(temp_dir) / "harness.ps1"
            # Windows PowerShell 5.1 otherwise decodes UTF-8 as the host ANSI
            # code page, corrupting the extracted Chinese UI labels on CI.
            harness.write_text(_harness(scenario, event_log), encoding="utf-8-sig")
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

    def test_healthy_ui_is_checked_in_place_without_launch_stop_or_fixed_wait(self):
        events = self._run_scenario("healthy")
        self.assertIn("ensure-transport", events)
        self.assertIn("ui-snapshot:1", events)
        self.assertIn("result:True", events)
        self.assertFalse(any(event in {"facebook-force-stop", "facebook-start"} for event in events), events)
        self.assertFalse(any(event == "sleep-seconds:6" for event in events), events)

    def test_healthy_ui_with_retry_label_is_not_treated_as_offline(self):
        events = self._run_scenario("healthy-retry-button")
        self.assertIn("empty-offline:False", events)
        self.assertIn("ui-snapshot:1", events)
        self.assertIn("result:True", events)
        self.assertFalse(any(event in {"facebook-force-stop", "facebook-start", "reset-wifi"} for event in events), events)
        self.assertFalse(any(event == "sleep-seconds:6" for event in events), events)

    def test_placeholder_ui_keeps_bounded_recovery_path(self):
        events = self._run_scenario("placeholder-first")
        self.assertIn("facebook-force-stop", events)
        self.assertIn("sleep-seconds:6", events)
        self.assertIn("ui-snapshot:2", events)
        self.assertIn("result:True", events)

    def _assert_recovery_after_first_probe(self, scenario: str):
        events = self._run_scenario(scenario)
        first_probe = events.index("ui-snapshot:1")
        recovery = events.index("facebook-force-stop")
        wait = events.index("sleep-seconds:6")
        second_probe = events.index("ui-snapshot:2")
        result = events.index("result:True")
        self.assertLess(first_probe, recovery, events)
        self.assertLess(recovery, wait, events)
        self.assertLess(wait, second_probe, events)
        self.assertLess(second_probe, result, events)
        return events

    def test_offline_ui_does_not_take_healthy_fast_path(self):
        self._assert_recovery_after_first_probe("offline-first")

    def test_english_offline_ui_does_not_take_healthy_fast_path(self):
        self._assert_recovery_after_first_probe("english-offline-first")

    def test_english_offline_ui_does_not_skip_recovery_after_launch(self):
        events = self._run_scenario("english-offline-after-launch")
        offline_probe = events.index("ui-snapshot:2")
        reset = events.index("reset-wifi")
        self.assertLess(offline_probe, reset, events)
        self.assertIn("ui-snapshot:3", events)
        self.assertIn("result:True", events)

    def test_blocked_anr_ui_does_not_take_healthy_fast_path(self):
        self._assert_recovery_after_first_probe("blocked-first")

    def test_non_facebook_foreground_does_not_take_healthy_fast_path(self):
        self._assert_recovery_after_first_probe("nonfacebook-first")

    def test_snapshot_failure_does_not_take_healthy_fast_path(self):
        events = self._assert_recovery_after_first_probe("snapshot-failure-first")
        self.assertIn("ui-snapshot-failed", events)


if __name__ == "__main__":
    unittest.main()
