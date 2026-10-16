[CmdletBinding()]
param(
    [int]$HealthTimeoutSeconds = 45,
    [int]$EmulatorBootTimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalConfigPath = Join-Path $Base 'facebook.local.json'
$WorkerConfigPath = Join-Path $Base 'facebook_workers.json'

function Get-JsonProperty {
    param(
        [Parameter(Mandatory = $true)][object]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Read-JsonObject {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing ${Label}: ${Path}. Run the project setup first."
    }
    try {
        $document = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    }
    catch {
        throw "Could not parse ${Label} at ${Path}: $($_.Exception.Message)"
    }
    if ($null -eq $document -or $document -is [System.Array]) {
        throw "$Label must contain a JSON object: $Path"
    }
    return $document
}

function Convert-ToPort {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$Name
    )
    [int]$parsed = 0
    if ($null -eq $Value -or -not [int]::TryParse([string]$Value, [ref]$parsed) -or $parsed -lt 1 -or $parsed -gt 65535) {
        throw "$Name must be an integer from 1 to 65535."
    }
    return $parsed
}

function Resolve-ConfiguredPath {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    if ([IO.Path]::IsPathRooted($Value)) {
        return [IO.Path]::GetFullPath($Value)
    }
    return [IO.Path]::GetFullPath((Join-Path $Base $Value))
}

function Set-ConfiguredEnvironment {
    param([object]$LocalConfig)

    $sdkValue = [string](Get-JsonProperty -Object $LocalConfig -Name 'sdk_root')
    $sdkRoot = Resolve-ConfiguredPath $sdkValue
    if ([string]::IsNullOrWhiteSpace($sdkRoot) -and -not [string]::IsNullOrWhiteSpace($env:FACEBOOK_SDK_ROOT)) {
        $sdkRoot = [IO.Path]::GetFullPath($env:FACEBOOK_SDK_ROOT)
    }
    if ([string]::IsNullOrWhiteSpace($sdkRoot) -and -not [string]::IsNullOrWhiteSpace($env:ANDROID_SDK_ROOT)) {
        $sdkRoot = [IO.Path]::GetFullPath($env:ANDROID_SDK_ROOT)
    }
    if ([string]::IsNullOrWhiteSpace($sdkRoot) -and (Test-Path -LiteralPath 'D:\Android\SdkManual')) {
        $sdkRoot = 'D:\Android\SdkManual'
    }

    $avdValue = [string](Get-JsonProperty -Object $LocalConfig -Name 'avd_home')
    $avdHome = Resolve-ConfiguredPath $avdValue
    if ([string]::IsNullOrWhiteSpace($avdHome) -and -not [string]::IsNullOrWhiteSpace($env:FACEBOOK_AVD_HOME)) {
        $avdHome = [IO.Path]::GetFullPath($env:FACEBOOK_AVD_HOME)
    }
    if ([string]::IsNullOrWhiteSpace($avdHome) -and -not [string]::IsNullOrWhiteSpace($env:ANDROID_AVD_HOME)) {
        $avdHome = [IO.Path]::GetFullPath($env:ANDROID_AVD_HOME)
    }
    if ([string]::IsNullOrWhiteSpace($avdHome) -and $sdkRoot -eq 'D:\Android\SdkManual') {
        $avdHome = 'D:\Android\AVDManual'
    }

    if (-not [string]::IsNullOrWhiteSpace($sdkRoot)) {
        $env:FACEBOOK_SDK_ROOT = $sdkRoot
        $env:ANDROID_HOME = $sdkRoot
        $env:ANDROID_SDK_ROOT = $sdkRoot
        $adb = Join-Path $sdkRoot 'platform-tools\adb.exe'
        if (Test-Path -LiteralPath $adb) {
            $env:FACEBOOK_ADB_EXE = $adb
            $env:PATH = (Split-Path -Parent $adb) + ';' + $env:PATH
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($avdHome)) {
        $env:FACEBOOK_AVD_HOME = $avdHome
        $env:ANDROID_AVD_HOME = $avdHome
    }

    $network = Get-JsonProperty -Object $LocalConfig -Name 'network'
    if ($null -ne $network) {
        $mode = [string](Get-JsonProperty -Object $network -Name 'mode')
        if ($mode -notin @('direct', 'upstream')) {
            throw 'facebook.local.json network.mode must be direct or upstream.'
        }
        $env:FACEBOOK_NETWORK_MODE = $mode
        if ($mode -eq 'upstream') {
            $proxy = [string](Get-JsonProperty -Object $network -Name 'upstream_url')
            if ([string]::IsNullOrWhiteSpace($proxy)) {
                throw 'facebook.local.json network.upstream_url is required in upstream mode.'
            }
            $env:FACEBOOK_UPSTREAM_PROXY = $proxy
        }
        else {
            $env:FACEBOOK_UPSTREAM_PROXY = $null
        }
    }

    $androidRoot = 'D:\Android'
    if (-not [string]::IsNullOrWhiteSpace($sdkRoot)) {
        if ((Split-Path -Leaf $sdkRoot) -ieq 'SdkManual') {
            $androidRoot = Split-Path -Parent $sdkRoot
        }
        else {
            $androidRoot = Split-Path -Parent $sdkRoot
        }
    }
    return [pscustomobject]@{
        SdkRoot = $sdkRoot
        AvdHome = $avdHome
        AndroidRoot = $androidRoot
    }
}

function Resolve-PowerShellExe {
    $candidate = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    $command = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($command) { return [string]$command.Source }
    throw 'Windows PowerShell executable could not be resolved.'
}

function Test-TcpPort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMilliseconds = 600
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        if (-not $task.Wait([Math]::Max(100, $TimeoutMilliseconds))) { return $false }
        return [bool]$client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-ApiHealth {
    param([Parameter(Mandatory = $true)][string]$HealthUri)
    try {
        return Invoke-RestMethod -Uri $HealthUri -TimeoutSec 2 -ErrorAction Stop
    }
    catch {
        return $null
    }
}

function Test-HealthCaptureActive {
    param([object]$Health)
    if ($null -eq $Health) { return $false }
    if ([string](Get-JsonProperty -Object $Health -Name 'worker') -eq 'busy') { return $true }
    $workers = @((Get-JsonProperty -Object $Health -Name 'workers'))
    foreach ($worker in $workers) {
        $currentJobId = [string](Get-JsonProperty -Object $worker -Name 'current_job_id')
        if (-not [string]::IsNullOrWhiteSpace($currentJobId)) { return $true }
    }
    return $false
}

function Test-HealthLeaseUncertain {
    param([object]$Health)
    if ($null -eq $Health) { return $false }
    $lease = Get-JsonProperty -Object $Health -Name 'capture_lease'
    return ([string](Get-JsonProperty -Object $lease -Name 'state') -eq 'uncertain')
}

function Wait-ForApiHealth {
    param(
        [Parameter(Mandatory = $true)][string]$HealthUri,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 45,
        [string]$StderrPath = ''
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(5, $TimeoutSeconds))
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            $detail = if (Test-Path -LiteralPath $StderrPath) { Get-Content -Raw -Encoding UTF8 -LiteralPath $StderrPath -ErrorAction SilentlyContinue } else { '' }
            throw "Capture API exited with code $($Process.ExitCode). $detail"
        }
        $health = Get-ApiHealth -HealthUri $HealthUri
        if ($null -ne $health -and [string](Get-JsonProperty -Object $health -Name 'service') -eq 'ready') {
            return $health
        }
        Start-Sleep -Seconds 1
    }
    throw "Capture API did not become ready at $HealthUri within $TimeoutSeconds seconds."
}

