[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [int]$DurationSeconds = 0,
    [int]$ScrollCount = 4,
    [int]$ScrollDelaySeconds = 4,
    [int]$ProxyPort = 8081,
    [int]$MaxFlows = 0,
    [int]$MaxBodyBytes = 6291456,
    [switch]$LaunchFacebook,
    [switch]$OpenShareList,
    [switch]$OpenReactionList,
    [int]$ShareScrollCount = 0,
    [int]$ReactionScrollCount = 24,
    [int]$ReactionTabLimit = 8,
    [int]$PaginationIdleLimit = 4,
    [int]$PaginationOpenIdleLimit = 8,
    [int]$ReactionStrategyIdleLimit = 2,
    [int]$ReactionOpenStrategyIdleLimit = 3,
    [int]$ReactionExtensionChunk = 6,
    [int]$ReactionAbsoluteCap = 0,
    [switch]$AutoScreenThenDeep,
    [switch]$ReactionProbeOnly,
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
    [int]$ReactionNativeScrollPort = 18766,
    [int]$ReactionUiDumpEveryN = 64,
    [int]$ReactionMinCycleMs = 700,
    [int]$ReactionInitialPaginationCooldownSeconds = 4,
    [int]$ReactionRateLimitBackoffSeconds = 15,
    [int]$ReactionRateLimitMaxConsecutive = 3,
    [int]$ReactionRateLimitMaxBackoffSeconds = 300,
    [int]$ReactionNoGrowthUiDumpEveryN = 1,
    [int]$EntryScanScrollCount = 4,
    [int]$UiDumpTimeoutSeconds = 15,
    [int]$UiDumpRetryCount = 1,
    [int]$ReadOnlyWaitSeconds = 4,
    [int]$FacebookReadyTimeoutSeconds = 20,
    [int]$PostActionGraceSeconds = 3,
    [switch]$DisableNativePermalinkFallback,
    [switch]$OpenComments,
    [string]$PostUrl = "",
    [string]$PostId = "",
    [int]$TargetOpenWaitSeconds = 8,
    [switch]$DisableAdaptiveTargetWait,
    [switch]$EnableAdaptiveTargetWait,
    [switch]$DisableRawHttpCapture,
    [switch]$EnableRawNetworkCapture,
    [switch]$EnableFridaSslUnpin,
    [Alias('ColdStart')]
    [switch]$ColdStartFacebook,
    [string]$RawNetworkInterface = "any",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$preparationClock = [System.Diagnostics.Stopwatch]::StartNew()
# Initialize native-command status so StrictMode does not turn a successful
# post-capture verifier into a false failure on hosts where it is still unset.
$global:LASTEXITCODE = 0
# Combined collection is two independent families inside this capture/lease.
# Keep all original Reaction-only setup, retry, and fallback behavior first.
$CombinedReactionShare = [bool]($OpenReactionList -and $OpenShareList)
$OriginalShareTargetInputUrl = [string]$PostUrl
if ($CombinedReactionShare -and $ReactionProbeOnly) { throw 'ReactionProbeOnly cannot be combined with Share collection.' }
if ($CombinedReactionShare) { $OpenShareList = $false }
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$WindowsSystem32 = Join-Path $env:SystemRoot 'System32'
if (Test-Path $WindowsSystem32) { $env:PATH = $WindowsSystem32 + ';' + $env:PATH }
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path $PowerShellExe)) {
    $powerShellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if (-not $powerShellCommand) { throw 'Windows PowerShell executable could not be resolved.' }
    $PowerShellExe = [string]$powerShellCommand.Source
}
$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'
if (-not (Test-Path $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
. $RuntimeHelper
$PythonExe = Join-Path $env:SystemRoot 'py.exe'
if (-not (Test-Path $PythonExe)) {
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue }
    if (-not $pythonCommand) { throw 'Python executable could not be resolved.' }
    $PythonExe = [string]$pythonCommand.Source
}
$PythonExe = Resolve-FacebookPythonExe -BasePath $Base
$PythonRuntimeExe = $PythonExe
$MitmdumpExe = Resolve-FacebookMitmdumpExe -PythonExe $PythonRuntimeExe
if ([string]::IsNullOrWhiteSpace($MitmdumpExe) -or -not (Test-Path $MitmdumpExe)) {
    $mitmdumpCommand = Get-Command mitmdump.exe -ErrorAction SilentlyContinue
    if (-not $mitmdumpCommand) { throw 'mitmdump executable could not be resolved from the active Python runtime or PATH.' }
    $MitmdumpExe = [string]$mitmdumpCommand.Source
}
$TransportHelper = Join-Path $Base "facebook_transport.ps1"
if (-not (Test-Path $TransportHelper)) { throw "Missing transport helper: $TransportHelper" }
. $TransportHelper
$null = Get-FacebookNetworkConfig

function Write-CaptureTiming([string]$Phase, [System.Diagnostics.Stopwatch]$Clock) {
    # Diagnostics only: do not expose argv/URLs, alter return values, or hide
    # operation failures when the optional timing output cannot be emitted.
    try { Write-Host ("[capture-timing] phase={0} elapsed_ms={1}" -f $Phase, $Clock.ElapsedMilliseconds) }
    catch { }
}

function Invoke-CaptureChildPowerShell([string]$ScriptPath, [string[]]$ScriptArguments) {
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $child = Invoke-FacebookPowerShellFileCaptured -PowerShellExe $PowerShellExe -ScriptPath $ScriptPath -ScriptArguments $ScriptArguments -WorkingDirectory $Base
        Write-FacebookChildProcessOutput -Result $child
        return [int]$child.ExitCode
    }
    finally { Write-CaptureTiming ([IO.Path]::GetFileName($ScriptPath)) $clock }
}

function Invoke-CapturePythonHidden {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$QuietStdout
    )
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $result = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -Arguments $Arguments -WorkingDirectory $Base
        $global:LASTEXITCODE = [int]$result.ExitCode
        if (-not $QuietStdout -and -not [string]::IsNullOrWhiteSpace([string]$result.Stdout)) {
            Write-Host ([string]$result.Stdout).TrimEnd()
        }
        if (-not [string]::IsNullOrWhiteSpace([string]$result.Stderr)) {
            Write-Warning ([string]$result.Stderr).TrimEnd()
        }
        return $result
    }
    finally { Write-CaptureTiming ([IO.Path]::GetFileName($Arguments[0])) $clock }
}

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunsRoot = [IO.Path]::GetFullPath((Join-Path $Base "runs"))
$RunDir = if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    Join-Path $RunsRoot $Timestamp
}
else {
    $candidate = if ([IO.Path]::IsPathRooted($OutputDir)) {
        [IO.Path]::GetFullPath($OutputDir)
    }
    else {
        [IO.Path]::GetFullPath((Join-Path $Base $OutputDir))
    }
    $runsPrefix = $RunsRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($runsPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "OutputDir must resolve inside the project runs directory: $RunsRoot"
    }
    $candidate
}
$Addon = Join-Path $Base "facebook_capture_addon_safe.py"
$RawAddon = Join-Path $Base "facebook_raw_flow_writer.py"
$Jsonl = Join-Path $RunDir "safe-facebook-http-capture.jsonl"
$RawFlow = Join-Path $RunDir "raw-facebook-http.mitm"
$RawIndex = Join-Path $RunDir "raw-facebook-http-index.jsonl"
$RawNetworkPcap = Join-Path $RunDir "raw-network.pcap"
$RawNetworkMetadata = Join-Path $RunDir "raw-network-capture.json"
$StdoutLog = Join-Path $RunDir "mitmdump.stdout.log"
$StderrLog = Join-Path $RunDir "mitmdump.stderr.log"
$FridaLog = Join-Path $RunDir "facebook-frida-unpin.log"
$AnalysisDir = Join-Path $RunDir "analysis"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$mitm = $null
$captureDeadline = $null
$readonlyActionsPerformed = $false
$captureCompleted = $false
$rawNetworkCapture = $null
$rawNetworkCaptureError = $null
$transportWatchStoppedCount = 0
$NativePermalinkFallbackPath = Join-Path $RunDir "native-permalink-fallback.json"
$FallbackProbeAnalysisDir = Join-Path $RunDir "fallback-probe-analysis"
$script:ResolvedCaptureSourcePostId = $null
$script:ResolvedTargetUrl = [string]$PostUrl
$script:PrimaryVerifiedPostId = $null
$script:PrimaryVerifiedAuthorId = $null
$script:PrimaryVerifiedReactionTotal = 0
$script:PrimarySourceDiscoveryAllowed = $false
$script:PrimarySourceDiscoveryExpectedPostId = ''

function Get-FacebookMainProcessId {
    $pidResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'pidof', 'com.facebook.katana') -TimeoutSeconds 3
    if (-not $pidResult.Success) { return '' }
    $candidatePids = @(([string]$pidResult.Stdout).Trim() -split '\s+' | Where-Object { $_ -match '^[0-9]+$' })
    foreach ($candidatePid in $candidatePids) {
        $processResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'ps', '-p', $candidatePid, '-o', 'NAME=') -TimeoutSeconds 3
        if ($processResult.Success -and ([string]$processResult.Stdout).Trim() -eq 'com.facebook.katana') {
            return [string]$candidatePid
        }
    }
    return ''
}

function Get-FacebookUnpinStatePath([string]$DeviceSerial) {
    $safeSerial = ([string]$DeviceSerial -replace '[^A-Za-z0-9_.-]', '_')
    if ([string]::IsNullOrWhiteSpace($safeSerial)) { return '' }
    return [IO.Path]::GetFullPath((Join-Path $Base ("worker-bootstrap\runtime-{0}-unpin.json" -f $safeSerial)))
}

function Test-FacebookUnpinHookLog([string]$LogPath) {
    try {
        if ([string]::IsNullOrWhiteSpace($LogPath) -or -not (Test-Path -LiteralPath $LogPath -PathType Leaf)) { return $false }
        $contents = [IO.File]::ReadAllText([IO.Path]::GetFullPath($LogPath))
        $primaryHook = [regex]::IsMatch($contents, '(?m)^\[facebook-unpin\] hook installed at 0x[0-9a-fA-F]+\s*$')
        $x509Hook = [regex]::IsMatch($contents, '(?m)^\[facebook-unpin\] coldstart X509 hook installed at 0x[0-9a-fA-F]+\s*$')
        return [bool]($primaryHook -and $x509Hook)
    }
    catch {
        return $false
    }
}

function Get-FacebookUnpinScriptHash([string]$ScriptPath) {
    $stream = $null
    $hasher = $null
    try {
        $stream = [IO.File]::OpenRead($ScriptPath)
        $hasher = [Security.Cryptography.SHA256]::Create()
        return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    }
    catch {
        return ''
    }
    finally {
        if ($stream) { $stream.Dispose() }
        if ($hasher) { $hasher.Dispose() }
    }
}

function Read-FacebookUnpinReuseState([string]$DeviceSerial) {
    $statePath = Get-FacebookUnpinStatePath $DeviceSerial
    if ([string]::IsNullOrWhiteSpace($statePath) -or -not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
    try {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($state.version -ne 1 -or $state.eternalized -ne $true) { return $null }
        $logPath = [string]$state.frida_log_path
        if ([string]::IsNullOrWhiteSpace($logPath)) { return $null }
        $runsPrefix = [IO.Path]::GetFullPath($RunsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        $fullLogPath = [IO.Path]::GetFullPath($logPath)
        if (-not $fullLogPath.StartsWith($runsPrefix, [StringComparison]::OrdinalIgnoreCase)) { return $null }
        if (-not (Test-FacebookUnpinHookLog $fullLogPath)) { return $null }
        return [pscustomobject]@{
            version = [int]$state.version
            eternalized = [bool]$state.eternalized
            serial = [string]$state.serial
            boot_id = [string]$state.boot_id
            main_pid = [string]$state.main_pid
            proc_starttime = [string]$state.proc_starttime
            script_sha256 = [string]$state.script_sha256
            frida_log_path = $fullLogPath
            state_path = $statePath
        }
    }
    catch {
        return $null
    }
}

function Get-FacebookUnpinIdentity([string]$ExpectedPid = '') {
    try {
        $bootResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'cat', '/proc/sys/kernel/random/boot_id') -TimeoutSeconds 5
        if (-not $bootResult.Success) { return $null }
        $bootId = ([string]$bootResult.Stdout).Trim()
        if ($bootId -notmatch '^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$') { return $null }

        $facebookPid = [string](Get-FacebookMainProcessId)
        if ([string]::IsNullOrWhiteSpace($facebookPid)) { return $null }
        if (-not [string]::IsNullOrWhiteSpace($ExpectedPid) -and $facebookPid -ne [string]$ExpectedPid) { return $null }

        $statResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'cat', ("/proc/{0}/stat" -f $facebookPid)) -TimeoutSeconds 5
        if (-not $statResult.Success) { return $null }
        $stat = ([string]$statResult.Stdout).Trim()
        if ($stat -notmatch ('^' + [regex]::Escape($facebookPid) + '\s+\(')) { return $null }
        $closeParen = $stat.LastIndexOf(')')
        if ($closeParen -lt 0) { return $null }
        $statTail = $stat.Substring($closeParen + 1).Trim() -split '\s+'
        # /proc/<pid>/stat field 22 is index 19 after the comm/state prefix.
        if ($statTail.Count -le 19 -or $statTail[19] -notmatch '^\d+$') { return $null }
        return [pscustomobject]@{
            serial = [string]$Serial
            boot_id = $bootId
            main_pid = $facebookPid
            proc_starttime = [string]$statTail[19]
        }
    }
    catch {
        return $null
    }
}

function Test-FacebookUnpinIdentityMatch([object]$Expected, [object]$Actual) {
    return [bool](
        $Expected -and $Actual -and
        -not [string]::IsNullOrWhiteSpace([string]$Actual.serial) -and
        -not [string]::IsNullOrWhiteSpace([string]$Actual.boot_id) -and
        ([string]$Actual.main_pid -match '^\d+$') -and
        ([string]$Actual.proc_starttime -match '^\d+$') -and
        ([string]$Expected.serial -ieq [string]$Actual.serial) -and
        ([string]$Expected.boot_id -ieq [string]$Actual.boot_id) -and
        ([string]$Expected.main_pid -eq [string]$Actual.main_pid) -and
        ([string]$Expected.proc_starttime -eq [string]$Actual.proc_starttime)
    )
}

