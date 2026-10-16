[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkerConfigPath = Join-Path $Base 'facebook_workers.json'
$TransportScript = Join-Path $Base 'facebook_transport.ps1'

function Read-FacebookWorkers {
    if (-not (Test-Path -LiteralPath $WorkerConfigPath -PathType Leaf)) {
        throw "Missing worker config: $WorkerConfigPath"
    }
    try {
        $config = Get-Content -LiteralPath $WorkerConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "facebook_workers.json is invalid: $($_.Exception.Message)"
    }
    $workers = @($config.workers)
    if ($workers.Count -eq 0) {
        throw 'facebook_workers.json contains no workers; refusing an unscoped emulator shutdown.'
    }
    foreach ($worker in $workers) {
        if ([string]::IsNullOrWhiteSpace([string]$worker.worker_id) -or
            [string]::IsNullOrWhiteSpace([string]$worker.serial) -or
            [string]::IsNullOrWhiteSpace([string]$worker.avd_name)) {
            throw 'Every configured worker must define worker_id, serial, and avd_name before STOP can run safely.'
        }
    }
    return $workers
}

function Invoke-VerifiedTaskKillTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    if ($ProcessId -eq $PID) {
        throw "Refusing to terminate the STOP process itself (PID $PID)."
    }
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    if (-not (Test-Path -LiteralPath $taskkill -PathType Leaf)) {
        $taskkillCommand = Get-Command taskkill.exe -ErrorAction SilentlyContinue
        if (-not $taskkillCommand) { throw 'taskkill.exe is unavailable.' }
        $taskkill = [string]$taskkillCommand.Source
    }
    & $taskkill /PID $ProcessId /T /F 2>$null | Out-Null
}

function Get-FacebookProjectProcesses {
    $markers = @(
        'start_api_and_emulator.ps1',
        'start_facebook_stable.ps1',
        'start_facebook_capture_api.ps1',
        'facebook_capture_api.py',
        'start_ui_v2.ps1',
        'start_ui_v2.py',
        'run_facebook_capture.ps1',
        'run_facebook_batch.ps1',
        'run_facebook_post_queue.ps1',
        'explore_facebook_readonly.ps1',
        'open_facebook_post_readonly.ps1',
        'facebook_capture_addon_safe.py',
        'facebook_raw_flow_writer.py',
        'facebook_ssl_unpin.js',
        'facebook_network_watch.ps1'
    )
    return @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            if (-not $_.CommandLine -or [int]$_.ProcessId -eq $PID) { return $false }
            if ($_.CommandLine.IndexOf($Base, [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
            foreach ($marker in $markers) {
                if ($_.CommandLine.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    return $true
                }
            }
            return $false
        }
    )
}

function Test-ConfiguredAvdCommandLine {
    param(
        [string]$CommandLine,
        [string]$AvdName
    )
    if ([string]::IsNullOrWhiteSpace($CommandLine) -or [string]::IsNullOrWhiteSpace($AvdName)) {
        return $false
    }
    $escaped = [regex]::Escape($AvdName)
    $pattern = '(?i)(?:^|\s)-avd\s+(?:"' + $escaped + '"|' + $escaped + ')(?=\s|$)'
    return [bool]($CommandLine -match $pattern)
}

function Invoke-VerifiedFacebookEmuKill {
    param(
        [Parameter(Mandatory = $true)][string]$AdbPath,
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string]$ExpectedAvdName
    )
    if ([string]::IsNullOrWhiteSpace($AdbPath) -or
        [string]::IsNullOrWhiteSpace($Serial) -or
        [string]::IsNullOrWhiteSpace($ExpectedAvdName)) {
        return $false
    }
    try {
        # Query the live device identity immediately before emu kill. A missing,
        # failed, or foreign identity must never receive a destructive command.
        $queryOutput = & $AdbPath -s $Serial shell getprop ro.boot.qemu.avd_name 2>$null
        $queryExitCode = $LASTEXITCODE
        $actualAvdName = (@($queryOutput) -join "`n").Trim()
        if ($queryExitCode -ne 0 -or
            [string]::IsNullOrWhiteSpace($actualAvdName) -or
            -not [string]::Equals($actualAvdName, $ExpectedAvdName, [StringComparison]::Ordinal)) {
            return $false
        }
        & $AdbPath -s $Serial emu kill 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Get-ConfiguredFacebookAvdProcesses {
    param([object[]]$Workers)
    $avdNames = @($Workers | ForEach-Object { [string]$_.avd_name } | Sort-Object -Unique)
    return @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            if ($_.Name -notin @('emulator.exe', 'qemu-system-x86_64.exe')) { return $false }
            foreach ($avdName in $avdNames) {
                if (Test-ConfiguredAvdCommandLine -CommandLine ([string]$_.CommandLine) -AvdName $avdName) {
                    return $true
                }
            }
            return $false
        }
    )
}

