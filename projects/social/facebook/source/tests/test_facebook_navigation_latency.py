import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPLORE = (ROOT / "explore_facebook_readonly.ps1").read_text(encoding="utf-8-sig")
EXPLORE_PATH = str(ROOT / "explore_facebook_readonly.ps1").replace("\\", "/")
OPEN_PATH = str(ROOT / "open_facebook_post_readonly.ps1").replace("\\", "/")


def block(text, start_marker, end_marker):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


SOURCE_DISCOVERY = block(
    EXPLORE,
    "            if ([string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId) -and $AllowSourceDiscovery) {",
    "            if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {",
)
SCREENING = block(
    EXPLORE,
    "            if (($AutoScreenThenDeep -or $ReactionProbeOnly) -and $deepCaptureAllowed) {",
    "            # The top row contains reaction filters, not action buttons.",
)
CATEGORY_LOOP = block(
    EXPLORE,
    "                $visitedTabs = @{}",
    "                if ($AutoScreenThenDeep -and $Diagnostics['reaction_screening']) {",
)


def _decode_powershell_output(payload):
    if payload is None:
        return ""
    for encoding in ("utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def run_powershell(script):
    shell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        raise unittest.SkipTest("PowerShell is required for navigation fixtures")
    handle, path = tempfile.mkstemp(prefix="facebook-navigation-latency-", suffix=".ps1")
    try:
        with os.fdopen(handle, "w", encoding="utf-8-sig", newline="\n") as stream:
            stream.write(script)
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", path],
            capture_output=True,
            cwd=ROOT,
            timeout=30,
        )
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    stdout = _decode_powershell_output(result.stdout)
    stderr = _decode_powershell_output(result.stderr)
    if result.returncode:
        raise AssertionError(
            "PowerShell fixture failed with exit code %s\nstdout:\n%s\nstderr:\n%s"
            % (result.returncode, stdout, stderr)
        )
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(
            "PowerShell fixture produced no JSON output\\nstdout:\\n%s\\nstderr:\\n%s"
            % (stdout, stderr)
        )
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise AssertionError(
            "PowerShell fixture final line was not JSON: %r\\nall output:\\n%s"
            % (lines[-1], stdout)
        ) from error


def fixture_script(functions, body, *, setup_before_functions=""):
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "Write-Output '__fixture_start'",
            "function Start-Sleep { param([int]$Milliseconds = 0, [int]$Seconds = 0); if ($Milliseconds -gt 0) { $script:Sleeps += $Milliseconds } elseif ($Seconds -gt 0) { $script:Sleeps += ($Seconds * 1000) } }",
            "function Invoke-FacebookAdbQuiet { $script:TapCount++; return $true }",
            "function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }",
            setup_before_functions,
            *functions,
            body,
        ]
    )


def ast_main_fixture_script(body):
    """Execute the real OpenReactionList AST with isolated PowerShell mocks."""
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$sourcePath = '{EXPLORE_PATH}'",
            "$tokens = $null; $parseErrors = $null",
            "$sourceAst = [System.Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)",
            "if ($parseErrors.Count -gt 0) { throw ($parseErrors | ForEach-Object Message -join '; ') }",
            "$functionNames = @('Activate-ReactionEntry', 'Invoke-ReactionEntryAndSnapshot')",
            "$functionAsts = $sourceAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $functionNames -contains $node.Name }, $true)",
            "foreach ($functionAst in $functionAsts) { Invoke-Expression $functionAst.Extent.Text }",
            body,
            "$mainAst = $sourceAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.TrimStart().StartsWith('if ($OpenReactionList)') }, $true) | Select-Object -Last 1",
            "if (-not $mainAst) { throw 'OpenReactionList AST was not found' }",
            "Invoke-Expression $mainAst.Extent.Text",
            "[pscustomobject]@{ activations = $script:ActivationCalls; gestures = $script:GestureCalls; pagination = $script:PaginationCalls; actions = @($Diagnostics.actions); dumps = @($script:DumpNames) } | ConvertTo-Json -Compress -Depth 5",
        ]
    )


