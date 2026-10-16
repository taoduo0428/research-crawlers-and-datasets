[CmdletBinding()]
param(
    [ValidateSet('Add', 'Resume', 'Finalize', 'Status')]
    [string]$Action = 'Add',
    [string]$WorkerId = '',
    [string]$SourceWorkerId = '',
    [string]$ConfigPath = '',
    [string]$AndroidRoot = 'D:\Android',
    [string]$CertificatePath = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem",
    [string]$OpenSslPath = '',
    [string]$ProbeUrl = 'https://www.facebook.com/share/r/1Dg59X6ysJ/',
    [int]$BootTimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ConfigPath)) { $ConfigPath = Join-Path $Base 'facebook_workers.json' }
$StateRoot = Join-Path $Base 'worker-bootstrap'
$LogRoot = Join-Path $StateRoot 'logs'
$AvdHome = Join-Path $AndroidRoot 'AVDManual'
if ($env:FACEBOOK_AVD_HOME) { $AvdHome = $env:FACEBOOK_AVD_HOME }
$Adb = Join-Path $Base 'platform-tools\adb.exe'
if ($env:FACEBOOK_ADB_EXE) { $Adb = $env:FACEBOOK_ADB_EXE }
$BootstrapPython = Join-Path $Base 'facebook_worker_bootstrap.py'
$ManageScript = Join-Path $Base 'manage_facebook_workers.ps1'
$StartScript = Join-Path $Base 'start_facebook_stable.ps1'
$ProbeScript = Join-Path $Base 'probe_facebook_reaction_health.ps1'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $PythonCommand) { throw 'python.exe could not be resolved.' }
$PythonExe = [string]$PythonCommand.Source
foreach ($required in @($ConfigPath, $Adb, $BootstrapPython, $ManageScript, $StartScript, $ProbeScript, $PowerShellExe)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required worker bootstrap file is missing: $required" }
}
. (Join-Path $Base 'facebook_transport.ps1')
. (Join-Path $Base 'facebook_certificate.ps1')
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$script:CurrentStage = 'initialize'
$script:LogPath = Join-Path $LogRoot ((Get-Date -Format 'yyyyMMdd-HHmmss') + "-$Action.log")

function Write-BootstrapLog {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    Add-Content -LiteralPath $script:LogPath -Encoding UTF8 -Value $line
}

function Invoke-BootstrapPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $PythonExe $BootstrapPython @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($exitCode -ne 0) { throw "Bootstrap helper failed: $($output -join ' ')" }
    $jsonLine = [string](@($output | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }) | Select-Object -Last 1)
    try { return ($jsonLine | ConvertFrom-Json) }
    catch { throw "Bootstrap helper returned invalid JSON: $jsonLine" }
}

function Get-WorkerRegistry {
    return (Get-Content -Raw -Encoding UTF8 -LiteralPath $ConfigPath | ConvertFrom-Json)
}

function Get-WorkerRecord {
    param([Parameter(Mandatory = $true)][string]$Id)
    $record = @((Get-WorkerRegistry).workers | Where-Object { [string]$_.worker_id -eq $Id }) | Select-Object -First 1
    if (-not $record) { throw "Unknown worker: $Id" }
    return $record
}

function Update-BootstrapState {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [string]$Status = '',
        [string]$Stage = '',
        [string]$FailedStage = '',
        [string]$ErrorMessage = '',
        [string]$SourceId = '',
        [string]$CertificateHash = ''
    )
    $arguments = @('mark', '--state-dir', $StateRoot, '--worker-id', $Id)
    if ($Status) { $arguments += @('--status', $Status) }
    if ($Stage) { $arguments += @('--stage', $Stage) }
    if ($FailedStage) { $arguments += @('--failed-stage', $FailedStage) }
    if ($ErrorMessage) { $arguments += @('--error', $ErrorMessage) }
    if ($SourceId) { $arguments += @('--source-worker-id', $SourceId) }
    if ($CertificateHash) { $arguments += @('--certificate-hash', $CertificateHash) }
    $null = Invoke-BootstrapPython -Arguments $arguments
}

