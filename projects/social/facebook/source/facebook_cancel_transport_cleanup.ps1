[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Serial,
    [Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$CapturePort,
    [string]$BasePath = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
$BasePath = [IO.Path]::GetFullPath($BasePath)
$RuntimeHelper = Join-Path $BasePath 'facebook_runtime.ps1'
$TransportHelper = Join-Path $BasePath 'facebook_transport.ps1'
if (-not (Test-Path -LiteralPath $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
if (-not (Test-Path -LiteralPath $TransportHelper)) { throw "Missing transport helper: $TransportHelper" }
. $RuntimeHelper
. $TransportHelper

$expectedCaptureProxy = "127.0.0.1:$CapturePort"
$result = [ordered]@{
    version = 1
    serial = $Serial
    capture_port = $CapturePort
    transport_cleanup_state = 'failed'
    transport_cleanup_error = $null
    current_proxy = $null
    normal_proxy = $null
    proxy_readback = $null
    reverse_list = $null
    watchdog_pid = $null
}

try {
    # Read the device proxy before changing any transport state.  A different
    # value belongs to another owner and must remain untouched.
    $proxyRead = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'settings', 'get', 'global', 'http_proxy') -TimeoutSeconds 8
    if (-not $proxyRead.Success) {
        throw "Failed to read Android proxy (timeout=$($proxyRead.TimedOut) exit=$($proxyRead.ExitCode))"
    }
    $currentProxy = ([string]$proxyRead.Stdout).Trim()
    $result.current_proxy = $currentProxy

    $network = Get-FacebookNetworkConfig
    $normalProxy = [string]$network.normal_proxy
    $result.normal_proxy = $normalProxy
    if ($currentProxy -ne $expectedCaptureProxy -and $currentProxy -ne $normalProxy) {
        $result.transport_cleanup_state = 'blocked-external-proxy'
        $result.transport_cleanup_error = "Android proxy changed externally; left untouched: $currentProxy"
    }
    else {
        $marker = Get-FacebookCaptureMarker -Serial $Serial
        if ($marker) { throw 'Capture marker is still active; refusing transport cleanup.' }

        $ports = @(Get-FacebookNormalReversePorts -CapturePort $CapturePort)
        $result.reverse_list = Ensure-FacebookReverseMappings -Serial $Serial -Ports $ports

        if ($currentProxy -eq $expectedCaptureProxy) {
            $null = Set-FacebookNormalProxy -Serial $Serial -TimeoutSeconds 12
        }

        $proxyReadback = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'settings', 'get', 'global', 'http_proxy') -TimeoutSeconds 8
        if (-not $proxyReadback.Success) {
            throw "Failed to read back Android proxy (timeout=$($proxyReadback.TimedOut) exit=$($proxyReadback.ExitCode))"
        }
        $actualProxy = ([string]$proxyReadback.Stdout).Trim()
        $result.proxy_readback = $actualProxy
        if ($actualProxy -ne $normalProxy) { throw "Android proxy readback mismatch: expected=$normalProxy actual=$actualProxy" }

        $reverseReadback = Get-FacebookReverseList -Serial $Serial
        foreach ($port in @($ports | Sort-Object -Unique)) {
            if (-not (Test-FacebookReverseMapping -ReverseList $reverseReadback -Port $port)) {
                throw "adb reverse readback missing tcp:$port -> tcp:$port"
            }
        }
        $result.reverse_list = $reverseReadback

        $watchdogPid = Start-FacebookTransportWatch -Serial $Serial -BasePath $BasePath -CapturePort $CapturePort
        if (-not $watchdogPid) { throw 'Transport watchdog could not be started.' }
        $result.watchdog_pid = [int]$watchdogPid
        $result.transport_cleanup_state = 'restored'
    }
}
catch {
    $result.transport_cleanup_state = if ($result.transport_cleanup_state -eq 'blocked-external-proxy') {
        'blocked-external-proxy'
    }
    else {
        'failed'
    }
    $result.transport_cleanup_error = $_.Exception.Message
}

$result | ConvertTo-Json -Compress
if ($result.transport_cleanup_state -ne 'restored') { exit 1 }
exit 0
