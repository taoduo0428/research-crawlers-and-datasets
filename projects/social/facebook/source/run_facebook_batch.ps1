[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [int]$ProxyPort = 8081,
    [int[]]$FeedScrollOffsets = @(2, 5, 8, 11, 14),
    [int]$DurationSeconds = 0,
    [int]$ScrollDelaySeconds = 1,
    [int]$ShareScrollCount = 24,
    [int]$ReactionScrollCount = 24,
    [int]$ReactionTabLimit = 8,
    [int]$EntryScanScrollCount = 4,
    [int]$PaginationIdleLimit = 4,
    [int]$PaginationOpenIdleLimit = 8,
    [int]$ReactionStrategyIdleLimit = 2,
    [int]$ReactionOpenStrategyIdleLimit = 3,
    [int]$ReactionExtensionChunk = 6,
    [int]$ReactionAbsoluteCap = 0,
    [int]$ReactionScreenScrollCount = 2,
    [int]$ReactionListReadyAttempts = 5,
    [int]$ReactionListReadyDelaySeconds = 2,
    [int]$ReactionProgressPollIntervalMs = 500,
    [int]$ReactionProgressPollTimeoutMs = 2200,
    [int]$ReactionLiveProgressPollIntervalMs = 150,
    [int]$ReactionLiveProgressPollTimeoutMs = 1200,
    [int]$ReactionSteadyNoGrowthPollTimeoutMs = 500,
    [int]$ReactionLongSwipeStartY = 1700,
    [int]$ReactionLongSwipeEndY = 250,
    [int]$ReactionLongSwipeDurationMs = 300,
    [int]$ReactionPipelineBurstSize = 2,
    [int]$ReactionPipelineSwipeGapMs = 150,
    [int]$ReactionNativeScrollEnabled = 1,
    [int]$ReactionNativeScrollTimeoutMs = 2500,
    [int]$ReactionUiDumpEveryN = 64,
    [int]$ReactionMinCycleMs = 700,
    [int]$ReactionInitialPaginationCooldownSeconds = 4,
    [int]$ReactionRateLimitBackoffSeconds = 15,
    [int]$ReactionRateLimitMaxConsecutive = 3,
    [int]$ReactionRateLimitMaxBackoffSeconds = 300,
    [int]$ReactionNoGrowthUiDumpEveryN = 1,
    [switch]$DisableAutoScreenThenDeep,
    [int]$UiDumpTimeoutSeconds = 15,
    [int]$UiDumpRetryCount = 1,
    [int]$ReadOnlyWaitSeconds = 4,
    [int]$FacebookReadyTimeoutSeconds = 20,
    [int]$MaxFlows = 0,
    [int]$CandidateRetryCount = 1,
    [switch]$DisableNativePermalinkFallback,
    [bool]$RestartFacebookAfterBatch = $true,
    [switch]$OpenShareList,
    [switch]$DisableShareList,
    [bool]$AdaptivePressureEnabled = $true,
    [int]$BaseInterCandidateCooldownSeconds = 20,
    [int]$MinInterCandidateCooldownSeconds = 10,
    [int]$MaxInterCandidateCooldownSeconds = 120
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$WindowsSystem32 = Join-Path $env:SystemRoot 'System32'
if (Test-Path $WindowsSystem32) { $env:PATH = $WindowsSystem32 + ';' + $env:PATH }
$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'
if (-not (Test-Path $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
. $RuntimeHelper
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path $PowerShellExe)) {
    $powerShellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if (-not $powerShellCommand) { throw 'Windows PowerShell executable could not be resolved.' }
    $PowerShellExe = [string]$powerShellCommand.Source
}
$PythonExe = Join-Path $env:SystemRoot 'py.exe'
if (-not (Test-Path $PythonExe)) {
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue }
    if (-not $pythonCommand) { throw 'Python executable could not be resolved.' }
    $PythonExe = [string]$pythonCommand.Source
}
$TransportHelper = Join-Path $Base "facebook_transport.ps1"
if (-not (Test-Path $TransportHelper)) { throw "Missing transport helper: $TransportHelper" }
. $TransportHelper
$BatchId = Get-Date -Format "yyyyMMdd-HHmmss"
$BatchDir = Join-Path $Base (Join-Path "batches" $BatchId)
$RunsRoot = Join-Path $Base "runs"
$SummaryScript = Join-Path $Base "facebook_batch_summary.py"
$PressureScript = Join-Path $Base "facebook_batch_pressure.py"
$CaptureScript = Join-Path $Base "run_facebook_capture.ps1"
$PressureStatePath = Join-Path $BatchDir 'pressure-state.json'
if ($AdaptivePressureEnabled -and -not (Test-Path $PressureScript)) { throw "Missing pressure controller: $PressureScript" }
New-Item -ItemType Directory -Force -Path $BatchDir | Out-Null
$runDirs = @()
$script:BatchLiveCaptureLock = Acquire-FacebookLiveCaptureLock -BasePath $Base -Owner 'batch' -OwnerId $BatchId
if (-not [bool]$script:BatchLiveCaptureLock.acquired) {
    Write-Warning "[BATCH] another Facebook live capture workflow is active; no live batch work started. lock=$($script:BatchLiveCaptureLock.path)"
    exit 61
}
$batchWatchdogStoppedCount = Stop-FacebookTransportWatch -Serial $Serial -BasePath $Base
$env:FACEBOOK_BATCH_OWNS_WATCHDOG = '1'
if ($batchWatchdogStoppedCount -gt 0) {
    Write-Host "[BATCH] paused watchdog for exclusive batch ADB ownership (stopped=$batchWatchdogStoppedCount)"
}
function Get-CaptureTransportHealth {
    $snapshot = Get-FacebookTransportSnapshot -Serial $Serial -RequiredReversePorts @(7897, $ProxyPort) -CommandTimeoutSeconds 4
    $listenerPids = @()
    foreach ($line in (Get-FacebookNetstatLines | Select-String 'LISTENING' | Select-String (":" + $ProxyPort))) {
        $match = [regex]::Match([string]$line, '\s([0-9]+)\s*$')
        if ($match.Success) { $listenerPids += [int]$match.Groups[1].Value }
    }
    $listenerPids = @($listenerPids | Select-Object -Unique)
    $reverse7897 = [bool]$snapshot.reverse['7897']
    $reverseCapture = [bool]$snapshot.reverse[[string]$ProxyPort]
    $networkReady = ([string]$snapshot.active_default_network -ne 'none')
    $healthy = (
        ([string]$snapshot.proxy -eq '127.0.0.1:7897') -and
        $networkReady -and
        [bool]$snapshot.host_7897_listening -and
        [bool]$snapshot.device_7897_reachable -and
        $reverse7897 -and
        $reverseCapture -and
        $listenerPids.Count -eq 0
    )
    return [pscustomobject]@{
        proxy = [string]$snapshot.proxy
        active_default_network = [string]$snapshot.active_default_network
        host_7897_listening = [bool]$snapshot.host_7897_listening
        device_7897_reachable = [bool]$snapshot.device_7897_reachable
        reverse_7897 = $reverse7897
        reverse_capture = $reverseCapture
        capture_port = $ProxyPort
        listener_pids = $listenerPids
        healthy = $healthy
    }
}