class ReactionNavigationLatencyTests(unittest.TestCase):
    def test_comments_normalizer_rejects_english_composer(self):
        action_bar = block(EXPLORE, "function Find-ActionBarNode", "function Find-CommentsEntryNode")
        comments = block(EXPLORE, "function Find-CommentsEntryNode", "function Find-CommentsDetailEntryNode")
        script = fixture_script(
            [action_bar, comments],
            """
$script:Nodes = @()
Write-Output 'BEFORE-NODES'
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.EditText'; Label = 'Comment'; ResourceId = ''; X1 = 210; Y1 = 1741; X2 = 1048; Y2 = 1840 }
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Like'; ResourceId = ''; X1 = 10; Y1 = 1200; X2 = 250; Y2 = 1300 }
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Comment'; ResourceId = ''; X1 = 260; Y1 = 1200; X2 = 500; Y2 = 1300 }
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Share'; ResourceId = ''; X1 = 510; Y1 = 1200; X2 = 750; Y2 = 1300 }
function Get-UiNodes([string]$Path) { return $script:Nodes }
$result = Find-CommentsEntryNode 'composer.xml'
[pscustomobject]@{ label = if ($result) { [string]$result.Label } else { '' }; class = if ($result) { [string]$result.Class } else { '' } } | ConvertTo-Json -Compress
""",
        )
        self.assertEqual(run_powershell(script), {"label": "Comment", "class": "android.widget.Button"})

    def test_comments_selector_handles_localized_labels_and_validated_blank_row(self):
        action_bar = block(EXPLORE, "function Find-ActionBarNode", "function Find-CommentsEntryNode")
        comments = block(EXPLORE, "function Find-CommentsEntryNode", "function Find-CommentsDetailEntryNode")
        script = fixture_script(
            [action_bar, comments],
            """
function Get-UiNodes([string]$Path) { return $script:Nodes }
$simplifiedLabel = -join @([char]0x8BC4, [char]0x8BBA)
$traditionalLabel = -join @([char]0x8A55, [char]0x8AD6)
$script:Nodes = @()
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = $simplifiedLabel; ResourceId = ''; X1 = 171; Y1 = 1559; X2 = 288; Y2 = 1675; Bounds = '[171,1559][288,1675]' }
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Like'; ResourceId = ''; X1 = 10; Y1 = 1559; X2 = 160; Y2 = 1675; Bounds = '[10,1559][160,1675]' }
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Share'; ResourceId = ''; X1 = 288; Y1 = 1559; X2 = 440; Y2 = 1675; Bounds = '[288,1559][440,1675]' }
$simplified = Find-CommentsEntryNode 'simplified.xml'
$script:Nodes = @()
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = $traditionalLabel; ResourceId = ''; X1 = 171; Y1 = 1559; X2 = 288; Y2 = 1675; Bounds = '[171,1559][288,1675]' }
$traditional = Find-CommentsEntryNode 'traditional.xml'
$script:Nodes = @()
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Comments'; ResourceId = ''; X1 = 171; Y1 = 1559; X2 = 288; Y2 = 1675; Bounds = '[171,1559][288,1675]' }
$english = Find-CommentsEntryNode 'english.xml'
$script:Nodes = @()
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Like button'; ResourceId = ''; X1 = 10; Y1 = 1559; X2 = 160; Y2 = 1675; Bounds = '[10,1559][160,1675]' }
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Share button'; ResourceId = ''; X1 = 288; Y1 = 1559; X2 = 440; Y2 = 1675; Bounds = '[288,1559][440,1675]' }
$namedFallback = Find-CommentsEntryNode 'named-fallback.xml'
$script:Nodes = @()
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Like'; ResourceId = ''; X1 = 10; Y1 = 1559; X2 = 160; Y2 = 1675; Bounds = '[10,1559][160,1675]' }
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = ''; ResourceId = ''; X1 = 171; Y1 = 1559; X2 = 288; Y2 = 1675; Bounds = '[171,1559][288,1675]' }
$script:Nodes += [pscustomobject]@{ Clickable = $true; Class = 'android.widget.Button'; Label = 'Share'; ResourceId = ''; X1 = 288; Y1 = 1559; X2 = 440; Y2 = 1675; Bounds = '[288,1559][440,1675]' }
$blankMiddle = Find-CommentsEntryNode 'blank-middle.xml'
$simplifiedText = if ($simplified) { [string]$simplified.Label } else { '' }
$traditionalText = if ($traditional) { [string]$traditional.Label } else { '' }
$englishText = if ($english) { [string]$english.Label } else { '' }
$namedFallbackText = if ($namedFallback) { [string]$namedFallback.Label } else { '' }
$blankMiddleText = if ($blankMiddle) { [string]$blankMiddle.Label } else { '' }
[pscustomobject]@{ simplified = $simplifiedText; traditional = $traditionalText; english = $englishText; named_fallback = $namedFallbackText; named_fallback_found = [bool]$namedFallback; blank_middle = $blankMiddleText; blank_middle_found = [bool]$blankMiddle } | ConvertTo-Json -Compress
""",
        )
        result = run_powershell(script)
        self.assertEqual(result["simplified"], "\u8bc4\u8bba")
        self.assertEqual(result["traditional"], "\u8a55\u8ad6")
        self.assertEqual(result["english"], "Comments")
        self.assertEqual(result["named_fallback"], "")
        self.assertFalse(result["named_fallback_found"])
        self.assertEqual(result["blank_middle"], "")
        self.assertTrue(result["blank_middle_found"])

    def test_failed_gestures_do_not_wait_or_report_activation(self):
        tap = block(EXPLORE, "function Tap-Node", "function Activate-ReactionEntry")
        activate = block(EXPLORE, "function Activate-ReactionEntry", "function Reveal-NormalizedPostSummary")
        script = fixture_script(
            [tap, activate],
            """
$script:Calls = 0
$script:Sleeps = @()
$WaitSeconds = 4
function Invoke-FacebookAdbQuiet { $script:Calls++; return $false }
$node = [pscustomobject]@{ Label = 'reaction-icons-summary'; Bounds = '[920,700][1040,820]'; X1 = 920; Y1 = 700; X2 = 1040; Y2 = 820; Package = 'com.facebook.katana'; Class = 'android.view.ViewGroup'; LongClickable = $false }
$tapResult = Tap-Node $node 'reaction entry'
$activationResult = Activate-ReactionEntry $node -FastProbe
[pscustomobject]@{ tap = $tapResult; activation = $activationResult; calls = $script:Calls; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        self.assertEqual(run_powershell(script), {
            "tap": False,
            "activation": False,
            "calls": 2,
            "sleeps": [],
        })

    def test_normalized_candidate_is_activated_once_by_real_main_ast(self):
        script = ast_main_fixture_script(
            """
$OpenReactionList = $true
$OpenShareList = $false
$OpenComments = $false
$OpenReactions = $false
$before = 'feed.xml'
$Out = '.'
$Serial = 'mock'
$WaitSeconds = 1
$ExpectedSourcePostId = ''
$AllowSourceDiscovery = $false
$ReactionProbeOnly = $true
$AutoScreenThenDeep = $false
$ReactionListReadyAttempts = 1
$ReactionListReadyDelaySeconds = 0
$ReactionScreenScrollCount = 0
$ReactionTabLimit = 0
$ReactionScrollCount = 0
$ReactionAbsoluteCap = 0
$CaptureMinLine = 1
$Diagnostics = [ordered]@{ actions = @(); pagination_runs = @(); reaction_native_fallback = $null; reaction_screening = $null }
$script:ActivationCalls = 0
$script:GestureCalls = 0
$script:PaginationCalls = 0
$script:DumpNames = @()
function Start-Sleep { param([int]$Milliseconds = 0, [int]$Seconds = 0) }
function Invoke-FacebookAdbQuiet { param([string]$Serial, [string[]]$Arguments); $script:GestureCalls++; if ($Arguments -contains 'tap' -and $Arguments -contains '980') { $script:ActivationCalls++ }; return $true }
function Dump-Ui([string]$Name) { $script:DumpNames += $Name; return $Name }
function Get-PageState([string]$Path) { if ($Path -eq 'feed.xml' -or $Path -like '*normalized*') { return 'post' }; return 'reaction-list' }
function Find-EntryWithFeedScan { return [pscustomobject]@{ Entry = $null; CommentEntry = [pscustomobject]@{ Label = 'Comments'; X1 = 100; Y1 = 700; X2 = 300; Y2 = 760 }; Path = 'feed.xml'; Scrolls = 0 } }
function Open-PostDetailViaComments { return [pscustomobject]@{ Path = 'normalized.xml'; State = 'comments-surface' } }
function Reveal-NormalizedPostSummary { return [pscustomobject]@{ Path = 'normalized.xml'; Attempts = 0; Revealed = $true } }
function Get-NormalizedPostSurfaceState([string]$Path) { return 'comments-surface' }
function Find-ReactionCountNode([string]$Path) { if ($Path -eq 'normalized.xml') { return [pscustomobject]@{ Label = '210'; Bounds = '[920,700][1040,820]'; X1 = 920; Y1 = 700; X2 = 1040; Y2 = 820; Package = 'com.facebook.katana'; Class = 'android.view.ViewGroup'; LongClickable = $false } }; return $null }
function Find-ReactionSummaryIconNode { return $null }
function Wait-ReactionListReady([string]$Path) { return [pscustomobject]@{ Path = $Path; State = 'reaction-list'; Ready = $true; Attempts = 0; LoadingObserved = $false } }
function Get-CaptureProgress { return [pscustomobject]@{ query_records = 0; user_count = 0; terminal_page_seen = $false; open_page_seen = $false; latest_rate_limited = $false; observed_source_post_ids = @() } }
function Test-ExpectedSourceGuard { return $true }
function Get-ReactionVisibilityHint { return 'none' }
function Get-ReactionTabNodes { return @() }
function Add-Diagnostic { param([string]$Action,[string]$BeforeState,$Candidate,[string]$AfterState,[bool]$Confirmed,[string]$FailureReason,[bool]$NormalizedViaComments=$false,[string]$VisibilityHint='',[int]$EntryScanScrolls=0,[string]$EntryMethod=''); $Diagnostics.actions += [pscustomobject]@{ action = $Action; confirmed = $Confirmed; failure_reason = $FailureReason } }
function Save-Diagnostics { }
function Get-SafeBoolProperty { return $false }
function Add-PaginationDiagnostic { $script:PaginationCalls++ }
"""
        )
        result = run_powershell(script)
        self.assertEqual(result["activations"], 1)
        self.assertEqual(result["pagination"], 0)
        self.assertEqual(result["actions"][0]["confirmed"], True)

    def test_click_helper_exists_and_reuses_ready_snapshot(self):
        self.assertIn("function Invoke-ReactionEntryAndSnapshot", EXPLORE)
        activate = block(EXPLORE, "function Activate-ReactionEntry", "function Reveal-NormalizedPostSummary")
        helper = block(EXPLORE, "function Invoke-ReactionEntryAndSnapshot", "function Find-EntryWithFeedScan")
        script = fixture_script(
            [activate, helper],
            """
$script:DumpCount = 0
$script:TapCount = 0
$script:Sleeps = @()
$WaitSeconds = 4
function Get-PageState([string]$Path) { return 'reaction-list' }
$node = [pscustomobject]@{ Label = 'reaction-icons-summary'; Bounds = '[920,700][1040,820]'; X1 = 920; Y1 = 700; X2 = 1040; Y2 = 820; Package = 'com.facebook.katana'; Class = 'android.view.ViewGroup'; LongClickable = $false }
$result = Invoke-ReactionEntryAndSnapshot $node 'reaction-list.xml'
[pscustomobject]@{ path = $result.Path; state = $result.State; dumps = $script:DumpCount; taps = $script:TapCount; fallback = $result.OriginalWaitFallback; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        self.assertEqual(script_result := run_powershell(script), {
            "path": "reaction-list.xml",
            "state": "reaction-list",
            "dumps": 1,
            "taps": 1,
            "fallback": False,
            "sleeps": [250],
        })

    def test_loading_transition_uses_existing_ready_wait_without_reclicing(self):
        self.assertIn("function Wait-ReactionListReady", EXPLORE)
        activate = block(EXPLORE, "function Activate-ReactionEntry", "function Reveal-NormalizedPostSummary")
        helper = block(EXPLORE, "function Invoke-ReactionEntryAndSnapshot", "function Find-EntryWithFeedScan")
        ready = block(EXPLORE, "function Wait-ReactionListReady", "$DiagnosticsPath")
        script = fixture_script(
            [activate, helper, ready],
            """
$script:DumpCount = 0
$script:TapCount = 0
$script:Sleeps = @()
$script:StateIndex = 0
$script:States = @('reaction-list-loading', 'reaction-list-loading', 'reaction-list')
$WaitSeconds = 4
function Get-PageState([string]$Path) { $value = $script:States[[Math]::Min($script:StateIndex, $script:States.Count - 1)]; $script:StateIndex++; return $value }
$node = [pscustomobject]@{ Label = 'reaction-icons-summary'; Bounds = '[920,700][1040,820]'; X1 = 920; Y1 = 700; X2 = 1040; Y2 = 820; Package = 'com.facebook.katana'; Class = 'android.view.ViewGroup'; LongClickable = $false }
$opened = Invoke-ReactionEntryAndSnapshot $node 'reaction-list.xml'
$result = Wait-ReactionListReady $opened.Path
[pscustomobject]@{ state = $result.State; ready = $result.Ready; attempts = $result.Attempts; dumps = $script:DumpCount; taps = $script:TapCount; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        result = run_powershell(script)
        self.assertEqual(result["state"], "reaction-list")
        self.assertTrue(result["ready"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["dumps"], 2)
        self.assertEqual(result["taps"], 1)
        self.assertEqual(result["sleeps"], [250, 1000])

    def test_never_ready_keeps_original_click_budget_without_reclick(self):
        activate = block(EXPLORE, "function Activate-ReactionEntry", "function Reveal-NormalizedPostSummary")
        helper = block(EXPLORE, "function Invoke-ReactionEntryAndSnapshot", "function Find-EntryWithFeedScan")
        script = fixture_script(
            [activate, helper],
            """
$script:DumpCount = 0
$script:TapCount = 0
$script:Sleeps = @()
$WaitSeconds = 4
function Get-PageState([string]$Path) { return 'placeholder-or-unknown' }
$node = [pscustomobject]@{ Label = 'reaction-icons-summary'; Bounds = '[920,700][1040,820]'; X1 = 920; Y1 = 700; X2 = 1040; Y2 = 820; Package = 'com.facebook.katana'; Class = 'android.view.ViewGroup'; LongClickable = $false }
$result = Invoke-ReactionEntryAndSnapshot $node 'reaction-list.xml'
[pscustomobject]@{ state = $result.State; fallback = $result.OriginalWaitFallback; dumps = $script:DumpCount; taps = $script:TapCount; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        result = run_powershell(script)
        self.assertEqual(result["state"], "placeholder-or-unknown")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["dumps"], 2)
        self.assertEqual(result["taps"], 1)
        self.assertEqual(result["sleeps"][0], 250)
        self.assertGreaterEqual(result["sleeps"][1], 3500)

    def test_detail_reveal_requires_new_snapshot_and_keeps_budget_on_miss(self):
        self.assertIn("function Invoke-ReactionDetailReveal", EXPLORE)
        helper = block(EXPLORE, "function Invoke-ReactionDetailReveal", "function Find-StrictTargetReactionEntry")
        script = fixture_script(
            [helper],
            """
$script:DumpCount = 0
$script:Sleeps = @()
$WaitSeconds = 4
function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }
$result = Invoke-ReactionDetailReveal 'reaction' 1
[pscustomobject]@{ path = $result.Path; early = $result.EarlyReady; dumps = $script:DumpCount; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        result = run_powershell(script)
        self.assertEqual(result["dumps"], 2)
        self.assertFalse(result["early"])
        self.assertGreater(result["sleeps"][1], 0)
        self.assertLessEqual(sum(result["sleeps"]), 4000)

    def test_detail_reveal_swipe_failure_is_reported_without_snapshot_or_wait(self):
        helper = block(EXPLORE, "function Invoke-ReactionDetailReveal", "function Find-StrictTargetReactionEntry")
        script = fixture_script(
            [helper],
            """
$script:DumpCount = 0
$script:Sleeps = @()
$WaitSeconds = 4
function Invoke-FacebookAdbQuiet { return $false }
function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }
$result = Invoke-ReactionDetailReveal 'reaction' 1 'before-detail.xml'
[pscustomobject]@{
    path = $result.Path
    gesture = $result.GestureSucceeded
    reason = $result.FailureReason
    dumps = $script:DumpCount
    sleeps = @($script:Sleeps)
} | ConvertTo-Json -Compress
""",
        )
        self.assertEqual(run_powershell(script), {
            "path": "before-detail.xml",
            "gesture": False,
            "reason": "reaction-detail-reveal-swipe-failed",
            "dumps": 0,
            "sleeps": [],
        })

    def test_source_discovery_filter_failure_stops_without_followup_dump(self):
        script = fixture_script(
            [SOURCE_DISCOVERY],
            """
[pscustomobject]@{
    tap = $script:TapCalls
    dumps = $script:DumpCount
    progress = $script:ProgressCalls
    allowed = $deepCaptureAllowed
    reason = $decisionReason
    status = $Diagnostics.source_discovery.status
    tapped = $Diagnostics.source_discovery.tab_tapped
} | ConvertTo-Json -Compress
""",
            setup_before_functions="""
$ExpectedSourcePostId = ''
$AllowSourceDiscovery = $true
$ReactionListReadyAttempts = 0
$ReactionListReadyDelaySeconds = 0
$reactionList = 'reaction-list.xml'
$initialReactionProgress = $null
$deepCaptureAllowed = $true
$decisionReason = ''
$Diagnostics = [ordered]@{ source_discovery = $null }
$script:DumpCount = 0
$script:ProgressCalls = 0
$script:TapCalls = 0
function Get-ReactionTabNodes([string]$Path) {
    return [pscustomobject]@{ Label = 'Not selected'; X1 = 10; Y1 = 100; X2 = 200; Y2 = 180 }
}
function Tap-Node($Node, [string]$Label) { $script:TapCalls++; return $false }
function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }
function Get-CaptureProgress([string]$Family) { $script:ProgressCalls++; return $null }
function Save-Diagnostics { }
""",
        )
        self.assertEqual(run_powershell(script), {
            "tap": 1,
            "dumps": 0,
            "progress": 0,
            "allowed": False,
            "reason": "reaction-source-discovery-filter-activation-failed",
            "status": "tap-failed",
            "tapped": False,
        })

    def test_screening_filter_failure_stops_without_probe_swipe(self):
        script = fixture_script(
            [SCREENING],
            """
[pscustomobject]@{
    gestures = $script:GestureCalls
    dumps = $script:DumpCount
    progress = $script:ProgressCalls
    classification = $screenClassification
    reason = $decisionReason
    attempts = $Diagnostics.reaction_screening.probe_attempts
    status = $Diagnostics.reaction_screening.probe_status
    tapped = $Diagnostics.reaction_screening.screen_tab_tapped
} | ConvertTo-Json -Compress
""",
            setup_before_functions="""
$AutoScreenThenDeep = $true
$ReactionProbeOnly = $false
$ReactionScreenScrollCount = 2
$WaitSeconds = 4
$reactionVisibility = 'none'
$deepCaptureAllowed = $true
$decisionReason = ''
$initialReactionProgress = [pscustomobject]@{ query_records = 0; user_count = 0; terminal_page_seen = $false; open_page_seen = $false; session_restricted = $false; latest_rate_limited = $false; observed_source_post_ids = @() }
$screenProgress = $initialReactionProgress
$reactionList = 'reaction-list.xml'
$screenClassification = 'pending'
$Diagnostics = [ordered]@{ reaction_screening = $null }
$script:DumpCount = 0
$script:ProgressCalls = 0
$script:GestureCalls = 0
function Get-ReactionTabNodes([string]$Path) {
    return [pscustomobject]@{ Label = 'Not selected'; X1 = 10; Y1 = 100; X2 = 200; Y2 = 180 }
}
function Tap-Node($Node, [string]$Label) { $script:GestureCalls++; return $false }
function Invoke-FacebookAdbQuiet { $script:GestureCalls++; return $false }
function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }
function Get-CaptureProgress([string]$Family) { $script:ProgressCalls++; return $screenProgress }
function Test-ExpectedSourceGuard { return $true }
function Save-Diagnostics { }
""",
        )
        self.assertEqual(run_powershell(script), {
            "gestures": 1,
            "dumps": 0,
            "progress": 0,
            "classification": "not-enumerable",
            "reason": "reaction-screening-filter-activation-failed",
            "attempts": 0,
            "status": "gesture-failed",
            "tapped": False,
        })

    def test_screening_probe_swipe_failure_stops_without_dump(self):
        script = fixture_script(
            [SCREENING],
            """
[pscustomobject]@{
    gestures = $script:GestureCalls
    dumps = $script:DumpCount
    progress = $script:ProgressCalls
    classification = $screenClassification
    reason = $decisionReason
    attempts = $Diagnostics.reaction_screening.probe_attempts
    status = $Diagnostics.reaction_screening.probe_status
    tapped = $Diagnostics.reaction_screening.screen_tab_tapped
} | ConvertTo-Json -Compress
""",
            setup_before_functions="""
$AutoScreenThenDeep = $true
$ReactionProbeOnly = $false
$ReactionScreenScrollCount = 1
$WaitSeconds = 4
$reactionVisibility = 'none'
$deepCaptureAllowed = $true
$decisionReason = ''
$initialReactionProgress = [pscustomobject]@{ query_records = 0; user_count = 0; terminal_page_seen = $false; open_page_seen = $false; session_restricted = $false; latest_rate_limited = $false; observed_source_post_ids = @() }
$screenProgress = $initialReactionProgress
$reactionList = 'reaction-list.xml'
$screenClassification = 'pending'
$Diagnostics = [ordered]@{ reaction_screening = $null }
$script:DumpCount = 0
$script:ProgressCalls = 0
$script:GestureCalls = 0
function Get-ReactionTabNodes([string]$Path) { return @() }
function Invoke-FacebookAdbQuiet { $script:GestureCalls++; return $false }
function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }
function Get-CaptureProgress([string]$Family) { $script:ProgressCalls++; return $screenProgress }
function Test-ExpectedSourceGuard { return $true }
function Save-Diagnostics { }
""",
        )
        self.assertEqual(run_powershell(script), {
            "gestures": 1,
            "dumps": 0,
            "progress": 0,
            "classification": "not-enumerable",
            "reason": "reaction-screen-probe-swipe-failed",
            "attempts": 1,
            "status": "gesture-failed",
            "tapped": False,
        })

    def test_category_filter_failure_stops_before_category_dump_or_pagination(self):
        script = fixture_script(
            [CATEGORY_LOOP],
            """
