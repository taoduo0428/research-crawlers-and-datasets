[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [int]$ProxyPort = 8081,
    [string]$PostUrl = "",
    [string]$PostId = "",
    [Parameter(Mandatory = $true)]
    [string]$CaptureJsonl,
    [Parameter(Mandatory = $true)]
    [string]$OutputDir,
    [int]$WaitSeconds = 8,
    [int]$CaptureMinLine = 1,
    [switch]$KeepFacebookProcess,
    [switch]$RequireFreshUi,
    [switch]$PreserveGroupInlineShareRoute,
    [switch]$EnableAdaptiveTargetWait,
    [switch]$DisableAdaptiveTargetWait,
    [switch]$ForceFacebookSearch,
    [switch]$EnableFeedCardFallback
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'
if (-not (Test-Path $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
. $RuntimeHelper
$PythonExe = Resolve-FacebookPythonExe -BasePath $Base
if (-not (Test-Path $PythonExe)) {
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue }
    if (-not $pythonCommand) { throw 'Python executable could not be resolved.' }
    $PythonExe = [string]$pythonCommand.Source
}
$TransportHelper = Join-Path $Base 'facebook_transport.ps1'
if (Test-Path $TransportHelper) { . $TransportHelper }
$Out = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$DiagnosticPath = Join-Path $Out "target-open-diagnostics.json"
$ResolvedUrl = $PostUrl
$Method = ""
$UiPath = Join-Path $Out "target-open-ui.xml"
$RecoveryRequestPath = Join-Path $Out "target-open-recovery-request.json"
if (Test-Path -LiteralPath $RecoveryRequestPath) {
    Remove-Item -LiteralPath $RecoveryRequestPath -Force -ErrorAction SilentlyContinue
}
$VerificationMinLine = [Math]::Max(1, $CaptureMinLine)
$VerificationAttempts = 0
$VerificationElapsedMs = 0
$VerificationTimeoutSeconds = [Math]::Max(8, [Math]::Min(20, $WaitSeconds + 8))
$ResolvedPostId = ''
$ResolvedAuthorId = ''
$ResolvedLegacyStoryId = ''
$NavigationUiReady = $false
$script:TargetUiState = 'unknown'
$script:TargetUiNodeCount = 0
$script:TargetUiLabelCount = 0
$InlineGroupShareRoute = $false
$NavigationWaits = @()
$script:SearchState = 'not-used'
$script:SearchPolls = 0
$script:FeedFallbackAttempted = $false
$script:FeedFallbackPolls = 0
$script:FeedFallbackReason = 'not-used'
$script:FeedFallbackStates = @()
$TargetOpenTimer = [Diagnostics.Stopwatch]::StartNew()

function Get-TargetWaitMilliseconds {
    return [double]([Diagnostics.Stopwatch]::GetTimestamp() * 1000.0 / [Diagnostics.Stopwatch]::Frequency)
}

function Get-CaptureJsonlLineCount {
    if (-not (Test-Path $CaptureJsonl)) { return 0 }
    return [int](@([IO.File]::ReadLines($CaptureJsonl)).Count)
}

function Set-VerificationBoundary {
    $nextLine = (Get-CaptureJsonlLineCount) + 1
    $script:VerificationMinLine = [Math]::Max([Math]::Max(1, $CaptureMinLine), $nextLine)
    Write-Host "[target-open] verification window starts at capture line $script:VerificationMinLine"
}

function Write-Diagnostic([bool]$Verified, [string]$Reason, [int]$AnalyzerExit = -1) {
    [ordered]@{
        serial = $Serial
        requested_post_id = $PostId
        requested_post_url = $PostUrl
        resolved_url = $ResolvedUrl
        method = $Method
        verified = $Verified
        reason = $Reason
        analyzer_exit = $AnalyzerExit
        verification_min_line = $VerificationMinLine
        verification_attempts = $script:VerificationAttempts
        verification_elapsed_ms = $script:VerificationElapsedMs
        verification_timeout_seconds = $VerificationTimeoutSeconds
        resolved_post_id = $script:ResolvedPostId
        resolved_author_id = $script:ResolvedAuthorId
        resolved_legacy_api_story_id = $script:ResolvedLegacyStoryId
        ui_state = $script:TargetUiState
        ui_node_count = $script:TargetUiNodeCount
        ui_label_count = $script:TargetUiLabelCount
        ui_path = $UiPath
        inline_group_share_route = [bool]$script:InlineGroupShareRoute
        search_state = $script:SearchState
        search_polls = $script:SearchPolls
        feed_card_fallback = [ordered]@{
            attempted = [bool]$script:FeedFallbackAttempted
            polls = [int]$script:FeedFallbackPolls
            reason = [string]$script:FeedFallbackReason
            states = @($script:FeedFallbackStates)
        }
        navigation_waits = @($script:NavigationWaits)
        target_open_elapsed_ms = [long]$TargetOpenTimer.ElapsedMilliseconds
        timestamp = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $DiagnosticPath
}

function Dump-Ui([string]$Path) {
    $remote = "/sdcard/codex-facebook-target-ui.xml"
    if ($RequireFreshUi -or (Get-Command Invoke-FacebookUiSnapshot -ErrorAction SilentlyContinue)) {
        $getSnapshot = {
            if ($RequireFreshUi) {
                # Share-only navigation must never pull stale legacy XML.
                return Invoke-FacebookFastUiDump -Serial $Serial -LocalPath $Path -TimeoutSeconds 10 -RequireFreshResult
            }
            return Invoke-FacebookUiSnapshot `
                -Serial $Serial `
                -LocalPath $Path `
                -RemotePath $remote `
                -LegacyTimeoutSeconds 12 `
                -LegacyRetryCount 1 `
                -FastTimeoutSeconds 10
        }
        $snapshot = & $getSnapshot
        if (-not $snapshot.Success) {
            if ($RequireFreshUi) { throw "Fresh UI dump failed during target-open: $($snapshot.Error)" }
            throw "UI dump failed via legacy and instrumentation fallback: $($snapshot.Error)"
        }
        if (Get-Command Get-FacebookUiBlockingState -ErrorAction SilentlyContinue) {
            $raw = [IO.File]::ReadAllText($Path)
            $blockingState = Get-FacebookUiBlockingState -Raw $raw
            $anrClose = Get-FacebookUiNodeCenterByResourceId -Raw $raw -ResourceId 'android:id/aerr_close'
            if ($anrClose -or $blockingState -eq 'system-ui-not-responding') {
                Write-Warning '[target-open] Android ANR dialog detected; closing the blocking app before the outer strict target retry.'
                $anrDismissed = $false
                $close = $anrClose
                if ($close) {
                    $anrDismissed = [bool](Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','input','tap',[string]$close.X,[string]$close.Y) -TimeoutSeconds 5)
                }
                if (-not $anrDismissed) {
                    $anrDismissed = [bool](Dismiss-FacebookSystemUiAnr -Serial $Serial -Raw $raw)
                }
                if (-not $anrDismissed) {
                    throw 'System UI ANR could not be dismissed during target-open'
                }
                Start-Sleep -Milliseconds 2000
                $snapshot = & $getSnapshot
                if (-not $snapshot.Success) {
                    throw "UI dump failed after target-open ANR recovery: $($snapshot.Error)"
                }
            }
        }
        return
    }
    # Legacy command shape: shell uiautomator dump <remote>.
    $dumpResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'uiautomator', 'dump', $remote) -TimeoutSeconds 12
    if (-not $dumpResult.Success) { throw "UI dump failed: $($dumpResult.Stderr)" }
    $pullResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('pull', $remote, $Path) -TimeoutSeconds 12
    if (-not $pullResult.Success) { throw "UI dump pull failed: $($pullResult.Stderr)" }
}

function Get-TargetUiState([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return 'ui-empty' }
    $raw = [IO.File]::ReadAllText($Path)
    if ([string]::IsNullOrWhiteSpace($raw)) { return 'ui-empty' }
    $script:TargetUiNodeCount = [regex]::Matches($raw, '<node\b').Count
    $script:TargetUiLabelCount = [regex]::Matches($raw, '(?:text|content-desc)="[^"]+"').Count
    $webErrorPattern = '(?i)(?:webpage\s+(?:isn''t|is\s+not|cannot|can''t)\s+(?:open|load|display)|(?:can''t|cannot)\s+load\s+(?:this\s+)?page|error\s+(?:loading|opening)\s+(?:this\s+)?page|no\s+internet\s+connection|this\s+page\s+isn''t\s+available|content\s+isn''t\s+available\s+right\s+now|网页(?:无法|不能|打不开)(?:打开|加载|显示)|无法打开网页|网页加载失败|内容目前无法显示)'
    if ($raw -match $webErrorPattern) { return 'web-error' }
    $packageMatch = [regex]::Match($raw, 'package="([^"]+)"')
    $package = if ($packageMatch.Success) { $packageMatch.Groups[1].Value } else { '' }
    if ($package -like 'com.facebook.katana*') {
        $hasWebView = $raw -match '(?i)class="(?:android\.webkit\.)?WebView"'
        if (($hasWebView -and $script:TargetUiLabelCount -lt 3) -or ($script:TargetUiLabelCount -eq 0 -and $script:TargetUiNodeCount -le 20)) {
            return 'empty-placeholder'
        }
        return 'facebook-content'
    }
    if ($package) { return 'foreign-surface' }
    return 'ui-empty'
}

function Test-ReelTargetUrl([string]$Url) {
    return [bool]($Url -match '(?i)^https://(?:www\.|m\.)?facebook\.com/reel/[0-9]{1,32}/?(?:[?#].*)?$')
}

function Test-ReelDetailSurface([string]$Path) {
    if ((Get-TargetUiState $Path) -ne 'facebook-content') { return $false }
    $raw = [IO.File]::ReadAllText($Path)
    if ($raw -match '(?i)(?:log\s*in|sign\s*in|\u767b\u5f55|\u767b\u5165|session\s+expired|\u5185\u5bb9\u76ee\u524d\u65e0\u6cd5\u663e\u793a|content\s+isn''t\s+available)') {
        return $false
    }
    $nodes = @(Get-UiNodes $Path)
    if ($nodes.Count -eq 0) { return $false }
    $screenRight = [int](($nodes | Measure-Object -Property X2 -Maximum).Maximum)
    if ($screenRight -le 0) { return $false }
    $hasReelDetail = [bool]($nodes | Where-Object {
        $_.Package -like 'com.facebook.katana*' -and $_.Label -match '(?i)(?:reels?\s*(?:\u8be6\u60c5|details?)|reels?\s+detail)'
    } | Select-Object -First 1)
    $reactionRail = $nodes | Where-Object {
        $_.Package -like 'com.facebook.katana*' -and $_.Clickable -and
        $_.X1 -ge [int]($screenRight * 0.70) -and
        $_.Label -match '(?i)^[0-9][0-9,.\s]*\s*(?:\u4e2a\u5fc3\u60c5|\u6b21\u5fc3\u60c5|reactions?|likes?)$'
    } | Select-Object -First 1
    return [bool]($hasReelDetail -and $reactionRail)
}

function Write-TargetOpenRecoveryRequest([string]$Reason, [string]$SearchPostId = '') {
    [ordered]@{
        serial = $Serial
        reason = $Reason
        search_post_id = $SearchPostId
        ui_path = $UiPath
        action = 'force-stop-and-relaunch-facebook-before-next-strict-attempt'
        account_data_touched = $false
        timestamp = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath $RecoveryRequestPath
}

function Get-UiNodes([string]$Path) {
    $raw = [IO.File]::ReadAllText($Path)
    foreach ($tag in [regex]::Matches($raw, '<node\b[^>]*>')) {
        $attrs = @{}
        foreach ($a in [regex]::Matches($tag.Value, '([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"')) {
            $attrs[$a.Groups[1].Value] = $a.Groups[2].Value
        }
        $m = [regex]::Match([string]$attrs['bounds'], '^\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]$')
        if (-not $m.Success) { continue }
        $label = if ($attrs['text']) { [string]$attrs['text'] } else { [string]$attrs['content-desc'] }
        [pscustomobject]@{
            Label = $label
            ResourceId = [string]$attrs['resource-id']
            Package = [string]$attrs['package']
            Class = [string]$attrs['class']
            Clickable = ([string]$attrs['clickable'] -eq 'true')
            Enabled = ([string]$attrs['enabled'] -eq 'true')
            Scrollable = ([string]$attrs['scrollable'] -eq 'true')
            X1 = [int]$m.Groups[1].Value
            Y1 = [int]$m.Groups[2].Value
            X2 = [int]$m.Groups[3].Value
            Y2 = [int]$m.Groups[4].Value
            Bounds = [string]$attrs['bounds']
        }
    }
}

function Tap-Node($Node, [int]$WaitMilliseconds = 2000) {
    if (-not $Node) { return $false }
    $x = [int](($Node.X1 + $Node.X2) / 2)
    $y = [int](($Node.Y1 + $Node.Y2) / 2)
    if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'tap', [string]$x, [string]$y))) {
        return $false
    }
    if ($WaitMilliseconds -gt 0) { Start-Sleep -Milliseconds $WaitMilliseconds }
    return $true
}

function Save-TargetUiProbe([string]$Path, [double]$Deadline) {
    # A single cheap probe consumes the existing wait budget. Do not run the
    # slower instrumentation/recovery path here; the normal Dump-Ui still owns
    # those fallbacks after the original navigation wait.
    $script:TargetUiProbeReason = 'probe-budget-exhausted'
    $timeout = [int][Math]::Floor(($Deadline - (Get-TargetWaitMilliseconds) - 500) / 1000)
    if ($timeout -lt 1) { return $false }
    # Stream this invocation's XML directly. This saves two ADB round trips
    # and cannot reuse a stale device file when uiautomator exits without XML.
    $dump = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('exec-out', 'uiautomator', 'dump', '/dev/tty') -TimeoutSeconds ([Math]::Min(6, $timeout))
    if (-not $dump.Success) {
        $script:TargetUiProbeReason = if ($dump.TimedOut) { 'ui-dump-timeout' } else { 'ui-dump-failed' }
        return $false
    }
    # The CLI appends a success banner after the XML document. Only the
    # complete hierarchy is passed to the strict local XML/identity reader.
    $xml = [regex]::Match([string]$dump.Stdout, '(?s)(?:<\?xml[^>]*>\s*)?<hierarchy\b[^>]*>.*?</hierarchy>')
    if (-not $xml.Success) {
        $script:TargetUiProbeReason = 'ui-output-invalid'
        return $false
    }
    [IO.File]::WriteAllText($Path, $xml.Value, [Text.UTF8Encoding]::new($false))
    return $true
}

function Invoke-TargetReadinessProbe([string]$Path, [int]$TimeoutMilliseconds, [switch]$RequireDetail) {
    $probeArgs = @(
        (Join-Path $Base 'facebook_target_readiness.py'),
        '--ui', $Path, '--capture', $CaptureJsonl,
        '--min-line', [string]$VerificationMinLine
    )
    if ($PostId) { $probeArgs += @('--expected-post-id', $PostId) }
    if ($ResolvedUrl) { $probeArgs += @('--expected-url', $ResolvedUrl) }
    if ($RequireDetail) { $probeArgs += '--require-detail' }
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = (@($probeArgs | ForEach-Object { ConvertTo-FacebookNativeArgument ([string]$_) }) -join ' ')
    $psi.WorkingDirectory = $Base
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $psi
    try {
        if (-not $process.Start()) { throw 'Readiness probe could not start.' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit([Math]::Max(1, $TimeoutMilliseconds))) {
            # This helper only reads local files and never starts children.
            $process.Kill()
            $null = $process.WaitForExit(1000)
            return [pscustomobject]@{ ready = $false; reason = 'probe-timeout' }
        }
        if ($process.ExitCode -ne 0) { return [pscustomobject]@{ ready = $false; reason = 'probe-failed' } }
        return ([string]$stdout.Result | ConvertFrom-Json)
    }
    finally {
        # Script-level interruption must not leave the optional local reader
        # alive. A hard parent termination is handled by the API's owned-tree
        # cancellation, as for the final analyzer and other capture children.
        try {
            if (-not $process.HasExited) {
                $process.Kill()
                $null = $process.WaitForExit(1000)
            }
        } catch {}
        $process.Dispose()
    }
}

function Wait-TargetNavigation([string]$Path, [int]$BudgetMilliseconds, [string]$Phase) {
    $started = Get-TargetWaitMilliseconds
    $deadline = $started + $BudgetMilliseconds
    $record = [ordered]@{
        phase = $Phase; budget_ms = $BudgetMilliseconds; elapsed_ms = 0
        sleep_ms = 0; ui_probe_ms = 0; evidence_probe_ms = 0
        early_ready = $false; reason = 'legacy-wait'; verification_min_line = $VerificationMinLine
        matched_line = $null
    }
    try {
        $helper = Join-Path $Base 'facebook_target_readiness.py'
        if ($DisableAdaptiveTargetWait -or $env:FACEBOOK_DISABLE_ADAPTIVE_TARGET_WAIT -eq '1') {
            $record.reason = 'disabled'
        }
        elseif (-not $EnableAdaptiveTargetWait) {
            # Real-device calibration has not established a stable speedup.
            # Keep normal capture on the original wait until explicitly tested.
            $record.reason = 'not-enabled'
        }
        elseif ($BudgetMilliseconds -lt 3000 -or -not (Test-Path -LiteralPath $helper)) {
            $record.reason = 'probe-unavailable'
        }
        else {
            # Unique local path prevents a failed pull from reusing an old run's
            # snapshot. The final analyzer is still mandatory, even on early exit.
            $probePath = Join-Path $Out ("navigation-{0}-{1}.xml" -f $Phase, [Guid]::NewGuid().ToString('N'))
            # The intent can return before the new card replaces an old sheet.
            # Let that short transition finish before spending the one probe.
            Start-Sleep -Milliseconds 1000
            $record.sleep_ms += 1000
            $probeStarted = Get-TargetWaitMilliseconds
            $script:TargetUiProbeReason = 'snapshot-unavailable'
            $snapshot = Save-TargetUiProbe $probePath $deadline
            $record.ui_probe_ms = [int]((Get-TargetWaitMilliseconds) - $probeStarted)
            $remaining = [int][Math]::Floor($deadline - (Get-TargetWaitMilliseconds))
            if ($snapshot -and $remaining -ge 500 -and (Test-Path -LiteralPath $CaptureJsonl)) {
                $probeStarted = Get-TargetWaitMilliseconds
                $proof = Invoke-TargetReadinessProbe $probePath ([Math]::Min(2500, $remaining)) -RequireDetail:($Phase -eq 'timestamp-detail')
                $record.evidence_probe_ms = [int]((Get-TargetWaitMilliseconds) - $probeStarted)
                $record.reason = [string]$proof.reason
                if ($proof.PSObject.Properties['matched_line']) { $record.matched_line = $proof.matched_line }
                if ($proof.ready -is [bool] -and $proof.ready) {
                    Copy-Item -LiteralPath $probePath -Destination $Path -Force
                    $record.early_ready = $true
                    Write-Host "[target-open] $Phase page and fresh target evidence ready; ending navigation wait early."
                    return $true
                }
            }
            elseif (-not $snapshot) { $record.reason = $script:TargetUiProbeReason }
            elseif ($remaining -lt 500) { $record.reason = 'probe-budget-exhausted' }
            else { $record.reason = 'capture-unavailable' }
        }
    }
    catch {
        # Optional acceleration cannot turn a slow/unsupported page into failure.
        # Do not log exception payloads: they may contain captured page content.
        $record.reason = 'probe-error'
    }
    finally {
        if (-not $record.early_ready) {
            $remaining = [int][Math]::Ceiling($deadline - (Get-TargetWaitMilliseconds))
            if ($remaining -gt 0) {
                Start-Sleep -Milliseconds $remaining
                $record.sleep_ms += $remaining
            }
        }
        $record.elapsed_ms = [int]((Get-TargetWaitMilliseconds) - $started)
        $script:NavigationWaits += [pscustomobject]$record
    }
    return $false
}

function Open-InlineFeedCardDetail([string]$Path, [string]$ExpectedAuthor = '', [string]$ExpectedMessage = '') {
    $nodes = @(Get-UiNodes $Path)
    $detailComposer = $nodes | Where-Object {
        $_.Class -match 'AutoCompleteTextView' -and $_.Clickable -and $_.Y1 -ge 1600
    } | Select-Object -First 1
    $detailClose = $nodes | Where-Object {
        $_.Clickable -and $_.Y1 -le 220 -and $_.Label -match '^\s*(?:Close|\u5173\u95ed|\u95dc\u9589)\s*$'
    } | Select-Object -First 1
    if ($detailComposer -and $detailClose) { return $false }

    # A web permalink can land on the target as an inline "From your messages"
    # feed card. Its timestamp is the bounded route into that same post detail.
    $timestamps = @($nodes | Where-Object {
        $_.Package -eq 'com.facebook.katana' -and
        $_.Label -match '(?i)(?:Shared with|Shared to|\u5206\u4eab\u5bf9\u8c61|\u5206\u4eab\u5c0d\u8c61)\s*[:\uFF1A]' -and
        $_.Y1 -ge 180 -and $_.Y1 -le 1500
    })
    if ($ExpectedAuthor -or $ExpectedMessage) {
        # A feed can contain several cards with the same "shared with" label.
        # Bind the timestamp to the one card whose author/body is proven by the
        # fresh target-readiness report; never choose the first coordinate match.
        $authorNodes = @($nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Label -and
            $ExpectedAuthor -and $_.Label -match [regex]::Escape($ExpectedAuthor) -and
            $_.Y1 -ge 180 -and $_.Y1 -le 1600
        })
        $messageNodes = @($nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Label -and
            $ExpectedMessage -and $_.Label.Length -ge 12 -and
            $_.Label -match [regex]::Escape(($ExpectedMessage.Substring(0, [Math]::Min(40, $ExpectedMessage.Length)))) -and
            $_.Y1 -ge 180 -and $_.Y1 -le 1800
        })
        $anchored = @($timestamps | Where-Object {
            $ts = $_
            $nearAuthor = $authorNodes | Where-Object { [Math]::Abs($_.Y1 - $ts.Y1) -le 180 }
            $nearMessage = $messageNodes | Where-Object { $_.Y1 -ge ($ts.Y1 - 80) -and $_.Y1 -le ($ts.Y1 + 700) }
            ($authorNodes.Count -eq 0 -or $nearAuthor) -and ($messageNodes.Count -eq 0 -or $nearMessage)
        })
        if ($anchored.Count -ne 1) {
            $script:FeedFallbackReason = if ($anchored.Count -eq 0) { 'target-card-timestamp-not-unique' } else { 'target-card-timestamp-ambiguous' }
            return $false
        }
        $timestamp = $anchored[0]
    }
    else {
        if ($timestamps.Count -ne 1) {
            $script:FeedFallbackReason = if ($timestamps.Count -eq 0) { 'inline-timestamp-not-found' } else { 'inline-timestamp-ambiguous' }
            return $false
        }
        $timestamp = $timestamps[0]
    }
    if (-not $timestamp) { return $false }

    Set-VerificationBoundary
    if (-not (Tap-Node $timestamp -WaitMilliseconds 0)) { return $false }
    $budgetMs = 2000 + ([Math]::Max(1, $WaitSeconds) * 1000)
    if (-not (Wait-TargetNavigation $Path $budgetMs 'timestamp-detail')) { Dump-Ui $Path }
    Write-Host '[target-open] promoted verified inline feed card to post detail via timestamp.'
    return $true
}

