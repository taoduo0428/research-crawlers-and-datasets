[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Create', 'Start', 'Status', 'Enable', 'Disable')]
    [string]$Action,
    [string]$WorkerId = 'worker-02',
    [string]$ConfigPath = '',
    [string]$AndroidRoot = 'D:\Android',
    [string]$FacebookApkPath = ''
)

$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $Base 'facebook_workers.json'
}
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Worker configuration not found: $ConfigPath" }

$config = Get-Content -Raw -Encoding UTF8 -LiteralPath $ConfigPath | ConvertFrom-Json
$worker = @($config.workers | Where-Object { [string]$_.worker_id -eq $WorkerId }) | Select-Object -First 1
if (-not $worker) { throw "Unknown worker: $WorkerId" }

$isMutation = $Action -in @('Create', 'Start', 'Enable', 'Disable')
if ($WorkerId -eq 'worker-01' -and $isMutation) {
    throw 'Worker-01 cannot be created or replaced by this management script.'
}

$SdkRoot = Join-Path $AndroidRoot 'SdkManual'
$AvdHome = Join-Path $AndroidRoot 'AVDManual'
if ($env:FACEBOOK_SDK_ROOT) { $SdkRoot = $env:FACEBOOK_SDK_ROOT }
if ($env:FACEBOOK_AVD_HOME) { $AvdHome = $env:FACEBOOK_AVD_HOME }
$AvdManager = Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
$EmulatorPackageMetadata = Join-Path $SdkRoot 'emulator\package.xml'
$PackageId = 'system-images;android-35;google_apis;x86_64'
$SystemImage = Join-Path $SdkRoot 'system-images\android-35\google_apis\x86_64\package.xml'
$Adb = Join-Path $Base 'platform-tools\adb.exe'
if ($env:FACEBOOK_ADB_EXE) { $Adb = $env:FACEBOOK_ADB_EXE }
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $PowerShellExe)) { throw "Windows PowerShell not found: $PowerShellExe" }
$AvdPath = Join-Path $AvdHome (([string]$worker.avd_name) + '.avd')
$ExternalIni = Join-Path $AvdHome (([string]$worker.avd_name) + '.ini')

$env:ANDROID_HOME = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot
$env:ANDROID_USER_HOME = Join-Path $AndroidRoot '.android-manual'
$env:ANDROID_AVD_HOME = $AvdHome

function Get-WorkerAdbState {
    if (-not (Test-Path -LiteralPath $Adb)) { return 'adb-not-found' }
    $raw = & $Adb devices 2>$null
    $match = [regex]::Match(($raw -join "`n"), "(?m)^$([regex]::Escape([string]$worker.serial))\s+(\S+)\s*$")
    if (-not $match.Success) { return 'missing' }
    return $match.Groups[1].Value.Trim().ToLowerInvariant()
}

function Test-FacebookPackageInstalled {
    if ((Get-WorkerAdbState) -ne 'device') { return $false }
    $path = & $Adb -s ([string]$worker.serial) shell pm path com.facebook.katana 2>$null
    return [bool](($path -join "`n") -match '(?m)^package:')
}

function Save-WorkerConfiguration {
    $parent = Split-Path -Parent $ConfigPath
    $temp = Join-Path $parent ('.facebook-workers-{0}-{1}.tmp' -f $PID, [guid]::NewGuid().ToString('N'))
    try {
        $config | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $temp
        Move-Item -Force -LiteralPath $temp -Destination $ConfigPath
    }
    finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -Force -LiteralPath $temp -ErrorAction SilentlyContinue }
    }
}

function Notify-RunningApi([string]$Operation) {
    try {
        $uri = "http://127.0.0.1:8765/v1/workers/$WorkerId/$Operation"
        $null = Invoke-RestMethod -Method Post -Uri $uri -TimeoutSec 3
        return $true
    }
    catch {
        return $false
    }
}

function New-ManualCleanAvdDefinition {
    if ((Test-Path -LiteralPath $AvdPath) -or (Test-Path -LiteralPath $ExternalIni)) {
        throw "Refusing manual fallback because a partial AVD definition exists for $WorkerId"
    }
    New-Item -ItemType Directory -Path $AvdPath | Out-Null
    @(
        'avd.ini.encoding=UTF-8',
        ('AvdId={0}' -f [string]$worker.avd_name),
        'target=android-35',
        ('image.sysdir.1={0}' -f (Split-Path -Parent $SystemImage)),
        'abi.type=x86_64',
        'tag.id=google_apis',
        'tag.display=Google APIs',
        'hw.cpu.arch=x86_64',
        'hw.cpu.ncore=2',
        'hw.ramSize=2048',
        'hw.device.name=pixel_2',
        'hw.device.manufacturer=Google',
        'hw.lcd.width=1080',
        'hw.lcd.height=1920',
        'hw.lcd.density=420',
        'hw.gpu.enabled=yes',
        'hw.gpu.mode=auto',
        'hw.keyboard=yes',
        'hw.audioInput=yes',
        'hw.audioOutput=yes',
        'hw.camera.back=virtualscene',
        'hw.camera.front=emulated',
        'hw.initialOrientation=Portrait',
        'fastboot.forceColdBoot=yes',
        'fastboot.forceFastBoot=no',
        'showDeviceFrame=no',
        'skin.dynamic=yes',
        'runtime.network.latency=none',
        'runtime.network.speed=full',
        'disk.dataPartition.size=10737418240',
        'PlayStore.enabled=no'
    ) | Set-Content -Encoding ASCII -LiteralPath (Join-Path $AvdPath 'config.ini')
    @(
        'avd.ini.encoding=UTF-8',
        ('path={0}' -f $AvdPath),
        ('path.rel=avd\{0}.avd' -f [string]$worker.avd_name),
        'target=android-35'
    ) | Set-Content -Encoding ASCII -LiteralPath $ExternalIni
}