$workers = @(Read-FacebookWorkers)
Write-Host "[STOP] Facebook workers in config: $($workers.Count)" -ForegroundColor Cyan

# Stop project watchdogs first so they cannot repair/restart transport while the
# rest of the Facebook runtime is being shut down.
if (Test-Path -LiteralPath $TransportScript -PathType Leaf) {
    . $TransportScript
    foreach ($serial in @($workers | ForEach-Object { [string]$_.serial } | Sort-Object -Unique)) {
        try {
            $stopped = Stop-FacebookTransportWatch -Serial $serial -BasePath $Base
            Write-Host "[STOP] watchdog serial=$serial stopped=$stopped"
        }
        catch {
            Write-Warning "Could not stop watchdog for ${serial}: $($_.Exception.Message)"
        }
    }
}

# Stop every project-owned host runtime (API/UI/capture/mitm/frida wrappers).
$hostDeadline = (Get-Date).AddSeconds([Math]::Max(5, $TimeoutSeconds))
do {
    $projectProcesses = @(Get-FacebookProjectProcesses)
    foreach ($process in $projectProcesses) {
        Write-Host "[STOP] project process=$($process.Name) pid=$($process.ProcessId)"
        try { Invoke-VerifiedTaskKillTree -ProcessId ([int]$process.ProcessId) } catch { }
    }
    if ($projectProcesses.Count -gt 0) { Start-Sleep -Milliseconds 500 }
} while ($projectProcesses.Count -gt 0 -and (Get-Date) -lt $hostDeadline)

$remainingProjectProcesses = @(Get-FacebookProjectProcesses)
if ($remainingProjectProcesses.Count -gt 0) {
    $remainingIds = ($remainingProjectProcesses | ForEach-Object { $_.ProcessId }) -join ','
    throw "Facebook project host processes did not stop within $TimeoutSeconds seconds. Remaining PID(s): $remainingIds"
}

# Ask each configured emulator to exit gracefully. This automatically includes
# future workers added to facebook_workers.json.
$adb = Join-Path $Base 'platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $adb -PathType Leaf)) {
    $adbCommand = Get-Command adb.exe -ErrorAction SilentlyContinue
    if ($adbCommand) { $adb = [string]$adbCommand.Source }
}
if (Test-Path -LiteralPath $adb -PathType Leaf) {
    foreach ($worker in $workers) {
        $serial = [string]$worker.serial
        $avdName = [string]$worker.avd_name
        Write-Host "[STOP] requesting emulator shutdown worker=$($worker.worker_id) serial=$serial avd=$avdName"
        $shutdownRequested = Invoke-VerifiedFacebookEmuKill -AdbPath $adb -Serial $serial -ExpectedAvdName $avdName
        if (-not $shutdownRequested) {
            Write-Warning "[STOP] skipped emulator shutdown worker=$($worker.worker_id): serial identity was not verified as avd=$avdName"
        }
    }
    Start-Sleep -Seconds 2
}

# Force-stop only emulator/qemu processes whose -avd name is present in the
# Facebook worker config. Other Android emulators on the PC are left untouched.
$avdDeadline = (Get-Date).AddSeconds([Math]::Max(5, $TimeoutSeconds))
do {
    $avdProcesses = @(Get-ConfiguredFacebookAvdProcesses -Workers $workers)
    foreach ($process in $avdProcesses) {
        Write-Host "[STOP] configured AVD process=$($process.Name) pid=$($process.ProcessId)"
        try { Invoke-VerifiedTaskKillTree -ProcessId ([int]$process.ProcessId) } catch { }
    }
    if ($avdProcesses.Count -gt 0) { Start-Sleep -Milliseconds 500 }
} while ($avdProcesses.Count -gt 0 -and (Get-Date) -lt $avdDeadline)

$remainingAvdProcesses = @(Get-ConfiguredFacebookAvdProcesses -Workers $workers)
if ($remainingAvdProcesses.Count -gt 0) {
    $remainingIds = ($remainingAvdProcesses | ForEach-Object { $_.ProcessId }) -join ','
    throw "Configured Facebook emulator processes did not stop within $TimeoutSeconds seconds. Remaining PID(s): $remainingIds"
}

Write-Host '[STOP] Facebook runtime stopped: API/UI/capture helpers/watchdogs/configured emulators are down.' -ForegroundColor Green
Write-Host '[STOP] ADB server and unrelated system/global proxy processes were intentionally left untouched.' -ForegroundColor DarkGray
exit 0