function Open-FeedCardFallback {
    $script:FeedFallbackAttempted = $true
    $script:FeedFallbackPolls = 0
    $script:FeedFallbackReason = 'starting'
    $script:SearchState = 'feed-fallback'

    # Return to the normal Feed without touching account data.  This is a
    # bounded last route after direct URL and exact-search failure; it never
    # clicks an unproved card.
    Set-VerificationBoundary
    $stopped = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'am', 'force-stop', 'com.facebook.katana') -TimeoutSeconds 10
    if (-not $stopped) { throw 'Feed-card fallback could not force-stop Facebook safely.' }
    Start-Sleep -Milliseconds 500
    if (-not (Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15)) {
        throw 'Feed-card fallback Facebook launch failed.'
    }
    try { Wait-FacebookUiReady ([Math]::Max(8, $WaitSeconds + 4)) | Out-Null } catch {}

    $deadline = (Get-Date).AddSeconds([Math]::Max(12, [Math]::Min(24, $WaitSeconds + 12)))
    $uiPath = Join-Path $Out 'feed-card-fallback.xml'
    $analysisDir = Join-Path $Out 'feed-card-fallback-analysis'
    $analysisReportPath = Join-Path $analysisDir 'facebook-sample-patterns.json'
    $expectedAuthor = ''
    $expectedMessage = ''
    $scrolls = 0
    do {
        $script:FeedFallbackPolls++
        Dump-Ui $uiPath
        $state = Get-TargetUiState $uiPath
        $rawUi = [IO.File]::ReadAllText($uiPath)
        # Instrumentation dumps can briefly report a foreign root while the
        # Facebook ViewPager is replacing its child.  Only normalize that
        # transient case when the hierarchy has a Facebook root and no visible
        # system-overlay package; a real foreign overlay remains fail-closed.
        if ($state -eq 'foreign-surface' -and
            $rawUi -match '<node\b[^>]*package="com\.facebook\.katana"' -and
            $rawUi -notmatch 'package="(?:com\.android\.systemui|android)"[^>]*visible-to-user="true"') {
            $state = 'facebook-content'
        }
        $script:FeedFallbackStates += [string]$state
        if ($state -in @('web-error', 'empty-placeholder', 'ui-empty', 'foreign-surface')) {
            $script:FeedFallbackReason = "feed-state-$state"
        }
        elseif (Test-Path -LiteralPath $CaptureJsonl) {
            $proof = Invoke-TargetReadinessProbe $uiPath 2500
            $script:FeedFallbackReason = [string]$proof.reason
            if ($proof.ready -is [bool] -and $proof.ready) {
                # Extract author/body from the same fresh window so the click
                # is anchored to the proven card, not the first matching label.
                New-Item -ItemType Directory -Force -Path $analysisDir | Out-Null
                $analysisArgs = @(
                    (Join-Path $Base 'analyze_facebook_capture.py'), '--input', $CaptureJsonl,
                    '--out-dir', $analysisDir, '--min-line', [string]$VerificationMinLine,
                    '--expected-post-id', $PostId, '--require-target-match'
                )
                if ($ResolvedUrl) { $analysisArgs += @('--expected-url', $ResolvedUrl) }
                $analysis = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -Arguments $analysisArgs -WorkingDirectory $Base
                if ($analysis.ExitCode -eq 0 -and (Test-Path -LiteralPath $analysisReportPath)) {
                    try {
                        $report = Get-Content -Raw -Encoding UTF8 $analysisReportPath | ConvertFrom-Json
                        $row = @($report.posts | Where-Object { [string]$_.post_id -eq [string]$PostId }) | Select-Object -First 1
                        if ($row) {
                            $expectedAuthor = [string]$row.author_name
                            $expectedMessage = [string]$row.message
                        }
                    } catch { $script:FeedFallbackReason = 'feed-target-report-invalid' }
                }
                Set-VerificationBoundary
                if (Open-InlineFeedCardDetail $uiPath $expectedAuthor $expectedMessage) {
                    $script:NavigationUiReady = $true
                    $script:Method = 'feed-card-timestamp-detail'
                    $script:FeedFallbackReason = 'target-card-promoted'
                    return $true
                }
            }
        }

        # At most three bounded Feed advances.  The target-readiness proof is
        # repeated after every dump; a swipe itself never counts as success.
        if ($scrolls -lt 3 -and (Get-Date) -lt $deadline) {
            $nodes = @(Get-UiNodes $uiPath)
            $scrollNode = $nodes | Where-Object {
                $_.Package -eq 'com.facebook.katana' -and $_.Scrollable -and
                $_.Y2 -gt $_.Y1 + 200 -and $_.Y1 -lt 1800
            } | Sort-Object @{Expression = { ($_.Y2 - $_.Y1) }; Descending = $true} | Select-Object -First 1
            if (-not $scrollNode) { Start-Sleep -Milliseconds 750; continue }
            $x = [int](($scrollNode.X1 + $scrollNode.X2) / 2)
            $fromY = [int]($scrollNode.Y2 - 120)
            $toY = [int]($scrollNode.Y1 + 120)
            if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', [string]$x, [string]$fromY, [string]$x, [string]$toY, '500') -TimeoutSeconds 8)) {
                $script:FeedFallbackReason = 'feed-scroll-failed'
                break
            }
            $scrolls++
            Start-Sleep -Milliseconds 900
        }
        else { break }
    } while ((Get-Date) -lt $deadline)

    throw "Feed-card fallback could not prove the requested post (reason=$script:FeedFallbackReason polls=$script:FeedFallbackPolls scrolls=$scrolls)."
}