function Write-FacebookUnpinReuseState([object]$Identity, [string]$ScriptHash, [string]$FridaLogPath) {
    if (-not $Identity -or [string]::IsNullOrWhiteSpace($ScriptHash) -or -not (Test-FacebookUnpinHookLog $FridaLogPath)) { return $false }
    $statePath = Get-FacebookUnpinStatePath $Serial
    if ([string]::IsNullOrWhiteSpace($statePath)) { return $false }
    $tempPath = $null
    try {
        $stateDir = Split-Path -Parent $statePath
        New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
        $record = [ordered]@{
            version = 1
            eternalized = $true
            serial = [string]$Identity.serial
            boot_id = [string]$Identity.boot_id
            main_pid = [string]$Identity.main_pid
            proc_starttime = [string]$Identity.proc_starttime
            script_sha256 = [string]$ScriptHash
            frida_log_path = [IO.Path]::GetFullPath($FridaLogPath)
            written_at = (Get-Date).ToUniversalTime().ToString('o')
        }
        $tempPath = $statePath + ".tmp-$PID-$([guid]::NewGuid().ToString('N'))"
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($tempPath, ($record | ConvertTo-Json -Depth 3), $utf8)
        if (Test-Path -LiteralPath $statePath -PathType Leaf) {
            [IO.File]::Replace($tempPath, $statePath, $null, $true)
        }
        else {
            [IO.File]::Move($tempPath, $statePath)
        }
        $tempPath = $null
        return $true
    }
    catch {
        Write-Warning "[facebook-unpin] hook reuse state write failed: $($_.Exception.Message)"
        return $false
    }
    finally {
        if ($tempPath -and (Test-Path -LiteralPath $tempPath)) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-FacebookWithFridaSslUnpin {
    $fridaScript = Join-Path $Base 'facebook_ssl_unpin.js'
    if (-not (Test-Path -LiteralPath $fridaScript)) { throw "Missing Frida script: $fridaScript" }
    $fridaCommand = Get-Command frida.exe -ErrorAction SilentlyContinue
    if (-not $fridaCommand) { throw 'frida.exe is unavailable.' }

    $serverPath = '/data/local/tmp/fb-fs17'
    $serverCheck = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', $serverPath, '--version') -TimeoutSeconds 8
    if (-not $serverCheck.Success) { throw "Frida server is unavailable on $Serial at $serverPath" }

    $serverPid = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'pidof', 'fb-fs17') -TimeoutSeconds 5
    if (-not $serverPid.Success -or [string]::IsNullOrWhiteSpace([string]$serverPid.Stdout)) {
        # frida-server stays alive when the bounded adb launcher disconnects.
        $null = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'su', '0', $serverPath, '-D') -TimeoutSeconds 2
        $serverDeadline = (Get-Date).AddSeconds(8)
        do {
            Start-Sleep -Milliseconds 250
            $serverPid = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'pidof', 'fb-fs17') -TimeoutSeconds 3
        } while ((-not $serverPid.Success -or [string]::IsNullOrWhiteSpace([string]$serverPid.Stdout)) -and (Get-Date) -lt $serverDeadline)
        if (-not $serverPid.Success -or [string]::IsNullOrWhiteSpace([string]$serverPid.Stdout)) {
            # Some rooted emulator boots return from `su ... -D` without leaving
            # the daemon alive. Start the same verified binary through a detached
            # shell and keep its diagnostic log on-device for inspection.
            $startCommand = "nohup $serverPath >/data/local/tmp/fb-fs17-start.log 2>&1 &"
            $null = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', $startCommand) -TimeoutSeconds 3
            $fallbackDeadline = (Get-Date).AddSeconds(5)
            do {
                Start-Sleep -Milliseconds 250
                $serverPid = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'pidof', 'fb-fs17') -TimeoutSeconds 3
            } while ((-not $serverPid.Success -or [string]::IsNullOrWhiteSpace([string]$serverPid.Stdout)) -and (Get-Date) -lt $fallbackDeadline)
        }
        if (-not $serverPid.Success -or [string]::IsNullOrWhiteSpace([string]$serverPid.Stdout)) {
            throw "Frida server did not start on $Serial"
        }
    }

    $facebookPid = ''
    if ($ColdStartFacebook) {
        Write-Warning '[facebook-unpin] explicit cold-start recovery requested; stopping Facebook before launch.'
        $forceStop = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','am','force-stop','com.facebook.katana') -TimeoutSeconds 10
        if (-not $forceStop.Success) { throw "Facebook force-stop failed before Frida attach on $Serial" }
    }
    else {
        # Warm capture keeps the existing account/session process intact.  The
        # readiness check below brings Facebook back to the foreground if a
        # stale activity from another package is currently visible.
        $facebookPid = Get-FacebookMainProcessId
        if (-not [string]::IsNullOrWhiteSpace($facebookPid)) {
            Write-Host "[facebook-unpin] reusing existing Facebook main pid=$facebookPid"
        }
        else {
            Write-Host '[facebook-unpin] Facebook main process is absent; launching it for warm attach.'
        }
    }

    if ([string]::IsNullOrWhiteSpace($facebookPid)) {
        $launch = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','am','start','-n','com.facebook.katana/.LoginActivity') -TimeoutSeconds 15
        if (-not $launch.Success) { throw "Facebook launch failed before Frida attach on $Serial" }
        $facebookDeadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 250
            $facebookPid = Get-FacebookMainProcessId
        } while ([string]::IsNullOrWhiteSpace($facebookPid) -and (Get-Date) -lt $facebookDeadline)
        if ([string]::IsNullOrWhiteSpace($facebookPid)) {
            Write-Warning '[facebook-unpin] Facebook cold start exceeded 15 seconds; retrying the launcher without another force-stop.'
            $launch = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','am','start','-W','-n','com.facebook.katana/.LoginActivity') -TimeoutSeconds 25
            if ($launch.Success) {
                $facebookDeadline = (Get-Date).AddSeconds(15)
                do {
                    Start-Sleep -Milliseconds 250
                    $facebookPid = Get-FacebookMainProcessId
                } while ([string]::IsNullOrWhiteSpace($facebookPid) -and (Get-Date) -lt $facebookDeadline)
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($facebookPid)) { throw 'Facebook did not start before Frida attach.' }

    $unpinScriptHash = Get-FacebookUnpinScriptHash $fridaScript
    $unpinReuseState = $null
    $unpinReuseIdentityBefore = $null
    $unpinReuseStartedAt = Get-Date
    $unpinReuseCandidate = $false
    if (-not $ColdStartFacebook -and -not [string]::IsNullOrWhiteSpace($unpinScriptHash)) {
        $unpinReuseState = Read-FacebookUnpinReuseState $Serial
        if ($unpinReuseState -and [string]$unpinReuseState.script_sha256 -ieq $unpinScriptHash) {
            # Check the cached boot, PID, and kernel start time before allowing
            # the UI readiness probe to run against an eternalized process.
            $unpinReuseIdentityBefore = Get-FacebookUnpinIdentity $facebookPid
            $unpinReuseCandidate = Test-FacebookUnpinIdentityMatch $unpinReuseState $unpinReuseIdentityBefore
        }
    }

    Wait-FacebookUiReady ([Math]::Max(8, $FacebookReadyTimeoutSeconds)) | Out-Null
    $facebookPid = Get-FacebookMainProcessId
    if ([string]::IsNullOrWhiteSpace($facebookPid)) { throw 'Facebook main process disappeared after its UI became ready.' }
    if ($unpinReuseCandidate) {
        $unpinReuseIdentityAfter = Get-FacebookUnpinIdentity $facebookPid
        if (
            (Test-FacebookUnpinIdentityMatch $unpinReuseState $unpinReuseIdentityAfter) -and
            (Test-FacebookUnpinIdentityMatch $unpinReuseIdentityBefore $unpinReuseIdentityAfter)
        ) {
            $reuseElapsedMs = [int][Math]::Round(((Get-Date) - $unpinReuseStartedAt).TotalMilliseconds)
            Write-Host "[facebook-unpin] reused eternalized hooks pid=$facebookPid reuse_elapsed_ms=$reuseElapsedMs state=$($unpinReuseState.state_path)"
            return
        }
        Write-Warning '[facebook-unpin] cached hook identity changed after UI readiness; falling back to normal Frida attach.'
    }
    $stablePidSamples = 1
    $stablePidDeadline = (Get-Date).AddSeconds(10)
    while ($stablePidSamples -lt 4 -and (Get-Date) -lt $stablePidDeadline) {
        Start-Sleep -Seconds 1
        $currentMainPid = Get-FacebookMainProcessId
        if ($currentMainPid -eq $facebookPid) {
            $stablePidSamples++
        }
        elseif (-not [string]::IsNullOrWhiteSpace($currentMainPid)) {
            $facebookPid = $currentMainPid
            $stablePidSamples = 1
        }
        else { $stablePidSamples = 0 }
    }
    if ($stablePidSamples -lt 4) { throw 'Facebook main process did not remain stable before Frida attach.' }

    # A failed replacement attach must never leave an older reusable claim.
    $unpinStatePath = Get-FacebookUnpinStatePath $Serial
    if ($unpinStatePath -and (Test-Path -LiteralPath $unpinStatePath -PathType Leaf)) {
        [IO.File]::Delete($unpinStatePath)
    }
    $preAttachIdentity = Get-FacebookUnpinIdentity $facebookPid

    $fridaArgs = @(
        '-D', $Serial,
        '-p', $facebookPid,
        '-l', $fridaScript,
        '--eternalize',
        '--exit-on-error',
        '-q',
        '-t', '8',
        '-o', $FridaLog
    )
    $fridaStdoutLog = $FridaLog + '.stdout.log'
    $fridaStderrLog = $FridaLog + '.stderr.log'
    $fridaAttachStartedAt = Get-Date
    $fridaProcess = Start-Process -FilePath ([string]$fridaCommand.Source) -ArgumentList $fridaArgs -WorkingDirectory $Base -WindowStyle Hidden -RedirectStandardOutput $fridaStdoutLog -RedirectStandardError $fridaStderrLog -PassThru -Wait
    $fridaAttachElapsedMs = [int][Math]::Round(((Get-Date) - $fridaAttachStartedAt).TotalMilliseconds)
    $global:LASTEXITCODE = [int]$fridaProcess.ExitCode
    if ($fridaProcess.ExitCode -ne 0) {
        throw "Frida spawn injection failed with exit code $($fridaProcess.ExitCode); inspect $FridaLog, $fridaStdoutLog, and $fridaStderrLog"
    }
    $pidResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'pidof', 'com.facebook.katana') -TimeoutSeconds 8
    $currentFacebookPids = @(([string]$pidResult.Stdout).Trim() -split '\s+' | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    if (-not $pidResult.Success -or $facebookPid -notin $currentFacebookPids) {
        throw 'Facebook did not remain on the attached process after Frida injection.'
    }
    $postAttachIdentity = Get-FacebookUnpinIdentity $facebookPid
    if (Test-FacebookUnpinHookLog $FridaLog) {
        if ((Test-FacebookUnpinIdentityMatch $preAttachIdentity $postAttachIdentity) -and -not [string]::IsNullOrWhiteSpace($unpinScriptHash)) {
            if (Write-FacebookUnpinReuseState $postAttachIdentity $unpinScriptHash $FridaLog) {
                Write-Host "[facebook-unpin] eternalized hook state cached pid=$facebookPid state=$(Get-FacebookUnpinStatePath $Serial)"
            }
        }
        elseif (-not $postAttachIdentity) {
            Write-Warning '[facebook-unpin] post-attach process identity was unreadable; hook reuse state was not cached.'
        }
    }
    else {
        Write-Warning '[facebook-unpin] both hook-installed log lines were not confirmed; hook reuse state was not cached.'
    }
    Write-Host "[facebook-unpin] attach injection active pid=$facebookPid server=$(([string]$serverPid.Stdout).Trim()) elapsed_ms=$fridaAttachElapsedMs log=$FridaLog"
}

function Get-LatestDiagnosticAction($Diagnostics, [string]$ActionName) {
    $actions = @($Diagnostics.actions)
    for ($i = $actions.Count - 1; $i -ge 0; $i--) {
        if ($actions[$i] -and [string]$actions[$i].action -eq $ActionName) { return $actions[$i] }
    }
    return $null
}

function Write-NativePermalinkFallbackRecord([hashtable]$Record) {
    $ordered = [ordered]@{
        source = 'android-native-permalink-fallback'
        attempted = [bool]$Record.attempted
        reaction_eligible = [bool]$Record.reaction_eligible
        share_eligible = [bool]$Record.share_eligible
        initial_reaction_status = $Record.initial_reaction_status
        initial_reaction_reason = $Record.initial_reaction_reason
        initial_share_classification = $Record.initial_share_classification
        initial_share_reason = $Record.initial_share_reason
        post_id = $Record.post_id
        author_id = $Record.author_id
        composite_story_id = $Record.composite_story_id
        deeplink = $Record.deeplink
        fallback_start_line = $Record.fallback_start_line
        family_capture_start_line = $Record.family_capture_start_line
        target_open_method = $Record.target_open_method
        target_open_verified = [bool]$Record.target_open_verified
        target_open_exit_code = $Record.target_open_exit_code
        target_open_diagnostics = $Record.target_open_diagnostics
        composite_deeplink_attempted = [bool]$Record.composite_deeplink_attempted
        explorer_exit_code = $Record.explorer_exit_code
        fallback_diagnostics = $Record.fallback_diagnostics
        target_validation_status = $Record.target_validation_status
        reaction_target_validation_status = $Record.reaction_target_validation_status
        share_target_validation_status = $Record.share_target_validation_status
        observed_reaction_source_post_ids = @($Record.observed_reaction_source_post_ids)
        observed_share_source_post_ids = @($Record.observed_share_source_post_ids)
        unavailable_reason = $Record.unavailable_reason
        error = $Record.error
    }
    $ordered | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $NativePermalinkFallbackPath
}

function Get-CaptureJsonlLineCount {
    if (-not (Test-Path $Jsonl)) { return 0 }
    $stream = $null
    $snapshot = $null
    $reader = $null
    try {
        # The mitm addon keeps the JSONL append handle open while a boundary is
        # sampled.  Share reads and writes, then bound this read to the length
        # observed at open time so a busy writer cannot extend the boundary.
        $stream = [IO.File]::Open(
            $Jsonl,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite
        )
        $snapshotLength = [int64]$stream.Length
        $snapshot = [IO.MemoryStream]::new()
        $buffer = [byte[]]::new(65536)
        $remaining = $snapshotLength
        while ($remaining -gt 0) {
            $readSize = [int][Math]::Min($buffer.Length, $remaining)
            $read = $stream.Read($buffer, 0, $readSize)
            if ($read -le 0) { break }
            $snapshot.Write($buffer, 0, $read)
            $remaining -= $read
        }
        $snapshot.Position = 0
        $reader = [IO.StreamReader]::new($snapshot, [Text.Encoding]::UTF8, $true)
        $count = 0
        while ($null -ne $reader.ReadLine()) { $count++ }
        return [int]$count
    }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($snapshot) { $snapshot.Dispose() }
        if ($stream) { $stream.Dispose() }
    }
}

function Test-TargetSpecificPostUrl([string]$Url) {
    if ([string]::IsNullOrWhiteSpace($Url)) { return $false }
    return [bool]($Url -match '(?i)(?:pfbid[A-Za-z0-9]+|[?&]story_fbid=|/posts/(?:[0-9]+|pfbid[A-Za-z0-9]+)|/permalink/[0-9]+|/reel/[0-9]+|/videos/[0-9]+)')
}

