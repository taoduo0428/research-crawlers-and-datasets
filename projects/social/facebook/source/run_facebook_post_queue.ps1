[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$QueueFile,
    [string]$StatePath = '',
    [string]$Serial = 'emulator-5554',
    [int]$ReactionScrollCount = 60,
    [int]$ReactionPipelineBurstSize = 2,
    [int]$ReactionNativeScrollEnabled = 1,
    [int]$ProbeToDeepCooldownSeconds = 15,
    [int]$BaseInterPostCooldownSeconds = 20,
    [int]$MinInterPostCooldownSeconds = 10,
    [int]$MaxInterPostCooldownSeconds = 120
)

$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'
if (-not (Test-Path $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
. $RuntimeHelper

$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path $PowerShellExe)) {
    $powerShellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if (-not $powerShellCommand) { throw 'Windows PowerShell executable could not be resolved.' }
    $PowerShellExe = [string]$powerShellCommand.Source
}
$PythonExe = Resolve-FacebookPythonExe -BasePath $Base
$CaptureScript = Join-Path $Base 'run_facebook_capture.ps1'
$Inspector = Join-Path $Base 'facebook_release_gate.py'
$PressureScript = Join-Path $Base 'facebook_batch_pressure.py'
$RunsRoot = Join-Path $Base 'runs'

foreach ($required in @($CaptureScript, $Inspector, $PressureScript)) {
    if (-not (Test-Path $required)) { throw "Missing queue dependency: $required" }
}
if (-not (Test-Path $QueueFile)) { throw "Queue file not found: $QueueFile" }
$QueueFile = [IO.Path]::GetFullPath($QueueFile)
if ([string]::IsNullOrWhiteSpace($StatePath)) { $StatePath = "$QueueFile.state.json" }
$StatePath = [IO.Path]::GetFullPath($StatePath)
$PressureStatePath = "$StatePath.pressure.json"
$QueueId = [IO.Path]::GetFileNameWithoutExtension($QueueFile)

function Save-QueueState {
    $script:QueueState.updated_at = (Get-Date).ToString('o')
    $temp = "$StatePath.tmp"
    $script:QueueState | ConvertTo-Json -Depth 20 | Set-Content -Path $temp -Encoding UTF8
    Move-Item -Force $temp $StatePath
}

function Invoke-InspectorJson {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('probe','full')][string]$Stage,
        [Parameter(Mandatory = $true)][string]$RunDir,
        [Parameter(Mandatory = $true)][string]$PostId
    )
    $args = @($Inspector, $Stage, '--run-dir', $RunDir)
    if ($Stage -eq 'probe') { $args += @('--expected-post-id', $PostId) }
    if ($Stage -eq 'full') { $args += @('--min-users', '0') }
    $process = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -Arguments $args -WorkingDirectory $Base
    if ([string]::IsNullOrWhiteSpace($process.Stdout)) {
        return [pscustomobject]@{ passed = $false; reason = "inspector-empty-output-$($process.ExitCode)"; exit_code = $process.ExitCode }
    }
    try {
        $result = [string]$process.Stdout | ConvertFrom-Json
        $result | Add-Member -NotePropertyName exit_code -NotePropertyValue ([int]$process.ExitCode) -Force
        return $result
    }
    catch {
        return [pscustomobject]@{ passed = $false; reason = "inspector-invalid-json-$($process.ExitCode)"; exit_code = $process.ExitCode }
    }
}

function Invoke-QueueCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Kind,
        [Parameter(Mandatory = $true)][string]$PostUrl,
        [Parameter(Mandatory = $true)][string]$PostId,
        [Parameter(Mandatory = $true)][int]$TaskIndex
    )
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $runDir = Join-Path $RunsRoot ("queue-{0}-{1:000}-{2}-{3}" -f $QueueId, $TaskIndex, $Kind, $stamp)
    $common = @(
        '-Serial', $Serial,
        '-LaunchFacebook',
        '-PostUrl', $PostUrl,
        '-PostId', $PostId,
        '-OutputDir', $runDir,
        '-PostActionGraceSeconds', '0',
        '-OpenReactionList'
    )
    if ($Kind -eq 'probe') {
        $common += @(
            '-ReactionProbeOnly',
            '-ScrollCount', '0',
            '-ReactionScreenScrollCount', '0',
            '-ReadOnlyWaitSeconds', '1',
            '-ReactionListReadyDelaySeconds', '1'
        )
    }
    else {
        $common += @(
            '-AutoScreenThenDeep',
            '-ReactionScrollCount', [string]$ReactionScrollCount,
            '-ReactionAbsoluteCap', '0',
            '-ReactionPipelineBurstSize', [string]$ReactionPipelineBurstSize,
            '-ReactionNativeScrollEnabled', [string]$ReactionNativeScrollEnabled,
            '-ReactionRateLimitMaxConsecutive', '1'
        )
    }

    Write-Host "[QUEUE $QueueId] task=$TaskIndex kind=$Kind post=$PostId run=$runDir"
    $child = Invoke-FacebookPowerShellFileCaptured -PowerShellExe $PowerShellExe -ScriptPath $CaptureScript -ScriptArguments $common -WorkingDirectory $Base
    Write-FacebookChildProcessOutput -Result $child
    return [pscustomobject]@{ run_dir = $runDir; exit_code = [int]$child.ExitCode }
}