function Restore-VerifiedInlineGroupCard([string]$BeforePath, [string]$ReportPath, [string]$ExpectedPostId, [bool]$PromotionOwned) {
    if (-not $PromotionOwned) { throw 'Group inline return requires this invocation to own the timestamp promotion.' }
    # Recheck the current full-detail surface after capture analysis; never send
    # a generic Back and never dismiss a central-close comments sheet.
    Dump-Ui $UiPath
    $nodes = @(Get-UiNodes $UiPath | Where-Object { $_.Package -eq 'com.facebook.katana' })
    $close = @($nodes | Where-Object { $_.Clickable -and $_.Y2 -le 220 -and $_.Label -match '^(?:Close|\u5173\u95ed|\u95dc\u9589)$' })
    $search = @($nodes | Where-Object { $_.Clickable -and $_.X1 -ge 800 -and $_.Y2 -le 220 -and $_.Label -match '^(?:Search|\u641c\u7d22|\u641c\u5c0b)$' })
    $composer = @($nodes | Where-Object { $_.Class -match 'AutoCompleteTextView' -and $_.Clickable -and $_.Y1 -ge 1600 })
    if ($close.Count -ne 1 -or $close[0].X2 -gt 200 -or $search.Count -ne 1 -or $composer.Count -ne 1) {
        throw 'Owned group promotion is not on a unique full-detail close surface; no return action taken.'
    }
    Copy-Item -LiteralPath $UiPath -Destination (Join-Path $Out 'inline-group-promoted-detail.xml') -Force
    if (-not (Tap-Node $close[0])) { throw 'Could not close the owned group timestamp detail.' }
    Dump-Ui $UiPath
    # Reuse the exact existing report/card proof and compare the full original
    # album anchor. A different card, author, caption, viewport or album fails.
    $compareCode = 'import sys; from facebook_group_inline_route import load,verify_report,initial,require; story=verify_report(load(sys.argv[3]),sys.argv[4],sys.argv[5],int(sys.argv[6])); require(initial(sys.argv[1],story)==initial(sys.argv[2],story),"returned-group-card-changed"); print("SAME_VERIFIED_GROUP_CARD")'
    $returnProof = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -WorkingDirectory $Base -Arguments @(
        '-c', $compareCode, $BeforePath, $UiPath, $ReportPath, $ResolvedUrl, $ExpectedPostId, [string]$VerificationMinLine
    )
    if ($returnProof.ExitCode -ne 0) { throw 'Returned group inline card does not match the freshly verified original anchor.' }
}