function Repair-CaptureTransport {
    param($BeforeHealth = $null)
    $before = if ($BeforeHealth) { $BeforeHealth } else { Get-CaptureTransportHealth }
    $repaired = $false
    foreach ($listenerPid in $before.listener_pids) {
        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=$listenerPid") -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -match '(?i)mitmdump' -and $proc.CommandLine -match 'facebook_capture_addon_safe\.py') {
            Write-Warning "Stopping stale project capture listener PID $listenerPid"
            Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
            $repaired = $true
            $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=$($proc.ParentProcessId)") -ErrorAction SilentlyContinue
            if ($parent -and $parent.CommandLine -match '(?i)mitmdump' -and $parent.CommandLine -match 'facebook_capture_addon_safe\.py') {
                Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
    }
    try {
        $null = Ensure-FacebookAndroidNetwork -Serial $Serial
        $null = Ensure-FacebookReverseMappings -Serial $Serial -Ports @(7897, $ProxyPort)
        $null = Set-FacebookAndroidProxy -Serial $Serial -Port 7897
        $repaired = $true
    }
    catch {
        Write-Warning "[transport] repair failed: $($_.Exception.Message)"
    }
    if ($repaired) { Start-Sleep -Seconds 2 }
    $after = Get-CaptureTransportHealth
    return [pscustomobject]@{
        repaired = $repaired
        before = $before
        after = $after
    }
}

function Ensure-CaptureTransportReady {
    $health = Get-CaptureTransportHealth
    if ($health.healthy) { return $true }
    $repair = Repair-CaptureTransport -BeforeHealth $health
    if ($repair.after.healthy) { return $true }
    for ($i = 0; $i -lt 6; $i++) {
        $health = Get-CaptureTransportHealth
        if ($health.healthy) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Rebuild-BatchSummary {
    if ($runDirs.Count -eq 0) { return }
    $args = @($SummaryScript, '--runs') + $runDirs + @(
        '--out-json', (Join-Path $BatchDir 'batch-summary.json'),
        '--out-csv', (Join-Path $BatchDir 'batch-summary.csv')
    )
    & $PythonExe @args
    if ($LASTEXITCODE -ne 0) { throw "batch summary rebuild failed" }
}

try {
    if (-not (Ensure-FacebookFrameworkServicesReady -Serial $Serial -TimeoutSeconds 45)) {
        throw "Android framework services are not ready before batch start: $Serial"
    }
    if (-not (Ensure-FacebookUiDumpHelper -Serial $Serial -BasePath $Base)) {
        Write-Warning '[BATCH] instrumentation UI dump fallback is unavailable; captures will use legacy UIAutomator only.'
    }
for ($index = 0; $index -lt $FeedScrollOffsets.Count; $index++) {
    $offset = [int]$FeedScrollOffsets[$index]
    $candidateNumber = $index + 1
    $started = Get-Date
    Write-Host "[BATCH $BatchId] candidate $candidateNumber/$($FeedScrollOffsets.Count), feed scroll offset=$offset"

    if (-not (Ensure-CaptureTransportReady)) {
        $placeholder = Join-Path $BatchDir ("failed-run-{0:00}" -f $candidateNumber)
        New-Item -ItemType Directory -Force -Path $placeholder | Out-Null
        [ordered]@{
            batch_id = $BatchId
            candidate_index = $candidateNumber
            feed_scroll_offset = $offset
            started_at = $started.ToString('o')
            finished_at = (Get-Date).ToString('o')
            error = 'capture-proxy-still-busy'
        } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $placeholder 'batch-run-metadata.json')
        $runDirs += $placeholder
        Rebuild-BatchSummary
        continue
    }

    $logPath = Join-Path $BatchDir ("candidate-{0:00}.log" -f $candidateNumber)
    $captureArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $CaptureScript,
        '-Serial', $Serial,
        '-ProxyPort', [string]$ProxyPort,
        '-DurationSeconds', [string]$DurationSeconds,
        '-ScrollCount', [string]$offset,
        '-ScrollDelaySeconds', [string]$ScrollDelaySeconds,
        '-LaunchFacebook',
        '-OpenReactionList',
        '-ShareScrollCount', [string]$ShareScrollCount,
        '-ReactionScrollCount', [string]$ReactionScrollCount,
        '-ReactionTabLimit', [string]$ReactionTabLimit,
        '-EntryScanScrollCount', [string]$EntryScanScrollCount,
        '-PaginationIdleLimit', [string]$PaginationIdleLimit,
        '-PaginationOpenIdleLimit', [string]$PaginationOpenIdleLimit,
        '-ReactionStrategyIdleLimit', [string]$ReactionStrategyIdleLimit,
        '-ReactionOpenStrategyIdleLimit', [string]$ReactionOpenStrategyIdleLimit,
        '-ReactionExtensionChunk', [string]$ReactionExtensionChunk,
        '-ReactionAbsoluteCap', [string]$ReactionAbsoluteCap,
        '-ReactionScreenScrollCount', [string]$ReactionScreenScrollCount,
        '-ReactionListReadyAttempts', [string]$ReactionListReadyAttempts,
        '-ReactionListReadyDelaySeconds', [string]$ReactionListReadyDelaySeconds,
        '-ReactionProgressPollIntervalMs', [string]$ReactionProgressPollIntervalMs,
        '-ReactionProgressPollTimeoutMs', [string]$ReactionProgressPollTimeoutMs,
        '-ReactionLiveProgressPollIntervalMs', [string]$ReactionLiveProgressPollIntervalMs,
        '-ReactionLiveProgressPollTimeoutMs', [string]$ReactionLiveProgressPollTimeoutMs,
        '-ReactionSteadyNoGrowthPollTimeoutMs', [string]$ReactionSteadyNoGrowthPollTimeoutMs,
        '-ReactionLongSwipeStartY', [string]$ReactionLongSwipeStartY,
        '-ReactionLongSwipeEndY', [string]$ReactionLongSwipeEndY,
        '-ReactionLongSwipeDurationMs', [string]$ReactionLongSwipeDurationMs,
        '-ReactionPipelineBurstSize', [string]$ReactionPipelineBurstSize,
        '-ReactionPipelineSwipeGapMs', [string]$ReactionPipelineSwipeGapMs,
        '-ReactionNativeScrollEnabled', [string]$ReactionNativeScrollEnabled,
        '-ReactionNativeScrollTimeoutMs', [string]$ReactionNativeScrollTimeoutMs,
        '-ReactionUiDumpEveryN', [string]$ReactionUiDumpEveryN,
        '-ReactionMinCycleMs', [string]$ReactionMinCycleMs,
        '-ReactionInitialPaginationCooldownSeconds', [string]$ReactionInitialPaginationCooldownSeconds,
        '-ReactionRateLimitBackoffSeconds', [string]$ReactionRateLimitBackoffSeconds,
        '-ReactionRateLimitMaxConsecutive', [string]$ReactionRateLimitMaxConsecutive,
        '-ReactionRateLimitMaxBackoffSeconds', [string]$ReactionRateLimitMaxBackoffSeconds,
        '-ReactionNoGrowthUiDumpEveryN', [string]$ReactionNoGrowthUiDumpEveryN,
        '-UiDumpTimeoutSeconds', [string]$UiDumpTimeoutSeconds,
        '-UiDumpRetryCount', [string]$UiDumpRetryCount,
        '-ReadOnlyWaitSeconds', [string]$ReadOnlyWaitSeconds,
        '-FacebookReadyTimeoutSeconds', [string]$FacebookReadyTimeoutSeconds,
        '-MaxFlows', [string]$MaxFlows
    )
    if (-not $DisableAutoScreenThenDeep) { $captureArgs += '-AutoScreenThenDeep' }
    if ($OpenShareList -or -not $DisableShareList) { $captureArgs += '-OpenShareList' }
    if ($DisableNativePermalinkFallback) { $captureArgs += '-DisableNativePermalinkFallback' }

    $exitCode = 1
    $captureAttempts = 0
    $transport = $null
    $attemptLogPath = $null
    $maxCaptureAttempts = 1 + [Math]::Max(0, $CandidateRetryCount)
    for ($captureAttempt = 1; $captureAttempt -le $maxCaptureAttempts; $captureAttempt++) {
        $captureAttempts = $captureAttempt
        $attemptLogPath = Join-Path $BatchDir ("candidate-{0:00}-attempt-{1:00}.log" -f $candidateNumber, $captureAttempt)
        Write-Host "[BATCH $BatchId] candidate $candidateNumber capture attempt $captureAttempt/$maxCaptureAttempts"
        $captureScriptArgs = @($captureArgs | Select-Object -Skip 5)
        $child = Invoke-FacebookPowerShellFileCaptured -PowerShellExe $PowerShellExe -ScriptPath $CaptureScript -ScriptArguments $captureScriptArgs -WorkingDirectory $Base
        $combinedLog = @()
        if (-not [string]::IsNullOrWhiteSpace([string]$child.Stdout)) { $combinedLog += ([string]$child.Stdout).TrimEnd() }
        if (-not [string]::IsNullOrWhiteSpace([string]$child.Stderr)) { $combinedLog += ([string]$child.Stderr).TrimEnd() }
        $combinedLog -join "`r`n" | Set-Content -Encoding UTF8 $attemptLogPath
        Write-FacebookChildProcessOutput -Result $child
        $exitCode = [int]$child.ExitCode
        if ($exitCode -eq 0) {
            $transport = Repair-CaptureTransport
            break
        }
        if ($captureAttempt -lt $maxCaptureAttempts) {
            Write-Warning "[BATCH $BatchId] candidate $candidateNumber attempt $captureAttempt failed with exit=$exitCode; validating restored transport and retrying the same feed offset."
            if (-not (Ensure-CaptureTransportReady)) {
                Write-Warning "[BATCH $BatchId] candidate $candidateNumber retry cancelled because normal transport could not be restored."
                $transport = Repair-CaptureTransport
                break
            }
            Start-Sleep -Seconds 1
        }
        else {
            $transport = Repair-CaptureTransport
        }
    }
    if ($attemptLogPath -and (Test-Path $attemptLogPath)) {
        Copy-Item -Force $attemptLogPath $logPath
    }
    $finished = Get-Date
    if (-not $transport) { $transport = Repair-CaptureTransport }
    $runDir = $null
    $match = Select-String -Path $logPath -Pattern '^Capture finished:\s+(.+)$' | Select-Object -Last 1
    if ($match) {
        $runDir = $match.Matches[0].Groups[1].Value.Trim()
    }
    if (-not $runDir -or -not (Test-Path $runDir)) {
        $runDir = Get-ChildItem $RunsRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -ge $started.AddSeconds(-2) } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $runDir) {
        $runDir = Join-Path $BatchDir ("failed-run-{0:00}" -f $candidateNumber)
        New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    }

    [ordered]@{
        batch_id = $BatchId
        candidate_index = $candidateNumber
        feed_scroll_offset = $offset
        started_at = $started.ToString('o')
        finished_at = $finished.ToString('o')
        error = if ($exitCode -eq 0) { $null } else { "capture-exit-$exitCode" }
        capture_attempts = [int]$captureAttempts
        capture_retried = [bool]($captureAttempts -gt 1)
        transport_repaired = [bool]$transport.repaired
        transport_health = [ordered]@{
            proxy = [string]$transport.after.proxy
            active_default_network = [string]$transport.after.active_default_network
            host_7897_listening = [bool]$transport.after.host_7897_listening
            device_7897_reachable = [bool]$transport.after.device_7897_reachable
            reverse_7897 = [bool]$transport.after.reverse_7897
            reverse_capture = [bool]$transport.after.reverse_capture
            capture_port = [int]$transport.after.capture_port
            listener_pids = @($transport.after.listener_pids)
            healthy = [bool]$transport.after.healthy
        }
    } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $runDir 'batch-run-metadata.json')

    $runDirs += $runDir
    Rebuild-BatchSummary
    Write-Host "[BATCH $BatchId] candidate $candidateNumber finished; run=$runDir exit=$exitCode"

    if ($AdaptivePressureEnabled) {
        $pressureRaw = & $PythonExe $PressureScript `
            --run-dir $runDir `
            --state $PressureStatePath `
            --out $PressureStatePath `
            --base-cooldown-seconds $BaseInterCandidateCooldownSeconds `
            --min-cooldown-seconds $MinInterCandidateCooldownSeconds `
            --max-cooldown-seconds $MaxInterCandidateCooldownSeconds
        $pressureExit = $LASTEXITCODE
        try { $pressure = [string]$pressureRaw | ConvertFrom-Json }
        catch { throw "pressure controller returned invalid JSON: $pressureRaw" }

        Write-Host "[BATCH $BatchId] pressure=$($pressure.health) decision=$($pressure.decision) cooldown=$($pressure.next_cooldown_seconds)s"
        if ([string]$pressure.decision -eq 'stop-batch') {
            $reasons = @($pressure.block_reasons) -join ','
            Write-Warning "[BATCH $BatchId] platform restriction detected; remaining live candidates paused. reasons=$reasons"
            break
        }
        if ($pressureExit -ne 0) {
            throw "pressure controller failed with exit=$pressureExit"
        }
        if ($candidateNumber -lt $FeedScrollOffsets.Count -and [int]$pressure.next_cooldown_seconds -gt 0) {
            Start-Sleep -Seconds ([int]$pressure.next_cooldown_seconds)
        }
    }
}