[pscustomobject]@{
    gestures = $script:GestureCalls
    dumps = $script:DumpCount
    progress = $script:ProgressCalls
    pagination = $script:PaginationCalls
    runs = @($Diagnostics.pagination_runs)
    reason = $Diagnostics.pagination_runs[0].stop_reason
} | ConvertTo-Json -Compress -Depth 5
""",
            setup_before_functions="""
$allFirstHandled = $false
$ReactionTabLimit = 1
$ReactionScrollCount = 2
$ReactionAbsoluteCap = 0
$ReactionExtensionChunk = 2
$ReactionNativeScrollEnabled = 0
$ReactionNativeScrollTimeoutMs = 500
$ReactionUiDumpEveryN = 1
$ReactionMinCycleMs = 0
$ReactionInitialPaginationCooldownSeconds = 0
$Diagnostics = [ordered]@{ pagination_runs = @() }
$script:DumpCount = 0
$script:ProgressCalls = 0
$script:GestureCalls = 0
$script:PaginationCalls = 0
function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }
function Get-ReactionTabNodes([string]$Path) { return [pscustomobject]@{ Label = 'Like'; X1 = 10; Y1 = 100; X2 = 200; Y2 = 180 } }
function Get-ReactionTabKey($Node) { return 'like' }
function Get-CaptureProgress([string]$Family) { $script:ProgressCalls++; return [pscustomobject]@{ query_records = 1; page_count = 1; user_count = 1; latest_line = 1; terminal_page_seen = $false; open_page_seen = $true; latest_group_key = '__ALL__'; groups = [pscustomobject]@{}; observed_source_post_ids = @() } }
function Tap-Node($Node, [string]$Label) { $script:GestureCalls++; return $false }
function Add-PaginationDiagnostic([int]$TabIndex, $Candidate, $Result) { $Diagnostics.pagination_runs += [pscustomobject]@{ stop_reason = $Result.stop_reason } }
function Save-Diagnostics { }
""",
        )
        self.assertEqual(run_powershell(script), {
            "gestures": 1,
            "dumps": 1,
            "progress": 1,
            "pagination": 0,
            "runs": [{"stop_reason": "filter-activation-failed"}],
            "reason": "filter-activation-failed",
        })

    def test_strict_reveal_checks_source_guard_before_accepting_entry(self):
        strict = block(EXPLORE, "function Find-StrictTargetReactionEntry", "function Find-StrictTargetShareEntry")
        reveal = strict.index("Invoke-ReactionDetailReveal")
        guard = strict.index("Test-ExpectedSourceGuard", reveal)
        accepted = strict.index("if ($reveal.Entry -or $reveal.CommentEntry)", guard)
        self.assertLess(reveal, guard)
        self.assertLess(guard, accepted)
        self.assertIn("Find-CommentsDetailEntryNode", strict)
        self.assertNotIn("Find-CommentsEntryNode $path", strict[:accepted])

    def test_pagination_rate_controls_and_all_cooldown_remain_intact(self):
        pagination = block(EXPLORE, "function Scroll-ReactionAdaptive", "function Is-ReactionTabLabel")
        self.assertIn("Start-Sleep -Seconds $initialPaginationCooldownSeconds", pagination)
        self.assertIn("Invoke-ReactionTriggerStrategy $strategy", pagination)
        self.assertIn("Wait-ReactionMinCycle $cycleStarted $minCycleMs", pagination)
        self.assertIn("Reaction GraphQL rate limit circuit breaker", pagination)
        self.assertIn("Test-ExpectedSourceGuard $immediate 'reaction' 'reaction-pagination'", pagination)
        self.assertIn("Test-ExpectedSourceGuard $polled 'reaction' 'reaction-pagination'", pagination)
        self.assertIn("Test-ExpectedSourceGuard $refreshed 'reaction' 'reaction-pagination'", pagination)
        self.assertIn("$ReactionPipelineBurstSize", pagination)


class TargetOpenInlineFeedFixtureTests(unittest.TestCase):
    @staticmethod
    def _node(label, bounds, *, package="com.facebook.katana", class_name="android.view.ViewGroup", clickable=False):
        return (
            f'<node text="{label}" content-desc="{label}" class="{class_name}" '
            f'package="{package}" clickable="{str(clickable).lower()}" enabled="true" '
            f'bounds="{bounds}" />'
        )

    @classmethod
    def _run_fixture(cls, xml, *, adb_result=True):
        with tempfile.TemporaryDirectory(prefix="facebook-target-open-") as temp_dir:
            xml_path = Path(temp_dir) / "target-open-ui.xml"
            xml_path.write_text(xml, encoding="utf-8")
            source_path = OPEN_PATH.replace("'", "''")
            fixture_path = str(xml_path).replace("\\", "/").replace("'", "''")
            script = f"""