function Test-PostSpecificFacebookUrl([string]$Url) {
    if ([string]::IsNullOrWhiteSpace($Url)) { return $false }
    return [bool]($Url -match '(?i)(?:pfbid[A-Za-z0-9]+|[?&]story_fbid=|/posts/(?:[0-9]+|pfbid[A-Za-z0-9]+)|/permalink/[0-9]+|/reel/[0-9]+|/videos/[0-9]+|/watch/\?v=[0-9]{1,32}(?:[&#]|$))')
}

function Test-FacebookShareNavigationUrl([string]$Url) {
    if ([string]::IsNullOrWhiteSpace($Url)) { return $false }
    try {
        $uri = [Uri]$Url
        if ($uri.Host -notmatch '^(?:(?:www|m)\.)?facebook\.com$') { return $false }
        return [bool]($uri.AbsolutePath -match '^/share/(?:p|r|v)/[A-Za-z0-9._-]+/?$')
    } catch {
        return $false
    }
}

function Get-NumericPostIdFromUrl([string]$Url) {
    if ([string]::IsNullOrWhiteSpace($Url)) { return '' }
    try {
        $uri = [Uri]$Url
        $pathMatch = [regex]::Match($uri.AbsolutePath, '(?i)/(?:posts|permalink)/([0-9]{1,32})(?:/|$)')
        if ($pathMatch.Success) { return [string]$pathMatch.Groups[1].Value }
        $queryMatch = [regex]::Match($uri.Query, '(?i)(?:^|[?&])story_fbid=([0-9]{1,32})(?:&|$)')
        if ($queryMatch.Success) { return [string]$queryMatch.Groups[1].Value }
    }
    catch { return '' }
    return ''
}

