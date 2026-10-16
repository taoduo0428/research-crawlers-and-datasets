[CmdletBinding()]
param(
    [string]$Serial = 'emulator-5554',
    [string]$AndroidRoot = 'D:\Android',
    [string]$AvdName = 'fb-x86_64-api35',
    [int]$CapturePort = 8081,
    [int]$NativePort = 18766,
    [string]$ExistingLauncher = '',
    [int]$CpuCores = 4,
    [int]$MemoryMb = 3072,
    [int]$BootTimeoutSeconds = 240,
    [switch]$SkipFacebookUiCheck,
    [switch]$WritableSystem,
    [switch]$RestoreMitmCertificate
)

$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$WindowsSystem32 = Join-Path $env:SystemRoot 'System32'
if (Test-Path $WindowsSystem32) { $env:PATH = $WindowsSystem32 + ';' + $env:PATH }
$LocalPlatformTools = Join-Path $Base 'platform-tools'
if (Test-Path (Join-Path $LocalPlatformTools 'adb.exe')) {
    $env:PATH = $LocalPlatformTools + ';' + $env:PATH
}
. (Join-Path $Base 'facebook_transport.ps1')
$null = Get-FacebookNetworkConfig

$SdkRoot = Join-Path $AndroidRoot 'SdkManual'
$AvdHome = Join-Path $AndroidRoot 'AVDManual'
if ($env:FACEBOOK_SDK_ROOT) { $SdkRoot = $env:FACEBOOK_SDK_ROOT }
if ($env:FACEBOOK_AVD_HOME) { $AvdHome = $env:FACEBOOK_AVD_HOME }
$EmulatorExe = Join-Path $SdkRoot 'emulator\emulator.exe'
if (-not (Test-Path -LiteralPath $EmulatorExe)) { throw "Missing emulator executable: $EmulatorExe" }
$env:ANDROID_HOME = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot
$env:ANDROID_USER_HOME = Join-Path $AndroidRoot '.android-manual'
$env:ANDROID_AVD_HOME = $AvdHome
$serialPortMatch = [regex]::Match($Serial, '^emulator-([0-9]+)$')
if (-not $serialPortMatch.Success) { throw "Serial must use emulator-<port> format: $Serial" }
$EmulatorPort = [int]$serialPortMatch.Groups[1].Value
if ($EmulatorPort -lt 5554 -or $EmulatorPort -gt 5682 -or ($EmulatorPort % 2) -ne 0) {
    throw "Emulator serial port must be an even value from 5554 through 5682: $EmulatorPort"
}

function Test-AdbDevice {
    return ((Get-FacebookAdbState -Serial $Serial) -eq 'device')
}

if (-not (Test-AdbDevice)) {
    $matchingProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq 'emulator.exe' -and $_.CommandLine -and $_.CommandLine -match ('(?i)-avd\s+' + [regex]::Escape($AvdName) + '(?:\s|$)')
    } | Select-Object -First 1
    if (-not $matchingProcess) {
        New-Item -ItemType Directory -Force -Path $AvdHome | Out-Null
        $avdPath = Join-Path $AvdHome ($AvdName + '.avd')
        $staleLocks = @(
            @(
                (Join-Path $avdPath 'hardware-qemu.ini.lock'),
                (Join-Path $avdPath 'multiinstance.lock')
            ) | Where-Object { Test-Path -LiteralPath $_ }
        )
        if ($staleLocks.Count -gt 0) {
            $lockBackup = Join-Path $AvdHome ('avd-lock-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
            New-Item -ItemType Directory -Force -Path $lockBackup | Out-Null
            foreach ($staleLock in $staleLocks) {
                Move-Item -LiteralPath $staleLock -Destination $lockBackup -Force
            }
            Write-Warning "[stable-start] no matching emulator process exists; moved stale AVD locks to $lockBackup"
        }
        $stdoutLog = Join-Path $AvdHome ($AvdName + '-stable.stdout.log')
        $stderrLog = Join-Path $AvdHome ($AvdName + '-stable.stderr.log')
        $emulatorArgs = @('-avd', $AvdName, '-port', [string]$EmulatorPort, '-no-snapshot', '-no-boot-anim', '-feature', '-WiFiPacketStream', '-dns-server', '8.8.8.8', '-cores', [string][Math]::Max(2, $CpuCores), '-memory', [string][Math]::Max(2048, $MemoryMb))
        # Android emulator keeps system CA changes in the writable-system overlay.
        # Restoring the Conscrypt bind without reopening that overlay would hide
        # the installed project CA after a full process restart.
        if ($WritableSystem -or $RestoreMitmCertificate) { $emulatorArgs += '-writable-system' }
        Write-Host "[stable-start] launching AVD directly without -http-proxy: $EmulatorExe"
        $matchingProcess = Start-Process -FilePath $EmulatorExe -ArgumentList $emulatorArgs -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
    }
    else {
        Write-Host "[stable-start] matching emulator process already exists pid=$($matchingProcess.ProcessId); waiting for ADB."
    }
}

