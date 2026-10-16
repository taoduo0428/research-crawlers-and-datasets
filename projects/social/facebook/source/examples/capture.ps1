param(
    [string]$Url,
    [string]$JobId,
    [string]$BaseUrl = 'http://127.0.0.1:8765',
    [ValidateRange(1, 86400)][int]$TimeoutSeconds = 1800,
    [string]$OutputPath = 'reactors.csv'
)
$ErrorActionPreference = 'Stop'
if ([bool]$Url -eq [bool]$JobId) { throw 'Use either -Url or -JobId' }
$BaseUrl = $BaseUrl.TrimEnd('/')
Invoke-RestMethod "$BaseUrl/health" -TimeoutSec 30 | Out-Null
if (-not $JobId) {
    $body = @{ post_url = $Url; collect = @('reactions'); save_artifacts = @('progress', 'safe_capture') } | ConvertTo-Json -Compress
    $created = Invoke-RestMethod "$BaseUrl/v1/jobs" -Method Post -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 30
    $JobId = $created.job_id
}
Write-Output "job_id=$JobId"
$path = "$BaseUrl/v1/jobs/$JobId"
$timer = [Diagnostics.Stopwatch]::StartNew()
while ($true) {
    $job = Invoke-RestMethod $path -TimeoutSec 30
    if ($job.status -eq 'completed') { break }
    if ($job.status -in @('failed', 'cancelled')) { throw ($job | ConvertTo-Json -Depth 20 -Compress) }
    if ($job.status -notin @('queued', 'running', 'cancelling')) { throw "Unknown status: $($job.status)" }
    Invoke-RestMethod "$path/progress" -TimeoutSec 30 | ConvertTo-Json -Depth 30 -Compress
    $remaining = $TimeoutSeconds - $timer.Elapsed.TotalSeconds
    if ($remaining -le 0) { throw "Resume with -JobId $JobId; job was not cancelled" }
    Start-Sleep -Milliseconds ([int][Math]::Min(2000, $remaining * 1000))
}
$result = Invoke-RestMethod "$path/result" -TimeoutSec 30
Invoke-WebRequest "$path/artifacts/facebook-reactor-users.csv?download=1" -UseBasicParsing -TimeoutSec 30 -OutFile $OutputPath
$result | ConvertTo-Json -Depth 50