$queueDocument = Get-Content -Raw -Encoding UTF8 $QueueFile | ConvertFrom-Json
if ($queueDocument.PSObject.Properties['posts']) {
    $posts = @($queueDocument.posts)
}
else {
    $posts = @($queueDocument)
}
if ($posts.Count -eq 0) { throw 'Queue has no posts.' }

$seenPostIds = @{}
foreach ($post in $posts) {
    $postId = [string]$post.post_id
    $postUrl = [string]$post.post_url
    if ([string]::IsNullOrWhiteSpace($postId) -or [string]::IsNullOrWhiteSpace($postUrl)) {
        throw 'Every queue item requires post_id and post_url.'
    }
    if ($seenPostIds.ContainsKey($postId)) { throw "Duplicate post_id in queue: $postId" }
    $seenPostIds[$postId] = $true
}

$existingState = $null
if (Test-Path $StatePath) {
    try { $existingState = Get-Content -Raw -Encoding UTF8 $StatePath | ConvertFrom-Json }
    catch { Write-Warning "[QUEUE] existing state is unreadable; starting a fresh state: $($_.Exception.Message)" }
}
$existingByPostId = @{}
if ($existingState -and $existingState.PSObject.Properties['tasks']) {
    foreach ($task in @($existingState.tasks)) {
        if ($task.post_id) { $existingByPostId[[string]$task.post_id] = $task }
    }
}

$taskStates = @()
for ($i = 0; $i -lt $posts.Count; $i++) {
    $post = $posts[$i]
    $postId = [string]$post.post_id
    if ($existingByPostId.ContainsKey($postId)) {
        $taskStates += $existingByPostId[$postId]
    }
    else {
        $taskStates += [pscustomobject][ordered]@{
            index = $i + 1
            post_id = $postId
            post_url = [string]$post.post_url
            status = 'pending'
            attempts = 0
            terminal_run_dir = ''
            reaction_users = 0
            reaction_pages = 0
            last_probe_run_dir = ''
            last_error = ''
        }
    }
}

$script:QueueState = [ordered]@{
    version = 1
    queue_id = $QueueId
    queue_file = $QueueFile
    state_path = $StatePath
    started_at = if ($existingState -and $existingState.started_at) { [string]$existingState.started_at } else { (Get-Date).ToString('o') }
    updated_at = (Get-Date).ToString('o')
    status = 'running'
    current_post_id = ''
    stop_reason = ''
    tasks = $taskStates
}
Save-QueueState

$script:QueueLiveLock = Acquire-FacebookLiveCaptureLock -BasePath $Base -Owner 'post-queue' -OwnerId $QueueId
if (-not [bool]$script:QueueLiveLock.acquired) {
    $script:QueueState.status = 'busy'
    $script:QueueState.stop_reason = 'another-live-capture-active'
    Save-QueueState
    Write-Warning "[QUEUE] another Facebook live capture workflow is active; no queue work started. lock=$($script:QueueLiveLock.path)"
    exit 61
}