function Invoke-Adb {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string[]]$AdbArguments,
        [switch]$AllowFailure
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Adb -s $Serial @AdbArguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "ADB failed on $Serial ($($AdbArguments -join ' ')): $($output -join ' ')"
    }
    return [pscustomobject]@{ Success = ($exitCode -eq 0); Output = @($output); ExitCode = $exitCode }
}

function Test-WorkerDevice {
    param([Parameter(Mandatory = $true)][string]$Serial)
    $devices = & $Adb devices 2>$null
    return [bool](($devices -join "`n") -match "(?m)^$([regex]::Escape($Serial))\s+device\s*$")
}

function Test-WorkerPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string]$PackageName
    )
    if (-not (Test-WorkerDevice -Serial $Serial)) { return $false }
    $result = Invoke-Adb -Serial $Serial -AdbArguments @('shell','pm','path',$PackageName) -AllowFailure
    return [bool]($result.Success -and (($result.Output -join "`n") -match '(?m)^package:'))
}

function Select-SourceWorker {
    param([Parameter(Mandatory = $true)][string]$TargetWorkerId)
    $workers = @((Get-WorkerRegistry).workers)
    if ($SourceWorkerId) {
        $workers = @($workers | Where-Object { [string]$_.worker_id -eq $SourceWorkerId })
        if ($workers.Count -ne 1) { throw "Source worker was not found: $SourceWorkerId" }
    }
    else {
        $workers = @($workers | Where-Object { [string]$_.worker_id -ne $TargetWorkerId } | Sort-Object @{Expression={-not [bool]$_.enabled}}, worker_id)
    }
    foreach ($candidate in $workers) {
        $serial = [string]$candidate.serial
        if (-not (Test-WorkerDevice -Serial $serial)) { continue }
        if (-not (Test-WorkerPackage -Serial $serial -PackageName 'com.facebook.katana')) { continue }
        $frida = Invoke-Adb -Serial $serial -AdbArguments @('shell','test','-x','/data/local/tmp/fb-fs17') -AllowFailure
        if ($frida.Success) { return $candidate }
    }
    throw 'No online source worker has both Facebook and /data/local/tmp/fb-fs17. Start an existing configured worker and retry.'
}

function Install-FacebookApksFromSource {
    param(
        [Parameter(Mandatory = $true)][string]$SourceSerial,
        [Parameter(Mandatory = $true)][string]$TargetSerial,
        [Parameter(Mandatory = $true)][string]$TempRoot
    )
    if (Test-WorkerPackage -Serial $TargetSerial -PackageName 'com.facebook.katana') {
        Write-BootstrapLog "Facebook is already installed on $TargetSerial; APK transfer skipped."
        return
    }
    $pathsResult = Invoke-Adb -Serial $SourceSerial -AdbArguments @('shell','pm','path','com.facebook.katana')
    $remotePaths = @()
    foreach ($line in $pathsResult.Output) {
        if ([string]$line -match '^package:(.+)$') { $remotePaths += $Matches[1].Trim() }
    }
    if ($remotePaths.Count -eq 0) { throw "Facebook APK paths were not exposed by $SourceSerial" }
    $apkRoot = Join-Path $TempRoot 'facebook-apks'
    New-Item -ItemType Directory -Force -Path $apkRoot | Out-Null
    $localPaths = @()
    for ($index = 0; $index -lt $remotePaths.Count; $index++) {
        $leaf = [IO.Path]::GetFileName([string]$remotePaths[$index])
        $localPath = Join-Path $apkRoot ('{0:D2}-{1}' -f ($index + 1), $leaf)
        $null = Invoke-Adb -Serial $SourceSerial -AdbArguments @('pull',[string]$remotePaths[$index],$localPath)
        if (-not (Test-Path -LiteralPath $localPath)) { throw "Facebook APK pull did not create $localPath" }
        $localPaths += $localPath
    }
    $installArguments = if ($localPaths.Count -eq 1) { @('install','-r',$localPaths[0]) } else { @('install-multiple','-r') + $localPaths }
    $null = Invoke-Adb -Serial $TargetSerial -AdbArguments $installArguments
    if (-not (Test-WorkerPackage -Serial $TargetSerial -PackageName 'com.facebook.katana')) {
        throw "Facebook installation verification failed on $TargetSerial"
    }
    Write-BootstrapLog "Installed Facebook from $($remotePaths.Count) APK file(s); no package data was copied."
}