function Invoke-NativePermalinkFallback {
    param(
        [string]$ReadonlyDirectoryName = 'readonly-ui',
        [string]$OutputSuffix = '',
        [string]$ExpectedStagePostId = ''
    )
    # Default paths preserve standalone behavior. Combined Share gets separate
    # files; it must not overwrite the completed Reaction fallback evidence.
    if ($OutputSuffix) {
        $NativePermalinkFallbackPath = Join-Path $RunDir ("native-permalink-fallback{0}.json" -f $OutputSuffix)
        $FallbackProbeAnalysisDir = Join-Path $RunDir ("fallback-probe-analysis{0}" -f $OutputSuffix)
    }
    if ($DisableNativePermalinkFallback -or (-not $OpenReactionList -and -not $OpenShareList) -or -not (Test-Path $Jsonl)) { return }

    $record = @{
        attempted = $false; reaction_eligible = $false; share_eligible = $false
        initial_reaction_status = $null; initial_reaction_reason = $null
        initial_share_classification = $null; initial_share_reason = $null
        post_id = $null; author_id = $null; composite_story_id = $null; deeplink = $null
        fallback_start_line = 0; family_capture_start_line = 0
        target_open_method = $null; target_open_verified = $false; target_open_exit_code = $null; target_open_diagnostics = $null
        composite_deeplink_attempted = $false
        explorer_exit_code = $null; fallback_diagnostics = $null
        target_validation_status = $null; reaction_target_validation_status = $null; share_target_validation_status = $null
        observed_reaction_source_post_ids = @(); observed_share_source_post_ids = @()
        unavailable_reason = $null; error = $null
    }

    try {
        Start-Sleep -Seconds 1
        New-Item -ItemType Directory -Force -Path $FallbackProbeAnalysisDir | Out-Null
        $fallbackAnalysis = Invoke-CapturePythonHidden @(
            (Join-Path $Base 'analyze_facebook_capture.py'), '--input', $Jsonl, '--out-dir', $FallbackProbeAnalysisDir
        ) -QuietStdout
        if ($fallbackAnalysis.ExitCode -ne 0) { throw "fallback probe analyzer exited with code $($fallbackAnalysis.ExitCode)" }

        $probeReportPath = Join-Path $FallbackProbeAnalysisDir 'facebook-sample-patterns.json'
        $initialDiagPath = Join-Path (Join-Path $RunDir $ReadonlyDirectoryName) 'exploration-diagnostics.json'
        if (-not (Test-Path $probeReportPath)) { throw 'fallback probe report was not created' }
        $report = Get-Content -Raw -Encoding UTF8 $probeReportPath | ConvertFrom-Json
        $diag = if (Test-Path $initialDiagPath) { Get-Content -Raw -Encoding UTF8 $initialDiagPath | ConvertFrom-Json } else { [pscustomobject]@{} }

        $reactionEnum = $report.enumeration_status.reactions
        $shareEnum = $report.enumeration_status.shares
        $reactionAction = Get-LatestDiagnosticAction $diag 'reaction'
        $shareAction = Get-LatestDiagnosticAction $diag 'share'
        $reactionStatus = [string]$reactionEnum.status
        $reactionVisibility = [string]$reactionEnum.ui_visibility_hint
        if (-not $reactionVisibility -and $reactionAction) { $reactionVisibility = [string]$reactionAction.visibility_hint }
        $reactionReason = if ($reactionAction) { [string]$reactionAction.failure_reason } else { $null }
        $reactionTargetMismatch = [bool](
            $diag.target_guard -and [string]$diag.target_guard.status -eq 'mismatch' -and
            -not [string]::IsNullOrWhiteSpace([string]$script:PrimaryVerifiedPostId) -and
            [string]$diag.target_guard.expected_post_id -eq [string]$script:PrimaryVerifiedPostId
        )
        $record.initial_reaction_status = $reactionStatus
        $record.initial_reaction_reason = $reactionReason

        $shareClass = [string]$shareEnum.classification
        $shareReason = [string]$shareEnum.decision_reason
        $record.initial_share_classification = $shareClass
        $record.initial_share_reason = $shareReason

        $explicitReactionRestriction = ($reactionVisibility -eq 'friends-only-names' -or $reactionStatus -eq 'query-triggered-friends-only-visibility')
        $reactionFailureReasons = @('reaction-list-not-confirmed', 'no-visible-reaction-count-entry', 'no-reaction-count-after-comments-normalization', 'no-reaction-count-after-strict-target-detail-scan')
        $record.reaction_eligible = [bool](
            $OpenReactionList -and
            -not $explicitReactionRestriction -and
            (
                $reactionStatus -eq 'query-not-triggered' -or
                $reactionTargetMismatch -or
                ($reactionAction -and -not [bool]$reactionAction.confirmed -and $reactionFailureReasons -contains $reactionReason)
            )
        )
        $record.share_eligible = [bool](
            $OpenShareList -and
            ($shareClass -eq 'no-share-entry' -or $shareClass -eq 'entry-or-query-failed')
        )

        if (-not $record.reaction_eligible -and -not $record.share_eligible) {
            $record.unavailable_reason = 'not-eligible'
            Write-NativePermalinkFallbackRecord $record
            return
        }

        $sourceIds = New-Object System.Collections.Generic.List[string]
        foreach ($page in @($report.share_pages)) {
            $value = [string]$page.source_post_id
            if ($value -and -not $sourceIds.Contains($value)) { $sourceIds.Add($value) }
        }
        foreach ($page in @($report.reactor_pages)) {
            $value = [string]$page.source_post_id
            if ($value -and -not $sourceIds.Contains($value)) { $sourceIds.Add($value) }
        }
        $usePrimaryVerifiedReactionTarget = $false
        $target = $null
        if (
            $reactionTargetMismatch -and $record.reaction_eligible -and -not $record.share_eligible -and
            -not [string]::IsNullOrWhiteSpace([string]$script:PrimaryVerifiedPostId) -and
            -not [string]::IsNullOrWhiteSpace([string]$script:PrimaryVerifiedAuthorId)
        ) {
            # The first click proved a different source. Never reuse that observed
            # source as a fallback target; open the already network-verified target
            # directly and require a new exact source proof before pagination.
            $usePrimaryVerifiedReactionTarget = $true
            $record.post_id = [string]$script:PrimaryVerifiedPostId
            $record.author_id = [string]$script:PrimaryVerifiedAuthorId
            Write-Host "[fallback] source mismatch; retrying readonly reactors list for strict target post=$($record.post_id) author=$($record.author_id)"
        }
        elseif ($sourceIds.Count -eq 1) {
            $sourcePostId = $sourceIds[0]
            $matches = @($report.posts | Where-Object { [string]$_.post_id -eq $sourcePostId -and [string]$_.author_id })
            if ($matches.Count -ne 1) {
                $record.unavailable_reason = if ($matches.Count -eq 0) { 'matching-post-author-unavailable' } else { 'ambiguous-matching-post' }
                Write-NativePermalinkFallbackRecord $record
                return
            }
            $target = $matches[0]
            $record.post_id = [string]$target.post_id
            $record.author_id = [string]$target.author_id
        }
        elseif (
            $sourceIds.Count -eq 0 -and
            $record.reaction_eligible -and -not $record.share_eligible -and
            -not [string]::IsNullOrWhiteSpace([string]$script:PrimaryVerifiedPostId) -and
            -not [string]::IsNullOrWhiteSpace([string]$script:PrimaryVerifiedAuthorId)
        ) {
            $usePrimaryVerifiedReactionTarget = $true
            $record.post_id = [string]$script:PrimaryVerifiedPostId
            $record.author_id = [string]$script:PrimaryVerifiedAuthorId
            Write-Host "[fallback] reusing strict primary target for readonly reactors list: post=$($record.post_id) author=$($record.author_id)"
        }
        else {
            $record.unavailable_reason = if ($sourceIds.Count -eq 0) { 'exact-source-post-id-unavailable' } else { 'ambiguous-source-post-id' }
            Write-NativePermalinkFallbackRecord $record
            return
        }

        if ($ExpectedStagePostId -and [string]$record.post_id -ne $ExpectedStagePostId) {
            $record.unavailable_reason = 'fallback-stage-target-mismatch'
            throw "Fallback target differs from the locked combined target: expected=$ExpectedStagePostId observed=$($record.post_id)"
        }
        $record.composite_story_id = "$($record.author_id)_$($record.post_id)"
        $script:ResolvedCaptureSourcePostId = $record.post_id
        if ($record.share_eligible) {
            # Share fallback must start from the native post detail. Opening the
            # reactors list first leaves the Share explorer on the wrong surface.
            # When both families are eligible, the common post detail is also the
            # safest starting point and each existing UI path can open its own list.
            $record.deeplink = "fb://feed_story_permalink_non_encoded_id/$($record.composite_story_id)"
        }
        else {
            $record.deeplink = "fb://feed_story_permalink_non_encoded_id/$($record.composite_story_id)?open_reactors_list=true"
        }
        $record.attempted = $true
        $record.fallback_start_line = Get-CaptureJsonlLineCount

        Write-Host "[fallback] target=$($record.composite_story_id) reaction=$($record.reaction_eligible) share=$($record.share_eligible)"
        $null = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort

        if ($usePrimaryVerifiedReactionTarget) {
            # Composite reactors deeplinks can misroute on Facebook 555. Start the
            # family evidence window before the deeplink and require the explorer
            # to prove the first fresh Reaction GraphQL belongs to this exact post
            # before it is allowed to paginate.
            $record.family_capture_start_line = Get-CaptureJsonlLineCount
            Write-Host "[fallback] launching readonly reactors-list deeplink; family boundary=$([int]$record.family_capture_start_line + 1)"
            $deeplinkStarted = Start-FacebookUrlBounded -Serial $Serial -Url $record.deeplink -TimeoutSeconds 15
            if (-not $deeplinkStarted) { throw 'verified reactors-list deeplink launch failed or timed out' }
            Start-Sleep -Seconds 5
            Wait-FacebookUiReady $FacebookReadyTimeoutSeconds | Out-Null
        }
        else {
        # Prefer the existing exact-post opener: it resolves a known post URL when
        # possible, otherwise uses Facebook's own search UI, and verifies only
        # traffic generated after the target-opening action.  Composite permalink
        # remains a bounded last resort because Facebook 555 can misroute it.
        $targetOpenOutput = Join-Path $RunDir ("fallback-target-open{0}" -f $OutputSuffix)
        # Never synthesize /<author>/posts/<id>. Facebook 555 may accept such a
        # URL yet route to another story. Reuse only a URL actually observed on
        # this exact target and only when it has a post-specific token.
        $targetPostUrl = ''
        $targetUrlProperty = $target.PSObject.Properties['url']
        if ($targetUrlProperty) { $targetPostUrl = [string]$targetUrlProperty.Value }
        if (-not (Test-TargetSpecificPostUrl $targetPostUrl)) { $targetPostUrl = '' }
        $targetOpenArgs = @(
            '-Serial', $Serial,
            '-ProxyPort', [string]$ProxyPort,
            '-PostId', $record.post_id,
            '-CaptureJsonl', $Jsonl,
            '-OutputDir', $targetOpenOutput,
            '-WaitSeconds', [string]$TargetOpenWaitSeconds,
            '-CaptureMinLine', [string]([int]$record.fallback_start_line + 1)
        )
        if ($targetPostUrl) { $targetOpenArgs += @('-PostUrl', $targetPostUrl) }
        if ($EnableFridaSslUnpin) { $targetOpenArgs += '-KeepFacebookProcess' }
        if ($DisableAdaptiveTargetWait) { $targetOpenArgs += '-DisableAdaptiveTargetWait' }
        if ($EnableAdaptiveTargetWait) { $targetOpenArgs += '-EnableAdaptiveTargetWait' }
        if ($OpenShareList -and -not $OpenReactionList) { $targetOpenArgs += '-RequireFreshUi' }
        $record.target_open_exit_code = Invoke-CaptureChildPowerShell (Join-Path $Base 'open_facebook_post_readonly.ps1') $targetOpenArgs
        $targetOpenDiagnosticPath = Join-Path $targetOpenOutput 'target-open-diagnostics.json'
        $record.target_open_diagnostics = "fallback-target-open$OutputSuffix/target-open-diagnostics.json"
        if (Test-Path $targetOpenDiagnosticPath) {
            try {
                $targetOpenDiagnostic = Get-Content -Raw -Encoding UTF8 $targetOpenDiagnosticPath | ConvertFrom-Json
                $record.target_open_method = [string]$targetOpenDiagnostic.method
                $record.target_open_verified = [bool]$targetOpenDiagnostic.verified
            }
            catch {
                Write-Warning "[fallback] target-open diagnostics could not be parsed: $($_.Exception.Message)"
            }
        }

        if ($record.target_open_exit_code -eq 0 -and $record.target_open_verified) {
            Write-Host "[fallback] exact target-open verified via $($record.target_open_method); reusing current post surface."
            Wait-FacebookUiSettledInPlace ([Math]::Max(8, $FacebookReadyTimeoutSeconds)) | Out-Null
        }
        else {
            Write-Warning "[fallback] exact target-open was not verified (exit=$($record.target_open_exit_code) method=$($record.target_open_method)); trying bounded composite permalink fallback."
            $record.composite_deeplink_attempted = $true
            if (-not $EnableFridaSslUnpin) {
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'am', 'force-stop', 'com.facebook.katana') -TimeoutSeconds 10
            }
            $deeplinkStarted = Start-FacebookUrlBounded -Serial $Serial -Url $record.deeplink -TimeoutSeconds 15
            if (-not $deeplinkStarted) { throw 'native permalink deeplink launch failed or timed out' }
            Start-Sleep -Seconds 6
            Wait-FacebookUiReady $FacebookReadyTimeoutSeconds | Out-Null
        }

            # Target-opening traffic must never count as family evidence.  The family
            # window starts immediately before the Share/Reaction explorer.
            $record.family_capture_start_line = Get-CaptureJsonlLineCount
        }

        $fallbackOutput = Join-Path $RunDir ("readonly-ui{0}-fallback" -f $OutputSuffix)
        $fallbackArgs = @(
            '-Serial', $Serial,
            '-ProxyPort', [string]$ProxyPort,
            '-WaitSeconds', [string]$ReadOnlyWaitSeconds,
            '-ShareScrollCount', [string]$ShareScrollCount,
            '-ReactionScrollCount', [string]$ReactionScrollCount,
            '-ReactionTabLimit', [string]$ReactionTabLimit,
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
            '-ReactionNativeScrollPort', [string]$ReactionNativeScrollPort,
            '-ReactionUiDumpEveryN', [string]$ReactionUiDumpEveryN,
            '-ReactionMinCycleMs', [string]$ReactionMinCycleMs,
            '-ReactionInitialPaginationCooldownSeconds', [string]$ReactionInitialPaginationCooldownSeconds,
            '-ReactionRateLimitBackoffSeconds', [string]$ReactionRateLimitBackoffSeconds,
            '-ReactionRateLimitMaxConsecutive', [string]$ReactionRateLimitMaxConsecutive,
            '-ReactionRateLimitMaxBackoffSeconds', [string]$ReactionRateLimitMaxBackoffSeconds,
            '-ReactionNoGrowthUiDumpEveryN', [string]$ReactionNoGrowthUiDumpEveryN,
            '-EntryScanScrollCount', [string]$EntryScanScrollCount,
            '-UiDumpTimeoutSeconds', [string]$UiDumpTimeoutSeconds,
            '-UiDumpRetryCount', [string]$UiDumpRetryCount,
            '-CaptureJsonl', $Jsonl,
            '-ExpectedSourcePostId', $record.post_id,
            '-CaptureMinLine', [string]([int]$record.family_capture_start_line + 1),
            '-OutputDir', $fallbackOutput
        )
        if ($record.reaction_eligible) { $fallbackArgs += '-OpenReactionList' }
        if ($record.share_eligible) { $fallbackArgs += '-OpenShareList' }
        if ($AutoScreenThenDeep -and $record.reaction_eligible) { $fallbackArgs += '-AutoScreenThenDeep' }

        $fallbackExplorerExit = Invoke-CaptureChildPowerShell (Join-Path $Base 'explore_facebook_readonly.ps1') $fallbackArgs
        $record.explorer_exit_code = $fallbackExplorerExit
        $record.fallback_diagnostics = "readonly-ui$OutputSuffix-fallback/exploration-diagnostics.json"
        if ($fallbackExplorerExit -ne 0) {
            $record.error = "fallback-explorer-exit-$fallbackExplorerExit"
        }
        else {
            $fallbackValidationDir = Join-Path $RunDir ("fallback-validation-analysis{0}" -f $OutputSuffix)
            $fallbackMinLine = [int]$record.family_capture_start_line + 1
            $fallbackValidation = Invoke-CapturePythonHidden @(
                (Join-Path $Base 'analyze_facebook_capture.py'),
                '--input', $Jsonl,
                '--out-dir', $fallbackValidationDir,
                '--min-line', [string]$fallbackMinLine,
                '--expected-post-id', [string]$record.post_id
            ) -QuietStdout
            if ($fallbackValidation.ExitCode -ne 0) { throw "fallback validation analyzer exited with code $($fallbackValidation.ExitCode)" }
            $validationReportPath = Join-Path $fallbackValidationDir 'facebook-sample-patterns.json'
            if (-not (Test-Path $validationReportPath)) { throw 'fallback validation report was not created' }
            $validation = Get-Content -Raw -Encoding UTF8 $validationReportPath | ConvertFrom-Json
            $record.observed_reaction_source_post_ids = @($validation.source_post_ids_by_family.reactions | Where-Object { $_ } | ForEach-Object { [string]$_ })
            $record.observed_share_source_post_ids = @($validation.source_post_ids_by_family.shares | Where-Object { $_ } | ForEach-Object { [string]$_ })

            if ($record.reaction_eligible) {
                if ($record.observed_reaction_source_post_ids.Count -eq 0) {
                    $record.reaction_target_validation_status = 'no-family-query'
                }
                elseif ($record.observed_reaction_source_post_ids.Count -eq 1 -and $record.observed_reaction_source_post_ids[0] -eq $record.post_id) {
                    $record.reaction_target_validation_status = 'matched'
                }
                else {
                    $record.reaction_target_validation_status = 'mismatch'
                }
            }
            if ($record.share_eligible) {
                if ($record.observed_share_source_post_ids.Count -eq 0) {
                    $record.share_target_validation_status = 'no-family-query'
                }
                elseif ($record.observed_share_source_post_ids.Count -eq 1 -and $record.observed_share_source_post_ids[0] -eq $record.post_id) {
                    $record.share_target_validation_status = 'matched'
                }
                else {
                    $record.share_target_validation_status = 'mismatch'
                }
            }

            $eligibleStatuses = @()
            if ($record.reaction_eligible) { $eligibleStatuses += [string]$record.reaction_target_validation_status }
            if ($record.share_eligible) { $eligibleStatuses += [string]$record.share_target_validation_status }
            if ($eligibleStatuses -contains 'mismatch') {
                $record.target_validation_status = 'mismatch'
                $record.unavailable_reason = 'fallback-target-mismatch'
                Write-Warning "[fallback] target mismatch expected=$($record.post_id) reaction_status=$($record.reaction_target_validation_status) reaction=[$($record.observed_reaction_source_post_ids -join ',')] share_status=$($record.share_target_validation_status) share=[$($record.observed_share_source_post_ids -join ',')]"
            }
            elseif ($eligibleStatuses -contains 'matched') {
                $record.target_validation_status = 'matched'
            }
            else {
                $record.target_validation_status = 'no-family-query'
            }
        }
    }
    catch {
        $record.error = $_.Exception.Message
        Write-Warning "[fallback] native permalink retry failed: $($record.error)"
    }
    finally {
        Write-NativePermalinkFallbackRecord $record
    }
}

