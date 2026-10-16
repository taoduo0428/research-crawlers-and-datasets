from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_facebook_capture.ps1").read_text(encoding="utf-8-sig")
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _extract_function(source: str, name: str, next_name: str | None = None) -> str:
    start = source.index(f"function {name}")
    end = source.index(f"\nfunction {next_name}", start) if next_name else len(source)
    return source[start:end]


MAIN_PID_FUNCTION = _extract_function(RUN, "Get-FacebookMainProcessId", "Get-FacebookUnpinStatePath")
STATE_PATH_FUNCTION = _extract_function(RUN, "Get-FacebookUnpinStatePath", "Test-FacebookUnpinHookLog")
HOOK_LOG_FUNCTION = _extract_function(RUN, "Test-FacebookUnpinHookLog", "Get-FacebookUnpinScriptHash")
SCRIPT_HASH_FUNCTION = _extract_function(RUN, "Get-FacebookUnpinScriptHash", "Read-FacebookUnpinReuseState")
READ_STATE_FUNCTION = _extract_function(RUN, "Read-FacebookUnpinReuseState", "Get-FacebookUnpinIdentity")
IDENTITY_FUNCTION = _extract_function(RUN, "Get-FacebookUnpinIdentity", "Test-FacebookUnpinIdentityMatch")
IDENTITY_MATCH_FUNCTION = _extract_function(RUN, "Test-FacebookUnpinIdentityMatch", "Write-FacebookUnpinReuseState")
WRITE_STATE_FUNCTION = _extract_function(RUN, "Write-FacebookUnpinReuseState", "Start-FacebookWithFridaSslUnpin")
FRIDA_FUNCTION = _extract_function(RUN, "Start-FacebookWithFridaSslUnpin", "Get-LatestDiagnosticAction")