function Install-FridaFromSource {
    param(
        [Parameter(Mandatory = $true)][string]$SourceSerial,
        [Parameter(Mandatory = $true)][string]$TargetSerial,
        [Parameter(Mandatory = $true)][string]$TempRoot
    )
    $fridaCommand = Get-Command frida.exe -ErrorAction SilentlyContinue
    if (-not $fridaCommand) { throw 'frida.exe is required on the host.' }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $hostVersion = [string]((& ([string]$fridaCommand.Source) --version 2>&1 | Select-Object -Last 1))
        $hostExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($hostExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($hostVersion)) { throw 'Could not determine the host Frida version.' }
    $sourceVersionResult = Invoke-Adb -Serial $SourceSerial -AdbArguments @('shell','/data/local/tmp/fb-fs17','--version')
    $sourceVersion = [string]($sourceVersionResult.Output | Select-Object -Last 1)
    if ($sourceVersion.Trim() -ne $hostVersion.Trim()) {
        throw "Frida version mismatch: host=$($hostVersion.Trim()) source=$($sourceVersion.Trim())"
    }
    $localPath = Join-Path $TempRoot 'fb-fs17'
    $null = Invoke-Adb -Serial $SourceSerial -AdbArguments @('pull','/data/local/tmp/fb-fs17',$localPath)
    $null = Invoke-Adb -Serial $TargetSerial -AdbArguments @('push',$localPath,'/data/local/tmp/fb-fs17')
    $null = Invoke-Adb -Serial $TargetSerial -AdbArguments @('shell','chmod','0755','/data/local/tmp/fb-fs17')
    $targetVersionResult = Invoke-Adb -Serial $TargetSerial -AdbArguments @('shell','/data/local/tmp/fb-fs17','--version')
    $targetVersion = [string]($targetVersionResult.Output | Select-Object -Last 1)
    if ($targetVersion.Trim() -ne $hostVersion.Trim()) { throw "Frida verification failed on $TargetSerial" }
    Write-BootstrapLog "Installed matching Frida server version $($hostVersion.Trim())."
}

function Start-FridaServer {
    param([Parameter(Mandatory = $true)][string]$Serial)
    $null = Invoke-Adb -Serial $Serial -AdbArguments @('root')
    if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 30 -RecoverOffline)) { throw "ADB did not return after root: $Serial" }
    $fridaPid = Invoke-Adb -Serial $Serial -AdbArguments @('shell','pidof','fb-fs17') -AllowFailure
    if (-not $fridaPid.Success -or [string]::IsNullOrWhiteSpace(($fridaPid.Output -join ''))) {
        $null = Invoke-Adb -Serial $Serial -AdbArguments @('shell','nohup /data/local/tmp/fb-fs17 >/dev/null 2>&1 &')
        Start-Sleep -Seconds 2
        $fridaPid = Invoke-Adb -Serial $Serial -AdbArguments @('shell','pidof','fb-fs17') -AllowFailure
    }
    if (-not $fridaPid.Success -or [string]::IsNullOrWhiteSpace(($fridaPid.Output -join ''))) { throw "Frida server did not start on $Serial" }
}