function Invoke-CombinedSharePhase([string[]]$PrimaryExplorerArguments) {
    # These are local to this function and its fallback child scope. Never pass
    # both switches to the explorer: Share-only enables fresh UI/photo routing.
    $OpenReactionList = $false
    $OpenShareList = $true
    $expectedPostId = [string]$script:PrimaryVerifiedPostId
    if ([string]::IsNullOrWhiteSpace($expectedPostId)) {
        throw 'Combined Share phase requires the exact Reaction target post ID.'
    }
    if ($script:ResolvedCaptureSourcePostId -and [string]$script:ResolvedCaptureSourcePostId -ne $expectedPostId) {
        throw 'Combined Reaction phase source disagrees with the locked target; Share is not started.'
    }

    # Only a strictly bound pfbid alias needs a numeric reopen route. Keep the
    # original URL and final provenance untouched, including group/Reel routes.
    $shareReopenUrl = [string]$PostUrl
    $strictPfbidReopen = $false
    $reopenUri = $null
    if ([Uri]::TryCreate($shareReopenUrl, [UriKind]::Absolute, [ref]$reopenUri) -and
        $reopenUri.Scheme -match '^https?$' -and
        $reopenUri.Host -match '^(?:(?:www|m)\.)?facebook\.com$' -and
        $reopenUri.AbsolutePath -match '^/[^/]+/posts/pfbid[A-Za-z0-9]+/?$') {
        $expectedAuthorId = [string]$script:PrimaryVerifiedAuthorId
        if ($expectedPostId -notmatch '^[0-9]{1,32}$' -or $expectedAuthorId -notmatch '^[0-9]{1,32}$') {
            throw 'Combined pfbid Share reopen requires a verified numeric post and author.'
        }
        $strictPfbidReopen = $true
        $shareReopenUrl = "https://www.facebook.com/$expectedAuthorId/posts/$expectedPostId"
    }

    # Explorer exit is not Reaction completion. Reuse the existing analyzer/API
    # acceptance contract before any Share navigation, including explicit bounded
    # samples and proven empty/visibility results; never infer success from UI.
    $reactionAnalysisArgs = @(
        (Join-Path $Base 'analyze_facebook_capture.py'), '--input', $Jsonl,
        '--out-dir', $AnalysisDir, '--expected-post-id', $expectedPostId,
        '--focus-source-post-id', $expectedPostId
    )
    if ($script:ResolvedTargetUrl) { $reactionAnalysisArgs += @('--expected-url', $script:ResolvedTargetUrl) }
    $reactionAnalysis = Invoke-CapturePythonHidden -Arguments $reactionAnalysisArgs
    if ($reactionAnalysis.ExitCode -ne 0) { throw 'Combined Reaction analysis failed; Share is not started.' }
    $reactionGateArgs = @((Join-Path $Base 'tools/verify_share_result.py'), '--run-dir', $RunDir, '--reaction-phase')
    if ($ReactionAbsoluteCap -gt 0) {
        $reactionGateArgs += @('--reaction-scroll-limit', [string]([Math]::Max($ReactionScrollCount, $ReactionAbsoluteCap)))
    }
    $reactionGate = Invoke-CapturePythonHidden -Arguments $reactionGateArgs
    if ($reactionGate.ExitCode -ne 0) { throw 'Combined Reaction result rejected; Share is not started.' }

    $targetOutput = Join-Path $RunDir 'target-open-share'
    $targetBoundary = Get-CaptureJsonlLineCount
    $targetArgs = @(
        '-Serial', $Serial, '-ProxyPort', [string]$ProxyPort,
        '-PostId', $expectedPostId, '-CaptureJsonl', $Jsonl,
        '-OutputDir', $targetOutput, '-WaitSeconds', [string]$TargetOpenWaitSeconds,
        '-CaptureMinLine', [string]([int]$targetBoundary + 1), '-RequireFreshUi', '-PreserveGroupInlineShareRoute'
    )
    if ($shareReopenUrl) { $targetArgs += @('-PostUrl', $shareReopenUrl) }
    if ($EnableFridaSslUnpin) { $targetArgs += '-KeepFacebookProcess' }
    if ($DisableAdaptiveTargetWait) { $targetArgs += '-DisableAdaptiveTargetWait' }
    if ($EnableAdaptiveTargetWait) { $targetArgs += '-EnableAdaptiveTargetWait' }
    Write-Host "[combined] Reaction phase finished; strictly reopening post=$expectedPostId for Share."
    $targetExit = Invoke-CaptureChildPowerShell (Join-Path $Base 'open_facebook_post_readonly.ps1') $targetArgs
    if ($targetExit -ne 0) { throw "Combined Share target reopen failed with exit code $targetExit" }
    $targetDiagnosticPath = Join-Path $targetOutput 'target-open-diagnostics.json'
    if (-not (Test-Path $targetDiagnosticPath)) { throw 'Combined Share target reopen produced no diagnostics.' }
    $targetDiagnostic = Get-Content -Raw -Encoding UTF8 $targetDiagnosticPath | ConvertFrom-Json
    if (-not [bool]$targetDiagnostic.verified -or [string]$targetDiagnostic.resolved_post_id -ne $expectedPostId) {
        throw 'Combined Share target reopen did not prove the same exact post.'
    }
    if ($strictPfbidReopen) {
        $freshReportPath = Join-Path $targetOutput 'verification-analysis/facebook-sample-patterns.json'
        if (-not (Test-Path -LiteralPath $freshReportPath)) { throw 'Combined pfbid reopen has no fresh identity report.' }
        $freshReport = Get-Content -Raw -Encoding UTF8 -LiteralPath $freshReportPath | ConvertFrom-Json
        $freshProof = $freshReport.target_verification
        $freshMinLine = [long]$targetDiagnostic.verification_min_line
        if ($targetDiagnostic.verified -isnot [bool] -or -not $targetDiagnostic.verified -or
            [string]$targetDiagnostic.resolved_author_id -ne $expectedAuthorId -or
            $freshMinLine -lt ([long]$targetBoundary + 1) -or
            [long]$freshReport.min_line -ne $freshMinLine -or
            [long]$freshProof.matched_line -lt $freshMinLine -or
            [string]$freshProof.status -ne 'matched' -or
            [string]$freshProof.matched_post_id -ne $expectedPostId -or
            [string]$freshProof.expected_url -ne $shareReopenUrl) {
            throw 'Combined pfbid reopen lacks the same fresh post and author proof.'
        }
        # Analyzer may fill a missing author from the numeric URL. Require an
        # actual nonempty author in every exact-post row from this fresh window.
        $actualRows = @($freshReport.posts | Where-Object { [string]$_.post_id -eq $expectedPostId })
        $missingAuthorRows = @($actualRows | Where-Object { [string]::IsNullOrWhiteSpace([string]$_.author_id) })
        $actualAuthorIds = @($actualRows | ForEach-Object { [string]$_.author_id } | Sort-Object -Unique)
        if ($actualRows.Count -eq 0 -or $missingAuthorRows.Count -gt 0 -or
            $actualAuthorIds.Count -ne 1 -or $actualAuthorIds[0] -ne $expectedAuthorId) {
            throw 'Combined pfbid reopen actual Story author is missing or conflicts with the locked target.'
        }
    }
    $null = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort
    Wait-FacebookUiSettledInPlace ([Math]::Max(8, $FacebookReadyTimeoutSeconds)) | Out-Null
    # Target-open traffic and earlier Reaction traffic never satisfy this window.
    $familyBoundary = Get-CaptureJsonlLineCount
    $shareArgs = @()
    for ($i = 0; $i -lt $PrimaryExplorerArguments.Count; $i++) {
        $argument = $PrimaryExplorerArguments[$i]
        if ($argument -in @('-OutputDir', '-ExpectedSourcePostId', '-CaptureMinLine')) { $i++; continue }
        if ($argument -in @('-OpenReactionList', '-OpenShareList', '-AllowSourceDiscovery', '-OpenComments', '-ReactionProbeOnly')) { continue }
        $shareArgs += $argument
    }
    $shareArgs += @(
        '-OutputDir', (Join-Path $RunDir 'readonly-ui-share'),
        '-ExpectedSourcePostId', $expectedPostId,
        '-CaptureMinLine', [string]([int]$familyBoundary + 1), '-OpenShareList',
        '-TargetOpenDiagnosticPath', $targetDiagnosticPath
    )
    Write-Host "[combined] Share-only explorer bound to post=$expectedPostId family_boundary=$([int]$familyBoundary + 1)"
    $shareExit = Invoke-CaptureChildPowerShell (Join-Path $Base 'explore_facebook_readonly.ps1') $shareArgs
    if ($shareExit -ne 0) { throw "Combined Share explorer failed with exit code $shareExit" }
    if (-not $ReactionProbeOnly) {
        Invoke-NativePermalinkFallback -ReadonlyDirectoryName 'readonly-ui-share' -OutputSuffix '-share' -ExpectedStagePostId $expectedPostId
    }
}

function Invoke-AdbTimedProcess([string[]]$Arguments, [int]$TimeoutSeconds) {
    $success = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments $Arguments -TimeoutSeconds ([Math]::Max(1, $TimeoutSeconds))
    return [pscustomobject]@{
        Success = [bool]$success
        TimedOut = (-not [bool]$success)
        ExitCode = if ($success) { 0 } else { $null }
    }
}

function Dump-Ui([string]$Path) {
    $snapshot = Invoke-FacebookUiSnapshot `
        -Serial $Serial `
        -LocalPath $Path `
        -RemotePath '/sdcard/codex-facebook-ui.xml' `
        -LegacyTimeoutSeconds $UiDumpTimeoutSeconds `
        -LegacyRetryCount $UiDumpRetryCount `
        -FastTimeoutSeconds ([Math]::Max(8, $UiDumpTimeoutSeconds))
    if (-not $snapshot.Success) {
        throw "UI dump failed via legacy and instrumentation fallback: $Path ($($snapshot.Error))"
    }
    if ($snapshot.Source -eq 'instrumentation') {
        Write-Warning "[ui] legacy dump unavailable; instrumentation fallback wrote $Path (package=$($snapshot.RootPackage) nodes=$($snapshot.NodeCount))."
    }
}

function Get-FacebookUiRecoveryKind {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw,
        [AllowEmptyString()][string]$SurfacePackage = '',
        [int]$LabelCount = 0
    )
    if ([string]::IsNullOrWhiteSpace($Raw)) { return 'ui-empty' }
    # WebView errors are transient navigation failures, not target evidence.
    # Keep this deliberately narrower than the privacy/visibility messages;
    # those must remain a normal strict failure instead of being retried.
    $webErrorPattern = '(?i)(?:webpage\s+(?:isn''t|is\s+not|cannot|can''t)\s+(?:open|load|display)|(?:can''t|cannot)\s+load\s+(?:this\s+)?page|error\s+(?:loading|opening)\s+(?:this\s+)?page|no\s+internet\s+connection|this\s+page\s+isn''t\s+available|content\s+isn''t\s+available\s+right\s+now|网页(?:无法|不能|打不开)(?:打开|加载|显示)|无法打开网页|网页加载失败|内容目前无法显示)'
    if ($Raw -match $webErrorPattern) { return 'web-error' }
    if ($SurfacePackage -like 'com.facebook.katana*') {
        $nodeCount = [regex]::Matches($Raw, '<node\b').Count
        $hasWebView = $Raw -match '(?i)class="(?:android\.webkit\.)?WebView"'
        # A Facebook-owned WebView with no meaningful labels, or a tiny
        # label-free Facebook hierarchy, is the blank/placeholder state seen
        # after a failed VIEW intent. Require two samples in the caller before
        # restarting so a normal first-frame transition is not treated as an
        # error.
        if (($hasWebView -and $LabelCount -lt 3) -or ($LabelCount -eq 0 -and $nodeCount -le 20)) {
            return 'empty-placeholder'
        }
    }
    return ''
}

function Write-FacebookUiRecoveryRecord {
    param(
        [Parameter(Mandatory = $true)][string]$Reason,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$Error = ''
    )
    $recordPath = Join-Path $RunDir 'facebook-ui-recovery.json'
    [ordered]@{
        serial = $Serial
        reason = $Reason
        status = $Status
        error = $Error
        account_data_touched = $false
        action = 'am-force-stop-and-launcher-start'
        timestamp = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath $recordPath
}

function Restart-FacebookForUiRecovery([string]$Reason) {
    Write-Warning "[ui] detected $Reason; force-stopping and relaunching Facebook without clearing account data."
    try {
        if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','am','force-stop','com.facebook.katana') -TimeoutSeconds 10)) {
            Write-FacebookUiRecoveryRecord -Reason $Reason -Status 'force-stop-failed'
            return $false
        }
        Start-Sleep -Seconds 1
        if (-not (Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15)) {
            Write-FacebookUiRecoveryRecord -Reason $Reason -Status 'launch-failed'
            return $false
        }
        Start-Sleep -Seconds 2
        Write-FacebookUiRecoveryRecord -Reason $Reason -Status 'restarted'
        return $true
    }
    catch {
        Write-FacebookUiRecoveryRecord -Reason $Reason -Status 'recovery-error' -Error $_.Exception.Message
        return $false
    }
}

function Get-TargetOpenRecoveryRequest {
    $path = Join-Path $RunDir 'target-open\target-open-recovery-request.json'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $request = Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json
        if ([string]$request.serial -ne [string]$Serial) { return $null }
        if ([string]$request.reason -notin @('web-error','empty-placeholder','ui-empty')) { return $null }
        return $request
    }
    catch {
        Write-Warning "[target-open] recovery request could not be read; retaining strict failure: $($_.Exception.Message)"
        return $null
    }
}

