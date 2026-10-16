[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [string]$PostUrl = "https://www.facebook.com/61563119350848/posts/122106880826437311",
    [string]$PostId = "122106880826437311",
    [int]$MinFullReactionUsers = 3000,
    [int]$StageCooldownSeconds = 30,
    [int]$ResumeMaxAgeHours = 24,
    [double]$NativeRecommendationMinSpeedupPercent = 5.0,
    [bool]$RunNativeAB = $true,
    [bool]$RunShareRegression = $true
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path $PowerShellExe)) {
    $powerShellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if (-not $powerShellCommand) { throw 'Windows PowerShell executable could not be resolved.' }
    $PowerShellExe = [string]$powerShellCommand.Source
}
$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'
if (-not (Test-Path $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
. $RuntimeHelper
$PythonExe = Resolve-FacebookPythonExe -BasePath $Base
$RunsRoot = Join-Path $Base 'runs'
$GateId = Get-Date -Format 'yyyyMMdd-HHmmss'
$StatePath = Join-Path $Base 'facebook-release-gate-state.json'
$CheckpointPath = Join-Path $Base 'facebook-release-gate-checkpoints.json'
$LockPath = Join-Path $Base 'facebook-release-gate.lock'
$CaptureScript = Join-Path $Base 'run_facebook_capture.ps1'
$Inspector = Join-Path $Base 'facebook_release_gate.py'
$Predeploy = Join-Path $Base 'validate_facebook_predeploy.py'
$FinalReportScript = Join-Path $Base 'facebook_release_report.py'
$FinalReportJson = Join-Path $Base 'facebook-release-final-report.json'
$FinalReportMd = Join-Path $Base 'facebook-release-final-report.md'

if (-not (Test-Path $CaptureScript)) { throw "Missing capture script: $CaptureScript" }
if (-not (Test-Path $Inspector)) { throw "Missing release gate inspector: $Inspector" }
if (-not (Test-Path $Predeploy)) { throw "Missing predeploy validator: $Predeploy" }
if (-not (Test-Path $FinalReportScript)) { throw "Missing final report generator: $FinalReportScript" }
if ([string]::IsNullOrWhiteSpace($PostId)) { throw 'PostId is required for strict machine validation.' }
if ([string]::IsNullOrWhiteSpace($PostUrl)) { throw 'PostUrl is required for strict target opening.' }

$script:GateLockHandle = $null
$script:LiveCaptureLock = $null
function Release-GateLock {
    if ($script:LiveCaptureLock) { Release-FacebookLiveCaptureLock -Lock $script:LiveCaptureLock; $script:LiveCaptureLock = $null }
    if ($script:GateLockHandle) {
        try { $script:GateLockHandle.Dispose() } catch {}
        $script:GateLockHandle = $null
    }
    if (Test-Path $LockPath) {
        try { Remove-Item -Force $LockPath } catch {}
    }
}

if (Test-Path $LockPath) {
    try {
        $staleHandle = [IO.File]::Open($LockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $staleHandle.Dispose()
        Remove-Item -Force $LockPath
    }
    catch {
        Write-Warning "[release-gate] another release gate is already active; no live work started. lock=$LockPath"
        exit 60
    }
}
try {
    $script:GateLockHandle = [IO.File]::Open($LockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $lockBytes = [Text.Encoding]::UTF8.GetBytes(("gate_id={0}`npid={1}`nstarted_at={2}`n" -f $GateId, $PID, (Get-Date).ToString('o')))
    $script:GateLockHandle.Write($lockBytes, 0, $lockBytes.Length)
    $script:GateLockHandle.Flush()
}
catch {
    Write-Warning "[release-gate] could not acquire exclusive gate lock; no live work started: $($_.Exception.Message)"
    exit 60
}

$script:LiveCaptureLock = Acquire-FacebookLiveCaptureLock -BasePath $Base -Owner 'release-gate' -OwnerId $GateId
if (-not [bool]$script:LiveCaptureLock.acquired) {
    $busyPath = [string]$script:LiveCaptureLock.path
    Release-GateLock
    Write-Warning "[release-gate] another Facebook live capture workflow is active; no live work started. lock=$busyPath"
    exit 61
}

$priorState = $null
if (Test-Path $StatePath) {
    try {
        $candidatePriorState = Get-Content -Raw -Encoding UTF8 $StatePath | ConvertFrom-Json
        if ([string]$candidatePriorState.post_id -eq [string]$PostId -and [string]$candidatePriorState.post_url -eq [string]$PostUrl) {
            $priorState = $candidatePriorState
        }
    }
    catch {
        Write-Warning "[release-gate] prior state could not be parsed; starting clean: $($_.Exception.Message)"
    }
}

$checkpointState = [ordered]@{
    post_id = $PostId
    post_url = $PostUrl
    updated_at = (Get-Date).ToString('o')
    stages = [ordered]@{}
}
if (Test-Path $CheckpointPath) {
    try {
        $candidateCheckpoint = Get-Content -Raw -Encoding UTF8 $CheckpointPath | ConvertFrom-Json
        if ([string]$candidateCheckpoint.post_id -eq [string]$PostId -and [string]$candidateCheckpoint.post_url -eq [string]$PostUrl -and $candidateCheckpoint.PSObject.Properties['stages']) {
            foreach ($property in $candidateCheckpoint.stages.PSObject.Properties) {
                $checkpointState.stages[$property.Name] = $property.Value
            }
        }
    }
    catch {
        Write-Warning "[release-gate] checkpoint file could not be parsed; keeping current run state only: $($_.Exception.Message)"
    }
}

$state = [ordered]@{
    gate_id = $GateId
    started_at = (Get-Date).ToString('o')
    updated_at = (Get-Date).ToString('o')
    status = 'running'
    current_stage = 'init'
    next_stage = 'probe'
    post_id = $PostId
    post_url = $PostUrl
    min_full_reaction_users = $MinFullReactionUsers
    stage_cooldown_seconds = $StageCooldownSeconds
    resume_max_age_hours = $ResumeMaxAgeHours
    prior_gate_id = if ($priorState) { [string]$priorState.gate_id } else { '' }
    checkpoint_path = $CheckpointPath
    persistent_checkpoint_count = $checkpointState.stages.Count
    native_recommendation_min_speedup_percent = $NativeRecommendationMinSpeedupPercent
    run_native_ab = $RunNativeAB
    run_share_regression = $RunShareRegression
    final_report_json = $FinalReportJson
    final_report_md = $FinalReportMd
    stages = [ordered]@{}
}

function Save-GateState {
    $state.updated_at = (Get-Date).ToString('o')
    $state.persistent_checkpoint_count = $checkpointState.stages.Count
    $temp = "$StatePath.tmp"
    $state | ConvertTo-Json -Depth 20 | Set-Content -Path $temp -Encoding UTF8
    Move-Item -Force $temp $StatePath
}

function Save-CheckpointState {
    $checkpointState.updated_at = (Get-Date).ToString('o')
    $temp = "$CheckpointPath.tmp"
    $checkpointState | ConvertTo-Json -Depth 20 | Set-Content -Path $temp -Encoding UTF8
    Move-Item -Force $temp $CheckpointPath
}

function New-StageRunDir([string]$Name) {
    return Join-Path $RunsRoot ("release-{0}-{1}" -f $GateId, $Name)
}

function Set-StageResult([string]$Name, $Result, [string]$RunDir = '') {
    $state.stages[$Name] = [ordered]@{
        run_dir = $RunDir
        result = $Result
        timestamp = (Get-Date).ToString('o')
    }
    Save-GateState
}

function Set-PersistentCheckpoint([string]$Name, $Result, [string]$RunDir, [string]$Timestamp = '', [string]$SourceGateId = '') {
    if (-not $Result -or -not [bool]$Result.passed) { return }
    if ($Name -notin @('full-adb', 'full-native', 'share')) { return }
    $checkpointState.stages[$Name] = [ordered]@{
        run_dir = $RunDir
        result = $Result
        timestamp = if ([string]::IsNullOrWhiteSpace($Timestamp)) { (Get-Date).ToString('o') } else { $Timestamp }
        gate_id = if ([string]::IsNullOrWhiteSpace($SourceGateId)) { $GateId } else { $SourceGateId }
    }
    Save-CheckpointState
    Save-GateState
}

function Get-ReusablePriorStage([string]$Name) {
    if ($ResumeMaxAgeHours -le 0) { return $null }

    $candidates = @()
    $checkpointEntry = $checkpointState.stages[$Name]
    if ($checkpointEntry) { $candidates += $checkpointEntry }
    if ($priorState -and $priorState.PSObject.Properties['stages']) {
        $stageProperty = $priorState.stages.PSObject.Properties[$Name]
        if ($stageProperty) { $candidates += $stageProperty.Value }
    }

    $bestEntry = $null
    $bestTime = $null
    $now = [DateTimeOffset]::Now
    foreach ($entry in $candidates) {
        if (-not $entry -or -not $entry.result -or -not [bool]$entry.result.passed) { continue }
        $runDir = [string]$entry.run_dir
        if ([string]::IsNullOrWhiteSpace($runDir) -or -not (Test-Path $runDir)) { continue }
        try {
            $stageTime = [DateTimeOffset]::Parse([string]$entry.timestamp)
            $ageHours = ($now - $stageTime).TotalHours
            if ($ageHours -lt 0 -or $ageHours -gt $ResumeMaxAgeHours) { continue }
            if ($null -eq $bestTime -or $stageTime -gt $bestTime) {
                $bestTime = $stageTime
                $bestEntry = $entry
            }
        }
        catch {
            continue
        }
    }
    return $bestEntry
}

function Stop-Gate([string]$Stage, [string]$Reason, [int]$ExitCode = 10) {
    $state.status = if ($Reason -match 'blocked|rate-limit|restriction') { 'blocked-live' } else { 'failed' }
    $state.current_stage = $Stage
    $state.next_stage = $Stage
    $state.stop_reason = $Reason
    $state.finished_at = (Get-Date).ToString('o')
    Save-GateState
    Write-Warning "[release-gate] stopped at stage=$Stage reason=$Reason"
    Release-GateLock
    exit $ExitCode
}

function Wait-StageCooldown([string]$BeforeStage) {
    if ($StageCooldownSeconds -le 0) { return }
    Write-Host "[release-gate] cooldown $StageCooldownSeconds second(s) before $BeforeStage."
    Start-Sleep -Seconds $StageCooldownSeconds
}

function Invoke-Inspector([string]$Stage, [string]$RunDir, [int]$MinUsers = 0) {
    $argsList = @($Inspector, $Stage, '--run-dir', $RunDir)
    if ($Stage -in @('probe', 'page2')) {
        $argsList += @('--expected-post-id', $PostId)
    }
    if ($Stage -eq 'full') {
        $argsList += @('--min-users', [string]$MinUsers)
    }
    $raw = & $PythonExe @argsList
    $code = $LASTEXITCODE
    if ([string]::IsNullOrWhiteSpace([string]$raw)) {
        return [pscustomobject]@{ passed = $false; reason = "inspector-empty-output-$code"; exit_code = $code }
    }
    try {
        $parsed = [string]$raw | ConvertFrom-Json
        $parsed | Add-Member -NotePropertyName exit_code -NotePropertyValue $code -Force
        return $parsed
    }
    catch {
        return [pscustomobject]@{ passed = $false; reason = "inspector-invalid-json-$code"; raw = [string]$raw; exit_code = $code }
    }
}

function Invoke-Capture([string]$StageName, [string[]]$ExtraArgs) {
    $runDir = New-StageRunDir $StageName
    $state.current_stage = $StageName
    $state.next_stage = $StageName
    Save-GateState
    $common = @(
        '-Serial', $Serial,
        '-LaunchFacebook',
        '-PostUrl', $PostUrl,
        '-PostId', $PostId,
        '-OutputDir', $runDir,
        '-PostActionGraceSeconds', '0'
    )
    Write-Host "[release-gate] stage=$StageName run=$runDir"
    # Keep child capture output visible while avoiding this host's executable-pipeline limitation.
    $child = Invoke-FacebookPowerShellFileCaptured -PowerShellExe $PowerShellExe -ScriptPath $CaptureScript -ScriptArguments (@($common) + @($ExtraArgs)) -WorkingDirectory $Base
    Write-FacebookChildProcessOutput -Result $child
    $captureExit = [int]$child.ExitCode
    if ($captureExit -ne 0) {
        Set-StageResult $StageName ([ordered]@{ passed = $false; reason = "capture-exit-$captureExit"; exit_code = $captureExit }) $runDir
        Stop-Gate $StageName "capture-exit-$captureExit" 20
    }
    return $runDir
}

function Invoke-ProbeStage([string]$StageName) {
    $runDir = Invoke-Capture $StageName @(
        '-OpenReactionList',
        '-ReactionProbeOnly',
        '-ScrollCount', '0',
        '-ReactionScreenScrollCount', '0',
        '-ReadOnlyWaitSeconds', '1',
        '-ReactionListReadyDelaySeconds', '1'
    )
    $result = Invoke-Inspector 'probe' $runDir
    Set-StageResult $StageName $result $runDir
    if (-not [bool]$result.passed) {
        Stop-Gate $StageName ([string]$result.reason) 10
    }
    return $result
}

function Invoke-ShareStage {
    $priorEntry = Get-ReusablePriorStage 'share'
    if ($priorEntry) {
        $priorRunDir = [string]$priorEntry.run_dir
        $rechecked = Invoke-Inspector 'share' $priorRunDir
        if ([bool]$rechecked.passed) {
            $sourceGateId = if ($priorEntry.PSObject.Properties['gate_id']) { [string]$priorEntry.gate_id } elseif ($priorState) { [string]$priorState.gate_id } else { '' }
            $sourceTimestamp = [string]$priorEntry.timestamp
            $rechecked | Add-Member -NotePropertyName reused_prior_stage -NotePropertyValue $true -Force
            $rechecked | Add-Member -NotePropertyName reused_from_gate_id -NotePropertyValue $sourceGateId -Force
            Set-StageResult 'share' $rechecked $priorRunDir
            Set-PersistentCheckpoint 'share' $rechecked $priorRunDir $sourceTimestamp $sourceGateId
            Write-Host "[release-gate] reused prior Share stage run=$priorRunDir gate=$sourceGateId"
            return $rechecked
        }
        Write-Warning '[release-gate] prior Share checkpoint failed current offline revalidation; running a fresh Share stage.'
    }

    $runDir = Invoke-Capture 'share' @(
        '-OpenShareList',
        '-ShareScrollCount', '24',
        '-ReadOnlyWaitSeconds', '2'
    )
    $result = Invoke-Inspector 'share' $runDir
    Set-StageResult 'share' $result $runDir
    if (-not [bool]$result.passed) {
        Stop-Gate 'share' ([string]$result.reason) 30
    }
    Set-PersistentCheckpoint 'share' $result $runDir
    return $result
}

function Invoke-FullReactionStage([string]$StageName, [int]$NativeEnabled) {
    $priorEntry = Get-ReusablePriorStage $StageName
    if ($priorEntry) {
        $priorRunDir = [string]$priorEntry.run_dir
        $rechecked = Invoke-Inspector 'full' $priorRunDir $MinFullReactionUsers
        if ([bool]$rechecked.passed) {
            $sourceGateId = if ($priorEntry.PSObject.Properties['gate_id']) { [string]$priorEntry.gate_id } elseif ($priorState) { [string]$priorState.gate_id } else { '' }
            $sourceTimestamp = [string]$priorEntry.timestamp
            $rechecked | Add-Member -NotePropertyName reused_prior_stage -NotePropertyValue $true -Force
            $rechecked | Add-Member -NotePropertyName reused_from_gate_id -NotePropertyValue $sourceGateId -Force
            $state.current_stage = $StageName
            $state.next_stage = $StageName
            Set-StageResult $StageName $rechecked $priorRunDir
            Set-PersistentCheckpoint $StageName $rechecked $priorRunDir $sourceTimestamp $sourceGateId
            Write-Host "[release-gate] reused prior terminal stage=$StageName run=$priorRunDir gate=$sourceGateId"
            return $rechecked
        }
        Write-Warning "[release-gate] prior stage=$StageName failed current offline revalidation; running a fresh stage."
    }

    $runDir = Invoke-Capture $StageName @(
        '-OpenReactionList',
        '-AutoScreenThenDeep',
        '-ReactionScrollCount', '60',
        '-ReactionAbsoluteCap', '0',
        '-ReactionPipelineBurstSize', '2',
        '-ReactionNativeScrollEnabled', [string]$NativeEnabled,
        '-ReactionRateLimitMaxConsecutive', '1'
    )
    $result = Invoke-Inspector 'full' $runDir $MinFullReactionUsers
    Set-StageResult $StageName $result $runDir
    if (-not [bool]$result.passed) {
        Stop-Gate $StageName ([string]$result.reason) 10
    }
    Set-PersistentCheckpoint $StageName $result $runDir
    return $result
}

trap {
    $unhandledMessage = [string]$_.Exception.Message
    try {
        if ($state) {
            $failedStage = if ([string]::IsNullOrWhiteSpace([string]$state.current_stage) -or [string]$state.current_stage -eq 'init') { 'unhandled-error' } else { [string]$state.current_stage }
            $state.status = 'failed'
            $state.current_stage = $failedStage
            $state.next_stage = $failedStage
            $state.stop_reason = "unhandled-error: $unhandledMessage"
            $state.finished_at = (Get-Date).ToString('o')
            Save-GateState
        }
    }
    catch {
        Write-Warning "[release-gate] failed to persist unhandled error state: $($_.Exception.Message)"
    }
    Write-Warning "[release-gate] unhandled local error: $unhandledMessage"
    Release-GateLock
    exit 70
}

Save-GateState

# Stage 1: zero-page2 session/source health probe.
$probe = Invoke-ProbeStage 'probe'

# Stage 2: conservative page2 validation. Deliberately disable burst/native so
# a successful page2 proves correctness without the speed variable involved.
Wait-StageCooldown 'page2'
$page2Run = Invoke-Capture 'page2' @(
    '-OpenReactionList',
    '-AutoScreenThenDeep',
    '-ReactionScrollCount', '2',
    '-ReactionAbsoluteCap', '2',
    '-ReactionPipelineBurstSize', '1',
    '-ReactionNativeScrollEnabled', '0',
    '-ReactionScreenScrollCount', '0',
    '-ReactionRateLimitMaxConsecutive', '1',
    '-ReadOnlyWaitSeconds', '1'
)
$page2 = Invoke-Inspector 'page2' $page2Run
Set-StageResult 'page2' $page2 $page2Run
if (-not [bool]$page2.passed) {
    Stop-Gate 'page2' ([string]$page2.reason) 10
}

# Stage 3: optimized ADB/burst terminal run. A recently passed terminal run may
# be reused after fresh Probe + page2 health gates and current offline revalidation.
Wait-StageCooldown 'full-adb'
$adb = Invoke-FullReactionStage 'full-adb' 0

# Re-probe after a heavy terminal run before allowing another heavy run.
Wait-StageCooldown 'post-adb-probe'
$postAdbProbe = Invoke-ProbeStage 'post-adb-probe'

if ($RunNativeAB) {
    Wait-StageCooldown 'full-native'
    $native = Invoke-FullReactionStage 'full-native' 1

    $adbElapsedMs = [int64]$adb.elapsed_ms
    $nativeElapsedMs = [int64]$native.elapsed_ms
    $nativeControllerUsed = ([int]$native.native_scroll_success -gt 0)
    $nativeFallbackFree = ([int]$native.native_scroll_fallbacks -eq 0)
    $abComparable = ($adbElapsedMs -gt 0 -and $nativeElapsedMs -gt 0 -and $nativeControllerUsed)
    $nativeSpeedupPercent = if ($abComparable -and $adbElapsedMs -gt 0) {
        [Math]::Round((([double]$adbElapsedMs - [double]$nativeElapsedMs) * 100.0 / [double]$adbElapsedMs), 2)
    }
    else { $null }
    $adbUsersPerMinute = if ($adbElapsedMs -gt 0) { [Math]::Round(([double]$adb.reaction_user_count * 60000.0 / [double]$adbElapsedMs), 2) } else { 0 }
    $nativeUsersPerMinute = if ($nativeElapsedMs -gt 0) { [Math]::Round(([double]$native.reaction_user_count * 60000.0 / [double]$nativeElapsedMs), 2) } else { 0 }
    $nativeHasMeaningfulWin = ($abComparable -and $nativeFallbackFree -and $nativeSpeedupPercent -ge $NativeRecommendationMinSpeedupPercent)
    $recommendedMode = if ($nativeHasMeaningfulWin) { 'native' } elseif ($abComparable) { 'adb' } else { 'unproven' }
    $recommendationReason = if (-not $abComparable) {
        'telemetry-or-native-controller-unproven'
    }
    elseif (-not $nativeFallbackFree) {
        'native-required-fallbacks'
    }
    elseif ($nativeHasMeaningfulWin) {
        'native-meaningfully-faster'
    }
    elseif ($nativeSpeedupPercent -gt 0) {
        'native-gain-below-threshold-keep-adb'
    }
    else {
        'adb-equal-or-faster'
    }
    $abResult = [ordered]@{
        passed = $abComparable
        recommended_mode = $recommendedMode
        recommendation_reason = $recommendationReason
        min_native_speedup_percent = $NativeRecommendationMinSpeedupPercent
        adb_elapsed_ms = $adbElapsedMs
        native_elapsed_ms = $nativeElapsedMs
        native_speedup_percent = $nativeSpeedupPercent
        adb_users_per_minute = $adbUsersPerMinute
        native_users_per_minute = $nativeUsersPerMinute
        adb_growths_per_minute = $adb.growths_per_minute
        native_growths_per_minute = $native.growths_per_minute
        adb_effective_swipes_per_growth = $adb.effective_swipes_per_growth
        native_effective_swipes_per_growth = $native.effective_swipes_per_growth
        native_scroll_success = [int]$native.native_scroll_success
        native_scroll_fallbacks = [int]$native.native_scroll_fallbacks
        native_fallback_free = $nativeFallbackFree
    }
    Set-StageResult 'ab-comparison' $abResult ''
    if (-not $abComparable) {
        Stop-Gate 'ab-comparison' 'ab-telemetry-or-native-controller-unproven' 20
    }
}

if ($RunShareRegression) {
    Wait-StageCooldown 'pre-share-probe'
    $preShareProbe = Invoke-ProbeStage 'pre-share-probe'

    Wait-StageCooldown 'share'
    $share = Invoke-ShareStage
}

$state.current_stage = 'predeploy'
$state.next_stage = 'predeploy'
Save-GateState
$predeployRaw = & $PythonExe $Predeploy
$predeployExit = $LASTEXITCODE
$predeployJson = Get-Content -Raw -Encoding UTF8 (Join-Path $Base 'predeploy-validation.json') | ConvertFrom-Json
$predeployPassed = ($predeployExit -eq 0 -and [string]$predeployJson.overall_status -like 'PASS*')
Set-StageResult 'predeploy' ([ordered]@{
    passed = $predeployPassed
    exit_code = $predeployExit
    overall_status = [string]$predeployJson.overall_status
    stdout = [string]$predeployRaw
}) ''
if (-not $predeployPassed) {
    Stop-Gate 'predeploy' ([string]$predeployJson.overall_status) 40
}

$state.status = 'passed'
$state.current_stage = 'final-report'
$state.next_stage = 'final-report'
$state.stop_reason = ''
$state.finished_at = (Get-Date).ToString('o')
Save-GateState

& $PythonExe $FinalReportScript `
    --state $StatePath `
    --checkpoints $CheckpointPath `
    --predeploy (Join-Path $Base 'predeploy-validation.json') `
    --out-json $FinalReportJson `
    --out-md $FinalReportMd | Out-Host
$reportExit = $LASTEXITCODE
if ($reportExit -ne 0) {
    Stop-Gate 'final-report' "final-report-exit-$reportExit" 50
}

$state.current_stage = 'complete'
$state.next_stage = ''
Save-GateState
Write-Host "[release-gate] PASS gate=$GateId state=$StatePath report=$FinalReportMd"
Release-GateLock
exit 0