try {
    for ($i = 0; $i -lt $posts.Count; $i++) {
        $post = $posts[$i]
        $task = $script:QueueState.tasks[$i]
        $postId = [string]$post.post_id
        $postUrl = [string]$post.post_url

        if ([string]$task.status -eq 'terminal-complete' -and -not [string]::IsNullOrWhiteSpace([string]$task.terminal_run_dir) -and (Test-Path ([string]$task.terminal_run_dir))) {
            Write-Host "[QUEUE $QueueId] skip terminal checkpoint post=$postId run=$($task.terminal_run_dir)"
            continue
        }

        $script:QueueState.current_post_id = $postId
        $task.status = 'probing'
        $task.attempts = [int]$task.attempts + 1
        $task.last_error = ''
        Save-QueueState

        $probeCapture = Invoke-QueueCapture -Kind 'probe' -PostUrl $postUrl -PostId $postId -TaskIndex ($i + 1)
        $task.last_probe_run_dir = [string]$probeCapture.run_dir
        if ($probeCapture.exit_code -ne 0) {
            $task.status = 'failed-local'
            $task.last_error = "probe-capture-exit-$($probeCapture.exit_code)"
            $script:QueueState.status = 'failed'
            $script:QueueState.stop_reason = $task.last_error
            Save-QueueState
            break
        }

        $probe = Invoke-InspectorJson -Stage 'probe' -RunDir ([string]$probeCapture.run_dir) -PostId $postId
        if (-not [bool]$probe.passed) {
            $task.last_error = [string]$probe.reason
            if ([string]$probe.reason -match 'blocked|rate-limit|restriction') {
                $task.status = 'blocked-live'
                $script:QueueState.status = 'blocked-live'
            }
            else {
                $task.status = 'failed-local'
                $script:QueueState.status = 'failed'
            }
            $script:QueueState.stop_reason = [string]$probe.reason
            Save-QueueState
            break
        }

        $task.status = 'probe-ready'
        Save-QueueState
        if ($ProbeToDeepCooldownSeconds -gt 0) { Start-Sleep -Seconds $ProbeToDeepCooldownSeconds }

        $task.status = 'capturing'
        Save-QueueState
        $fullCapture = Invoke-QueueCapture -Kind 'full' -PostUrl $postUrl -PostId $postId -TaskIndex ($i + 1)
        if ($fullCapture.exit_code -ne 0) {
            $task.status = 'failed-local'
            $task.last_error = "full-capture-exit-$($fullCapture.exit_code)"
            $script:QueueState.status = 'failed'
            $script:QueueState.stop_reason = $task.last_error
            Save-QueueState
            break
        }

        $full = Invoke-InspectorJson -Stage 'full' -RunDir ([string]$fullCapture.run_dir) -PostId $postId
        $pressureArgs = @(
            $PressureScript,
            '--run-dir', [string]$fullCapture.run_dir,
            '--state', $PressureStatePath,
            '--out', $PressureStatePath,
            '--base-cooldown-seconds', [string]$BaseInterPostCooldownSeconds,
            '--min-cooldown-seconds', [string]$MinInterPostCooldownSeconds,
            '--max-cooldown-seconds', [string]$MaxInterPostCooldownSeconds
        )
        $pressureProcess = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -Arguments $pressureArgs -WorkingDirectory $Base
        try { $pressure = [string]$pressureProcess.Stdout | ConvertFrom-Json }
        catch { throw "pressure controller returned invalid JSON: $($pressureProcess.Stdout)" }

        if (-not [bool]$full.passed) {
            $task.last_error = [string]$full.reason
            if ([string]$pressure.decision -eq 'stop-batch' -or [string]$full.reason -match 'blocked|rate-limit|restriction') {
                $task.status = 'blocked-live'
                $script:QueueState.status = 'blocked-live'
            }
            else {
                $task.status = 'incomplete'
                $script:QueueState.status = 'paused-incomplete'
            }
            $script:QueueState.stop_reason = [string]$task.last_error
            Save-QueueState
            break
        }

        $task.status = 'terminal-complete'
        $task.terminal_run_dir = [string]$fullCapture.run_dir
        $task.reaction_users = [int]$full.reaction_user_count
        $task.reaction_pages = [int]$full.reaction_page_count
        $task.last_error = ''
        Save-QueueState
        Write-Host "[QUEUE $QueueId] terminal post=$postId users=$($task.reaction_users) pages=$($task.reaction_pages)"

        if ($i -lt ($posts.Count - 1) -and [int]$pressure.next_cooldown_seconds -gt 0) {
            Write-Host "[QUEUE $QueueId] cooldown=$($pressure.next_cooldown_seconds)s before next post"
            Start-Sleep -Seconds ([int]$pressure.next_cooldown_seconds)
        }
    }

    $remaining = @($script:QueueState.tasks | Where-Object { [string]$_.status -ne 'terminal-complete' })
    if ($remaining.Count -eq 0) {
        $script:QueueState.status = 'passed'
        $script:QueueState.current_post_id = ''
        $script:QueueState.stop_reason = ''
        $script:QueueState.finished_at = (Get-Date).ToString('o')
        Save-QueueState
        Write-Host "[QUEUE $QueueId] PASS posts=$($posts.Count) state=$StatePath"
        exit 0
    }

    Save-QueueState
    Write-Warning "[QUEUE $QueueId] stopped status=$($script:QueueState.status) reason=$($script:QueueState.stop_reason) state=$StatePath"
    exit 10
}
finally {
    Release-FacebookLiveCaptureLock -Lock $script:QueueLiveLock
}