$ErrorActionPreference = 'Stop'
$sourcePath = '{source_path}'
$fixturePath = '{fixture_path}'
$tokens = $null
$parseErrors = $null
$sourceAst = [System.Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) {{ throw ($parseErrors | ForEach-Object Message -join '; ') }}
$functionNames = @('Get-UiNodes', 'Tap-Node', 'Open-InlineFeedCardDetail')
$functionAsts = $sourceAst.FindAll({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $functionNames -contains $node.Name }}, $true)
foreach ($functionAst in $functionAsts) {{ Invoke-Expression $functionAst.Extent.Text }}
$Serial = 'fixture'
$WaitSeconds = 1
$script:AdbResult = ${str(bool(adb_result)).lower()}
$script:TapCalls = @()
$script:BoundaryCalls = 0
$script:DumpCalls = 0
function Start-Sleep {{ param([int]$Milliseconds = 0, [int]$Seconds = 0) }}
function Invoke-FacebookAdbQuiet {{
    param([string]$Serial, [string[]]$Arguments)
    $script:TapCalls += ,([string]::Join(' ', $Arguments))
    return $script:AdbResult
}}
function Set-VerificationBoundary {{ $script:BoundaryCalls++ }}
function Wait-TargetNavigation {{ param([string]$Path, [int]$BudgetMilliseconds, [string]$Phase); return $false }}
function Dump-Ui([string]$Path) {{ $script:DumpCalls++; return $Path }}
$result = Open-InlineFeedCardDetail $fixturePath
[pscustomobject]@{{
    result = [bool]$result
    taps = [int]$script:TapCalls.Count
    tap = if ($script:TapCalls.Count -gt 0) {{ [string]$script:TapCalls[0] }} else {{ '' }}
    boundaries = [int]$script:BoundaryCalls
    dumps = [int]$script:DumpCalls
}} | ConvertTo-Json -Compress
"""
            return run_powershell(script)

    def test_timestamp_navigation_respects_localized_labels_and_guards(self):
        cases = {
            "chinese-simplified": {
                "xml": "<hierarchy>" + self._node("9\u67088\u65e5\u2022\u5206\u4eab\u5bf9\u8c61\uff1a\u516c\u5f00", "[158,468][319,514]") + "</hierarchy>",
                "adb_result": True,
                "result": True,
                "taps": 1,
                "tap": "shell input tap 238 491",
                "boundaries": 1,
                "dumps": 1,
            },
            "chinese-traditional": {
                "xml": "<hierarchy>" + self._node("9\u67088\u65e5\u2022\u5206\u4eab\u5c0d\u8c61:\u516c\u958b", "[158,468][319,514]") + "</hierarchy>",
                "adb_result": True,
                "result": True,
                "taps": 1,
                "tap": "shell input tap 238 491",
                "boundaries": 1,
                "dumps": 1,
            },
            "english": {
                "xml": "<hierarchy>" + self._node("Sep 8 \u00b7 Shared with: Public", "[158,468][319,514]") + "</hierarchy>",
                "adb_result": True,
                "result": True,
                "taps": 1,
                "tap": "shell input tap 238 491",
                "boundaries": 1,
                "dumps": 1,
            },
            "english-fullwidth-colon": {
                "xml": "<hierarchy>" + self._node("Sep 8 \u00b7 Shared to\uff1aPublic", "[158,468][319,514]") + "</hierarchy>",
                "adb_result": True,
                "result": True,
                "taps": 1,
                "tap": "shell input tap 238 491",
                "boundaries": 1,
                "dumps": 1,
            },
            "existing-detail": {
                "xml": "<hierarchy>"
                + self._node("Close", "[20,100][80,180]", class_name="android.widget.Button", clickable=True)
                + self._node("", "[32,1700][1048,1800]", class_name="android.widget.AutoCompleteTextView", clickable=True)
                + self._node("9\u67088\u65e5\u2022\u5206\u4eab\u5bf9\u8c61\uff1a\u516c\u5f00", "[158,468][319,514]")
                + "</hierarchy>",
                "adb_result": True,
                "result": False,
                "taps": 0,
                "tap": "",
                "boundaries": 0,
                "dumps": 0,
            },
            "foreign-package": {
                "xml": "<hierarchy>"
                + self._node("9\u67088\u65e5\u2022\u5206\u4eab\u5bf9\u8c61\uff1a\u516c\u5f00", "[158,468][319,514]", package="com.example.other")
                + "</hierarchy>",
                "adb_result": True,
                "result": False,
                "taps": 0,
                "tap": "",
                "boundaries": 0,
                "dumps": 0,
            },
            "wrong-bounds": {
                "xml": "<hierarchy>" + self._node("9\u67088\u65e5\u2022\u5206\u4eab\u5bf9\u8c61\uff1a\u516c\u5f00", "[158,120][319,166]") + "</hierarchy>",
                "adb_result": True,
                "result": False,
                "taps": 0,
                "tap": "",
                "boundaries": 0,
                "dumps": 0,
            },
            "tap-failure": {
                "xml": "<hierarchy>" + self._node("9\u67088\u65e5\u2022\u5206\u4eab\u5bf9\u8c61\uff1a\u516c\u5f00", "[158,468][319,514]") + "</hierarchy>",
                "adb_result": False,
                "result": False,
                "taps": 1,
                "tap": "shell input tap 238 491",
                "boundaries": 1,
                "dumps": 0,
            },
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                result = self._run_fixture(expected["xml"], adb_result=expected["adb_result"])
                self.assertEqual(result, {
                    "result": expected["result"],
                    "taps": expected["taps"],
                    "tap": expected["tap"],
                    "boundaries": expected["boundaries"],
                    "dumps": expected["dumps"],
                })


class TargetUiSnapshotTests(unittest.TestCase):
    @staticmethod
    def run_probe(mode="fresh", budget=8000):
        script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{OPEN_PATH}', [ref]$tokens, [ref]$parseErrors)
$fn = $ast.FindAll({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Save-TargetUiProbe' }}, $true)[0]
Invoke-Expression $fn.Extent.Text
$Serial = 'fixture'
$script:Clock = 0; $script:Commands = @()
function Get-TargetWaitMilliseconds {{ return $script:Clock }}
function Invoke-FacebookAdbCommand {{
    param($Serial, $Arguments, $TimeoutSeconds)
    $script:Commands += ($Arguments -join ' ')
    $script:Clock += 1200
    $output = if ('{mode}' -eq 'fresh') {{ '<?xml version="1.0"?><hierarchy><node text="current" /></hierarchy>UI hierchary dumped to: /dev/tty' }} else {{ 'ERROR: no XML written' }}
    return [pscustomobject]@{{ Success = ('{mode}' -ne 'timeout'); TimedOut = ('{mode}' -eq 'timeout'); Stdout = $output }}
}}
$local = Join-Path ([IO.Path]::GetTempPath()) ('facebook-probe-fixture-' + [Guid]::NewGuid().ToString('N') + '.xml')
try {{
    [IO.File]::WriteAllText($local, 'previous-snapshot')
    $ready = Save-TargetUiProbe $local {budget}
    [pscustomobject]@{{ ready = $ready; commands = @($script:Commands); reason = $script:TargetUiProbeReason; content = [IO.File]::ReadAllText($local) }} | ConvertTo-Json -Compress
}} finally {{ Remove-Item -LiteralPath $local -Force -ErrorAction SilentlyContinue }}
"""
        return run_powershell(script)

    def test_zero_exit_without_new_xml_cannot_accept_old_snapshot(self):
        result = self.run_probe("zero-without-write")
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "ui-output-invalid")
        self.assertEqual(result["content"], "previous-snapshot")

    def test_timeout_keeps_failure_reason_and_does_not_write_snapshot(self):
        result = self.run_probe("timeout")
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "ui-dump-timeout")
        self.assertEqual(len(result["commands"]), 1)
        self.assertEqual(result["content"], "previous-snapshot")

    def test_current_stdout_xml_is_accepted_without_cli_banner(self):
        result = self.run_probe()
        self.assertTrue(result["ready"])
        self.assertEqual(result["commands"], ["exec-out uiautomator dump /dev/tty"])
        self.assertIn('text="current"', result["content"])
        self.assertTrue(result["content"].endswith("</hierarchy>"))

    def test_small_budget_preserves_time_for_identity_proof(self):
        result = self.run_probe(budget=1400)
        self.assertFalse(result["ready"])
        self.assertEqual(len(result["commands"]), 0)


