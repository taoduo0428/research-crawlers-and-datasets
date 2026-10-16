import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPLORE = (ROOT / "explore_facebook_readonly.ps1").read_text(encoding="utf-8-sig")


def block(text, start_marker, end_marker):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


GUARD = block(EXPLORE, "function Test-ExpectedSourceGuard", "function Add-Diagnostic")
PAGINATION = block(EXPLORE, "function Scroll-ReactionAdaptive", "function Is-ReactionTabLabel")
RATE_LIMIT_LINE = block(EXPLORE, "function Get-ReactionRateLimitLine", "function Get-ProgressMetric")
PROGRESS_METRIC = block(EXPLORE, "function Get-ProgressMetric", "function Invoke-ReactionTriggerStrategy")
REACTION_GROUP = block(EXPLORE, "function Get-ReactionGroup", "function Get-SafeBoolProperty")


def run_powershell(script):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        raise unittest.SkipTest("PowerShell is required for navigation fixtures")
    handle, path = tempfile.mkstemp(prefix="facebook-navigation-", suffix=".ps1")
    try:
        with os.fdopen(handle, "w", encoding="utf-8-sig", newline="\n") as stream:
            stream.write(script)
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", path],
            text=True,
            capture_output=True,
            cwd=ROOT,
            timeout=30,
        )
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    if result.returncode:
        raise AssertionError(
            "PowerShell fixture failed with exit code %s\nstdout:\n%s\nstderr:\n%s"
            % (result.returncode, result.stdout, result.stderr)
        )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(
            "PowerShell fixture produced no JSON output\nstdout:\n%s\nstderr:\n%s"
            % (result.stdout, result.stderr)
        )
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise AssertionError(
            "PowerShell fixture final line was not JSON: %r\nall output:\n%s"
            % (lines[-1], result.stdout)
        ) from error