function Invoke-TargetOpenUiRecovery($Request) {
    $reason = [string]$Request.reason
    if (-not (Restart-FacebookForUiRecovery $reason)) { return $false }
    try {
        if ($EnableFridaSslUnpin) {
            # The target child is allowed to stop the pre-injected process only
            # through this parent-owned path; restore the hook before retrying.
            Start-FacebookWithFridaSslUnpin
        }
        else {
            Wait-FacebookUiReady ([Math]::Max(8, $FacebookReadyTimeoutSeconds)) | Out-Null
        }
        Write-Host "[target-open] recovered Facebook UI state=$reason; retrying strict target navigation."
        return $true
    }
    catch {
        Write-Warning "[target-open] Facebook UI recovery could not reattach/readiness-check: $($_.Exception.Message)"
        return $false
    }
}

function Wait-FacebookUiReady([int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds([Math]::Max(4, $TimeoutSeconds))
    $attempt = 0
    $blockingRecoveries = 0
    $placeholderSamples = 0
    $placeholderRecoveries = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        $path = Join-Path $RunDir ("ui-ready-{0:00}.xml" -f $attempt)
        try {
            Dump-Ui $path
            $raw = [IO.File]::ReadAllText($path)
            $surfacePackage = if (Get-Command Get-FacebookUiSurfacePackage -ErrorAction SilentlyContinue) { Get-FacebookUiSurfacePackage -Raw $raw } else { '' }
            if ($surfacePackage -eq 'com.google.android.permissioncontroller') {
                Write-Warning '[ui] Android Permission Controller is covering Facebook; dismissing it with Back, relaunching Facebook, and retrying readiness.'
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4') -TimeoutSeconds 5
                Start-Sleep -Seconds 1
                $null = Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15
                Start-Sleep -Seconds 3
                continue
            }
            $blockingState = if (Get-Command Get-FacebookUiBlockingState -ErrorAction SilentlyContinue) { Get-FacebookUiBlockingState -Raw $raw } else { '' }
            if ($blockingState -in @('system-ui-not-responding','app-not-responding')) {
                Write-Warning "[ui] Android ANR ($blockingState) is covering Facebook; choosing Wait and refreshing readiness without restarting Facebook."
                if (Dismiss-FacebookAnrWait -Serial $Serial -Raw $raw) {
                    Start-Sleep -Seconds 3
                    Dump-Ui $path
                    $raw = [IO.File]::ReadAllText($path)
                    $surfacePackage = Get-FacebookUiSurfacePackage -Raw $raw
                    $blockingState = Get-FacebookUiBlockingState -Raw $raw
                }
            }
            if ($blockingState -in @('system-ui-not-responding','app-not-responding')) {
                Write-Warning "[ui] Android ANR ($blockingState) remains visible after choosing Wait."
                continue
            }
            if ($surfacePackage -and $surfacePackage -ne 'com.facebook.katana') {
                Write-Warning "[ui] readiness saw non-Facebook surface package=$surfacePackage; relaunching Facebook."
                if ($blockingRecoveries -lt 2) {
                    $blockingRecoveries++
                    $null = Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15
                    Start-Sleep -Seconds 3
                }
                continue
            }
            $labelCount = [regex]::Matches($raw, '(?:text|content-desc)="[^"]+"').Count
            $recoveryKind = Get-FacebookUiRecoveryKind -Raw $raw -SurfacePackage $surfacePackage -LabelCount $labelCount
            if ($recoveryKind) {
                if ($recoveryKind -eq 'web-error') { $placeholderSamples = 2 }
                else { $placeholderSamples++ }
                if ($placeholderSamples -ge 2) {
                    if ($placeholderRecoveries -lt 1) {
                        $placeholderRecoveries++
                        $placeholderSamples = 0
                        if (Restart-FacebookForUiRecovery $recoveryKind) { continue }
                    }
                    Write-Warning "[ui] Facebook $recoveryKind remained visible after bounded recovery."
                }
            }
            else {
                $placeholderSamples = 0
            }
            if ($recoveryKind) {
                # A Facebook error/placeholder surface is never content-ready,
                # even when it happens to expose a few labels. Keep the strict
                # readiness gate closed after the bounded recovery is exhausted.
                Start-Sleep -Seconds 2
                continue
            }
            if ($blockingState) {
                Write-Warning "[ui] Facebook readiness saw $blockingState instead of app content."
                if ($blockingRecoveries -lt 2) {
                    $blockingRecoveries++
                    $null = Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15 -ForceStop
                    Start-Sleep -Seconds 3
                }
                continue
            }
            if ($surfacePackage -eq 'com.facebook.katana' -and $labelCount -ge 3) {
                Write-Host "[ui] Facebook content ready after $attempt check(s), labels=$labelCount"
                return $true
            }
        }
        catch {
            Write-Warning "[ui] readiness check $attempt failed: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 2
    }
    throw "Facebook UI did not become content-ready within $TimeoutSeconds second(s); refusing to scan a blank/placeholder page."
}

function Wait-FacebookUiSettledInPlace([int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds([Math]::Max(4, $TimeoutSeconds))
    $attempt = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        $path = Join-Path $RunDir ("ui-settle-{0:00}.xml" -f $attempt)
        try {
            Dump-Ui $path
            $raw = [IO.File]::ReadAllText($path)
            $surfacePackage = if (Get-Command Get-FacebookUiSurfacePackage -ErrorAction SilentlyContinue) { Get-FacebookUiSurfacePackage -Raw $raw } else { '' }
            $blockingState = if (Get-Command Get-FacebookUiBlockingState -ErrorAction SilentlyContinue) { Get-FacebookUiBlockingState -Raw $raw } else { '' }
            if ($blockingState -in @('system-ui-not-responding','app-not-responding')) {
                Write-Warning "[ui] Android ANR ($blockingState) appeared after feed positioning; choosing Wait without relaunching Facebook."
                if (Dismiss-FacebookAnrWait -Serial $Serial -Raw $raw) {
                    Start-Sleep -Seconds 3
                    Dump-Ui $path
                    $raw = [IO.File]::ReadAllText($path)
                    $surfacePackage = Get-FacebookUiSurfacePackage -Raw $raw
                    $blockingState = Get-FacebookUiBlockingState -Raw $raw
                }
            }
            if ($blockingState) {
                throw "Facebook became blocked by $blockingState after feed positioning; refusing to relaunch because that would change the selected candidate."
            }
            if ($surfacePackage -and $surfacePackage -ne 'com.facebook.katana') {
                throw "Non-Facebook surface package=$surfacePackage appeared after feed positioning; refusing to relaunch because that would change the selected candidate."
            }
            $labelCount = [regex]::Matches($raw, '(?:text|content-desc)="[^"]+"').Count
            if ($surfacePackage -eq 'com.facebook.katana' -and $labelCount -ge 3) {
                Write-Host "[ui] Facebook content settled in place after $attempt check(s), labels=$labelCount"
                return $true
            }
        }
        catch {
            if ($_.Exception.Message -match 'refusing to relaunch') { throw }
            Write-Warning "[ui] in-place settle check $attempt failed: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 2
    }
    throw "Facebook remained blank/placeholder for $TimeoutSeconds second(s) after feed positioning; candidate aborted without relaunch."
}

function Get-CapturePortListenerPids([int]$Port) {
    $pids = @()
    foreach ($line in (Get-FacebookNetstatLines | Select-String 'LISTENING' | Select-String (":" + $Port + "\s"))) {
        $match = [regex]::Match([string]$line, '\s([0-9]+)\s*$')
        if ($match.Success) { $pids += [int]$match.Groups[1].Value }
    }
    return @($pids | Select-Object -Unique)
}

function Clear-StaleProjectCaptureListener([int]$Port) {
    # A port/name match does not establish ownership or staleness. Refuse collisions.
    return @(Get-CapturePortListenerPids $Port)
}

function Start-FacebookRawNetworkCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Interface
    )
    if ($Interface -notmatch '^[A-Za-z0-9_.:-]+$') { throw "Unsupported raw network interface: $Interface" }
    $rootProbe = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','su','0','id','-u') -TimeoutSeconds 8
    if (-not $rootProbe.Success -or ([string]$rootProbe.Stdout).Trim() -ne '0') {
        throw 'Android su 0 is unavailable; tcpdump cannot run without restarting the ADB transport.'
    }
    $probe = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','su','0','test','-x','/system/bin/tcpdump') -TimeoutSeconds 8
    if (-not $probe.Success) {
        throw '/system/bin/tcpdump is unavailable in the Android emulator; raw network capture cannot start.'
    }
    $remotePath = "/data/local/tmp/facebook-capture-$PID-$(Get-Date -Format 'yyyyMMdd-HHmmss').pcap"
    $command = "su 0 /system/bin/tcpdump -i $Interface -s 0 -U -w $remotePath tcp port $Port >/dev/null 2>&1 & echo `$!"
    $start = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell',$command) -TimeoutSeconds 10
    if (-not $start.Success) { throw "tcpdump start failed: $($start.Stderr)" }
    $pidMatches = [regex]::Matches([string]$start.Stdout, '(?m)^\s*(\d+)\s*$')
    if ($pidMatches.Count -lt 1) { throw "tcpdump did not return a process id: $($start.Stdout)" }
    $tcpdumpPid = [int]$pidMatches[$pidMatches.Count - 1].Groups[1].Value
    Start-Sleep -Milliseconds 500
    $alive = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','su','0','kill','-0',[string]$tcpdumpPid) -TimeoutSeconds 6
    if (-not $alive.Success) {
        $null = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','su','0','rm','-f',$remotePath) -TimeoutSeconds 6
        throw "tcpdump exited before capture began: $($alive.Stderr)"
    }
    Write-Host "[pcap] raw network capture started pid=$tcpdumpPid interface=$Interface filter=tcp/$Port"
    return [pscustomobject]@{
        Pid = $tcpdumpPid
        RemotePath = $remotePath
        StartedAt = (Get-Date).ToUniversalTime().ToString('o')
    }
}

function Stop-FacebookRawNetworkCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)]$Capture,
        [Parameter(Mandatory = $true)][string]$LocalPath,
        [Parameter(Mandatory = $true)][string]$MetadataPath
    )
    if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 40 -RecoverOffline -AllowServerRestart)) {
        throw "ADB unavailable before tcpdump stop. Remote evidence was preserved at $($Capture.RemotePath)"
    }
    $signal = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','su','0','kill','-2',[string]$Capture.Pid) -TimeoutSeconds 6
    if (-not $signal.Success -and [string]$signal.Stderr -notmatch '(?i)no such process') {
        Write-Warning "[pcap] tcpdump SIGINT returned: $($signal.Stderr)"
    }
    for ($waitAttempt = 1; $waitAttempt -le 10; $waitAttempt++) {
        Start-Sleep -Milliseconds 500
        $alive = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','su','0','kill','-0',[string]$Capture.Pid) -TimeoutSeconds 4
        if (-not $alive.Success) { break }
    }
    if ($alive.Success) {
        Write-Warning '[pcap] tcpdump did not exit after SIGINT; sending SIGTERM before pull.'
        $null = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','su','0','kill','-15',[string]$Capture.Pid) -TimeoutSeconds 6
        Start-Sleep -Seconds 1
    }

    $pullError = 'adb pull was not attempted.'
    $pullSucceeded = $false
    for ($pullAttempt = 1; $pullAttempt -le 2; $pullAttempt++) {
        if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 20 -RecoverOffline -AllowServerRestart)) {
            $pullError = "ADB unavailable before PCAP pull attempt $pullAttempt"
            continue
        }
        $pull = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('pull',[string]$Capture.RemotePath,$LocalPath) -TimeoutSeconds 90
        if (-not $pull.Success) {
            $pullError = "adb pull attempt $pullAttempt failed: $($pull.Stderr)"
            Start-Sleep -Seconds 2
            continue
        }
        if (-not (Test-Path -LiteralPath $LocalPath)) {
            $pullError = 'adb pull reported success but the local PCAP is missing.'
            continue
        }
        if ((Get-Item -LiteralPath $LocalPath).Length -le 24) {
            $pullError = 'raw network PCAP contains no captured packets.'
            continue
        }
        $pullSucceeded = $true
        $pullError = $null
        break
    }
    if (-not $pullSucceeded) {
        throw "$pullError Remote evidence was preserved at $($Capture.RemotePath)"
    }
    $cleanup = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','su','0','rm','-f',[string]$Capture.RemotePath) -TimeoutSeconds 8
    if (-not $cleanup.Success) { Write-Warning "[pcap] local file is safe, but remote cleanup failed: $($cleanup.Stderr)" }
    $file = Get-Item -LiteralPath $LocalPath
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    $hashStream = [IO.File]::OpenRead($LocalPath)
    try {
        $hashBytes = $hashAlgorithm.ComputeHash($hashStream)
    }
    finally {
        $hashStream.Dispose()
        $hashAlgorithm.Dispose()
    }
    $hashHex = [BitConverter]::ToString($hashBytes).Replace('-', '')
    [ordered]@{
        version = 1
        status = 'ready'
        encrypted = $true
        started_at = [string]$Capture.StartedAt
        stopped_at = (Get-Date).ToUniversalTime().ToString('o')
        file = [IO.Path]::GetFileName($LocalPath)
        size_bytes = [int64]$file.Length
        sha256 = $hashHex
    } | ConvertTo-Json | Set-Content -LiteralPath $MetadataPath -Encoding UTF8
    Write-Host "[pcap] raw network capture saved: $LocalPath bytes=$($file.Length)"
}

