[CmdletBinding()]
param(
    [string]$Serial = 'emulator-5554',
    [int]$CapturePort = 8081,
    [int]$IntervalSeconds = 8,
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Continue'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalPlatformTools = Join-Path $Base 'platform-tools'
if (Test-Path (Join-Path $LocalPlatformTools 'adb.exe')) {
    $env:PATH = $LocalPlatformTools + ';' + $env:PATH
}
. (Join-Path $Base 'facebook_transport.ps1')
$null = Get-FacebookNetworkConfig
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $Base 'facebook-network-watch.log'
}

$mutexName = 'FacebookTransportWatch_' + ($Serial -replace '[^A-Za-z0-9_.-]', '_')
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$ownsMutex = $false
try {
    $ownsMutex = $mutex.WaitOne(0, $false)
    if (-not $ownsMutex) { exit 0 }

    function Write-WatchLog([string]$Message) {
        $line = '{0} {1}' -f (Get-Date).ToString('s'), $Message
        Add-Content -Encoding UTF8 -Path $LogPath -Value $line
    }

    Write-WatchLog "watch-start serial=$Serial mode=$((Get-FacebookNetworkConfig).mode)"
    $lastState = ''
    while ($true) {
        try {
            # During a live capture the capture process owns the emulator ADB
            # transport and proxy lifecycle.  Even read-only watchdog probes
            # (get-state, settings, nc, connectivity checks) can overlap with
            # uiautomator and make the emulator shell intermittently stall.
            # The capture marker is a local file, so check it before issuing
            # any ADB command and remain completely passive until capture ends.
            if ([bool](Get-FacebookCaptureMarker -Serial $Serial)) {
                if ($lastState -ne 'capture-passive') { Write-WatchLog 'capture-active; passive wait (no adb probes)' }
                $lastState = 'capture-passive'
                Start-Sleep -Seconds ([Math]::Max(2, $IntervalSeconds))
                continue
            }

            $state = Get-FacebookAdbState -Serial $Serial
            if ($state -ne 'device') {
                if ($state -eq 'offline') {
                    if ($lastState -ne 'adb-offline') { Write-WatchLog 'adb-offline; attempting recovery' }
                    $lastState = 'adb-offline'
                    $allowRestart = -not [bool](Get-FacebookCaptureMarker)
                    if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 18 -RecoverOffline -AllowServerRestart:$allowRestart)) {
                        Start-Sleep -Seconds ([Math]::Max(2, $IntervalSeconds))
                        continue
                    }
                }
                else {
                    if ($lastState -ne 'adb-missing') { Write-WatchLog 'adb-missing; waiting for emulator' }
                    $lastState = 'adb-missing'
                    Start-Sleep -Seconds ([Math]::Max(2, $IntervalSeconds))
                    continue
                }
            }

            if (-not (Ensure-FacebookAndroidServicesReady -Serial $Serial -TimeoutSeconds 8)) {
                if ($lastState -ne 'android-services-wait') { Write-WatchLog 'android-services-wait; waiting for connectivity service' }
                $lastState = 'android-services-wait'
                Start-Sleep -Seconds ([Math]::Max(2, $IntervalSeconds))
                continue
            }

            $result = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $CapturePort
            $summary = 'ok network={0} proxy={1} reverseNormal={2} reverseCapture={3} capturePort={4} capture={5} repaired={6}' -f `
                $result.active_default_network, $result.proxy, $result.reverse_normal, $result.reverse_capture, $CapturePort, $result.capture_active, $result.repaired
            if ($result.repaired -or $lastState -ne $summary) { Write-WatchLog $summary }
            $lastState = $summary
        }
        catch {
            $message = 'repair-error: ' + $_.Exception.Message
            if ($lastState -ne $message) { Write-WatchLog $message }
            $lastState = $message
        }
        Start-Sleep -Seconds ([Math]::Max(2, $IntervalSeconds))
    }
}
finally {
    if ($ownsMutex) { try { $mutex.ReleaseMutex() } catch {} }
    $mutex.Dispose()
}