function Invoke-ApiWorkerReload {
    try {
        $null = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/v1/workers/reload' -TimeoutSec 5
        return $true
    }
    catch { return $false }
}

function Assert-WorkerPrerequisites {
    param(
        [Parameter(Mandatory = $true)]$Worker,
        [Parameter(Mandatory = $true)][string]$CertificateHash
    )
    $serial = [string]$Worker.serial
    if (-not (Test-WorkerDevice -Serial $serial)) { throw "Worker device is offline: $serial" }
    $boot = Invoke-Adb -Serial $serial -AdbArguments @('shell','getprop','sys.boot_completed')
    if ([string]($boot.Output | Select-Object -Last 1) -ne '1') { throw "Android boot is incomplete: $serial" }
    foreach ($package in @('com.facebook.katana','com.openai.facebookuidump')) {
        if (-not (Test-WorkerPackage -Serial $serial -PackageName $package)) { throw "Required package is missing on ${serial}: $package" }
    }
    $frida = Invoke-Adb -Serial $serial -AdbArguments @('shell','test','-x','/data/local/tmp/fb-fs17') -AllowFailure
    if (-not $frida.Success) { throw "Frida server is missing or not executable on $serial" }
    foreach ($certificate in @(
        "/system/etc/security/cacerts/$CertificateHash.0",
        "/apex/com.android.conscrypt/cacerts/$CertificateHash.0"
    )) {
        $check = Invoke-Adb -Serial $serial -AdbArguments @('shell','test','-r',$certificate) -AllowFailure
        if (-not $check.Success) { throw "Certificate verification failed on ${serial}: $certificate" }
    }
    $transport = Ensure-FacebookProjectTransport -Serial $serial -CapturePort ([int]$Worker.capture_port)
    if (-not $transport.reverse_7897 -or -not $transport.reverse_capture -or $transport.proxy -ne '127.0.0.1:7897') {
        throw "Proxy/reverse mapping verification failed on $serial"
    }
    Start-FridaServer -Serial $serial
}

function Assert-FacebookLoginCompleted {
    param([Parameter(Mandatory = $true)][string]$Serial)
    $top = Invoke-Adb -Serial $Serial -AdbArguments @('shell','dumpsys','activity','top')
    $raw = $top.Output -join "`n"
    if (
        $raw -match '(?i)ACTIVITY\s+com\.facebook\.katana/[^\s]*loggedout' -or
        $raw -match '(?i)analytics_module=com\.bloks\.www\.caa\.login'
    ) {
        throw "Facebook manual login is not complete on $Serial. Finish login in the emulator before Finalize."
    }
    if ($raw -notmatch '(?i)ACTIVITY\s+com\.facebook\.katana/') {
        throw "Facebook is not the foreground app on $Serial. Open Facebook and confirm login before Finalize."
    }
}

