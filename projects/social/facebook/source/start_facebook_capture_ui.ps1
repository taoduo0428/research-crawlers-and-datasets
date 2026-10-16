[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Serial = "emulator-5554"
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $PowerShellExe)) {
    $command = Get-Command powershell.exe -ErrorAction Stop
    $PowerShellExe = [string]$command.Source
}
$Url = "http://127.0.0.1:$Port/"
$HealthUrl = "http://127.0.0.1:$Port/health"

$alreadyReady = $false
try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
    $alreadyReady = $response.StatusCode -eq 200
}
catch {}

if (-not $alreadyReady) {
    $stdout = Join-Path $Base 'api-server.stdout.log'
    $stderr = Join-Path $Base 'api-server.stderr.log'
    $server = Start-Process -FilePath $PowerShellExe `
        -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $Base 'start_facebook_capture_api.ps1'),'-Port',[string]$Port,'-Serial',$Serial) `
        -WorkingDirectory $Base `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if ($server.HasExited) {
            $detail = if (Test-Path -LiteralPath $stderr) { Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue } else { '' }
            throw "Facebook Capture UI service exited early: $detail"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $alreadyReady = $true; break }
        }
        catch {}
        Start-Sleep -Milliseconds 500
    }
    if (-not $alreadyReady) { throw "Facebook Capture UI did not become ready within 20 seconds. Logs: $stdout ; $stderr" }
}

Start-Process $Url
Write-Output "Facebook Capture UI ready: $Url"