def fixture_script(body):
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$script:Gestures = 0",
            "$script:DumpCount = 0",
            "$script:Sleeps = @()",
            "$script:ProgressIndex = 0",
            "$script:Progresses = @()",
            "function Start-Sleep { param([int]$Milliseconds = 0, [int]$Seconds = 0); if ($Milliseconds -gt 0) { $script:Sleeps += $Milliseconds } elseif ($Seconds -gt 0) { $script:Sleeps += ($Seconds * 1000) } }",
            "function Invoke-FacebookAdbQuiet { $script:Gestures++; return $true }",
            "function Scroll-And-Dump([string]$Prefix, [int]$Count, [int]$StartY = 1600, [int]$EndY = 500) { for ($i = 0; $i -lt $Count; $i++) { $script:Gestures++; $script:DumpCount++ } }",
            "function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }",
            "function Get-UiNodes([string]$Path) { return @() }",
            "function Get-PageState([string]$Path) { return 'reaction-list' }",
            "function Invoke-ReactionTriggerStrategy([string]$Strategy, [string]$GuardResponse = '') { $script:Gestures++ }",
            "function Wait-ReactionMinCycle([datetime]$Started, [int]$MinCycleMs) { }",
            "function Save-Diagnostics([switch]$Throttle) { }",
            "function Get-CaptureProgress([string]$Family) { $value = $null; if ($script:ProgressIndex -lt $script:Progresses.Count) { $value = $script:Progresses[$script:ProgressIndex] }; $script:ProgressIndex++; return $value }",
            "function New-Progress([int]$Line, [string]$Source = 'target', [bool]$RateLimited = $false, [bool]$Terminal = $false, [bool]$SessionRestricted = $false) { $group = [pscustomobject]@{ query_records = $Line; page_count = $Line; user_count = $Line; latest_line = $Line; open_page_seen = $true; terminal_page_seen = $Terminal }; $value = [pscustomobject]@{ query_records = $Line; page_count = $Line; user_count = $Line; latest_line = $Line; latest_group_key = '__ALL__'; progress_source = 'fallback'; observed_source_post_ids = @($Source); latest_rate_limited = $RateLimited; latest_rate_limit_line = if ($RateLimited) { $Line } else { 0 }; latest_graphql_error_code = if ($RateLimited) { '1675004' } else { $null }; groups = [pscustomobject]@{ '__ALL__' = $group }; session_restricted = $SessionRestricted; session_restriction_graphql_code = if ($SessionRestricted) { '1675030' } else { $null }; session_restriction_api_error_code = if ($SessionRestricted) { 368 } else { $null }; session_restriction_line = if ($SessionRestricted) { $Line } else { 0 } }; return $value }",
            "$Diagnostics = [ordered]@{ target_guard = $null; target_guard_events = @(); target_guard_check_count = 0; target_guard_matched_count = 0; target_guard_mismatch_count = 0 }",
            "$ExpectedSourcePostId = ''",
            "$CaptureMinLine = 1",
            "$Serial = 'mock'",
            "$WaitSeconds = 1",
            "$ReactionExtensionChunk = 2",
            "$ReactionAbsoluteCap = 0",
            "$ReactionProgressPollIntervalMs = 1",
            "$ReactionProgressPollTimeoutMs = 100",
            "$ReactionLiveProgressPollIntervalMs = 1",
            "$ReactionLiveProgressPollTimeoutMs = 100",
            "$ReactionSteadyNoGrowthPollTimeoutMs = 100",
            "$ReactionPipelineBurstSize = 1",
            "$ReactionNativeScrollEnabled = 0",
            "$ReactionNativeScrollTimeoutMs = 500",
            "$ReactionUiDumpEveryN = 64",
            "$ReactionMinCycleMs = 0",
            "$ReactionInitialPaginationCooldownSeconds = 0",
            "$ReactionRateLimitBackoffSeconds = 1",
            "$ReactionRateLimitMaxConsecutive = 3",
            "$ReactionRateLimitMaxBackoffSeconds = 10",
            "$ReactionNoGrowthUiDumpEveryN = 1",
            "$ReactionStrategyIdleLimit = 2",
            "$ReactionOpenStrategyIdleLimit = 3",
            "$ReactionLongSwipeStartY = 1700",
            "$ReactionLongSwipeEndY = 500",
            "$ReactionLongSwipeDurationMs = 600",
            "$ReactionMediumSwipeStartY = 1500",
            "$ReactionMediumSwipeEndY = 700",
            "$ReactionMediumSwipeDurationMs = 500",
            "$ReactionShortSwipeStartY = 1250",
            "$ReactionShortSwipeEndY = 850",
            "$ReactionShortSwipeDurationMs = 400",
            "$ReactionReboundSwipeStartY = 900",
            "$ReactionReboundSwipeEndY = 1250",
            "$ReactionReboundSwipeDurationMs = 400",
            GUARD,
            REACTION_GROUP,
            RATE_LIMIT_LINE,
            PROGRESS_METRIC,
            PAGINATION,
            body,
        ]
    )