switch ($Action) {
    'Create' {
        if (Test-Path -LiteralPath $AvdPath) {
            Write-Host "[worker] AVD already exists; leaving it unchanged: $AvdPath"
            break
        }
        if (Test-Path -LiteralPath $ExternalIni) {
            throw "Refusing to replace unmatched AVD metadata: $ExternalIni"
        }
        if (-not (Test-Path -LiteralPath $AvdManager)) { throw "avdmanager.bat not found: $AvdManager" }
        if (-not (Test-Path -LiteralPath $SystemImage)) { throw "Android system image not found: $PackageId" }
        New-Item -ItemType Directory -Force -Path $AvdHome | Out-Null
        if (Test-Path -LiteralPath $EmulatorPackageMetadata) {
            'no' | & $AvdManager create avd --force --name ([string]$worker.avd_name) --package $PackageId --device 'pixel_2'
        }
        if (-not (Test-Path -LiteralPath $EmulatorPackageMetadata) -or $LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $AvdPath)) {
            Write-Warning 'avdmanager package metadata is incomplete; creating an isolated definition from the installed system image.'
            New-ManualCleanAvdDefinition
        }
        Write-Host "[worker] clean AVD created from $PackageId at $AvdPath"
    }
    'Start' {
        $startScript = Join-Path $Base 'start_facebook_stable.ps1'
        & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $startScript `
            -Serial ([string]$worker.serial) `
            -AvdName ([string]$worker.avd_name) `
            -AndroidRoot $AndroidRoot `
            -CapturePort ([int]$worker.capture_port) `
            -NativePort ([int]$worker.native_port) `
            -SkipFacebookUiCheck
        if ($LASTEXITCODE -ne 0) { throw "Worker emulator start failed: $WorkerId" }
        if (-not [string]::IsNullOrWhiteSpace($FacebookApkPath)) {
            $resolvedApk = (Resolve-Path -LiteralPath $FacebookApkPath).Path
            & $Adb -s ([string]$worker.serial) install -r $resolvedApk
            if ($LASTEXITCODE -ne 0) { throw "Facebook APK installation failed on $WorkerId" }
        }
        if (Test-FacebookPackageInstalled) {
            & $Adb -s ([string]$worker.serial) shell am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n com.facebook.katana/.LoginActivity | Out-Null
            Write-Host "[worker] Facebook login screen requested on $($worker.serial); worker remains enabled=$($worker.enabled)."
        }
        else {
            Write-Warning "Facebook is not installed on $WorkerId. Re-run Start with -FacebookApkPath before login."
        }
    }
    'Enable' {
        if ((Get-WorkerAdbState) -ne 'device') { throw "Worker device is not online: $($worker.serial)" }
        if (-not (Test-FacebookPackageInstalled)) { throw "Facebook is not installed on $WorkerId" }
        $worker.enabled = $true
        Save-WorkerConfiguration
        $liveUpdated = Notify-RunningApi 'enable'
        Write-Host "[worker] $WorkerId enabled (running_api_updated=$liveUpdated)."
    }
    'Disable' {
        $worker.enabled = $false
        if (-not @($config.workers | Where-Object { [bool]$_.enabled }).Count) {
            throw 'At least one worker must remain enabled.'
        }
        Save-WorkerConfiguration
        $liveUpdated = Notify-RunningApi 'disable'
        Write-Host "[worker] $WorkerId disabled (running_api_updated=$liveUpdated). A running job is not moved to another worker."
    }
    'Status' {
        [ordered]@{
            worker_id = [string]$worker.worker_id
            serial = [string]$worker.serial
            avd_name = [string]$worker.avd_name
            enabled = [bool]$worker.enabled
            avd_exists = [bool](Test-Path -LiteralPath $AvdPath)
            adb_state = Get-WorkerAdbState
            facebook_installed = Test-FacebookPackageInstalled
            capture_port = [int]$worker.capture_port
            native_port = [int]$worker.native_port
        } | ConvertTo-Json
    }
}