function Get-UiPage {
    param([Parameter(Mandatory = $true)][string]$UiUri)
    try {
        return Invoke-WebRequest -UseBasicParsing -Uri $UiUri -TimeoutSec 2 -ErrorAction Stop
    }
    catch {
        return $null
    }
}

function Test-ProjectUiPage {
    param([object]$Response)
    if ($null -eq $Response -or [int]$Response.StatusCode -ne 200) { return $false }
    return ([string]$Response.Content -match 'OmniCapture Console v2')
}

function Wait-ForProjectUi {
    param(
        [Parameter(Mandatory = $true)][string]$UiUri,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 45,
        [string]$StderrPath = ''
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(5, $TimeoutSeconds))
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            $detail = if (Test-Path -LiteralPath $StderrPath) { Get-Content -Raw -Encoding UTF8 -LiteralPath $StderrPath -ErrorAction SilentlyContinue } else { '' }
            throw "V2 UI server exited with code $($Process.ExitCode). $detail"
        }
        if (Test-ProjectUiPage (Get-UiPage -UiUri $UiUri)) { return }
        Start-Sleep -Seconds 1
    }
    throw "V2 UI server did not become ready at $UiUri within $TimeoutSeconds seconds."
}

$powerShellExe = Resolve-PowerShellExe
$setupScript = Join-Path $Base 'setup.ps1'
if (-not (Test-Path -LiteralPath $setupScript -PathType Leaf)) {
    throw "Missing setup script: $setupScript"
}
$setupProcess = Start-Process -FilePath $powerShellExe `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $setupScript, '-InstallOnly') `
    -WorkingDirectory $Base `
    -Wait `
    -NoNewWindow `
    -PassThru
if ($setupProcess.ExitCode -ne 0) {
    throw "Project setup failed with exit code $($setupProcess.ExitCode)."
}

$localConfig = Read-JsonObject -Path $LocalConfigPath -Label 'facebook.local.json'
$workerConfig = Read-JsonObject -Path $WorkerConfigPath -Label 'facebook_workers.json'

$apiPortValue = Get-JsonProperty -Object $localConfig -Name 'port'
$apiPort = if ($null -eq $apiPortValue) { 8765 } else { Convert-ToPort -Value $apiPortValue -Name 'facebook.local.json port' }
$configuredWorkers = @((Get-JsonProperty -Object $workerConfig -Name 'workers'))
if ($configuredWorkers.Count -eq 0) {
    throw 'facebook_workers.json must contain at least one worker.'
}
$primary = @($configuredWorkers | Where-Object { (Get-JsonProperty -Object $_ -Name 'enabled') -eq $true }) | Select-Object -First 1
if ($null -eq $primary) {
    throw 'facebook_workers.json has no enabled worker to use as the primary emulator.'
}

$workerId = [string](Get-JsonProperty -Object $primary -Name 'worker_id')
$serial = [string](Get-JsonProperty -Object $primary -Name 'serial')
$avdName = [string](Get-JsonProperty -Object $primary -Name 'avd_name')
if ([string]::IsNullOrWhiteSpace($workerId) -or [string]::IsNullOrWhiteSpace($serial) -or [string]::IsNullOrWhiteSpace($avdName)) {
    throw 'The first enabled worker must define worker_id, serial, and avd_name.'
}
$capturePort = Convert-ToPort -Value (Get-JsonProperty -Object $primary -Name 'capture_port') -Name "$workerId capture_port"
$nativePort = Convert-ToPort -Value (Get-JsonProperty -Object $primary -Name 'native_port') -Name "$workerId native_port"

$runtime = Set-ConfiguredEnvironment -LocalConfig $localConfig
$transportScript = Join-Path $Base 'facebook_transport.ps1'
if (-not (Test-Path -LiteralPath $transportScript -PathType Leaf)) {
    throw "Missing transport helper: $transportScript"
}
. $transportScript

$healthUri = "http://127.0.0.1:$apiPort/health"
$health = Get-ApiHealth -HealthUri $healthUri
$apiRunning = $false
if ($null -ne $health) {
    if ([string](Get-JsonProperty -Object $health -Name 'service') -ne 'ready') {
        throw "Port $apiPort responded, but it is not the configured Facebook Capture API. Existing service was left untouched."
    }
    $apiRunning = $true
    Write-Host "[one-click] Capture API already healthy on http://127.0.0.1:$apiPort."
}
elseif (Test-TcpPort -Port $apiPort) {
    throw "Port $apiPort is occupied but $healthUri is unavailable. Existing service was left untouched."
}

try {
    $adbState = [string](Get-FacebookAdbState -Serial $serial)
}
catch {
    $adbState = 'missing'
    Write-Warning "Could not query ADB for $serial; the stable starter will report the actionable device error."
}

if ($adbState -ne 'device') {
    if ($apiRunning -and (Test-HealthCaptureActive -Health $health)) {
        throw "The capture API reports an active capture while $serial is unavailable; no emulator or service was changed."
    }
    Write-Host "[one-click] Primary emulator $serial is $adbState; starting the configured AVD."

    $currentSessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
    if ($currentSessionId -eq 0) {
        if ($workerId -notin @('worker-01', 'worker-02')) {
            throw "Session 0 startup requires an interactive-session launcher for $workerId; supported launchers currently exist only for worker-01 and worker-02."
        }
        $visibleLauncher = Join-Path $Base 'launch_visible_in_user_session.ps1'
        if (-not (Test-Path -LiteralPath $visibleLauncher -PathType Leaf)) {
            throw "Missing interactive-session emulator launcher: $visibleLauncher"
        }
        Write-Host "[one-click] Session 0 detected; launching $workerId in the interactive user session before stable initialization."
        & $powerShellExe -NoProfile -ExecutionPolicy Bypass -File $visibleLauncher -WorkerId $workerId
        if ($LASTEXITCODE -ne 0) {
            throw "Interactive-session emulator launcher failed with exit code $LASTEXITCODE."
        }

        $interactiveDeadline = (Get-Date).AddSeconds([Math]::Max(20, $EmulatorBootTimeoutSeconds))
        do {
            Start-Sleep -Seconds 2
            try {
                $adbState = [string](Get-FacebookAdbState -Serial $serial)
            }
            catch {
                $adbState = 'missing'
            }
        } while ($adbState -ne 'device' -and (Get-Date) -lt $interactiveDeadline)
        if ($adbState -ne 'device') {
            throw "Interactive-session emulator did not become ADB-ready within $EmulatorBootTimeoutSeconds seconds: $serial"
        }
    }

    $stableScript = Join-Path $Base 'start_facebook_stable.ps1'
    if (-not (Test-Path -LiteralPath $stableScript -PathType Leaf)) {
        throw "Missing stable emulator starter: $stableScript"
    }
    $stableArguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $stableScript,
        '-Serial', $serial,
        '-AndroidRoot', $runtime.AndroidRoot,
        '-AvdName', $avdName,
        '-CapturePort', [string]$capturePort,
        '-NativePort', [string]$nativePort,
        '-BootTimeoutSeconds', [string]$EmulatorBootTimeoutSeconds,
        '-SkipFacebookUiCheck'
    )
    & $powerShellExe @stableArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Primary emulator starter failed with exit code $LASTEXITCODE."
    }
}
else {
    Write-Host "[one-click] Primary emulator $serial is already available; reusing it."
}

if (-not $apiRunning) {
    $apiScript = Join-Path $Base 'start_facebook_capture_api.ps1'
    if (-not (Test-Path -LiteralPath $apiScript -PathType Leaf)) {
        throw "Missing capture API starter: $apiScript"
    }
    $apiStdout = Join-Path $Base 'api-server.stdout.log'
    $apiStderr = Join-Path $Base 'api-server.stderr.log'
    $apiArguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $apiScript,
        '-Port', [string]$apiPort,
        '-Serial', $serial
    )
    $apiProcess = Start-Process -FilePath $powerShellExe `
        -ArgumentList $apiArguments `
        -WorkingDirectory $Base `
        -WindowStyle Hidden `
        -RedirectStandardOutput $apiStdout `
        -RedirectStandardError $apiStderr `
        -PassThru
    Write-Host "[one-click] Capture API started in the background (PID $($apiProcess.Id)); waiting for /health."
    $health = Wait-ForApiHealth -HealthUri $healthUri -Process $apiProcess -TimeoutSeconds $HealthTimeoutSeconds -StderrPath $apiStderr
    $apiRunning = $true
}
else {
    $health = Get-ApiHealth -HealthUri $healthUri
}

if ($null -eq $health -or [string](Get-JsonProperty -Object $health -Name 'service') -ne 'ready') {
    throw "Capture API is not healthy at $healthUri."
}

$primaryHealth = @((Get-JsonProperty -Object $health -Name 'workers') | Where-Object {
        $candidateId = [string](Get-JsonProperty -Object $_ -Name 'worker_id')
        $candidateSerial = [string](Get-JsonProperty -Object $_ -Name 'serial')
        $candidateId -eq $workerId -or $candidateSerial -eq $serial
    }) | Select-Object -First 1
if ($null -ne $primaryHealth -and [string](Get-JsonProperty -Object $primaryHealth -Name 'state') -eq 'failed') {
    $currentJobId = [string](Get-JsonProperty -Object $primaryHealth -Name 'current_job_id')
    $captureActive = Test-HealthCaptureActive -Health $health
    $leaseUncertain = Test-HealthLeaseUncertain -Health $health
    $noCaptureRunning = -not $captureActive -and [string]::IsNullOrWhiteSpace($currentJobId) -and -not $leaseUncertain
    if ($noCaptureRunning) {
        $workerUri = "http://127.0.0.1:$apiPort/v1/workers/$([Uri]::EscapeDataString($workerId))/enable"
        try {
            $null = Invoke-RestMethod -Method Post -Uri $workerUri -TimeoutSec 10 -ErrorAction Stop
            Write-Host "[one-click] Cleared stale failed state for $workerId."
        }
        catch {
            throw "Could not re-enable failed worker ${workerId}: $($_.Exception.Message)"
        }
    }
    else {
        Write-Warning "Worker $workerId is failed, but a capture is active or its lease is uncertain; it was left untouched."
    }
}

$uiPort = 8766
$uiUri = 'http://127.0.0.1:8766/'
$uiPage = Get-UiPage -UiUri $uiUri
if (Test-ProjectUiPage $uiPage) {
    Write-Host "[one-click] Project V2 UI already healthy at $uiUri; reusing it."
}
elseif (Test-TcpPort -Port $uiPort) {
    throw "Port $uiPort is occupied but does not serve the project V2 page. Existing service was left untouched."
}
else {
    $uiScript = Join-Path $Base 'start_ui_v2.ps1'
    if (-not (Test-Path -LiteralPath $uiScript -PathType Leaf)) {
        throw "Missing V2 UI starter: $uiScript"
    }
    $uiStdout = Join-Path $Base 'ui-server.stdout.log'
    $uiStderr = Join-Path $Base 'ui-server.stderr.log'
    $uiArguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $uiScript,
        '-UiPort', [string]$uiPort,
        '-ApiPort', [string]$apiPort,
        '-NoBrowser'
    )
    $uiProcess = Start-Process -FilePath $powerShellExe `
        -ArgumentList $uiArguments `
        -WorkingDirectory $Base `
        -WindowStyle Hidden `
        -RedirectStandardOutput $uiStdout `
        -RedirectStandardError $uiStderr `
        -PassThru
    Write-Host "[one-click] V2 UI server started in the background (PID $($uiProcess.Id)); waiting for $uiUri."
    Wait-ForProjectUi -UiUri $uiUri -Process $uiProcess -TimeoutSeconds $HealthTimeoutSeconds -StderrPath $uiStderr
}

Write-Host "[one-click] Runtime ready. Open the V2 page: $uiUri" -ForegroundColor Green