function Start-AndProvisionWorker {
    param([Parameter(Mandatory = $true)]$Worker)
    $id = [string]$Worker.worker_id
    $serial = [string]$Worker.serial
    $source = Select-SourceWorker -TargetWorkerId $id
    $sourceId = [string]$source.worker_id
    Update-BootstrapState -Id $id -Status 'provisioning' -SourceId $sourceId
    Write-BootstrapLog "Provisioning $id on $serial from application-only source $sourceId."

    $script:CurrentStage = 'avd-created'
    & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $ManageScript -Action Create -WorkerId $id -ConfigPath $ConfigPath -AndroidRoot $AndroidRoot
    if ($LASTEXITCODE -ne 0) { throw "AVD creation failed for $id" }
    Update-BootstrapState -Id $id -Stage $script:CurrentStage

    $script:CurrentStage = 'emulator-started'
    & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $StartScript `
        -Serial $serial -AvdName ([string]$Worker.avd_name) -AndroidRoot $AndroidRoot `
        -CapturePort ([int]$Worker.capture_port) -NativePort ([int]$Worker.native_port) `
        -BootTimeoutSeconds $BootTimeoutSeconds -SkipFacebookUiCheck -WritableSystem
    if ($LASTEXITCODE -ne 0) { throw "Emulator startup failed for $id" }
    Update-BootstrapState -Id $id -Stage $script:CurrentStage

    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('facebook-worker-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    try {
        $script:CurrentStage = 'facebook-installed'
        Install-FacebookApksFromSource -SourceSerial ([string]$source.serial) -TargetSerial $serial -TempRoot $tempRoot
        Update-BootstrapState -Id $id -Stage $script:CurrentStage

        $script:CurrentStage = 'frida-installed'
        Install-FridaFromSource -SourceSerial ([string]$source.serial) -TargetSerial $serial -TempRoot $tempRoot
        Update-BootstrapState -Id $id -Stage $script:CurrentStage
    }
    finally {
        if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }
    }

    $script:CurrentStage = 'certificate-installed'
    $certificate = Ensure-FacebookMitmCertificate -Serial $serial -CertificatePath $CertificatePath -OpenSslPath $OpenSslPath -BootTimeoutSeconds $BootTimeoutSeconds
    Update-BootstrapState -Id $id -Stage $script:CurrentStage -CertificateHash ([string]$certificate.certificate_hash)

    $script:CurrentStage = 'prerequisites-verified'
    if (-not (Ensure-FacebookUiDumpHelper -Serial $serial -BasePath $Base)) { throw "UI dump helper verification failed on $serial" }
    Assert-WorkerPrerequisites -Worker $Worker -CertificateHash ([string]$certificate.certificate_hash)
    Update-BootstrapState -Id $id -Stage $script:CurrentStage

    $script:CurrentStage = 'login-opened'
    $launch = Invoke-Adb -Serial $serial -AdbArguments @(
        'shell','am','start','-a','android.intent.action.MAIN','-c','android.intent.category.LAUNCHER',
        '-n','com.facebook.katana/.LoginActivity'
    )
    if (-not $launch.Success) { throw "Facebook login screen could not be opened on $serial" }
    Update-BootstrapState -Id $id -Status 'awaiting-login' -Stage $script:CurrentStage
    $reloaded = Invoke-ApiWorkerReload
    Write-BootstrapLog "$id is provisioned and remains disabled. api_reload=$reloaded"
    Write-Host ''
    Write-Host "Complete the Facebook login manually in the $serial window, then run:"
    Write-Host ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -Action Finalize -WorkerId {1}' -f $PSCommandPath, $id)
}

function Finalize-Worker {
    param([Parameter(Mandatory = $true)]$Worker)
    if ([string]::IsNullOrWhiteSpace($ProbeUrl)) { throw 'Finalize requires -ProbeUrl for a real low-frequency login/capture check.' }
    $id = [string]$Worker.worker_id
    $serial = [string]$Worker.serial
    $certificateHash = Get-FacebookMitmCertificateHash -CertificatePath $CertificatePath -OpenSslPath $OpenSslPath
    $script:CurrentStage = 'final-prerequisites-verified'
    Assert-WorkerPrerequisites -Worker $Worker -CertificateHash $certificateHash
    Update-BootstrapState -Id $id -Status 'provisioning' -Stage $script:CurrentStage

    $script:CurrentStage = 'login-verified'
    Assert-FacebookLoginCompleted -Serial $serial
    Update-BootstrapState -Id $id -Stage $script:CurrentStage

    $script:CurrentStage = 'reaction-probe-passed'
    Write-BootstrapLog "Running one bounded ReactionProbeOnly check on $id."
    & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $ProbeScript `
        -Serial $serial -ProxyPort ([int]$Worker.capture_port) `
        -ReactionNativeScrollPort ([int]$Worker.native_port) -EnableFridaSslUnpin `
        -PostUrl $ProbeUrl -TargetOpenWaitSeconds 10
    if ($LASTEXITCODE -ne 0) { throw "Reaction health probe failed for $id; worker remains disabled." }
    Update-BootstrapState -Id $id -Stage $script:CurrentStage

    $script:CurrentStage = 'worker-enabled'
    $apiOnline = $false
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 5
        $apiOnline = ([string]$health.service -eq 'ready')
    }
    catch {}
    if ($apiOnline) {
        if (-not (Invoke-ApiWorkerReload)) { throw 'The running API does not support safe worker reload. Restart it with the current code, then rerun Finalize.' }
        $enabled = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/v1/workers/$id/enable" -TimeoutSec 10
        if (-not [bool]$enabled.enabled) { throw "API did not enable $id" }
    }
    else {
        & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $ManageScript -Action Enable -WorkerId $id -ConfigPath $ConfigPath -AndroidRoot $AndroidRoot
        if ($LASTEXITCODE -ne 0) { throw "Offline worker enable failed for $id" }
    }
    Update-BootstrapState -Id $id -Status 'ready' -Stage $script:CurrentStage
    Write-BootstrapLog "$id passed the real probe and is enabled."
}