$deadline = (Get-Date).AddSeconds([Math]::Max(20, $BootTimeoutSeconds))
$earlyTransportReady = $false
$bootCompleted = $false
$servicesReady = $false
while ((Get-Date) -lt $deadline) {
    if (Test-AdbDevice) {
        if (-not $earlyTransportReady) {
            # Android's global proxy persists across emulator restarts while
            # adb reverse mappings do not. Recreate the reverse mapping as soon
            # as ADB appears. ADB can report `device` slightly before reverse
            # is ready, so treat the first failure as a boot race and retry.
            try {
                $early = Initialize-FacebookColdBootTransport -Serial $Serial -Ports @(Get-FacebookNormalReversePorts -CapturePort $CapturePort)
                Write-Host "[stable-start] early reverse ready mode=$($early.network_mode) upstreamReachable=$($early.upstream_reachable) reverseNormal=$($early.reverse_normal) reverseCapture=$($early.reverse_capture) capturePort=$CapturePort"
                $earlyTransportReady = $true
            }
            catch {
                Write-Warning "[stable-start] early reverse not ready yet: $($_.Exception.Message)"
            }
        }
        $bootProbe = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','getprop','sys.boot_completed') -TimeoutSeconds 4
        $bootCompleted = [bool]($bootProbe.Success -and ([string]$bootProbe.Stdout).Trim() -eq '1')
        if ($bootCompleted -and (Ensure-FacebookAndroidServicesReady -Serial $Serial -TimeoutSeconds 4)) {
            $servicesReady = $true
            break
        }
    }
    Start-Sleep -Seconds 2
}
if (-not (Test-AdbDevice)) { throw "ADB device did not become ready: $Serial" }
if (-not $bootCompleted) { throw "Android did not reach sys.boot_completed=1: $Serial" }
if (-not $servicesReady) { throw "Android connectivity service did not become ready: $Serial" }
if (-not $earlyTransportReady) {
    $null = Initialize-FacebookColdBootTransport -Serial $Serial -Ports @(Get-FacebookNormalReversePorts -CapturePort $CapturePort)
}

# The bounded readiness helper probes immediately and waits only while services
# are unavailable. A fixed delay here penalizes every already-ready emulator.
if (-not (Ensure-FacebookFrameworkServicesReady -Serial $Serial -TimeoutSeconds 45)) {
    throw "Android framework services did not become ready: $Serial"
}
if ($RestoreMitmCertificate) {
    $certificateScript = Join-Path $Base 'facebook_certificate.ps1'
    $certificatePath = Join-Path $env:USERPROFILE '.mitmproxy\mitmproxy-ca-cert.pem'
    if ((Test-Path -LiteralPath $certificateScript) -and (Test-Path -LiteralPath $certificatePath)) {
        . $certificateScript
        $certificateHash = Get-FacebookMitmCertificateHash -CertificatePath $certificatePath
        if (Restore-FacebookMitmCertificateRuntime -Serial $Serial -CertificateHash $certificateHash) {
            Write-Host "[stable-start] restored Android Conscrypt CA runtime binding hash=$certificateHash"
        }
        else {
            Write-Host '[stable-start] project CA is not installed in the system store; runtime restore skipped.'
        }
    }
}
$result = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $CapturePort
Write-Host "[stable-start] network=$($result.active_default_network) proxy=$($result.proxy) mode=$($result.network_mode) reverseNormal=$($result.reverse_normal) reverseCapture=$($result.reverse_capture) capturePort=$CapturePort nativePort=$NativePort upstreamReachable=$($result.upstream_reachable)"
if (-not (Ensure-FacebookUiDumpHelper -Serial $Serial -BasePath $Base)) {
    Write-Warning '[stable-start] instrumentation UI dump fallback is unavailable; continuing with legacy UIAutomator only.'
}
try {
    if ($SkipFacebookUiCheck) {
        Write-Host '[stable-start] Facebook UI check skipped; emulator is ready for app installation or manual login.'
    }
    else {
        $null = Confirm-FacebookUiOnline -Serial $Serial -CapturePort $CapturePort -Attempts 2 -WaitSeconds 8
    }
}
finally {
    Start-FacebookTransportWatch -Serial $Serial -BasePath $Base -CapturePort $CapturePort | Out-Null
}
if ($SkipFacebookUiCheck) {
    Write-Host '[stable-start] Emulator transport is ready; Facebook login remains a manual step.'
}
else {
    Write-Host '[stable-start] Facebook started and UI network health was checked.'
}