function Resolve-LocalPostUrl([string]$Id) {
    if ([string]::IsNullOrWhiteSpace($Id)) { return "" }
    $candidates = @()
    $current = Join-Path $Base 'analysis-current\facebook-posts.csv'
    if (Test-Path $current) { $candidates += Get-Item $current }
    $runs = Join-Path $Base 'runs'
    if (Test-Path $runs) {
        $candidates += Get-ChildItem $runs -Filter 'facebook-posts.csv' -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    }
    foreach ($file in $candidates) {
        try {
            $row = Import-Csv $file.FullName | Where-Object { [string]$_.post_id -eq $Id -and $_.url } | Select-Object -First 1
            if ($row -and $row.url -and (Test-PostSpecificFacebookUrl ([string]$row.url))) {
                return [string]$row.url
            }
        } catch {
            continue
        }
    }
    return ""
}

function Convert-ToFacebookIntentUrl([string]$Url) {
    $value = [string]$Url
    if ([string]::IsNullOrWhiteSpace($value)) { return $value }
    try {
        $uri = [Uri]$value
        if ($uri.Host -notmatch '^(?:(?:www|m)\.)?facebook\.com$') {
            return $value
        }
        if ($uri.AbsolutePath -match '^/watch/?$') {
            $video = [regex]::Match($uri.Query, '(?:^|[?&])v=([0-9]{1,32})(?:&|$)', 'IgnoreCase')
            if ($video.Success) {
                return "https://www.facebook.com/reel/$($video.Groups[1].Value)/"
            }
            return $value
        }
        if ($uri.AbsolutePath -notmatch '^/story\.php$') {
            return $value
        }
        $story = [regex]::Match($uri.Query, '(?:^|[?&])story_fbid=(pfbid[A-Za-z0-9]+|[0-9]{1,32})(?:&|$)', 'IgnoreCase')
        $owner = [regex]::Match($uri.Query, '(?:^|[?&])id=(\d+)(?:&|$)', 'IgnoreCase')
        if ($story.Success -and $owner.Success) {
            return "https://www.facebook.com/$($owner.Groups[1].Value)/posts/$($story.Groups[1].Value)"
        }
    } catch {
        return $value
    }
    return $value
}

function Open-Url([string]$Url) {
    # A verified GraphQL prefetch is not enough if Facebook keeps an older story
    # surface on screen.  Stop only the Facebook app, keep mitmdump/proxy/login
    # state intact, then establish a fresh capture boundary for this URL launch.
    $script:NavigationUiReady = $false
    if ($KeepFacebookProcess) {
        Write-Host '[target-open] preserving pre-injected Facebook process.'
    }
    elseif (Get-Command Invoke-FacebookAdbQuiet -ErrorAction SilentlyContinue) {
        $stopped = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @(
            'shell', 'am', 'force-stop', 'com.facebook.katana'
        ) -TimeoutSeconds 10
        if (-not $stopped) { throw "Facebook force-stop failed or timed out before target URL launch" }
    }
    else {
        if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'am', 'force-stop', 'com.facebook.katana') -TimeoutSeconds 10)) {
            throw "Facebook force-stop failed before target URL launch"
        }
    }
    if (-not $KeepFacebookProcess) { Start-Sleep -Milliseconds 500 }
    Set-VerificationBoundary
    $intentUrl = Convert-ToFacebookIntentUrl $Url
    if ($intentUrl -ne $Url) {
        Write-Host "[target-open] normalized mobile story URL for Android VIEW intent: $intentUrl"
    }
    if (Get-Command Start-FacebookUrlBounded -ErrorAction SilentlyContinue) {
        $started = Start-FacebookUrlBounded -Serial $Serial -Url $intentUrl -TimeoutSeconds 15
        if (-not $started) { throw "Facebook explicit URL intent failed or timed out" }
        "explicit Facebook URL intent started: $Url" | Set-Content -Encoding UTF8 (Join-Path $Out 'am-start.txt')
    }
    else {
        $component = 'com.facebook.katana/.IntentUriHandler'
        $flags = '0x34000000'
        $shellUrl = $intentUrl
        if ($intentUrl -match '[\s&|;<>()$`"]') { $shellUrl = "'" + $intentUrl.Replace("'", "'\''") + "'" }
        $process = Start-Process -FilePath 'adb' -ArgumentList @(
            '-s', $Serial, 'shell', 'am', 'start', '-W',
            '-a', 'android.intent.action.VIEW', '-d', $shellUrl,
            '-n', $component, '-f', $flags
        ) -PassThru -WindowStyle Hidden
        if (-not $process.WaitForExit(15000)) {
            try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
            throw "Facebook explicit URL intent timed out"
        }
        if ($process.ExitCode -ne 0) { throw "Facebook explicit URL intent failed" }
        "explicit Facebook URL intent started: $Url" | Set-Content -Encoding UTF8 (Join-Path $Out 'am-start.txt')
    }
    $script:NavigationUiReady = Wait-TargetNavigation $UiPath ([Math]::Max(1, $WaitSeconds) * 1000) 'url-open'
}