Write-CaptureTiming "runner-initialization" $preparationClock
$preparationClock.Restart()
try {
    # Capture owns the emulator ADB channel. Pause the background watchdog before
    # the first capture-side ADB operation so an already-running watchdog probe
    # cannot overlap uiautomator, proxy changes, or app launches.
    $transportWatchStoppedCount = Stop-FacebookTransportWatch -Serial $Serial -BasePath $Base
    if ($transportWatchStoppedCount -gt 0) {
        Write-Host "[transport] paused watchdog for exclusive capture ADB ownership (stopped=$transportWatchStoppedCount)"
    }
    if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 20 -RecoverOffline -AllowServerRestart)) { throw "ADB device is not ready: $Serial" }
    if (-not (Ensure-FacebookFrameworkServicesReady -Serial $Serial -TimeoutSeconds 45)) { throw "Android framework services are not ready: $Serial" }
    if (-not (Ensure-FacebookUiDumpHelper -Serial $Serial -BasePath $Base)) {
        Write-Warning '[ui] instrumentation fallback helper is unavailable; legacy UIAutomator remains the only dump path for this run.'
    }
    Write-CaptureTiming "device-preparation" $preparationClock
    $preparationClock.Restart()
    if ([string]::IsNullOrWhiteSpace($MitmdumpExe) -or -not (Test-Path $MitmdumpExe)) { throw 'mitmdump executable is unavailable.' }
    if ($EnableRawNetworkCapture) {
        $rawNetworkCapture = Start-FacebookRawNetworkCapture -Serial $Serial -Port $ProxyPort -Interface $RawNetworkInterface
    }
    $null = Ensure-FacebookNormalNetwork
    $defaultNetwork = Ensure-FacebookAndroidNetwork -Serial $Serial
    $reverseList = Ensure-FacebookReverseMappings -Serial $Serial -Ports @(Get-FacebookNormalReversePorts -CapturePort $ProxyPort)
    $normalProxy = Set-FacebookNormalProxy -Serial $Serial
    Write-Host "[transport] ready default_network=$defaultNetwork proxy=$normalProxy"
    Write-Host "[transport] reverse=$($reverseList -replace "`r?`n", '; ')"

    $env:FACEBOOK_CAPTURE_DIR = $RunDir
    $env:FACEBOOK_CAPTURE_MAX_FLOWS = [string]$MaxFlows
    $env:FACEBOOK_CAPTURE_MAX_BODY_BYTES = [string]$MaxBodyBytes
    $env:FACEBOOK_RAW_CAPTURE_FILE = $RawFlow
    $env:FACEBOOK_RAW_INDEX_FILE = $RawIndex
    $mitmArgs = @(
        "--quiet",
        "--listen-host", "127.0.0.1",
        "--listen-port", [string]$ProxyPort,
        "--mode", (Get-FacebookMitmMode),
        "-s", $Addon
    )
    if (-not $DisableRawHttpCapture) {
        if (-not (Test-Path $RawAddon)) { throw "Missing raw HTTP capture addon: $RawAddon" }
        $mitmArgs += @("-s", $RawAddon)
        Write-Host "[capture] raw HTTP flow layer enabled: $RawFlow"
    }
    else {
        Write-Host '[capture] raw HTTP flow layer disabled for this run'
    }
    $listeners = @(Clear-StaleProjectCaptureListener $ProxyPort)
    if ($listeners.Count -gt 0) {
        throw "Capture port $ProxyPort is already used by a non-project listener PID(s): $($listeners -join ',')"
    }

    $mitmStartError = $null
    for ($mitmAttempt = 1; $mitmAttempt -le 3; $mitmAttempt++) {
        $mitm = Start-Process -FilePath $MitmdumpExe -ArgumentList $mitmArgs -WorkingDirectory $RunDir -WindowStyle Hidden -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog -PassThru
        Start-Sleep -Seconds 3
        if (-not $mitm.HasExited) {
            $mitmStartError = $null
            break
        }
        $mitmStartError = "mitmdump exited early on attempt $mitmAttempt"
        Write-Warning "[transport] $mitmStartError; waiting for port $ProxyPort release before retry."
        Start-Sleep -Seconds 2
        $listeners = @(Clear-StaleProjectCaptureListener $ProxyPort)
        if ($listeners.Count -gt 0) {
            throw "Capture port $ProxyPort remains occupied by non-project listener PID(s): $($listeners -join ',')"
        }
    }
    if ($mitmStartError -or -not $mitm -or $mitm.HasExited) {
        throw "mitmdump could not stay running after 3 attempts; inspect $StderrLog"
    }

    Set-FacebookCaptureMarker -Serial $Serial -OwnerPid $PID -MitmPid $mitm.Id -Port $ProxyPort
    $captureProxy = Set-FacebookAndroidProxy -Serial $Serial -Port $ProxyPort
    Write-Host "[transport] capture proxy=$captureProxy"
    if ($EnableFridaSslUnpin) {
        Start-FacebookWithFridaSslUnpin
    }
    Write-CaptureTiming "network-capture-preparation" $preparationClock
    $captureDeadline = if ($DurationSeconds -gt 0) { (Get-Date).AddSeconds($DurationSeconds) } else { $null }
    if ($PostUrl -or $PostId) {
        $targetArgs = @(
            "-Serial", $Serial,
            "-ProxyPort", [string]$ProxyPort,
            "-CaptureJsonl", $Jsonl,
            "-OutputDir", (Join-Path $RunDir "target-open"),
            "-WaitSeconds", [string]$TargetOpenWaitSeconds
        )
        if ($PostUrl) { $targetArgs += @("-PostUrl", $PostUrl) }
        if ($PostId) { $targetArgs += @("-PostId", $PostId) }
        if ($EnableFridaSslUnpin) { $targetArgs += @('-KeepFacebookProcess') }
        if ($DisableAdaptiveTargetWait) { $targetArgs += @('-DisableAdaptiveTargetWait') }
        if ($EnableAdaptiveTargetWait) { $targetArgs += @('-EnableAdaptiveTargetWait') }
        if ($OpenShareList -and -not $OpenReactionList) { $targetArgs += @('-RequireFreshUi', '-PreserveGroupInlineShareRoute') }
        $targetOpenSucceeded = $false
        $targetSearchFallbackApplied = $false
        # Share-only jobs must stay bound to the supplied URL. Facebook search does
        # not resolve raw post IDs reliably, and blind Feed scrolling cannot prove
        # an arbitrary permalink. Retry the same URL once after bounded UI recovery.
        $shareDirectUrlOnly = ($OpenShareList -and -not $OpenReactionList)
        $targetOpenAttemptLimit = if ($shareDirectUrlOnly) { 2 } else { 3 }
        for ($targetOpenAttempt = 1; $targetOpenAttempt -le $targetOpenAttemptLimit; $targetOpenAttempt++) {
            Write-Host "[target-open] strict attempt $targetOpenAttempt/$targetOpenAttemptLimit"
            # A cold Facebook WebView can take longer than the normal target
            # budget to replace the launch surface.  Give only the first
            # share-only direct-URL attempt a bounded warm-up window; search
            # and Feed fallback keep the normal short budget so a bad URL does
            # not turn into three long waits.  Readiness/source validation
            # remains mandatory and this does not relax any identity guard.
            $attemptWaitSeconds = if ($OpenShareList -and $targetOpenAttempt -eq 1) {
                [Math]::Max($TargetOpenWaitSeconds, 24)
            }
            else {
                $TargetOpenWaitSeconds
            }
            for ($waitArgIndex = 0; $waitArgIndex -lt $targetArgs.Count; $waitArgIndex++) {
                if ([string]$targetArgs[$waitArgIndex] -eq '-WaitSeconds' -and ($waitArgIndex + 1) -lt $targetArgs.Count) {
                    $targetArgs[$waitArgIndex + 1] = [string]$attemptWaitSeconds
                    break
                }
            }
            # Share-only jobs retry the supplied URL and never search a raw ID
            # or scan unrelated Feed cards. Other modes retain their existing
            # bounded fallback behavior and strict source proof.
            if (-not $shareDirectUrlOnly -and $targetOpenAttempt -eq $targetOpenAttemptLimit -and $OpenShareList -and $targetArgs -notcontains '-EnableFeedCardFallback') {
                $targetArgs += '-EnableFeedCardFallback'
            }
            $targetOpenExit = Invoke-CaptureChildPowerShell (Join-Path $Base "open_facebook_post_readonly.ps1") $targetArgs
            if ($targetOpenExit -eq 0) {
                $targetOpenSucceeded = $true
                break
            }
            $targetRecoveryRequest = Get-TargetOpenRecoveryRequest
            if ($targetRecoveryRequest -and $targetOpenAttempt -lt $targetOpenAttemptLimit) {
                $searchPostId = [string]$targetRecoveryRequest.search_post_id
                if (-not $shareDirectUrlOnly -and -not $targetSearchFallbackApplied -and $searchPostId -match '^[0-9]{1,32}$') {
                    $postIdIndex = -1
                    for ($argIndex = 0; $argIndex -lt $targetArgs.Count; $argIndex++) {
                        if ([string]$targetArgs[$argIndex] -eq '-PostId') { $postIdIndex = $argIndex; break }
                    }
                    if ($postIdIndex -ge 0 -and ($postIdIndex + 1) -lt $targetArgs.Count) {
                        $targetArgs[$postIdIndex + 1] = $searchPostId
                    }
                    else {
                        $targetArgs += @('-PostId', $searchPostId)
                    }
                    if ($targetArgs -notcontains '-ForceFacebookSearch') { $targetArgs += '-ForceFacebookSearch' }
                    $targetSearchFallbackApplied = $true
                    Write-Host "[target-open] empty target surface recovered; next strict attempt uses exact Facebook search for post_id=$searchPostId."
                }
                if (-not (Invoke-TargetOpenUiRecovery $targetRecoveryRequest)) {
                    throw "Target-open UI recovery failed for state=$($targetRecoveryRequest.reason); capture aborted without relaxing target validation."
                }
            }
            if ($targetOpenAttempt -lt $targetOpenAttemptLimit) {
                if ($shareDirectUrlOnly) {
                    Write-Warning "[target-open] direct URL attempt $targetOpenAttempt did not verify target; retrying the same URL after bounded UI recovery."
                }
                else {
                    Write-Warning "[target-open] strict attempt $targetOpenAttempt did not verify target; retrying without relaxing validation."
                }
                Start-Sleep -Seconds 2
            }
        }
        if (-not $targetOpenSucceeded -and $OpenReactionList) {
            $cachedDiagnosticPath = Join-Path $RunDir 'target-open\target-open-diagnostics.json'
            if (Test-Path $cachedDiagnosticPath) {
                try {
                    $cachedDiagnostic = Get-Content -Raw -Encoding UTF8 $cachedDiagnosticPath | ConvertFrom-Json
                    $cachedResolvedUrl = [string]$cachedDiagnostic.resolved_url
                    $cachedPermalinkMatch = [regex]::Match(
                        $cachedResolvedUrl,
                        '(?i)/groups/([0-9]{1,32})/permalink/([0-9]{1,32})/?(?:[?#].*)?$'
                    )
                    $cachedPermalinkGroupId = if ($cachedPermalinkMatch.Success) { [string]$cachedPermalinkMatch.Groups[1].Value } else { '' }
                    $cachedPermalinkPostId = if ($cachedPermalinkMatch.Success) { [string]$cachedPermalinkMatch.Groups[2].Value } else { '' }
                    $requestedPermalinkMatch = [regex]::Match(
                        [string]$PostUrl,
                        '(?i)/groups/([0-9]{1,32})/permalink/([0-9]{1,32})/?(?:[?#].*)?$'
                    )
                    $cachedGroupPermalinkMatchesRequested = $true
                    if ($requestedPermalinkMatch.Success) {
                        $cachedGroupPermalinkMatchesRequested = (
                            $cachedPermalinkMatch.Success -and
                            [string]$cachedPermalinkGroupId -eq [string]$requestedPermalinkMatch.Groups[1].Value -and
                            [string]$cachedPermalinkPostId -eq [string]$requestedPermalinkMatch.Groups[2].Value
                        )
                    }
                    $cachedMethodAllowed = (
                        [string]$cachedDiagnostic.method -eq 'share-url-http-redirect' -or
                        (
                            [string]$cachedDiagnostic.method -eq 'url-deeplink' -and
                            $requestedPermalinkMatch.Success -and
                            $cachedGroupPermalinkMatchesRequested
                        )
                    )
                    $cachedUiPath = [string]$cachedDiagnostic.ui_path
                    $cachedUiIsFacebook = $false
                    if ($cachedUiPath -and (Test-Path $cachedUiPath)) {
                        $cachedUiRaw = [IO.File]::ReadAllText($cachedUiPath)
                        $cachedUiIsFacebook = $cachedUiRaw -match 'package="com\.facebook\.katana"'
                    }
                    $cachedPostIdCompatible = [string]::IsNullOrWhiteSpace($PostId) -or [string]$PostId -eq $cachedPermalinkPostId
                    if (
                        $cachedPermalinkMatch.Success -and
                        $cachedPostIdCompatible -and
                        $cachedUiIsFacebook -and
                        $cachedMethodAllowed -and
                        [string]$cachedDiagnostic.reason -eq 'target-not-confirmed-in-capture'
                    ) {
                        # A cached group permalink may render without issuing a fresh
                        # post query. Continue only with the permalink's numeric ID as
                        # the exact reactor source guard; the explorer must prove that
                        # source before any tab or pagination action.
                        $targetOpenSucceeded = $true
                        $script:ResolvedTargetUrl = $cachedResolvedUrl
                        $script:PrimaryVerifiedPostId = $cachedPermalinkPostId
                        $script:ResolvedCaptureSourcePostId = $cachedPermalinkPostId
                        Write-Host "[target-open] cached-group-permalink-source-proof: deferring target proof to exact reactor source_post_id=$cachedPermalinkPostId before pagination."
                    }
                }
                catch {
                    Write-Warning "[target-open] cached group permalink diagnostic could not be evaluated: $($_.Exception.Message)"
                }
            }
        }
        if (-not $targetOpenSucceeded) { throw "Target post could not be confirmed after $targetOpenAttemptLimit strict attempts; capture aborted." }

        # Reel detail can legitimately appear before autoplay emits a target token.
        # The target opener may authorize only the first bounded Reaction click;
        # explorer source discovery must then produce one unique fresh source ID
        # before any pagination or final analysis is allowed.
        $primaryTargetDiagnosticPath = Join-Path $RunDir 'target-open\target-open-diagnostics.json'
        if ($OpenReactionList -and -not $OpenShareList -and (Test-Path -LiteralPath $primaryTargetDiagnosticPath)) {
            try {
                $primaryTargetDiagnostic = Get-Content -Raw -Encoding UTF8 -LiteralPath $primaryTargetDiagnosticPath | ConvertFrom-Json
                if (
                    [bool]$primaryTargetDiagnostic.verified -and
                    [string]$primaryTargetDiagnostic.reason -eq 'reel-ui-source-discovery-required' -and
                    [string]$primaryTargetDiagnostic.resolved_url -match '(?i)^https://(?:www\.|m\.)?facebook\.com/reel/[0-9]{1,32}/?(?:[?#].*)?$'
                ) {
                    $script:PrimarySourceDiscoveryAllowed = $true
                    Write-Host '[target] strict Reel detail confirmed; one fresh unique source_post_id is required before pagination.'
                }
            }
            catch {
                Write-Warning "[target] Reel source-discovery authorization could not be read: $($_.Exception.Message)"
            }
        }

        $primaryTargetReport = Join-Path $RunDir 'target-open\verification-analysis\facebook-sample-patterns.json'
        if (Test-Path $primaryTargetReport) {
            try {
                $primary = Get-Content -Raw -Encoding UTF8 $primaryTargetReport | ConvertFrom-Json
                $verification = $primary.target_verification
                if ($verification -and [string]$verification.status -eq 'matched') {
                    $expectedUrlProperty = $verification.PSObject.Properties['expected_url']
                    $verifiedExpectedUrl = if ($expectedUrlProperty) { [string]$expectedUrlProperty.Value } else { '' }
                    if (-not [string]::IsNullOrWhiteSpace($verifiedExpectedUrl)) {
                        $script:ResolvedTargetUrl = $verifiedExpectedUrl
                    }
                    $matchedPostProperty = $verification.PSObject.Properties['matched_post_id']
                    $matchedPostId = if ($matchedPostProperty) { [string]$matchedPostProperty.Value } else { '' }
                    $expectedAuthorProperty = $verification.PSObject.Properties['expected_url_author_id']
                    $matchedAuthorId = if ($expectedAuthorProperty) { [string]$expectedAuthorProperty.Value } else { '' }
                    $resolvedAuthorProperty = $verification.PSObject.Properties['resolved_author_id']
                    if (-not $matchedAuthorId -and $resolvedAuthorProperty) { $matchedAuthorId = [string]$resolvedAuthorProperty.Value }
                    if (($OpenShareList -or $CombinedReactionShare) -and -not $matchedPostId) {
                        # A response URL token alone is not a source identity. Bind a
                        # numeric posts URL only to its matching captured post/author
                        # row from this same, capture-window-filtered target report.
                        $numericPostPattern = '(?i)^https://(?:www\.|m\.)?facebook\.com/([0-9]{1,32})/posts/([0-9]{1,32})/?(?:[?#].*)?$'
                        $requestedNumericPost = [regex]::Match([string]$PostUrl, $numericPostPattern)
                        $verifiedNumericPost = [regex]::Match($verifiedExpectedUrl, $numericPostPattern)
                        $tokenProperty = $verification.PSObject.Properties['matched_token']
                        $lineProperty = $verification.PSObject.Properties['matched_line']
                        if ($requestedNumericPost.Success -and $verifiedNumericPost.Success -and
                            $requestedNumericPost.Groups[1].Value -eq $verifiedNumericPost.Groups[1].Value -and
                            $requestedNumericPost.Groups[2].Value -eq $verifiedNumericPost.Groups[2].Value -and
                            (-not $PostId -or [string]$PostId -eq $requestedNumericPost.Groups[2].Value) -and
                            [string]$verification.matched_by -eq 'graphql-response-url-token' -and
                            $tokenProperty -and [string]$tokenProperty.Value -eq $requestedNumericPost.Groups[2].Value -and
                            $lineProperty -and [int]$lineProperty.Value -gt 0) {
                            $numericRows = @($primary.posts | Where-Object { [string]$_.post_id -eq $requestedNumericPost.Groups[2].Value })
                            if ($numericRows.Count -eq 1 -and [string]$numericRows[0].author_id -eq $requestedNumericPost.Groups[1].Value) {
                                $matchedPostId = [string]$numericRows[0].post_id
                                $matchedAuthorId = [string]$numericRows[0].author_id
                            }
                        }
                    }
                    # Group ID is not the owner. Promote only a unique captured
                    # post/author row with the same group+post URL in this verified
                    # window; neither the URL token nor the author alone is proof.
                    if (($OpenShareList -or $CombinedReactionShare) -and -not $matchedPostId) {
                        $groupPattern = '(?i)^https://(?:www\.|m\.)?facebook\.com/groups/([0-9]{1,32})/(?:posts|permalink)/([0-9]{1,32})/?(?:[?#].*)?$'
                        $requestedGroupPost = [regex]::Match([string]$PostUrl, $groupPattern)
                        $verifiedGroupPost = [regex]::Match($verifiedExpectedUrl, $groupPattern)
                        if ([string]$verification.status -eq 'matched' -and $requestedGroupPost.Success -and $verifiedGroupPost.Success -and
                            $requestedGroupPost.Groups[1].Value -eq $verifiedGroupPost.Groups[1].Value -and
                            $requestedGroupPost.Groups[2].Value -eq $verifiedGroupPost.Groups[2].Value -and
                            (-not $PostId -or [string]$PostId -eq $requestedGroupPost.Groups[2].Value) -and
                            [string]$verification.matched_by -eq 'graphql-response-url-token' -and
                            [string]$verification.matched_token -eq $requestedGroupPost.Groups[2].Value -and
                            [int]$primary.min_line -gt 0 -and [int]$verification.matched_line -ge [int]$primary.min_line) {
                            $groupRows = @($primary.posts | Where-Object { [string]$_.post_id -eq $requestedGroupPost.Groups[2].Value })
                            if ($groupRows.Count -eq 1 -and [string]$groupRows[0].author_id -match '^[0-9]{1,32}$') {
                                $capturedGroupPost = [regex]::Match([string]$groupRows[0].url, $groupPattern)
                                if ($capturedGroupPost.Success -and
                                    $capturedGroupPost.Groups[1].Value -eq $requestedGroupPost.Groups[1].Value -and
                                    $capturedGroupPost.Groups[2].Value -eq $requestedGroupPost.Groups[2].Value -and
                                    (-not $matchedAuthorId -or $matchedAuthorId -eq [string]$groupRows[0].author_id)) {
                                    $matchedPostId = [string]$groupRows[0].post_id
                                    $matchedAuthorId = [string]$groupRows[0].author_id
                                }
                            }
                        }
                    }
                    if (-not $matchedAuthorId -and $matchedPostId) {
                        $matchedRows = @($primary.posts | Where-Object { [string]$_.post_id -eq $matchedPostId -and [string]$_.author_id })
                        if ($matchedRows.Count -eq 1) { $matchedAuthorId = [string]$matchedRows[0].author_id }
                    }
                    if ($matchedPostId -and $matchedAuthorId) {
                        $script:PrimaryVerifiedPostId = $matchedPostId
                        $script:PrimaryVerifiedAuthorId = $matchedAuthorId
                        $matchedRows = @($primary.posts | Where-Object { [string]$_.post_id -eq $matchedPostId })
                        if ($matchedRows.Count -eq 1) { $script:PrimaryVerifiedReactionTotal = [int]($matchedRows[0].reaction_count) }
                        $script:ResolvedCaptureSourcePostId = $matchedPostId
                        Write-Host "[target] verified numeric post=$matchedPostId author=$matchedAuthorId reactions=$($script:PrimaryVerifiedReactionTotal)"
                    }
                    elseif (-not $matchedPostId) {
                        $matchedByProperty = $verification.PSObject.Properties['matched_by']
                        $matchedTokenProperty = $verification.PSObject.Properties['matched_token']
                        $matchedBy = if ($matchedByProperty) { [string]$matchedByProperty.Value } else { '' }
                        $matchedToken = if ($matchedTokenProperty) { [string]$matchedTokenProperty.Value } else { '' }
                        $numericTargetMatch = [regex]::Match($verifiedExpectedUrl, '(?i)/(?:reel|permalink)/([0-9]{1,32})/?(?:[?#].*)?$')
                        if ($matchedBy -eq 'graphql-response-url-token' -and $numericTargetMatch.Success -and $matchedToken -eq $numericTargetMatch.Groups[1].Value) {
                            $script:PrimarySourceDiscoveryAllowed = $true
                            if ($verifiedExpectedUrl -match '(?i)/permalink/') {
                                $script:PrimarySourceDiscoveryExpectedPostId = [string]$numericTargetMatch.Groups[1].Value
                            }
                            Write-Host "[target] canonical numeric target token verified; source must be discovered from the first fresh reactor query before pagination."
                        }
                    }
                }
            }
            catch {
                Write-Warning "[target] strict target metadata could not be parsed: $($_.Exception.Message)"
            }
        }
        # Explicit Share targets must never silently enter unbound feed probing.
        # Reaction-only source discovery retains its existing separate contract.
        if ($OpenShareList -and [string]::IsNullOrWhiteSpace([string]$script:PrimaryVerifiedPostId)) {
            throw 'Share target identity unavailable after verification; refusing unbound exploration.'
        }
    }
    elseif ($LaunchFacebook) {
        $facebookStarted = $false
        for ($launchAttempt = 1; $launchAttempt -le 2 -and -not $facebookStarted; $launchAttempt++) {
            if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 8 -RecoverOffline)) {
                if ($launchAttempt -ge 2) { throw "ADB device went offline before Facebook launch: $Serial" }
                continue
            }
            $launchNetwork = Get-FacebookActiveDefaultNetwork -Serial $Serial
            if ($launchNetwork -eq 'none') {
                Write-Warning '[transport] default network disappeared after capture proxy switch; waiting briefly without resetting transports.'
                for ($networkWait = 1; $networkWait -le 3 -and $launchNetwork -eq 'none'; $networkWait++) {
                    Start-Sleep -Seconds 1
                    $launchNetwork = Get-FacebookActiveDefaultNetwork -Serial $Serial
                }
                if ($launchNetwork -eq 'none') {
                    if ($launchAttempt -ge 2) { throw 'Android default network remained unavailable during capture launch preflight.' }
                    Write-Warning '[transport] capture launch preflight still has no default network; retrying launch preflight once.'
                    continue
                }
            }
            $proxyResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','settings','get','global','http_proxy') -TimeoutSeconds 6
            $launchProxy = if ($proxyResult.Success) { ([string]$proxyResult.Stdout).Trim() } else { '' }
            $expectedCaptureProxy = "127.0.0.1:$ProxyPort"
            if ($launchProxy -ne $expectedCaptureProxy) {
                $launchProxy = Set-FacebookAndroidProxy -Serial $Serial -Port $ProxyPort -TimeoutSeconds 8
            }
            $captureActive = [bool](Get-FacebookCaptureMarker -Serial $Serial)
            $launchMode = if ($launchAttempt -eq 1) { 'force-stop+start' } else { 'start-only' }
            Write-Host "[transport] Facebook launch attempt $launchAttempt mode=$launchMode network=$launchNetwork proxy=$launchProxy capture=$captureActive"
            if ($launchAttempt -eq 1) {
                $launchOk = Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 12 -ForceStop
            }
            else {
                $launchOk = Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 12
            }
            if ($launchOk) {
                $facebookStarted = $true
                break
            }
            Write-Warning "[transport] Facebook launcher attempt $launchAttempt failed/timed out; retrying once after a short delay."
            Start-Sleep -Seconds 2
        }
        if (-not $facebookStarted) { throw "Facebook launcher activity failed after two attempts" }
        # Wait-FacebookUiReady performs the first bounded content probe itself;
        # do not pay an unconditional post-launch delay before that check.
        Wait-FacebookUiReady $FacebookReadyTimeoutSeconds | Out-Null
    }

    for ($i = 0; $i -lt $ScrollCount; $i++) {
        $scrollTransport = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort
        Write-Host "[transport] feed-scroll $($i + 1)/$ScrollCount network=$($scrollTransport.active_default_network) proxy=$($scrollTransport.proxy) capture=$($scrollTransport.capture_active)"
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1700', '540', '650', '600')
        try {
            Dump-Ui (Join-Path $RunDir ("ui-scroll-{0:00}.xml" -f ($i + 1)))
        }
        catch {
            Write-Warning "Feed-position UI dump failed at scroll $($i + 1)/$ScrollCount; continuing: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds ([Math]::Max(0, $ScrollDelaySeconds))
    }
    if ($OpenShareList -or $OpenReactionList -or $OpenComments) {
        $readonlyActionsPerformed = $true
        $explorerTransport = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort
        Write-Host "[transport] pre-explorer network=$($explorerTransport.active_default_network) proxy=$($explorerTransport.proxy) capture=$($explorerTransport.capture_active)"
        Wait-FacebookUiSettledInPlace ([Math]::Max(8, $FacebookReadyTimeoutSeconds)) | Out-Null
        # Target-open traffic proves navigation, but it must never count as
        # Reaction/Share family evidence. When a strict target was matched,
        # bind the primary explorer to that exact post and start a fresh family
        # evidence window immediately before the readonly action.
        $primaryFamilyCaptureStartLine = Get-CaptureJsonlLineCount
        $readonlyArgs = @(
            "-Serial", $Serial,
            "-ProxyPort", [string]$ProxyPort,
            "-WaitSeconds", [string]$ReadOnlyWaitSeconds,
            "-ShareScrollCount", [string]$ShareScrollCount,
            "-ReactionScrollCount", [string]$ReactionScrollCount,
            "-ReactionTabLimit", [string]$ReactionTabLimit,
            "-PaginationIdleLimit", [string]$PaginationIdleLimit,
            "-PaginationOpenIdleLimit", [string]$PaginationOpenIdleLimit,
            "-ReactionStrategyIdleLimit", [string]$ReactionStrategyIdleLimit,
            "-ReactionOpenStrategyIdleLimit", [string]$ReactionOpenStrategyIdleLimit,
            "-ReactionExtensionChunk", [string]$ReactionExtensionChunk,
            "-ReactionAbsoluteCap", [string]$ReactionAbsoluteCap,
            "-ReactionScreenScrollCount", [string]$ReactionScreenScrollCount,
            "-ReactionListReadyAttempts", [string]$ReactionListReadyAttempts,
            "-ReactionListReadyDelaySeconds", [string]$ReactionListReadyDelaySeconds,
            "-ReactionProgressPollIntervalMs", [string]$ReactionProgressPollIntervalMs,
            "-ReactionProgressPollTimeoutMs", [string]$ReactionProgressPollTimeoutMs,
            "-ReactionLiveProgressPollIntervalMs", [string]$ReactionLiveProgressPollIntervalMs,
            "-ReactionLiveProgressPollTimeoutMs", [string]$ReactionLiveProgressPollTimeoutMs,
            "-ReactionSteadyNoGrowthPollTimeoutMs", [string]$ReactionSteadyNoGrowthPollTimeoutMs,
            "-ReactionLongSwipeStartY", [string]$ReactionLongSwipeStartY,
            "-ReactionLongSwipeEndY", [string]$ReactionLongSwipeEndY,
            "-ReactionLongSwipeDurationMs", [string]$ReactionLongSwipeDurationMs,
            "-ReactionPipelineBurstSize", [string]$ReactionPipelineBurstSize,
            "-ReactionPipelineSwipeGapMs", [string]$ReactionPipelineSwipeGapMs,
            "-ReactionNativeScrollEnabled", [string]$ReactionNativeScrollEnabled,
            "-ReactionNativeScrollTimeoutMs", [string]$ReactionNativeScrollTimeoutMs,
            "-ReactionNativeScrollPort", [string]$ReactionNativeScrollPort,
            "-ReactionUiDumpEveryN", [string]$ReactionUiDumpEveryN,
            "-ReactionMinCycleMs", [string]$ReactionMinCycleMs,
            "-ReactionInitialPaginationCooldownSeconds", [string]$ReactionInitialPaginationCooldownSeconds,
            "-ReactionRateLimitBackoffSeconds", [string]$ReactionRateLimitBackoffSeconds,
            "-ReactionRateLimitMaxConsecutive", [string]$ReactionRateLimitMaxConsecutive,
            "-ReactionRateLimitMaxBackoffSeconds", [string]$ReactionRateLimitMaxBackoffSeconds,
            "-ReactionNoGrowthUiDumpEveryN", [string]$ReactionNoGrowthUiDumpEveryN,
            "-EntryScanScrollCount", [string]$EntryScanScrollCount,
            "-UiDumpTimeoutSeconds", [string]$UiDumpTimeoutSeconds,
            "-UiDumpRetryCount", [string]$UiDumpRetryCount,
            "-CaptureJsonl", $Jsonl,
            "-OutputDir", (Join-Path $RunDir "readonly-ui")
        )
        if (-not [string]::IsNullOrWhiteSpace([string]$script:PrimaryVerifiedPostId)) {
            $readonlyArgs += @(
                "-ExpectedSourcePostId", [string]$script:PrimaryVerifiedPostId,
                "-CaptureMinLine", [string]([int]$primaryFamilyCaptureStartLine + 1)
            )
            Write-Host "[target] primary explorer bound to post=$($script:PrimaryVerifiedPostId) family_boundary=$([int]$primaryFamilyCaptureStartLine + 1)"
        }
        elseif ($script:PrimarySourceDiscoveryAllowed -and $OpenReactionList) {
            $readonlyArgs += @(
                "-AllowSourceDiscovery",
                "-CaptureMinLine", [string]([int]$primaryFamilyCaptureStartLine + 1)
            )
            Write-Host "[target] primary explorer requires one fresh source ID before any pagination; family_boundary=$([int]$primaryFamilyCaptureStartLine + 1)"
        }
        if ($OpenShareList -and -not $OpenReactionList) {
            $readonlyArgs += @('-TargetOpenDiagnosticPath', (Join-Path $RunDir 'target-open/target-open-diagnostics.json'))
        }
        if ($OpenShareList) { $readonlyArgs += "-OpenShareList" }
        if ($OpenReactionList) { $readonlyArgs += "-OpenReactionList" }
        if ($AutoScreenThenDeep) { $readonlyArgs += "-AutoScreenThenDeep" }
        if ($ReactionProbeOnly) { $readonlyArgs += "-ReactionProbeOnly" }
        if ($OpenComments) { $readonlyArgs += "-OpenComments" }
        $readonlyExit = Invoke-CaptureChildPowerShell (Join-Path $Base "explore_facebook_readonly.ps1") $readonlyArgs
        if ($readonlyExit -ne 0) { throw "Readonly explorer failed with exit code $readonlyExit" }
        if ($script:PrimarySourceDiscoveryAllowed -and $OpenReactionList) {
            $sourceDiscoveryDiagPath = Join-Path $RunDir 'readonly-ui\exploration-diagnostics.json'
            if (-not (Test-Path $sourceDiscoveryDiagPath)) { throw 'Reel source discovery diagnostics were not written.' }
            $sourceDiscoveryDiag = Get-Content -Raw -Encoding UTF8 $sourceDiscoveryDiagPath | ConvertFrom-Json
            $sourceDiscoveryProperty = $sourceDiscoveryDiag.PSObject.Properties['source_discovery']
            $sourceDiscovery = if ($sourceDiscoveryProperty) { $sourceDiscoveryProperty.Value } else { $null }
            if (-not $sourceDiscovery -or [string]$sourceDiscovery.status -ne 'matched' -or [string]::IsNullOrWhiteSpace([string]$sourceDiscovery.source_post_id)) {
                throw 'Source discovery did not produce one fresh source_post_id; capture cannot be completed.'
            }
            if (
                -not [string]::IsNullOrWhiteSpace([string]$script:PrimarySourceDiscoveryExpectedPostId) -and
                [string]$sourceDiscovery.source_post_id -ne [string]$script:PrimarySourceDiscoveryExpectedPostId
            ) {
                throw "Discovered source_post_id does not match the permalink target: expected=$($script:PrimarySourceDiscoveryExpectedPostId) observed=$($sourceDiscovery.source_post_id)"
            }
            $script:PrimaryVerifiedPostId = [string]$sourceDiscovery.source_post_id
            $script:ResolvedCaptureSourcePostId = [string]$sourceDiscovery.source_post_id
            Write-Host "[target] source discovery promoted source_post_id=$($script:ResolvedCaptureSourcePostId) to the final analysis guard."
        }

        # Facebook 555 occasionally verifies the exact target at the network
        # layer but renders the post detail without its Reaction summary.  In a
        # strict-target run, retry the same exact target rather than scrolling
        # into another feed story or relaxing source validation.
        if ($OpenReactionList -and -not [string]::IsNullOrWhiteSpace([string]$script:PrimaryVerifiedPostId)) {
            $primaryReactionRetryLimit = 1
            for ($primaryReactionRetry = 1; $primaryReactionRetry -le $primaryReactionRetryLimit; $primaryReactionRetry++) {
                $primaryDiagPath = Join-Path $RunDir 'readonly-ui\exploration-diagnostics.json'
                if (-not (Test-Path $primaryDiagPath)) { break }
                try {
                    $primaryDiag = Get-Content -Raw -Encoding UTF8 $primaryDiagPath | ConvertFrom-Json
                }
                catch {
                    Write-Warning "[target] primary Reaction diagnostics could not be parsed for retry: $($_.Exception.Message)"
                    break
                }
                $primaryReactionAction = Get-LatestDiagnosticAction $primaryDiag 'reaction'
                if (-not $primaryReactionAction -or [bool]$primaryReactionAction.confirmed) { break }
                if ([string]$primaryReactionAction.failure_reason -ne 'no-reaction-count-after-strict-target-detail-scan') { break }

                Write-Warning "[target] strict target Reaction summary was not rendered; reopening the same verified target (retry $primaryReactionRetry/$primaryReactionRetryLimit)."
                $retryOpenBoundary = Get-CaptureJsonlLineCount
                $retryTargetOutput = Join-Path $RunDir ("target-open-retry-{0:00}" -f $primaryReactionRetry)
                $retryTargetArgs = @(
                    '-Serial', $Serial,
                    '-ProxyPort', [string]$ProxyPort,
                    '-CaptureJsonl', $Jsonl,
                    '-OutputDir', $retryTargetOutput,
                    '-WaitSeconds', [string]$TargetOpenWaitSeconds,
                    '-CaptureMinLine', [string]([int]$retryOpenBoundary + 1)
                )
                if ($PostUrl) { $retryTargetArgs += @('-PostUrl', $PostUrl) }
                if ($PostId) { $retryTargetArgs += @('-PostId', $PostId) }
                if ($DisableAdaptiveTargetWait) { $retryTargetArgs += @('-DisableAdaptiveTargetWait') }
                if ($EnableAdaptiveTargetWait) { $retryTargetArgs += @('-EnableAdaptiveTargetWait') }
                $retryTargetExit = Invoke-CaptureChildPowerShell (Join-Path $Base 'open_facebook_post_readonly.ps1') $retryTargetArgs
                if ($retryTargetExit -ne 0) {
                    Write-Warning "[target] strict target reopen retry $primaryReactionRetry failed verification; keeping existing fail-safe path."
                    break
                }

                $retryTargetDiagPath = Join-Path $retryTargetOutput 'target-open-diagnostics.json'
                if (-not (Test-Path $retryTargetDiagPath)) {
                    Write-Warning "[target] strict target reopen retry $primaryReactionRetry produced no diagnostics; stopping retry."
                    break
                }
                try {
                    $retryTargetDiag = Get-Content -Raw -Encoding UTF8 $retryTargetDiagPath | ConvertFrom-Json
                }
                catch {
                    Write-Warning "[target] strict target reopen retry diagnostics could not be parsed: $($_.Exception.Message)"
                    break
                }
                $retryResolvedPostId = [string]$retryTargetDiag.resolved_post_id
                if (-not [bool]$retryTargetDiag.verified -or $retryResolvedPostId -ne [string]$script:PrimaryVerifiedPostId) {
                    Write-Warning "[target] strict target reopen retry did not prove the same post; expected=$($script:PrimaryVerifiedPostId) resolved=$retryResolvedPostId."
                    break
                }

                Wait-FacebookUiSettledInPlace ([Math]::Max(8, $FacebookReadyTimeoutSeconds)) | Out-Null
                $primaryFamilyCaptureStartLine = Get-CaptureJsonlLineCount
                $retryReadonlyArgs = @(
                    "-Serial", $Serial,
                    "-ProxyPort", [string]$ProxyPort,
                    "-WaitSeconds", [string]$ReadOnlyWaitSeconds,
                    "-ShareScrollCount", [string]$ShareScrollCount,
                    "-ReactionScrollCount", [string]$ReactionScrollCount,
                    "-ReactionTabLimit", [string]$ReactionTabLimit,
                    "-PaginationIdleLimit", [string]$PaginationIdleLimit,
                    "-PaginationOpenIdleLimit", [string]$PaginationOpenIdleLimit,
                    "-ReactionStrategyIdleLimit", [string]$ReactionStrategyIdleLimit,
                    "-ReactionOpenStrategyIdleLimit", [string]$ReactionOpenStrategyIdleLimit,
                    "-ReactionExtensionChunk", [string]$ReactionExtensionChunk,
                    "-ReactionAbsoluteCap", [string]$ReactionAbsoluteCap,
                    "-ReactionScreenScrollCount", [string]$ReactionScreenScrollCount,
                    "-ReactionListReadyAttempts", [string]$ReactionListReadyAttempts,
                    "-ReactionListReadyDelaySeconds", [string]$ReactionListReadyDelaySeconds,
                    "-ReactionProgressPollIntervalMs", [string]$ReactionProgressPollIntervalMs,
                    "-ReactionProgressPollTimeoutMs", [string]$ReactionProgressPollTimeoutMs,
                    "-ReactionLiveProgressPollIntervalMs", [string]$ReactionLiveProgressPollIntervalMs,
                    "-ReactionLiveProgressPollTimeoutMs", [string]$ReactionLiveProgressPollTimeoutMs,
                    "-ReactionSteadyNoGrowthPollTimeoutMs", [string]$ReactionSteadyNoGrowthPollTimeoutMs,
                    "-ReactionLongSwipeStartY", [string]$ReactionLongSwipeStartY,
                    "-ReactionLongSwipeEndY", [string]$ReactionLongSwipeEndY,
                    "-ReactionLongSwipeDurationMs", [string]$ReactionLongSwipeDurationMs,
                    "-ReactionPipelineBurstSize", [string]$ReactionPipelineBurstSize,
                    "-ReactionPipelineSwipeGapMs", [string]$ReactionPipelineSwipeGapMs,
                    "-ReactionNativeScrollEnabled", [string]$ReactionNativeScrollEnabled,
                    "-ReactionNativeScrollTimeoutMs", [string]$ReactionNativeScrollTimeoutMs,
                    "-ReactionNativeScrollPort", [string]$ReactionNativeScrollPort,
                    "-ReactionUiDumpEveryN", [string]$ReactionUiDumpEveryN,
                    "-ReactionMinCycleMs", [string]$ReactionMinCycleMs,
                    "-ReactionInitialPaginationCooldownSeconds", [string]$ReactionInitialPaginationCooldownSeconds,
                    "-ReactionRateLimitBackoffSeconds", [string]$ReactionRateLimitBackoffSeconds,
                    "-ReactionRateLimitMaxConsecutive", [string]$ReactionRateLimitMaxConsecutive,
                    "-ReactionRateLimitMaxBackoffSeconds", [string]$ReactionRateLimitMaxBackoffSeconds,
                    "-ReactionNoGrowthUiDumpEveryN", [string]$ReactionNoGrowthUiDumpEveryN,
                    "-EntryScanScrollCount", [string]$EntryScanScrollCount,
                    "-UiDumpTimeoutSeconds", [string]$UiDumpTimeoutSeconds,
                    "-UiDumpRetryCount", [string]$UiDumpRetryCount,
                    "-CaptureJsonl", $Jsonl,
                    "-ExpectedSourcePostId", [string]$script:PrimaryVerifiedPostId,
                    "-CaptureMinLine", [string]([int]$primaryFamilyCaptureStartLine + 1),
                    "-OutputDir", (Join-Path $RunDir "readonly-ui"),
                    "-OpenReactionList"
                )
                if ($AutoScreenThenDeep) { $retryReadonlyArgs += "-AutoScreenThenDeep" }
                if ($ReactionProbeOnly) { $retryReadonlyArgs += "-ReactionProbeOnly" }
                Write-Host "[target] retry explorer bound to post=$($script:PrimaryVerifiedPostId) family_boundary=$([int]$primaryFamilyCaptureStartLine + 1)"
                $retryReadonlyExit = Invoke-CaptureChildPowerShell (Join-Path $Base "explore_facebook_readonly.ps1") $retryReadonlyArgs
                if ($retryReadonlyExit -ne 0) {
                    Write-Warning "[target] retry explorer failed with exit code $retryReadonlyExit; stopping bounded retry."
                    break
                }
            }
        }
        if ($ReactionProbeOnly) {
            Write-Host '[probe] ReactionProbeOnly: fallback path skipped.'
        }
        else {
            Invoke-NativePermalinkFallback
        }
        if ($CombinedReactionShare) { Invoke-CombinedSharePhase $readonlyArgs }
    }
    if ($captureDeadline) {
        $remaining = [int][Math]::Floor(($captureDeadline - (Get-Date)).TotalSeconds)
        if ($remaining -gt 0) {
            if ($readonlyActionsPerformed) {
                Start-Sleep -Seconds ([Math]::Min($remaining, [Math]::Max(0, $PostActionGraceSeconds)))
            }
            else {
                Start-Sleep -Seconds $remaining
            }
        }
    }
    $captureCompleted = $true
    Write-Output "Capture finished: $RunDir"
}
finally {
    $cleanupClock = [System.Diagnostics.Stopwatch]::StartNew()
    if ($mitm -and -not $mitm.HasExited) {
        Stop-Process -Id $mitm.Id -Force -ErrorAction SilentlyContinue
        $mitm.WaitForExit()
    }
    Clear-FacebookCaptureMarker -Serial $Serial -OwnerPid $PID
    if ($rawNetworkCapture) {
        try {
            Stop-FacebookRawNetworkCapture -Serial $Serial -Capture $rawNetworkCapture -LocalPath $RawNetworkPcap -MetadataPath $RawNetworkMetadata
        }
        catch {
            $rawNetworkCaptureError = $_.Exception.Message
            Write-Warning "[pcap] raw network capture finalization failed: $rawNetworkCaptureError"
        }
    }
    $finalAdbReady = Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 30 -RecoverOffline -AllowServerRestart
    if (-not $finalAdbReady) {
        Write-Warning "[transport] final restore could not reach ADB device: $Serial"
    }
    else {
        if ($captureCompleted) {
            try { $null = Ensure-FacebookAndroidNetwork -Serial $Serial }
            catch { Write-Warning "[transport] final network readiness check failed; continuing proxy restore: $($_.Exception.Message)" }
        }
        try { $null = Ensure-FacebookReverseMappings -Serial $Serial -Ports @(Get-FacebookNormalReversePorts -CapturePort $ProxyPort) }
        catch { Write-Warning "[transport] final reverse mapping restore failed: $($_.Exception.Message)" }
        try {
            $restoredProxy = Set-FacebookNormalProxy -Serial $Serial -TimeoutSeconds 12
            Write-Host "[transport] restored proxy=$restoredProxy via adb reverse"
        }
        catch { Write-Warning "[transport] final proxy restore failed: $($_.Exception.Message)" }
        if ($captureCompleted) {
            try { $null = Confirm-FacebookUiOnline -Serial $Serial -CapturePort $ProxyPort -Attempts 2 -WaitSeconds 6 }
            catch { Write-Warning "[transport] final UI network confirmation failed: $($_.Exception.Message)" }
        }
        else {
            Write-Host '[transport] failed capture fast-restore complete; network/UI recovery is deferred to the candidate retry or batch finalization.'
        }
    }
    Remove-Item Env:FACEBOOK_CAPTURE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:FACEBOOK_CAPTURE_MAX_FLOWS -ErrorAction SilentlyContinue
    Remove-Item Env:FACEBOOK_CAPTURE_MAX_BODY_BYTES -ErrorAction SilentlyContinue
    Remove-Item Env:FACEBOOK_RAW_CAPTURE_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:FACEBOOK_RAW_INDEX_FILE -ErrorAction SilentlyContinue
    if ($env:FACEBOOK_BATCH_OWNS_WATCHDOG -eq '1') {
        Write-Host '[transport] watchdog restart deferred to batch owner'
    }
    else {
        try {
            $watchPid = Start-FacebookTransportWatch -Serial $Serial -BasePath $Base -CapturePort $ProxyPort
            if ($watchPid) { Write-Host "[transport] watchdog resumed pid=$watchPid" }
        }
        catch {
            Write-Warning "[transport] watchdog resume failed: $($_.Exception.Message)"
        }
    }
    Write-CaptureTiming "transport-cleanup" $cleanupClock
}