class NavigationGuardTests(unittest.TestCase):
    def test_strict_missing_initial_progress_stops_before_any_gesture(self):
        result = run_powershell(
            fixture_script(
                """
$ExpectedSourcePostId = 'target'
$script:Progresses = @($null)
$result = Scroll-ReactionAdaptive 'strict-missing' 3 $null '__ALL__'
[pscustomobject]@{ stop_reason = $result.stop_reason; attempts = $result.attempts; gestures = $script:Gestures; progress_calls = $script:ProgressIndex } | ConvertTo-Json -Compress
"""
            )
        )
        self.assertEqual(result["stop_reason"], "strict-progress-unavailable")
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["gestures"], 0)
        self.assertEqual(result["progress_calls"], 1)

    def test_nonstrict_missing_initial_progress_keeps_legacy_fixed_scroll(self):
        result = run_powershell(
            fixture_script(
                """
$script:Progresses = @($null)
$result = Scroll-ReactionAdaptive 'legacy-missing' 3 $null ''
[pscustomobject]@{ stop_reason = $result.stop_reason; attempts = $result.attempts; gestures = $script:Gestures; dumps = $script:DumpCount } | ConvertTo-Json -Compress
"""
            )
        )
        self.assertEqual(result["stop_reason"], "legacy-fixed-scroll")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(result["gestures"], 3)
        self.assertEqual(result["dumps"], 3)

    def test_matching_source_can_paginate_to_terminal(self):
        result = run_powershell(
            fixture_script(
                """
$ExpectedSourcePostId = 'target'
$script:Progresses = @((New-Progress 1 'target' $false $false), (New-Progress 2 'target' $false $true))
$result = Scroll-ReactionAdaptive 'matching' 1 $null '__ALL__'
[pscustomobject]@{ stop_reason = $result.stop_reason; attempts = $result.attempts; growth_events = $result.growth_events; gestures = $script:Gestures; matched = $Diagnostics.target_guard_matched_count } | ConvertTo-Json -Compress
"""
            )
        )
        self.assertEqual(result["stop_reason"], "terminal")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["growth_events"], 1)
        self.assertEqual(result["gestures"], 1)
        self.assertGreaterEqual(result["matched"], 1)

    def test_initial_all_cooldown_mismatch_stops_before_next_trigger(self):
        result = run_powershell(
            fixture_script(
                """
$ExpectedSourcePostId = 'target'
$ReactionInitialPaginationCooldownSeconds = 1
$script:Progresses = @((New-Progress 1 'target' $false $false), (New-Progress 2 'other' $false $false))
$result = Scroll-ReactionAdaptive 'cooldown-mismatch' 2 $null '__ALL__'
[pscustomobject]@{ stop_reason = $result.stop_reason; attempts = $result.attempts; gestures = $script:Gestures; guard_status = $Diagnostics.target_guard.status; guard_phase = $Diagnostics.target_guard.phase; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
"""
            )
        )
        self.assertEqual(result["stop_reason"], "target-mismatch")
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["gestures"], 0)
        self.assertEqual(result["guard_status"], "mismatch")
        self.assertEqual(result["guard_phase"], "reaction-initial-pagination-cooldown")
        self.assertIn(1000, result["sleeps"])

    def test_rate_limit_backoff_mismatch_stops_before_next_trigger(self):
        result = run_powershell(
            fixture_script(
                """
$ExpectedSourcePostId = 'target'
$ReactionAbsoluteCap = 2
$script:Progresses = @(
    (New-Progress 1 'target' $false $false),
    (New-Progress 2 'target' $true $false),
    (New-Progress 3 'other' $false $false),
    (New-Progress 4 'other' $false $false)
)
$result = Scroll-ReactionAdaptive 'rate-limit-mismatch' 2 $null '__ALL__'
[pscustomobject]@{ stop_reason = $result.stop_reason; attempts = $result.attempts; gestures = $script:Gestures; guard_status = $Diagnostics.target_guard.status; guard_phase = $Diagnostics.target_guard.phase; sleeps = @($script:Sleeps); max_rate_limits = $result.max_consecutive_rate_limits_seen } | ConvertTo-Json -Compress
"""
            )
        )
        self.assertEqual(result["stop_reason"], "target-mismatch")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["gestures"], 1)
        self.assertEqual(result["guard_status"], "mismatch")
        self.assertEqual(result["guard_phase"], "reaction-rate-limit-backoff")
        self.assertIn(1000, result["sleeps"])
        self.assertEqual(result["max_rate_limits"], 1)

    def test_rate_limit_backoff_session_restriction_stops_before_next_trigger(self):
        result = run_powershell(
            fixture_script(
                """
$ExpectedSourcePostId = 'target'
$ReactionAbsoluteCap = 2
$script:Progresses = @(
    (New-Progress 1 'target' $true $false $false),
    (New-Progress 2 'target' $false $false $true)
)
$result = Scroll-ReactionAdaptive 'rate-limit-session-restricted' 1 $null '__ALL__'
[pscustomobject]@{ stop_reason = $result.stop_reason; attempts = $result.attempts; gestures = $script:Gestures; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
"""
            )
        )
        self.assertEqual(result["stop_reason"], "session-restricted")
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["gestures"], 0)
        self.assertIn(1000, result["sleeps"])


if __name__ == "__main__":
    unittest.main()