def _powershell_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _harness(scenario: str, base: Path, event_log: Path, fake_frida: Path) -> str:
    return f"""\
$ErrorActionPreference = 'Stop'
$Scenario = {_powershell_literal(scenario)}
$Base = {_powershell_literal(base)}
$RunsRoot = {_powershell_literal(base / 'runs')}
$Serial = 'fake-serial'
$FridaLog = {_powershell_literal(base / 'runs' / 'capture-001' / 'facebook-frida-unpin.log')}
$FacebookReadyTimeoutSeconds = 20
$ColdStartFacebook = $false
$EventLog = {_powershell_literal(event_log)}
$ScriptFile = Join-Path $Base 'facebook_ssl_unpin.js'
$script:Phase = 'first'
$script:CurrentPid = '111'
$script:CurrentProcStart = '9001'
$script:IdentityCallCount = 0
$script:AttachCount = 0

New-Item -ItemType Directory -Force -Path $Base, $RunsRoot, (Split-Path -Parent $FridaLog) | Out-Null
Set-Content -LiteralPath $ScriptFile -Value "const unpinVersion = 'v1';" -Encoding UTF8

function Add-Event([string]$Value) {{ Add-Content -LiteralPath $EventLog -Value $Value }}

function Start-Sleep {{
    param([int]$Seconds = 0, [int]$Milliseconds = 0)
}}

function Wait-FacebookUiReady([int]$TimeoutSeconds) {{
    Add-Event 'ui-ready'
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

function Start-Process {{
    [CmdletBinding()]
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [object]$WindowStyle,
        [string]$RedirectStandardOutput,
        [string]$RedirectStandardError,
        [switch]$PassThru,
        [switch]$Wait
    )
    $script:AttachCount++
    Add-Event 'frida-attach'
    if ($RedirectStandardOutput) {{ Set-Content -LiteralPath $RedirectStandardOutput -Value '' -Encoding UTF8 }}
    if ($RedirectStandardError) {{ Set-Content -LiteralPath $RedirectStandardError -Value '' -Encoding UTF8 }}
    if ($Scenario -eq 'stale-attach-failure' -and $script:Phase -eq 'failed') {{
        return [pscustomobject]@{{ ExitCode = 1 }}
    }}
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $FridaLog) | Out-Null
    Add-Content -LiteralPath $FridaLog -Value '[facebook-unpin] hook installed at 0x1234'
    Add-Content -LiteralPath $FridaLog -Value '[facebook-unpin] coldstart X509 hook installed at 0x5678'
    return [pscustomobject]@{{ ExitCode = 0 }}
}}

function Invoke-FacebookAdbCommand {{
    param(
        [string]$Serial,
        [string[]]$Arguments,
        [int]$TimeoutSeconds
    )
    $joined = @($Arguments) -join ' '
    Add-Event ("adb:" + $joined)
    if ($joined -eq 'shell /data/local/tmp/fb-fs17 --version') {{
        return [pscustomobject]@{{ Success = $true; Stdout = 'Frida 17'; Stderr = '' }}
    }}
    if ($joined -eq 'shell pidof fb-fs17') {{
        return [pscustomobject]@{{ Success = $true; Stdout = '4242'; Stderr = '' }}
    }}
    if ($joined -eq 'shell am force-stop com.facebook.katana') {{
        $script:CurrentPid = ''
        return [pscustomobject]@{{ Success = $true; Stdout = ''; Stderr = '' }}
    }}
    if ($joined -like 'shell am start*') {{
        if ([string]::IsNullOrWhiteSpace($script:CurrentPid)) {{ $script:CurrentPid = '111' }}
        return [pscustomobject]@{{ Success = $true; Stdout = 'Starting: Intent'; Stderr = '' }}
    }}
    if ($joined -eq 'shell pidof com.facebook.katana') {{
        if ($Scenario -eq 'pid-change' -and $script:Phase -eq 'second') {{ $script:CurrentPid = '222' }}
        $fakePid = [string]$script:CurrentPid
        return [pscustomobject]@{{ Success = $true; Stdout = $fakePid; Stderr = '' }}
    }}
    if ($joined -like 'shell ps -p *') {{
        return [pscustomobject]@{{ Success = $true; Stdout = 'com.facebook.katana'; Stderr = '' }}
    }}
    if ($joined -eq 'shell cat /proc/sys/kernel/random/boot_id') {{
        $script:IdentityCallCount++
        $boot = '11111111-1111-1111-1111-111111111111'
        if (($Scenario -eq 'boot-change' -or $Scenario -eq 'stale-attach-failure') -and $script:Phase -eq 'second') {{
            $boot = '22222222-2222-2222-2222-222222222222'
        }}
        if ($Scenario -eq 'stale-attach-failure' -and $script:Phase -eq 'failed') {{
            $boot = '33333333-3333-3333-3333-333333333333'
        }}
        return [pscustomobject]@{{ Success = $true; Stdout = $boot; Stderr = '' }}
    }}
    if ($joined -match '^shell cat /proc/(?<pid>\\d+)/stat$') {{
        $startTime = [string]$script:CurrentProcStart
        if (($Scenario -eq 'proc-change') -and $script:Phase -eq 'second') {{ $startTime = '9002' }}
        if (($Scenario -eq 'after-ui-change') -and $script:Phase -eq 'second' -and $script:IdentityCallCount -ge 4) {{ $startTime = '9002' }}
        $tail = @('S') + (1..18 | ForEach-Object {{ '0' }}) + @($startTime)
        $stat = $Matches['pid'] + ' (com.facebook.katana) ' + ($tail -join ' ')
        return [pscustomobject]@{{ Success = $true; Stdout = $stat; Stderr = '' }}
    }}
    throw "unexpected fake ADB command: $joined"
}}

{MAIN_PID_FUNCTION}
{STATE_PATH_FUNCTION}
{HOOK_LOG_FUNCTION}
{SCRIPT_HASH_FUNCTION}
{READ_STATE_FUNCTION}
{IDENTITY_FUNCTION}
{IDENTITY_MATCH_FUNCTION}
{WRITE_STATE_FUNCTION}
{FRIDA_FUNCTION}

$hashAlgorithm = [Security.Cryptography.SHA256]::Create()
try {{
    $expectedHash = [BitConverter]::ToString($hashAlgorithm.ComputeHash([IO.File]::ReadAllBytes($ScriptFile))).Replace('-', '').ToLowerInvariant()
}} finally {{
    $hashAlgorithm.Dispose()
}}
$actualHash = Get-FacebookUnpinScriptHash $ScriptFile
if ($actualHash -ne $expectedHash) {{ throw "SHA256 mismatch expected=$expectedHash actual=$actualHash" }}
Add-Event 'hash-ok'

Start-FacebookWithFridaSslUnpin
$statePath = Get-FacebookUnpinStatePath $Serial
Add-Event ("state-after-first:" + [bool](Test-Path -LiteralPath $statePath -PathType Leaf))

$script:Phase = 'second'
if ($Scenario -eq 'script-change' -or $Scenario -eq 'stale-attach-failure') {{
    Add-Content -LiteralPath $ScriptFile -Value "`nconst changed = true;" -Encoding UTF8
}}
if ($Scenario -eq 'missing-log') {{ Remove-Item -LiteralPath $FridaLog -Force }}
if ($Scenario -eq 'read-failure') {{ Set-Content -LiteralPath $statePath -Value '{{not-json' -Encoding UTF8 }}
if ($Scenario -eq 'cold-start') {{ $ColdStartFacebook = $true }}
if ($Scenario -eq 'stale-attach-failure') {{ $script:Phase = 'failed' }}

if ($Scenario -eq 'stale-attach-failure') {{
    try {{ Start-FacebookWithFridaSslUnpin }} catch {{ Add-Event 'failed-attach-caught' }}
    Add-Event ("state-after-failure:" + [bool](Test-Path -LiteralPath $statePath -PathType Leaf))
    $script:Phase = 'third'
    Start-FacebookWithFridaSslUnpin
    Add-Event ("state-after-recovery:" + [bool](Test-Path -LiteralPath $statePath -PathType Leaf))
}} else {{
    Start-FacebookWithFridaSslUnpin
}}
Add-Event ("attach-count:" + $script:AttachCount)
"""


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell is required for extracted Frida reuse tests")
class FacebookUnpinReusePowerShellTests(unittest.TestCase):
    def _run_scenario(self, scenario: str) -> tuple[list[str], str]:
        with tempfile.TemporaryDirectory(prefix="facebook-unpin-reuse-") as temp_dir:
            base = Path(temp_dir) / "project"
            event_log = Path(temp_dir) / "events.log"
            fake_frida = base / ("frida.cmd" if os.name == "nt" else "frida")
            base.mkdir(parents=True)
            if os.name == "nt":
                fake_frida.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii", newline="")
            else:
                fake_frida.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
                fake_frida.chmod(0o700)
            harness = Path(temp_dir) / "harness.ps1"
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
            return event_log.read_text(encoding="utf-8").splitlines(), result.stdout

    def _assert_two_attach_calls(self, events: list[str]):
        self.assertIn("hash-ok", events)
        self.assertIn("state-after-first:True", events)
        self.assertIn("attach-count:2", events)

    def test_same_boot_pid_starttime_and_script_hash_reuse_without_second_injection(self):
        events, output = self._run_scenario("same")
        self.assertIn("hash-ok", events)
        self.assertIn("state-after-first:True", events)
        self.assertIn("attach-count:1", events)
        self.assertEqual(events.count("frida-attach"), 1, events)
        self.assertIn("reused eternalized hooks", output)

    def test_boot_pid_or_process_start_change_forces_full_attach(self):
        for scenario in ("boot-change", "pid-change", "proc-change"):
            with self.subTest(scenario=scenario):
                events, output = self._run_scenario(scenario)
                self._assert_two_attach_calls(events)
                self.assertNotIn("reused eternalized hooks", output)

    def test_identity_change_after_ui_readiness_forces_full_attach(self):
        events, output = self._run_scenario("after-ui-change")
        self._assert_two_attach_calls(events)
        self.assertIn("cached hook identity changed after UI readiness", output)
        self.assertNotIn("reused eternalized hooks", output)

    def test_cold_start_bypasses_cache_and_force_stops(self):
        events, output = self._run_scenario("cold-start")
        self._assert_two_attach_calls(events)
        self.assertEqual(sum(event == "adb:shell am force-stop com.facebook.katana" for event in events), 1, events)
        self.assertNotIn("reused eternalized hooks", output)

    def test_missing_hook_log_invalidates_cache_and_attaches(self):
        events, output = self._run_scenario("missing-log")
        self._assert_two_attach_calls(events)
        self.assertNotIn("reused eternalized hooks", output)

    def test_unreadable_state_invalidates_cache_and_attaches(self):
        events, output = self._run_scenario("read-failure")
        self._assert_two_attach_calls(events)
        self.assertNotIn("reused eternalized hooks", output)

    def test_script_hash_change_invalidates_cache_and_attaches(self):
        events, output = self._run_scenario("script-change")
        self._assert_two_attach_calls(events)
        self.assertNotIn("reused eternalized hooks", output)

    def test_invalidated_cache_is_removed_before_failed_attach_and_not_reused(self):
        events, output = self._run_scenario("stale-attach-failure")
        self.assertIn("hash-ok", events)
        self.assertIn("failed-attach-caught", events)
        self.assertIn("state-after-failure:False", events)
        self.assertIn("state-after-recovery:True", events)
        self.assertIn("attach-count:3", events)
        self.assertEqual(events.count("frida-attach"), 3, events)
        self.assertNotIn("reused eternalized hooks", output)


if __name__ == "__main__":
    unittest.main()