function Open-BySearch([string]$Id) {
    $script:SearchState = 'starting'
    $script:SearchPolls = 0
    if (-not $KeepFacebookProcess) {
        if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'am', 'force-stop', 'com.facebook.katana') -TimeoutSeconds 10)) {
            throw "Facebook force-stop failed before target search"
        }
    }
    else {
        Write-Host '[target-open] preserving pre-injected Facebook process for target search.'
    }
    if (-not (Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15)) {
        throw "Facebook app launch failed during target search"
    }
    Start-Sleep -Seconds 5
    $searchHomeUi = Join-Path $Out 'search-home.xml'
    Dump-Ui $searchHomeUi
    $homeNodes = @(Get-UiNodes $searchHomeUi)
    $edit = $homeNodes | Where-Object { $_.Class -match '(?i)EditText' } | Select-Object -First 1
    if (-not $edit) {
        $search = $homeNodes | Where-Object {
            $_.Clickable -and $_.Label -match '(?i)^\s*(?:Search|\u641c\u7d22|\u641c\u5bfb|\u641c\u5c0b)\s*$'
        } | Select-Object -First 1
        if (-not $search -or -not (Tap-Node $search)) {
            $script:SearchState = 'entry-not-found'
            throw "Facebook search entry not found"
        }
        $searchUi = Join-Path $Out 'search-entry.xml'
        Dump-Ui $searchUi
        $edit = @(Get-UiNodes $searchUi) | Where-Object { $_.Class -match '(?i)EditText' } | Select-Object -First 1
        if (-not $edit) {
            $script:SearchState = 'box-not-found'
            throw "Facebook search box not found"
        }
        if (-not (Tap-Node $edit)) {
            $script:SearchState = 'box-not-clickable'
            throw "Facebook search box could not be focused"
        }
    }
    else {
        # Facebook often restores the previous search activity after a relaunch.
        # Reuse that surface instead of requiring the home-page search button.
        $script:SearchState = 'existing-search-surface'
        if (-not (Tap-Node $edit -WaitMilliseconds 0)) {
            $script:SearchState = 'existing-box-not-clickable'
            throw "Facebook restored a search page, but its search box could not be focused"
        }
    }

    $existingText = [string]$edit.Label
    if ($existingText -ne $Id) {
        if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'text', $Id) -TimeoutSeconds 10)) {
            $script:SearchState = 'input-failed'
            throw "Facebook search text input failed"
        }
    }
    # Text input alone can leave Facebook's search surface in an idle state.
    # Submit once; the bounded polling below decides whether it is loading,
    # empty, or has produced an exact-ID result.
    if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '66') -TimeoutSeconds 10)) {
        $script:SearchState = 'submit-failed'
        throw "Facebook search submit failed"
    }

    $suggestUi = Join-Path $Out 'search-suggestion.xml'
    $deadline = (Get-Date).AddSeconds([Math]::Max(12, [Math]::Min(25, $WaitSeconds + 12)))
    $script:SearchState = 'loading'
    do {
        $script:SearchPolls++
        Dump-Ui $suggestUi
        $nodes = @(Get-UiNodes $suggestUi)
        $exact = $nodes | Where-Object {
            $_.Clickable -and
            $_.Class -notmatch '(?i)EditText' -and
            [string]$_.ResourceId -notmatch '(?i)search|query|input' -and
            $_.Y1 -ge 180 -and
            ([string]$_.Label -eq $Id -or [string]$_.Label -match "(?<!\d)$([regex]::Escape($Id))(?!\d)")
        } | Select-Object -First 1
        if ($exact) {
            $script:SearchState = 'exact-result'
            Set-VerificationBoundary
            if (-not (Tap-Node $exact)) {
                $script:SearchState = 'exact-result-not-clickable'
                throw "Exact post-id search suggestion could not be opened"
            }
            Start-Sleep -Seconds ([Math]::Max(3, $WaitSeconds))
            return
        }
        $labels = @($nodes | ForEach-Object { [string]$_.Label }) -join ' | '
        $labelsLower = $labels.ToLowerInvariant()
        $noResultSignals = @('no results', 'no posts found', 'nothing found')
        $noResult = $false
        foreach ($signal in $noResultSignals) {
            if ($labelsLower.Contains($signal)) { $noResult = $true; break }
        }
        if ($noResult) {
            $script:SearchState = 'no-result'
            throw "Exact post-id search returned no result"
        }
        $errorSignals = @('error', 'try again', 'unable to search')
        $searchError = $false
        foreach ($signal in $errorSignals) {
            if ($labelsLower.Contains($signal)) { $searchError = $true; break }
        }
        if ($searchError) {
            $script:SearchState = 'error'
            throw "Facebook search returned an error state"
        }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)
    $script:SearchState = 'loading-timeout'
    throw "Exact post-id search remained loading until the bounded timeout"
}

if ([string]::IsNullOrWhiteSpace($PostUrl) -and [string]::IsNullOrWhiteSpace($PostId)) {
    Write-Diagnostic $false 'missing-target'
    throw "PostUrl or PostId is required"
}

$adbState = Get-FacebookAdbState -Serial $Serial
if ($adbState -ne 'device') { throw "ADB device is not ready: $Serial" }
if (Get-Command Ensure-FacebookProjectTransport -ErrorAction SilentlyContinue) {
    $transport = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort
    Write-Host "[transport] target-open network=$($transport.active_default_network) proxy=$($transport.proxy) capture=$($transport.capture_active)"
}