if (Test-Path $Jsonl) {
    $analysisArgs = @((Join-Path $Base "analyze_facebook_capture.py"), "--input", $Jsonl, "--out-dir", $AnalysisDir)
    if ($PostId) { $analysisArgs += @("--expected-post-id", $PostId) }
    elseif ($script:ResolvedCaptureSourcePostId) { $analysisArgs += @("--expected-post-id", $script:ResolvedCaptureSourcePostId) }
    if ($script:ResolvedTargetUrl) { $analysisArgs += @("--expected-url", $script:ResolvedTargetUrl) }
    if ($script:ResolvedCaptureSourcePostId) { $analysisArgs += @("--focus-source-post-id", $script:ResolvedCaptureSourcePostId) }
    if (($OpenShareList -or $CombinedReactionShare) -and $OriginalShareTargetInputUrl) {
        $analysisArgs += @('--share-target-input-url', $OriginalShareTargetInputUrl)
    }
    $analysisResult = Invoke-CapturePythonHidden -Arguments $analysisArgs
    if ($analysisResult.ExitCode -ne 0) { throw "Capture analysis failed with exit code $($analysisResult.ExitCode)" }
    $networkResult = Invoke-CapturePythonHidden -Arguments @(
        (Join-Path $Base "build_network_data.py"), '--analysis-dir', $AnalysisDir, '--out-dir', $AnalysisDir
    )
    if ($networkResult.ExitCode -ne 0) { throw "Network analysis failed with exit code $($networkResult.ExitCode)" }
}