try {
    switch ($Action) {
        'Add' {
            $script:CurrentStage = 'reserved'
            $reservation = Invoke-BootstrapPython -Arguments @(
                'reserve', '--config', $ConfigPath, '--avd-home', $AvdHome,
                '--state-dir', $StateRoot, '--adb', $Adb
            )
            $WorkerId = [string]$reservation.worker.worker_id
            Write-BootstrapLog "Reserved $WorkerId config_backup=$($reservation.backup_path)"
            Start-AndProvisionWorker -Worker $reservation.worker
        }
        'Resume' {
            if ([string]::IsNullOrWhiteSpace($WorkerId)) { throw 'Resume requires -WorkerId.' }
            Start-AndProvisionWorker -Worker (Get-WorkerRecord -Id $WorkerId)
        }
        'Finalize' {
            if ([string]::IsNullOrWhiteSpace($WorkerId)) { throw 'Finalize requires -WorkerId.' }
            Finalize-Worker -Worker (Get-WorkerRecord -Id $WorkerId)
        }
        'Status' {
            $registry = Get-WorkerRegistry
            $items = foreach ($worker in @($registry.workers)) {
                $statePath = Join-Path $StateRoot (([string]$worker.worker_id) + '.json')
                $state = if (Test-Path -LiteralPath $statePath) { Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json } else { $null }
                $completedStages = @()
                if ($state) { $completedStages = @($state.completed_stages) }
                [ordered]@{
                    worker_id = [string]$worker.worker_id
                    serial = [string]$worker.serial
                    avd_name = [string]$worker.avd_name
                    enabled = [bool]$worker.enabled
                    adb_state = if (Test-WorkerDevice -Serial ([string]$worker.serial)) { 'device' } else { 'offline' }
                    bootstrap_status = if ($state) { [string]$state.status } else { 'unmanaged' }
                    completed_stages = $completedStages
                }
            }
            [ordered]@{ version = 1; workers = @($items) } | ConvertTo-Json -Depth 6
        }
    }
}
catch {
    $message = $_.Exception.Message
    Write-BootstrapLog "FAILED stage=$($script:CurrentStage) error=$message"
    if (-not [string]::IsNullOrWhiteSpace($WorkerId) -and (Test-Path -LiteralPath (Join-Path $StateRoot ($WorkerId + '.json')))) {
        try {
            if ($script:CurrentStage -eq 'login-verified' -and $message -match 'Facebook (?:manual login is not complete|is not the foreground app)') {
                Update-BootstrapState -Id $WorkerId -Status 'awaiting-login'
            }
            else {
                Update-BootstrapState -Id $WorkerId -Status 'failed' -FailedStage $script:CurrentStage -ErrorMessage $message
            }
        }
        catch { Write-Warning "Could not update bootstrap failure state: $($_.Exception.Message)" }
    }
    throw
}