class TargetNavigationWaitTests(unittest.TestCase):
    @staticmethod
    def run_wait(*, proof="ready", snapshot=True, enabled=True, disabled=False, ui_ms=1200, proof_ms=150, budget=8000, phase="fixture", env_disabled=False):
        # Execute the shipped function with a controlled monotonic clock. The
        # proof itself is covered by test_facebook_target_readiness.py.
        script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{OPEN_PATH}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) {{ throw 'Source parse failed' }}
$fn = $ast.FindAll({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Wait-TargetNavigation' }}, $true)[0]
Invoke-Expression $fn.Extent.Text
$Base = 'fixture'; $Out = 'fixture'; $CaptureJsonl = 'capture.jsonl'
$VerificationMinLine = 17
$DisableAdaptiveTargetWait = ${str(disabled).lower()}
$EnableAdaptiveTargetWait = ${str(enabled).lower()}
$env:FACEBOOK_DISABLE_ADAPTIVE_TARGET_WAIT = '{"1" if env_disabled else ""}'
$script:NavigationWaits = @(); $script:Clock = 0; $script:Sleeps = @()
$script:Copies = 0; $script:UiCalls = 0; $script:ProofCalls = 0
$script:RequireDetailSeen = $false
function Get-TargetWaitMilliseconds {{ return $script:Clock }}
function Start-Sleep {{ param([int]$Milliseconds); $script:Sleeps += $Milliseconds; $script:Clock += $Milliseconds }}
function Test-Path {{ return $true }}
function Copy-Item {{ $script:Copies++ }}
function Save-TargetUiProbe {{ param($Path, $Deadline); $script:UiCalls++; $script:Clock += {ui_ms}; return ${str(snapshot).lower()} }}
function Invoke-TargetReadinessProbe {{
    param($Path, $TimeoutMilliseconds, [switch]$RequireDetail)
    $script:RequireDetailSeen = [bool]$RequireDetail
    $script:ProofCalls++; $script:Clock += {proof_ms}
    if ('{proof}' -eq 'exception') {{ throw 'injected probe failure' }}
    if ('{proof}' -eq 'malformed') {{ return [pscustomobject]@{{ ready = 'true'; reason = 'invalid' }} }}
    return [pscustomobject]@{{ ready = ('{proof}' -eq 'ready'); reason = '{proof}' }}
}}
$ready = Wait-TargetNavigation 'final.xml' {budget} '{phase}'
[pscustomobject]@{{ ready = [bool]$ready; elapsed = $script:Clock; sleeps = @($script:Sleeps); copies = $script:Copies; ui_calls = $script:UiCalls; proof_calls = $script:ProofCalls; require_detail = $script:RequireDetailSeen; waits = @($script:NavigationWaits) }} | ConvertTo-Json -Compress -Depth 5
"""
        return run_powershell(script)

    def test_proven_target_finishes_early_and_reuses_only_current_snapshot(self):
        result = self.run_wait()
        self.assertTrue(result["ready"])
        self.assertEqual(result["elapsed"], 2350)
        self.assertEqual(result["sleeps"], [1000])
        self.assertEqual(result["copies"], 1)
        self.assertEqual(result["waits"][0]["verification_min_line"], 17)

    def test_unproven_target_keeps_original_budget_including_probe_cost(self):
        result = self.run_wait(proof="target-not-visible", ui_ms=2000, proof_ms=400)
        self.assertFalse(result["ready"])
        self.assertEqual(result["elapsed"], 8000)
        self.assertEqual(result["sleeps"], [1000, 4600])
        self.assertEqual(result["copies"], 0)

    def test_probe_failure_and_malformed_proof_fall_back_without_false_success(self):
        for proof in ("exception", "malformed", "probe-timeout"):
            with self.subTest(proof=proof):
                result = self.run_wait(proof=proof)
                self.assertFalse(result["ready"])
                self.assertEqual(result["elapsed"], 8000)
                self.assertEqual(result["copies"], 0)

    def test_failed_snapshot_does_not_read_capture_or_reuse_old_file(self):
        result = self.run_wait(snapshot=False)
        self.assertFalse(result["ready"])
        self.assertEqual(result["elapsed"], 8000)
        self.assertEqual(result["proof_calls"], 0)
        self.assertEqual(result["copies"], 0)

    def test_disabled_and_small_waits_use_exact_legacy_budget(self):
        for disabled, budget in ((True, 8000), (False, 1000)):
            with self.subTest(disabled=disabled, budget=budget):
                result = self.run_wait(disabled=disabled, budget=budget)
                self.assertFalse(result["ready"])
                self.assertEqual(result["elapsed"], budget)
                self.assertEqual(result["sleeps"], [budget])
                self.assertEqual(result["ui_calls"], 0)

    def test_detail_transition_retains_combined_tap_and_navigation_budget(self):
        result = self.run_wait(proof="target-not-visible", budget=10000, phase="timestamp-detail")
        self.assertFalse(result["ready"])
        self.assertEqual(result["elapsed"], 10000)
        self.assertTrue(result["require_detail"])

    def test_environment_override_disables_probes(self):
        result = self.run_wait(env_disabled=True)
        self.assertEqual(result["ui_calls"], 0)
        self.assertEqual(result["sleeps"], [8000])

    def test_default_retains_original_wait_without_starting_probe(self):
        result = self.run_wait(enabled=False)
        self.assertFalse(result["ready"])
        self.assertEqual(result["ui_calls"], 0)
        self.assertEqual(result["proof_calls"], 0)
        self.assertEqual(result["sleeps"], [8000])
        self.assertEqual(result["waits"][0]["reason"], "not-enabled")

    def test_insufficient_remaining_budget_does_not_launch_another_process(self):
        result = self.run_wait(ui_ms=6800)
        self.assertEqual(result["elapsed"], 8000)
        self.assertEqual(result["proof_calls"], 0)
        self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main()


class CaptureTimingContractTests(unittest.TestCase):
    def test_timing_preserves_child_returns_errors_and_output(self):
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not shell:
            self.skipTest("PowerShell unavailable")
        runner = str(ROOT / "run_facebook_capture.ps1").replace("'", "''")
        script = r"""
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('__RUNNER__',[ref]$tokens,[ref]$errors)
if($errors.Count){ throw 'runner AST failed' }
$names=@('Write-CaptureTiming','Invoke-CaptureChildPowerShell','Invoke-CapturePythonHidden')
$defs=$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $names},$true)
if($defs.Count -ne 3){ throw 'missing timing wrappers' }
foreach($d in $defs){ Invoke-Expression $d.Extent.Text }
$Base='dummy'; $PowerShellExe='dummy'; $PythonExe='dummy'
$script:fail=$false; $script:code=0
function Invoke-FacebookPowerShellFileCaptured {
    if($script:fail){throw 'original-child-error'}
    return [pscustomobject]@{ExitCode=$script:code;Stdout='ok';Stderr=''}
}
function Write-FacebookChildProcessOutput {param($Result)}
function Invoke-FacebookPythonCaptured {
    if($script:fail){throw 'original-python-error'}
    return [pscustomobject]@{ExitCode=$script:code;Stdout='payload';Stderr=''}
}
foreach($script:code in @(0,7)) {
    $got=@(Invoke-CaptureChildPowerShell 'safe-child.ps1' @('PRIVATE_ARG'))
    if($got.Count -ne 1 -or $got[0] -ne $script:code){throw 'changed child result'}
    $got=@(Invoke-CapturePythonHidden @('safe-python.py','PRIVATE_ARG'))
    if($got.Count -ne 1 -or $got[0].ExitCode -ne $script:code -or $global:LASTEXITCODE -ne $script:code){throw 'changed Python result'}
    $quiet=@(Invoke-CapturePythonHidden @('safe-python.py') -QuietStdout)
    if($quiet.Count -ne 1){throw 'quiet output polluted'}
}
$script:fail=$true
try {Invoke-CaptureChildPowerShell 'safe-child.ps1' @();throw 'missing error'} catch {if($_.Exception.Message -ne 'original-child-error'){throw}}
try {Invoke-CapturePythonHidden @('safe-python.py');throw 'missing error'} catch {if($_.Exception.Message -ne 'original-python-error'){throw}}
$script:fail=$false
# An optional logging failure cannot replace a successful operation result.
function Write-Host {param($Object) throw 'timing-sink-error'}
$got=Invoke-CaptureChildPowerShell 'safe-child.ps1' @()
if($got -ne 7){throw 'logging failure changed return'}
$got=Invoke-CapturePythonHidden @('safe-python.py') -QuietStdout
if($got.ExitCode -ne 7){throw 'logging failure changed Python result'}
""".replace("__RUNNER__", runner)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "timing-contract.ps1"
            path.write_text(script, encoding="utf-8-sig")
            result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(path)],
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[capture-timing] phase=safe-child.ps1 elapsed_ms=", result.stdout)
        self.assertIn("[capture-timing] phase=safe-python.py elapsed_ms=", result.stdout)
        self.assertNotIn("PRIVATE_ARG", result.stdout + result.stderr)


class PreparationTimingBoundaryTests(unittest.TestCase):
    def test_preparation_marks_are_disjoint_and_do_not_reorder_readiness(self):
        source = (ROOT / "run_facebook_capture.ps1").read_text(encoding="utf-8-sig")
        markers = [
            '$preparationClock = [System.Diagnostics.Stopwatch]::StartNew()',
            'Write-CaptureTiming "runner-initialization" $preparationClock',
            'Ensure-FacebookFrameworkServicesReady -Serial $Serial -TimeoutSeconds 45',
            'Write-CaptureTiming "device-preparation" $preparationClock',
            '$null = Ensure-FacebookNormalNetwork',
            'Write-CaptureTiming "network-capture-preparation" $preparationClock',
            '$captureDeadline = if ($DurationSeconds -gt 0)',
        ]
        positions = [source.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(source.count('$preparationClock.Restart()'), 2)
        self.assertIn('Start-Sleep -Seconds 3', source)