try {
    if ($ResolvedUrl -and -not (Test-PostSpecificFacebookUrl $ResolvedUrl) -and -not (Test-FacebookShareNavigationUrl $ResolvedUrl)) {
        Write-Warning "[target-open] ignoring non-specific Facebook URL; falling back to exact PostId resolution: $ResolvedUrl"
        $ResolvedUrl = ""
    }
    if ([string]::IsNullOrWhiteSpace($ResolvedUrl) -and $PostId) {
        $ResolvedUrl = Resolve-LocalPostUrl $PostId
        if ($ResolvedUrl) { $Method = 'local-id-to-url' }
    }
    if ($ResolvedUrl -and (Test-FacebookShareNavigationUrl $ResolvedUrl)) {
        $shareResolver = Join-Path $Base 'resolve_facebook_share_url.py'
        if (Test-Path $shareResolver) {
            $resolveResult = Invoke-FacebookPythonCaptured `
                -PythonExe $PythonExe `
                -Arguments @($shareResolver, '--url', $ResolvedUrl, '--timeout-seconds', '15') `
                -WorkingDirectory $Base
            $canonicalUrl = ([string]$resolveResult.Stdout).Trim()
            if ($resolveResult.ExitCode -eq 0 -and (Test-PostSpecificFacebookUrl $canonicalUrl)) {
                Write-Host "[target-open] resolved Facebook share URL to canonical target: $canonicalUrl"
                $ResolvedUrl = $canonicalUrl
                $Method = 'share-url-http-redirect'
            }
            else {
                $resolverError = ([string]$resolveResult.Stderr).Trim()
                if (-not $resolverError) { $resolverError = "exit=$($resolveResult.ExitCode)" }
                Write-Warning "[target-open] share URL redirect resolution failed; using the original navigation URL: $resolverError"
            }
        }
        else {
            Write-Warning "[target-open] share URL resolver is missing; using the original navigation URL: $shareResolver"
        }
    }
    if ($EnableFeedCardFallback) {
        Open-FeedCardFallback | Out-Null
    }
    elseif ($ForceFacebookSearch) {
        if ([string]::IsNullOrWhiteSpace($PostId)) {
            Write-Diagnostic $false 'search-fallback-post-id-unavailable'
            throw 'Exact Facebook search fallback requires a numeric post ID.'
        }
        $Method = 'facebook-search-id'
        Open-BySearch $PostId
    }
    elseif ($ResolvedUrl) {
        $mobileTargetUrl = Convert-ToFacebookIntentUrl $ResolvedUrl
        if ($mobileTargetUrl -ne $ResolvedUrl) {
            $wasVideoUrl = $ResolvedUrl -match '(?i)(?:/reel/|/videos?/|/watch(?:/|\?))'
            Write-Host "[target-open] normalized Facebook video URL to supported mobile detail: $mobileTargetUrl"
            $ResolvedUrl = $mobileTargetUrl
            if ($Method -ne 'share-url-http-redirect') {
                $Method = if ($wasVideoUrl) { 'watch-url-canonical-reel' } else { 'story-url-canonical-post' }
            }
        }
        if (-not $Method) {
            $Method = if (Test-FacebookShareNavigationUrl $ResolvedUrl) { 'share-url-navigation' } else { 'url-deeplink' }
        }
        Open-Url $ResolvedUrl
    } else {
        $Method = 'facebook-search-id'
        Open-BySearch $PostId
    }
    if (-not $script:NavigationUiReady) { Dump-Ui $UiPath }
    $script:TargetUiState = Get-TargetUiState $UiPath
    if ($script:TargetUiState -in @('web-error','empty-placeholder','ui-empty')) {
        $searchPostId = if ($PostId -match '^[0-9]{1,32}$') { $PostId } else { Get-NumericPostIdFromUrl $ResolvedUrl }
        Write-TargetOpenRecoveryRequest $script:TargetUiState $searchPostId
        throw "Target-open UI state=$script:TargetUiState; strict target verification is deferred until Facebook is relaunched."
    }
    # Group Share needs timestamp detail traffic even when the inline card is cached.
    # Save that card, own this one promotion, and return only after fresh proof.
    $preserveInlineGroup = $PreserveGroupInlineShareRoute -and $RequireFreshUi -and
        $ResolvedUrl -match '^https://(?:www\.|m\.)?facebook\.com/groups/[0-9]{1,32}/(?:posts|permalink)/[0-9]{1,32}/?(?:[?#].*)?$'
    $inlineGroupRouteCandidate = $false
    $useInlineGroupRoute = $false
    $groupTimestampPromoted = $false
    $inlineGroupBeforePath = Join-Path $Out 'inline-group-before-timestamp.xml'
    if ($preserveInlineGroup) {
        $inlineSurface = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -WorkingDirectory $Base -Arguments @(
            '-c', 'import sys; from facebook_group_inline_route import surface; surface(sys.argv[1])', $UiPath
        )
        if ($inlineSurface.ExitCode -eq 0) {
            Copy-Item -LiteralPath $UiPath -Destination $inlineGroupBeforePath -Force
            $groupTimestampPromoted = Open-InlineFeedCardDetail $UiPath
            if ($groupTimestampPromoted) {
                $inlineGroupRouteCandidate = $true
                $Method += '-timestamp-detail'
            }
            else {
                Write-Warning 'Group inline surface did not prove timestamp promotion; continuing with common single-post detail route.'
                $Method += '-group-post-detail-common-route'
            }
        }
        else {
            # A group permalink can resolve directly to the same single-post
            # detail surface used by ordinary posts. Do not reject that valid
            # surface merely because the album-style inline route is absent.
            Write-Warning 'Group inline surface is not applicable; continuing with common single-post detail route.'
            $Method += '-group-post-detail-common-route'
        }
    }
    elseif (Open-InlineFeedCardDetail $UiPath) {
        $Method += '-timestamp-detail'
    }

    $captureWaitStarted = Get-Date
    $deadline = $captureWaitStarted.AddSeconds($VerificationTimeoutSeconds)
    while (-not (Test-Path $CaptureJsonl) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
    }
    $script:VerificationElapsedMs = [int][Math]::Round(((Get-Date) - $captureWaitStarted).TotalMilliseconds)
    if (-not (Test-Path $CaptureJsonl)) {
        Write-Diagnostic $false 'capture-jsonl-not-created'
        exit 4
    }

    $verifyDir = Join-Path $Out 'verification-analysis'
    $args = @(
        (Join-Path $Base 'analyze_facebook_capture.py'),
        '--input', $CaptureJsonl,
        '--out-dir', $verifyDir,
        '--min-line', [string]$VerificationMinLine,
        '--require-target-match'
    )
    if ($PostId) { $args += @('--expected-post-id', $PostId) }
    if ($ResolvedUrl) { $args += @('--expected-url', $ResolvedUrl) }
    $verifyStarted = Get-Date
    $verifyDeadline = $verifyStarted.AddSeconds($VerificationTimeoutSeconds)
    $verifyExit = 3
    while ($true) {
        $script:VerificationAttempts++
        $verifyProcess = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -Arguments $args -WorkingDirectory $Base
        if (-not [string]::IsNullOrWhiteSpace([string]$verifyProcess.Stdout)) { Write-Host ([string]$verifyProcess.Stdout).TrimEnd() }
        if (-not [string]::IsNullOrWhiteSpace([string]$verifyProcess.Stderr)) { Write-Warning ([string]$verifyProcess.Stderr).TrimEnd() }
        $verifyExit = [int]$verifyProcess.ExitCode
        $script:VerificationElapsedMs = [int][Math]::Round(((Get-Date) - $verifyStarted).TotalMilliseconds)
        if ($verifyExit -eq 0) { break }
        # Exit 3 is the analyzer's strict "target not matched yet" result.  Keep
        # waiting only inside this bounded window; all other failures are final.
        if ($verifyExit -ne 3) {
            Write-Diagnostic $false 'target-verification-analyzer-failed' $verifyExit
            exit $verifyExit
        }
        if ((Get-Date) -ge $verifyDeadline) { break }
        Start-Sleep -Seconds 2
    }
    if ($verifyExit -ne 0) {
        # Reel navigation often renders the exact detail before autoplay issues a
        # target-bearing network request. Permit only one bounded Reaction-source
        # discovery from the strict Reel detail rail; the explorer must observe one
        # unique fresh source_post_id before it may paginate or report success.
        if ((Test-ReelTargetUrl $ResolvedUrl)) {
            Dump-Ui $UiPath
            if (Test-ReelDetailSurface $UiPath) {
                $script:TargetUiState = Get-TargetUiState $UiPath
                Write-Diagnostic $true 'reel-ui-source-discovery-required' 0
                Write-Output "Reel detail confirmed; fresh source_post_id discovery is required before pagination: url=$ResolvedUrl"
                exit 0
            }
        }
        Write-Diagnostic $false 'target-not-confirmed-in-capture' $verifyExit
        exit $verifyExit
    }
    $verificationReportPath = Join-Path $verifyDir 'facebook-sample-patterns.json'
    $verificationReport = $null
    if (Test-Path $verificationReportPath) {
        try {
            $verificationReport = Get-Content -Raw -Encoding UTF8 $verificationReportPath | ConvertFrom-Json
            $targetVerification = $verificationReport.target_verification
            if ($targetVerification -and [string]$targetVerification.status -eq 'matched') {
                $matchedPostProperty = $targetVerification.PSObject.Properties['matched_post_id']
                if ($matchedPostProperty) { $script:ResolvedPostId = [string]$matchedPostProperty.Value }
                $expectedAuthorProperty = $targetVerification.PSObject.Properties['expected_url_author_id']
                if ($expectedAuthorProperty) { $script:ResolvedAuthorId = [string]$expectedAuthorProperty.Value }
                $resolvedAuthorProperty = $targetVerification.PSObject.Properties['resolved_author_id']
                if (-not $script:ResolvedAuthorId -and $resolvedAuthorProperty) { $script:ResolvedAuthorId = [string]$resolvedAuthorProperty.Value }
                $legacyStoryProperty = $targetVerification.PSObject.Properties['matched_legacy_api_story_id']
                if ($legacyStoryProperty) { $script:ResolvedLegacyStoryId = [string]$legacyStoryProperty.Value }
            }
        } catch {
            Write-Warning "[target-open] matched verification metadata could not be read: $($_.Exception.Message)"
        }
    }
    if (-not $useInlineGroupRoute) {
        # For ordinary/share/p/pfbid/group targets, the fresh capture must resolve
        # to one numeric post identity. Do not require author and body to remain in
        # the viewport: long posts and comments legitimately move them off-screen.
        # The explorer still rejects the run unless its first fresh Reaction/Share
        # query has this exact source_post_id.
        $networkMatchedReel = Test-ReelTargetUrl $ResolvedUrl
        # Some token-matched numeric/group URLs do not populate matched_post_id
        # even though the same fresh report contains exactly one matching Story.
        # Promote only that exact URL token and its unique numeric author row.
        if ($verificationReport -and $script:ResolvedPostId -notmatch '^[0-9]{1,32}$') {
            $urlIdentity = [regex]::Match(
                [string]$ResolvedUrl,
                '(?i)^https://(?:www\.|m\.)?facebook\.com/(?:groups/[0-9]{1,32}/(?:posts|permalink)/|[0-9]{1,32}/posts/)([0-9]{1,32})/?(?:[?#].*)?$'
            )
            $tokenProperty = if ($targetVerification) { $targetVerification.PSObject.Properties['matched_token'] } else { $null }
            $matchedLineProperty = if ($targetVerification) { $targetVerification.PSObject.Properties['matched_line'] } else { $null }
            if (
                $urlIdentity.Success -and $targetVerification -and
                [string]$targetVerification.status -eq 'matched' -and
                $tokenProperty -and [string]$tokenProperty.Value -eq $urlIdentity.Groups[1].Value -and
                $matchedLineProperty -and [int]$matchedLineProperty.Value -ge [int]$VerificationMinLine
            ) {
                $urlRows = @($verificationReport.posts | Where-Object {
                    [string]$_.post_id -eq $urlIdentity.Groups[1].Value -and [string]$_.author_id -match '^[0-9]{1,32}$'
                })
                $urlAuthors = @($urlRows | ForEach-Object { [string]$_.author_id } | Sort-Object -Unique)
                if ($urlRows.Count -ge 1 -and $urlAuthors.Count -eq 1) {
                    $script:ResolvedPostId = $urlIdentity.Groups[1].Value
                    $script:ResolvedAuthorId = $urlAuthors[0]
                }
            }
        }
        if (-not $verificationReport -or $script:ResolvedPostId -notmatch '^[0-9]{1,32}$') {
            Write-Diagnostic $false 'target-network-match-has-no-numeric-post-id' 3
            exit 3
        }
        $identityRows = @($verificationReport.posts | Where-Object { [string]$_.post_id -eq [string]$script:ResolvedPostId })
        $identityAuthors = @($identityRows | ForEach-Object { [string]$_.author_id } | Where-Object { $_ -match '^[0-9]{1,32}$' } | Sort-Object -Unique)
        if ($identityRows.Count -eq 0 -or $identityAuthors.Count -ne 1) {
            Write-Diagnostic $false 'target-network-match-is-not-a-unique-post-identity' 3
            exit 3
        }
        if ($script:ResolvedAuthorId -and [string]$script:ResolvedAuthorId -ne [string]$identityAuthors[0]) {
            Write-Diagnostic $false 'target-network-author-conflicts-with-post-row' 3
            exit 3
        }
        $script:ResolvedAuthorId = [string]$identityAuthors[0]
        if ($PostId -match '^[0-9]{1,32}$' -and [string]$PostId -ne [string]$script:ResolvedPostId) {
            Write-Diagnostic $false 'resolved-post-id-does-not-match-requested-post-id' 3
            exit 3
        }

        # A pfbid alias may be network-matched while the app leaves an unrelated
        # photo card visible. Reopen the already verified numeric identity once;
        # no identity is inferred from this UI and the later source guard remains
        # authoritative. Preserve the original verification boundary/report.
        if ($ResolvedUrl -match '(?i)^https://(?:www\.|m\.)?facebook\.com/[^/]+/posts/pfbid[A-Za-z0-9]+/?(?:[?#].*)?$') {
            $canonicalTargetUrl = "https://www.facebook.com/$($script:ResolvedAuthorId)/posts/$($script:ResolvedPostId)"
            $verifiedBoundary = $script:VerificationMinLine
            Write-Host "[target-open] reopening verified pfbid identity as numeric target: $canonicalTargetUrl"
            Open-Url $canonicalTargetUrl
            $script:VerificationMinLine = $verifiedBoundary
            $ResolvedUrl = $canonicalTargetUrl
            $Method += '-verified-numeric-reopen'
        }

        Dump-Ui $UiPath
        $script:TargetUiState = Get-TargetUiState $UiPath
        if ($networkMatchedReel) {
            if (-not (Test-ReelDetailSurface $UiPath)) {
                Write-Diagnostic $false 'reel-detail-surface-invalid-after-network-match' 3
                exit 3
            }
        }
        elseif ($script:TargetUiState -ne 'facebook-content') {
            Write-Diagnostic $false ("target-ui-invalid-after-network-match: {0}" -f $script:TargetUiState) 3
            exit 3
        }
    }
    if ($inlineGroupRouteCandidate) {
        $inlineProofArgs = @(
            (Join-Path $Base 'facebook_group_inline_route.py'), 'bind', '--xml', $inlineGroupBeforePath,
            '--report', $verificationReportPath, '--url', $ResolvedUrl,
            '--min-line', [string]$VerificationMinLine
        )
        if (-not [string]::IsNullOrWhiteSpace($PostId)) { $inlineProofArgs += @('--post-id', $PostId) }
        $inlineProof = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -WorkingDirectory $Base -Arguments $inlineProofArgs
        if ($inlineProof.ExitCode -eq 0) {
            $inlineIdentity = $inlineProof.Stdout | ConvertFrom-Json
            Restore-VerifiedInlineGroupCard -BeforePath $inlineGroupBeforePath -ReportPath $verificationReportPath -ExpectedPostId ([string]$inlineIdentity.post_id) -PromotionOwned:$groupTimestampPromoted
            $script:ResolvedPostId = [string]$inlineIdentity.post_id
            $script:ResolvedAuthorId = [string]$inlineIdentity.author_id
            $useInlineGroupRoute = $true
            $script:InlineGroupShareRoute = $true
            $Method += '-inline-group-share'
        }
        else {
            Write-Warning 'Group inline Share proof was not unique; continuing with common single-post detail route.'
            $Method += '-group-post-detail-common-route'
        }
    }
    Write-Diagnostic $true 'target-confirmed-in-capture' $verifyExit
    Write-Output "Target confirmed: requested_post_id=$PostId resolved_post_id=$script:ResolvedPostId method=$Method url=$ResolvedUrl"
}
catch {
    Write-Diagnostic $false $_.Exception.Message
    throw
}