Write-Host "Batch complete: $BatchDir"
Write-Host "Summary JSON: $(Join-Path $BatchDir 'batch-summary.json')"
Write-Host "Summary CSV:  $(Join-Path $BatchDir 'batch-summary.csv')"

# Restoring the proxy is not always enough for Facebook's current Activity to
# discard sockets created while the temporary capture proxy was active.  A
# final process restart makes the app establish a fresh normal-proxy session.
# This does not clear app data, cache, login state, or Android settings.
if ($RestartFacebookAfterBatch) {
    if (Ensure-CaptureTransportReady) {
        Write-Host '[BATCH] validating Facebook UI after transport restore'
        $null = Confirm-FacebookUiOnline -Serial $Serial -CapturePort $ProxyPort -Attempts 2 -WaitSeconds 6
    }
    else {
        Write-Warning 'Skipping final Facebook restart because normal transport could not be confirmed.'
    }
}
}
finally {
    Remove-Item Env:FACEBOOK_BATCH_OWNS_WATCHDOG -ErrorAction SilentlyContinue
    try {
    $watchPid = Start-FacebookTransportWatch -Serial $Serial -BasePath $Base -CapturePort $ProxyPort
        if ($watchPid) { Write-Host "[BATCH] watchdog resumed pid=$watchPid" }
    }
    catch {
        Write-Warning "[BATCH] watchdog resume failed: $($_.Exception.Message)"
    }
    Release-FacebookLiveCaptureLock -Lock $script:BatchLiveCaptureLock
}