if (-not $DisableRawHttpCapture -and (Test-Path $RawFlow)) {
    $RawSummary = Join-Path $RunDir "raw-facebook-http-summary.json"
    $rawSummaryResult = Invoke-CapturePythonHidden -Arguments @(
        (Join-Path $Base "inspect_facebook_raw_capture.py"), '--input', $RawFlow, '--out', $RawSummary
    )
    if ($rawSummaryResult.ExitCode -ne 0) { throw "Raw HTTP archive verification failed with exit code $($rawSummaryResult.ExitCode)" }
    Write-Host "[capture] raw HTTP archive verified: $RawFlow"
    Write-Host "[capture] raw HTTP index: $RawIndex"
    Write-Host "[capture] raw HTTP summary: $RawSummary"
}

if ($EnableRawNetworkCapture -and $captureCompleted -and $rawNetworkCaptureError) {
    throw "Raw network capture was requested but did not finalize: $rawNetworkCaptureError"
}

# Process completion is not Share-list completion. Validate only after finally
# has restored transport, retaining rejected/partial runs for diagnosis.
if ($OpenShareList -or $CombinedReactionShare) {
    $shareResult = Invoke-CapturePythonHidden -Arguments @(
        (Join-Path $Base 'tools/verify_share_result.py'), '--run-dir', $RunDir
    )
    if ($shareResult.ExitCode -ne 0) { throw 'Share result rejected; retained analysis explains the incomplete capture.' }
}
