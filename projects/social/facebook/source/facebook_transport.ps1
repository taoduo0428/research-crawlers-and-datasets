Set-StrictMode -Version 2.0

$FacebookTransportPlatformTools = Join-Path $PSScriptRoot 'platform-tools'
$FacebookTransportAdb = Join-Path $FacebookTransportPlatformTools 'adb.exe'
if ($env:FACEBOOK_ADB_EXE) {
    $FacebookTransportAdb = $env:FACEBOOK_ADB_EXE
    $FacebookTransportPlatformTools = Split-Path -Parent $FacebookTransportAdb
}
$FacebookNetstatExe = 'C:\Windows\System32\netstat.exe'
if (-not (Test-Path $FacebookNetstatExe)) {
    $FacebookNetstatExe = Join-Path $env:SystemRoot 'System32\netstat.exe'
}
if (-not (Test-Path $FacebookNetstatExe)) { throw 'Windows netstat executable could not be resolved.' }
if (Test-Path $FacebookTransportAdb) {
    $pathParts = @($env:PATH -split ';' | Where-Object { $_ })
    if ($pathParts.Count -eq 0 -or $pathParts[0] -ne $FacebookTransportPlatformTools) {
        $env:PATH = $FacebookTransportPlatformTools + ';' + $env:PATH
    }
}

function Get-FacebookNetworkConfig {
    $mode = $env:FACEBOOK_NETWORK_MODE
    $url = $env:FACEBOOK_UPSTREAM_PROXY
    $legacy = [string]::IsNullOrEmpty($mode) -and [string]::IsNullOrEmpty($url)
    if ([string]::IsNullOrEmpty($mode)) { $mode = 'upstream' }
    if ($legacy) { $url = 'http://127.0.0.1:7897' }
    if ($mode -cnotin @('direct', 'upstream')) { throw 'Invalid FACEBOOK_NETWORK_MODE; use direct or upstream.' }
    $uri = $null
    if ($url) {
        $valid = [Uri]::TryCreate($url, [UriKind]::Absolute, [ref]$uri)
        if (-not $valid -or $uri.Scheme -notin @('http','https') -or -not $uri.Host -or
            $uri.UserInfo -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment -or
            $uri.Port -lt 1 -or $uri.Port -gt 65535 -or $url -match '[\s\x00-\x1f\x7f\\"<>?#@]' -or $url -match ':/?$') {
            throw 'FACEBOOK_UPSTREAM_PROXY must be an HTTP(S) proxy origin without credentials, query or fragment.'
        }
    }
    if ($mode -eq 'upstream' -and -not $uri) { throw 'FACEBOOK_UPSTREAM_PROXY is required in upstream mode.' }
    $normalProxy = ':0'
    $reversePort = 0
    $relayPort = 0
    if ($mode -eq 'upstream') {
        if ($uri.Scheme -eq 'https') {
            # One passthrough relay per endpoint, shared by workers. Never replace a listener.
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($uri.AbsoluteUri)) }
            finally { $sha.Dispose() }
            $relayPort = 20000 + ([BitConverter]::ToUInt32($hash, 0) % 10000)
            $reversePort = $relayPort
            $normalProxy = "127.0.0.1:$relayPort"
        }
        elseif ($uri.IsLoopback) {
            $reversePort = $uri.Port
            $normalProxy = "127.0.0.1:$reversePort"
        }
        else { $normalProxy = '{0}:{1}' -f $uri.Host, $uri.Port }
    }
    return [pscustomobject]@{
        mode=$mode; upstream_url=$url; uri=$uri; legacy=$legacy
        normal_proxy=$normalProxy; reverse_port=[int]$reversePort; relay_port=[int]$relayPort
    }
}

function Get-FacebookMitmMode {
    $network = Get-FacebookNetworkConfig
    if ($network.mode -eq 'direct') { return 'regular' }
    return ('upstream:' + $network.upstream_url)
}

function Get-FacebookNormalReversePorts {
    param([int]$CapturePort = 8081)
    $network = Get-FacebookNetworkConfig
    if ($network.reverse_port -gt 0) {
        if ($network.reverse_port -eq $CapturePort) { throw 'Capture port conflicts with the normal upstream proxy port.' }
        $network.reverse_port
    }
    if ($CapturePort -gt 0) { $CapturePort }
}

function Test-FacebookUpstream {
    $network = Get-FacebookNetworkConfig
    if ($network.mode -eq 'direct') { return $true }
    $client = New-Object Net.Sockets.TcpClient
    try {
        $connect = $client.ConnectAsync($network.uri.DnsSafeHost, $network.uri.Port)
        if (-not $connect.Wait(3000)) { return $false }
        return $client.Connected
    }
    catch { return $false }
    finally { $client.Dispose() }
}

function Ensure-FacebookNormalNetwork {
    $network = Get-FacebookNetworkConfig
    if ($network.mode -eq 'direct') { return $true }
    if ($network.legacy) { return (Ensure-FacebookHostProxy) }
    if (-not (Test-FacebookUpstream)) { throw 'Configured upstream proxy is unreachable.' }
    if ($network.relay_port -gt 0) {
        $exe = Join-Path $PSScriptRoot '.venv\Scripts\mitmdump.exe'
        if (-not (Test-Path -LiteralPath $exe)) { throw 'HTTPS upstream requires project .venv mitmdump.' }
        $relayMode = Get-FacebookMitmMode
        $relayArgs = @('--quiet','--listen-host','127.0.0.1','--listen-port',[string]$network.relay_port,
            '--mode',$relayMode,'--ignore-hosts','.*')
        if (Test-FacebookHostListener -Port $network.relay_port) {
            $owned = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
                $_.ExecutablePath -eq $exe -and $_.CommandLine -and
                $_.CommandLine.Contains($relayMode) -and
                $_.CommandLine -match ('--listen-port\s+' + $network.relay_port + '(?:\s|$)') -and
                $_.CommandLine -match '--ignore-hosts\s+\.\*'
            })
            if ($owned.Count -eq 0) { throw 'HTTPS upstream relay port is occupied; existing process was left untouched.' }
            return $true
        }
        $process = Start-Process -FilePath $exe -ArgumentList $relayArgs -WindowStyle Hidden -PassThru
        for ($attempt=0; $attempt -lt 20; $attempt++) {
            if ($process.HasExited) { throw 'HTTPS upstream relay exited during startup.' }
            if (Test-FacebookHostListener -Port $network.relay_port) { return $true }
            Start-Sleep -Milliseconds 250
        }
        # Only terminate the process started here.
        if (-not $process.HasExited) { $process.Kill() }
        throw 'HTTPS upstream relay did not become ready.'
    }
    return $true
}

function Set-FacebookNormalProxy {
    param([Parameter(Mandatory=$true)][string]$Serial, [int]$TimeoutSeconds=10)
    $network = Get-FacebookNetworkConfig
    return Set-FacebookAndroidProxy -Serial $Serial -Proxy $network.normal_proxy -TimeoutSeconds $TimeoutSeconds
}

function Get-FacebookAdbState {
    param([Parameter(Mandatory = $true)][string]$Serial)
    $result = Invoke-AdbCommandBounded -Arguments @('devices') -TimeoutSeconds 6
    $raw = if ($result.Success) { [string]$result.Stdout } else { '' }
    $match = [regex]::Match($raw, "(?m)^$([regex]::Escape($Serial))\s+(\S+)\s*$")
    if (-not $match.Success) { return 'missing' }
    return $match.Groups[1].Value.Trim().ToLowerInvariant()
}

function Ensure-FacebookAdbReady {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$TimeoutSeconds = 15,
        [switch]$RecoverOffline,
        [switch]$AllowServerRestart
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(3, $TimeoutSeconds))
    $serverRestarted = $false
    $offlineSeenAt = $null
    $missingSeenAt = $null
    while ((Get-Date) -lt $deadline) {
        $state = Get-FacebookAdbState -Serial $Serial
        if ($state -eq 'device') { return $true }
        if ($state -eq 'missing') {
            if (-not $missingSeenAt) { $missingSeenAt = Get-Date }
            Start-Sleep -Seconds 2
            if ((Get-FacebookAdbState -Serial $Serial) -eq 'device') { return $true }
            $captureActive = $false
            if (Get-Command Get-FacebookCaptureMarker -ErrorAction SilentlyContinue) {
                $captureActive = [bool](Get-FacebookCaptureMarker)
            }
            $missingSeconds = [int][Math]::Floor(((Get-Date) - $missingSeenAt).TotalSeconds)
            if ($AllowServerRestart -and -not $captureActive -and -not $serverRestarted -and $missingSeconds -ge 8) {
                Write-Warning '[transport] ADB device remained missing; restarting ADB server.'
                $null = Invoke-AdbCommandBounded -Arguments @('kill-server') -TimeoutSeconds 8
                Start-Sleep -Seconds 1
                $null = Invoke-AdbCommandBounded -Arguments @('start-server') -TimeoutSeconds 8
                $serverRestarted = $true
                Start-Sleep -Seconds 3
            }
            continue
        }
        if ($RecoverOffline -and $state -eq 'offline') {
            if (-not $offlineSeenAt) { $offlineSeenAt = Get-Date }
            # Let a transient transport flap settle before doing anything that
            # can disrupt in-flight adb commands.
            Start-Sleep -Seconds 2
            if ((Get-FacebookAdbState -Serial $Serial) -eq 'device') { return $true }
            $null = Invoke-AdbCommandBounded -Arguments @('reconnect','offline') -TimeoutSeconds 8
            Start-Sleep -Seconds 2
            if ((Get-FacebookAdbState -Serial $Serial) -eq 'device') { return $true }
            $captureActive = $false
            if (Get-Command Get-FacebookCaptureMarker -ErrorAction SilentlyContinue) {
                $captureActive = [bool](Get-FacebookCaptureMarker)
            }
            $offlineSeconds = [int][Math]::Floor(((Get-Date) - $offlineSeenAt).TotalSeconds)
            if ($AllowServerRestart -and -not $captureActive -and -not $serverRestarted -and $offlineSeconds -ge 8) {
                Write-Warning '[transport] ADB transport remained offline; restarting ADB server.'
                $null = Invoke-AdbCommandBounded -Arguments @('kill-server') -TimeoutSeconds 8
                Start-Sleep -Seconds 1
                $null = Invoke-AdbCommandBounded -Arguments @('start-server') -TimeoutSeconds 8
                $serverRestarted = $true
                Start-Sleep -Seconds 3
                continue
            }
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Ensure-FacebookAndroidServicesReady {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(4, $TimeoutSeconds))
    while ((Get-Date) -lt $deadline) {
        if ((Get-FacebookAdbState -Serial $Serial) -ne 'device') {
            Start-Sleep -Seconds 2
            continue
        }
        try {
            $serviceResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','service','check','connectivity') -TimeoutSeconds 8
            $service = if ($serviceResult.Success) { ([string]$serviceResult.Stdout).Trim() } else { '' }
            if ($service -match '(?i)connectivity:\s*found') { return $true }
        }
        catch {}
        Start-Sleep -Seconds 2
    }
    return $false
}

function Get-FacebookActiveDefaultNetwork {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$TimeoutSeconds = 10
    )
    try {
        $networkResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','dumpsys','connectivity') -TimeoutSeconds ([Math]::Max(1, $TimeoutSeconds))
        if (-not $networkResult.Success) { return 'none' }
        $raw = [string]$networkResult.Stdout
    }
    catch {
        return 'none'
    }
    $match = [regex]::Match($raw, '(?m)^Active default network:\s*(.+?)\s*$')
    if (-not $match.Success) { return 'none' }
    $value = $match.Groups[1].Value.Trim()
    if ([string]::IsNullOrWhiteSpace($value)) { return 'none' }
    return $value
}

function Ensure-FacebookAndroidNetwork {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$Attempts = 5,
        [int]$DelaySeconds = 2
    )
    if (-not (Ensure-FacebookAndroidServicesReady -Serial $Serial -TimeoutSeconds 30)) {
        throw 'Android connectivity service is not ready.'
    }
    $network = Get-FacebookActiveDefaultNetwork -Serial $Serial
    if ($network -ne 'none') { return $network }

    # The emulator can use either validated CELLULAR/eth0 or WIFI/wlan0 as its
    # default network. Do not immediately tear down Wi-Fi just because the
    # default selector is briefly between networks; first allow Connectivity
    # to settle and make both transports available.
    Write-Warning '[transport] Android has no active default network; waiting for Connectivity to settle.'
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        Start-Sleep -Seconds ([Math]::Max(1, $DelaySeconds))
        $network = Get-FacebookActiveDefaultNetwork -Serial $Serial
        if ($network -ne 'none') {
            Write-Host "[transport] Android default network recovered without transport reset: $network"
            return $network
        }
    }

    Write-Warning '[transport] default network is still absent; enabling mobile data and Wi-Fi without cycling either transport.'
    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','svc','data','enable') -TimeoutSeconds 8
    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','svc','wifi','enable') -TimeoutSeconds 8
    Start-Sleep -Seconds 2
    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','cmd','wifi','connect-network','AndroidWifi','open','-r','none') -TimeoutSeconds 10
    for ($attempt = 1; $attempt -le [Math]::Max(4, $Attempts); $attempt++) {
        Start-Sleep -Seconds ([Math]::Max(1, $DelaySeconds))
        $network = Get-FacebookActiveDefaultNetwork -Serial $Serial
        if ($network -ne 'none') {
            Write-Host "[transport] Android default network restored after enabling transports: $network"
            return $network
        }
    }

    for ($cycle = 1; $cycle -le 2; $cycle++) {
        Write-Warning "[transport] enabling transports did not restore a default network; AndroidWifi reset cycle $cycle/2."
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','svc','wifi','disable') -TimeoutSeconds 8
        Start-Sleep -Seconds 2
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','svc','wifi','enable') -TimeoutSeconds 8
        Start-Sleep -Seconds 3
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','cmd','wifi','connect-network','AndroidWifi','open','-r','none') -TimeoutSeconds 10
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','svc','data','enable') -TimeoutSeconds 8

        for ($attempt = 1; $attempt -le [Math]::Max(1, $Attempts); $attempt++) {
            Start-Sleep -Seconds ([Math]::Max(1, $DelaySeconds))
            $network = Get-FacebookActiveDefaultNetwork -Serial $Serial
            if ($network -ne 'none') {
                Write-Host "[transport] Android default network restored on reset cycle ${cycle}: $network"
                return $network
            }
        }
    }
    throw 'Android default network is still unavailable after passive settle, transport enable, and two AndroidWifi reset cycles.'
}

function Get-FacebookReverseList {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$TimeoutSeconds = 8
    )
    $result = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('reverse','--list') -TimeoutSeconds ([Math]::Max(1, $TimeoutSeconds))
    if (-not $result.Success) { return '' }
    return ([string]$result.Stdout).Trim()
}

function Test-FacebookReverseMapping {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$ReverseList,
        [Parameter(Mandatory = $true)][int]$Port
    )
    if ([string]::IsNullOrWhiteSpace($ReverseList)) { return $false }
    return [regex]::IsMatch($ReverseList, "(?m)\btcp:$Port\s+tcp:$Port\b")
}

function Ensure-FacebookReverseMappings {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int[]]$Ports = @(Get-FacebookNormalReversePorts)
    )
    $uniquePorts = @($Ports | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
    foreach ($port in $uniquePorts) {
        $reverseOk = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('reverse',("tcp:{0}" -f $port),("tcp:{0}" -f $port)) -TimeoutSeconds 8
        if (-not $reverseOk) { throw "Failed to install adb reverse mapping for tcp:$port" }
    }
    $reverseList = Get-FacebookReverseList -Serial $Serial
    foreach ($port in $uniquePorts) {
        if (-not (Test-FacebookReverseMapping -ReverseList $reverseList -Port $port)) {
            throw "adb reverse mapping tcp:$port -> tcp:$port was not confirmed"
        }
    }
    return $reverseList
}

function Initialize-FacebookColdBootTransport {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int[]]$Ports = @(Get-FacebookNormalReversePorts),
        [int]$AdbTimeoutSeconds = 20
    )
    if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds $AdbTimeoutSeconds -RecoverOffline -AllowServerRestart)) {
        throw "ADB device is not ready for cold-boot transport initialization: $Serial"
    }
    $null = Ensure-FacebookNormalNetwork
    $network = Get-FacebookNetworkConfig
    $reverseList = Ensure-FacebookReverseMappings -Serial $Serial -Ports $Ports
    $capturePorts = @($Ports | Where-Object { $_ -ne $network.reverse_port })
    $capturePort = if ($capturePorts.Count -gt 0) { [int]$capturePorts[0] } else { 8081 }
    # Clear a persisted capture/legacy proxy as soon as Android settings is ready.
    if (-not (Get-FacebookCaptureMarker -Serial $Serial)) { $null = Set-FacebookNormalProxy -Serial $Serial }
    return [pscustomobject]@{
        network_mode = $network.mode
        upstream_reachable = [bool](Test-FacebookUpstream)
        reverse_normal = ($network.reverse_port -eq 0 -or (Test-FacebookReverseMapping -ReverseList $reverseList -Port $network.reverse_port))
        host_7897_listening = ($network.reverse_port -eq 7897 -and (Test-FacebookHostListener -Port 7897))
        reverse_7897 = [bool](Test-FacebookReverseMapping -ReverseList $reverseList -Port 7897)
        reverse_8081 = [bool](Test-FacebookReverseMapping -ReverseList $reverseList -Port 8081)
        reverse_capture = [bool](Test-FacebookReverseMapping -ReverseList $reverseList -Port $capturePort)
        capture_port = $capturePort
        reverse_list = $reverseList
    }
}

function Set-FacebookAndroidProxy {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$Port = 0,
        [string]$Proxy = '',
        [int]$TimeoutSeconds = 10
    )
    $proxy = if ($Proxy) { $Proxy } else { "127.0.0.1:$Port" }
    $put = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','settings','put','global','http_proxy',$proxy) -TimeoutSeconds $TimeoutSeconds
    if (-not $put.Success) { throw "Failed to set Android proxy to $proxy (timeout=$($put.TimedOut) exit=$($put.ExitCode))" }
    $get = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','settings','get','global','http_proxy') -TimeoutSeconds $TimeoutSeconds
    if (-not $get.Success) { throw "Failed to verify Android proxy $proxy (timeout=$($get.TimedOut) exit=$($get.ExitCode))" }
    $actual = ([string]$get.Stdout).Trim()
    if ($actual -ne $proxy) { throw 'Android proxy verification failed.' }
    return $actual
}

function Get-FacebookNetstatLines {
    $process = $null
    try {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $FacebookNetstatExe
        $startInfo.Arguments = '-ano'
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) { return @() }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(8000)) {
            try { $process.Kill() } catch {}
            return @()
        }
        $stdout = ''
        try { if ($stdoutTask.Wait(1000)) { $stdout = [string]$stdoutTask.Result } } catch {}
        return @($stdout -split "`r?`n" | Where-Object { $_ })
    }
    catch {
        return @()
    }
    finally {
        if ($process) { $process.Dispose() }
    }
}

function Test-FacebookHostListener {
    param([Parameter(Mandatory = $true)][int]$Port)
    $pattern = ":$Port\s+.*LISTENING"
    return [bool](Get-FacebookNetstatLines | Select-String $pattern | Select-Object -First 1)
}

function Ensure-FacebookHostProxy {
    param(
        [int]$Port = 7897,
        [string]$ClashPath = 'D:\Clash Verge\clash-verge.exe',
        [int]$WaitSeconds = 15
    )
    if (Test-FacebookHostListener -Port $Port) { return $true }
    if (Test-Path $ClashPath) {
        Write-Warning "[transport] host proxy :$Port is not listening; starting Clash Verge."
        Start-Process -FilePath $ClashPath -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
        $deadline = (Get-Date).AddSeconds([Math]::Max(3, $WaitSeconds))
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 1
            if (Test-FacebookHostListener -Port $Port) { return $true }
        }
    }
    throw "Host proxy port $Port is not listening."
}

function Test-FacebookDeviceProxyPort {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSeconds = 6
    )
    return [bool](Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','nc','-z','-w','3','127.0.0.1',[string]$Port) -TimeoutSeconds ([Math]::Max(1, $TimeoutSeconds)))
}

function Get-FacebookTransportSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int[]]$RequiredReversePorts = @(Get-FacebookNormalReversePorts),
        [int]$CommandTimeoutSeconds = 10
    )
    $commandTimeout = [Math]::Max(1, $CommandTimeoutSeconds)
    $proxyResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','settings','get','global','http_proxy') -TimeoutSeconds $commandTimeout
    $proxy = if ($proxyResult.Success) { ([string]$proxyResult.Stdout).Trim() } else { '' }
    $network = Get-FacebookActiveDefaultNetwork -Serial $Serial -TimeoutSeconds $commandTimeout
    $reverseList = Get-FacebookReverseList -Serial $Serial -TimeoutSeconds $commandTimeout
    $reverseState = [ordered]@{}
    foreach ($port in @($RequiredReversePorts | Sort-Object -Unique)) {
        $reverseState[[string]$port] = [bool](Test-FacebookReverseMapping -ReverseList $reverseList -Port $port)
    }
    $networkConfig = Get-FacebookNetworkConfig
    $device7897 = $false
    if ($networkConfig.reverse_port -eq 7897 -and (Test-FacebookReverseMapping -ReverseList $reverseList -Port 7897)) {
        $device7897 = Test-FacebookDeviceProxyPort -Serial $Serial -Port 7897 -TimeoutSeconds ([Math]::Min(6, $commandTimeout))
    }
    return [pscustomobject]@{
        proxy = $proxy
        active_default_network = $network
        reverse_list = $reverseList
        reverse = $reverseState
        host_7897_listening = ($networkConfig.reverse_port -eq 7897 -and (Test-FacebookHostListener -Port 7897))
        device_7897_reachable = [bool]$device7897
    }
}

function Get-FacebookCaptureMarkerPath {
    param([string]$Serial = '')
    if ([string]::IsNullOrWhiteSpace($Serial)) {
        return (Join-Path $PSScriptRoot '.facebook-capture-active.json')
    }
    $safeSerial = $Serial -replace '[^A-Za-z0-9_.-]', '_'
    return (Join-Path $PSScriptRoot ('.facebook-capture-active.{0}.json' -f $safeSerial))
}

function Set-FacebookCaptureMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][int]$OwnerPid,
        [Parameter(Mandatory = $true)][int]$MitmPid,
        [Parameter(Mandatory = $true)][int]$Port
    )
    [ordered]@{
        owner_pid = $OwnerPid
        mitm_pid = $MitmPid
        port = $Port
        serial = $Serial
        started_at = (Get-Date).ToString('o')
    } | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 (Get-FacebookCaptureMarkerPath -Serial $Serial)
}

function Get-FacebookCaptureMarker {
    param([string]$Serial = '')
    $paths = if ([string]::IsNullOrWhiteSpace($Serial)) {
        @(
            Get-FacebookCaptureMarkerPath
            Get-ChildItem -LiteralPath $PSScriptRoot -Filter '.facebook-capture-active.*.json' -File -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty FullName
        )
    }
    else {
        @(Get-FacebookCaptureMarkerPath -Serial $Serial)
    }
    foreach ($path in $paths) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        try {
            $marker = Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json
            $ownerPid = [int]$marker.owner_pid
            $mitmPid = [int]$marker.mitm_pid
            $port = [int]$marker.port
            if ($ownerPid -le 0 -or $mitmPid -le 0 -or $port -le 0) { throw 'invalid capture marker' }
            $owner = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
            $mitm = Get-Process -Id $mitmPid -ErrorAction SilentlyContinue
            if (-not $owner -or -not $mitm) {
                Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $path
                continue
            }
            return $marker
        }
        catch {
            Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $path
        }
    }
    return $null
}

function Clear-FacebookCaptureMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$OwnerPid = 0
    )
    $path = Get-FacebookCaptureMarkerPath -Serial $Serial
    if (-not (Test-Path -LiteralPath $path)) { return }
    if ($OwnerPid -gt 0) {
        try {
            $marker = Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json
            if ([int]$marker.owner_pid -ne $OwnerPid) { return }
        }
        catch {}
    }
    Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $path
}

function Ensure-FacebookProjectTransport {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$CapturePort = 0
    )
    $allowRestart = -not [bool](Get-FacebookCaptureMarker)
    if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 18 -RecoverOffline -AllowServerRestart:$allowRestart)) {
        throw "ADB device is not ready: $Serial"
    }
    if (-not (Ensure-FacebookAndroidServicesReady -Serial $Serial -TimeoutSeconds 30)) {
        throw "Android connectivity service is not ready: $Serial"
    }
    $repaired = $false
    $null = Ensure-FacebookNormalNetwork
    $network = Get-FacebookNetworkConfig
    $captureMarker = Get-FacebookCaptureMarker -Serial $Serial
    $activeCapturePort = if ($captureMarker) { [int]$captureMarker.port } else { 0 }
    $workerCapturePort = if ($CapturePort -gt 0) { $CapturePort } elseif ($activeCapturePort -gt 0) { $activeCapturePort } else { 8081 }
    $beforeNetwork = Get-FacebookActiveDefaultNetwork -Serial $Serial
    if ($beforeNetwork -eq 'none') {
        $null = Ensure-FacebookAndroidNetwork -Serial $Serial
        $repaired = $true
    }

    $requiredPorts = @(Get-FacebookNormalReversePorts -CapturePort $workerCapturePort)
    if ($activeCapturePort -gt 0) { $requiredPorts += $activeCapturePort }
    $beforeReverse = Get-FacebookReverseList -Serial $Serial
    $missingPorts = @($requiredPorts | Where-Object { -not (Test-FacebookReverseMapping -ReverseList $beforeReverse -Port $_) })
    if ($missingPorts.Count -gt 0) {
        $null = Ensure-FacebookReverseMappings -Serial $Serial -Ports $requiredPorts
        $repaired = $true
    }

    $proxyRead = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','settings','get','global','http_proxy') -TimeoutSeconds 10
    $proxy = if ($proxyRead.Success) { ([string]$proxyRead.Stdout).Trim() } else { '' }
    $captureActive = [bool]$captureMarker
    if ($captureActive) {
        $expectedCaptureProxy = "127.0.0.1:$activeCapturePort"
        if ($proxy -ne $expectedCaptureProxy) {
            $proxy = Set-FacebookAndroidProxy -Serial $Serial -Port $activeCapturePort
            $repaired = $true
        }
    }
    elseif ($proxy -ne $network.normal_proxy) {
        $proxy = Set-FacebookNormalProxy -Serial $Serial
        $repaired = $true
    }

    if ($network.reverse_port -gt 0 -and -not (Test-FacebookDeviceProxyPort -Serial $Serial -Port $network.reverse_port)) {
        Write-Warning '[transport] normal proxy is not reachable; rebuilding adb reverse mappings.'
        $null = Ensure-FacebookReverseMappings -Serial $Serial -Ports $requiredPorts
        Start-Sleep -Seconds 1
        if ($network.reverse_port -gt 0 -and -not (Test-FacebookDeviceProxyPort -Serial $Serial -Port $network.reverse_port)) {
            throw 'Device cannot reach the configured normal proxy through adb reverse.'
        }
        $repaired = $true
    }

    $snapshot = Get-FacebookTransportSnapshot -Serial $Serial -RequiredReversePorts $requiredPorts
    return [pscustomobject]@{
        network_mode = $network.mode
        upstream_reachable = [bool](Test-FacebookUpstream)
        reverse_normal = ($network.reverse_port -eq 0 -or [bool]$snapshot.reverse[[string]$network.reverse_port])
        proxy = [string]$snapshot.proxy
        active_default_network = [string]$snapshot.active_default_network
        host_7897_listening = [bool]$snapshot.host_7897_listening
        device_7897_reachable = [bool]$snapshot.device_7897_reachable
        reverse_7897 = [bool]$snapshot.reverse['7897']
        reverse_8081 = [bool]$snapshot.reverse['8081']
        reverse_capture = [bool]$snapshot.reverse[[string]$workerCapturePort]
        capture_port = $workerCapturePort
        capture_active = [bool]$captureActive
        repaired = [bool]$repaired
    }
}

function Start-FacebookTransportWatch {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [string]$BasePath = $PSScriptRoot,
        [int]$CapturePort = 8081
    )
    $watchScript = Join-Path $BasePath 'facebook_network_watch.ps1'
    if (-not (Test-Path $watchScript)) { return $null }
    $existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '(?i)^powershell(?:\.exe)?$' -and
        $_.CommandLine -and
        $_.CommandLine -match '(?i)(^|\s)-File(\s|$)' -and
        $_.CommandLine -match ('(?i)(?:^|\s)-File\s+"?' + [regex]::Escape($watchScript) + '"?(?:\s|$)') -and
        $_.CommandLine -match ('(?i)(?:^|\s)-Serial\s+"?' + [regex]::Escape($Serial) + '"?(?:\s|$)')
    } | Select-Object -First 1
    # Environment belongs to the parent process. Recreate our exact worker watcher
    # so an earlier legacy watcher cannot undo a newly selected direct route.
    if ($existing) {
        if (Get-FacebookCaptureMarker -Serial $Serial) { return $existing.ProcessId }
        $null = Stop-FacebookTransportWatch -Serial $Serial -BasePath $BasePath
    }
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', ('"' + $watchScript + '"'), '-Serial', $Serial, '-CapturePort', [string]$CapturePort
    ) -WindowStyle Hidden -PassThru
    return $process.Id
}

function Stop-FacebookTransportWatch {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [string]$BasePath = $PSScriptRoot
    )
    $watchScript = Join-Path $BasePath 'facebook_network_watch.ps1'
    $watchers = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ProcessId -ne $PID -and
            $_.Name -match '(?i)^powershell(?:\.exe)?$' -and
            $_.CommandLine -and
            $_.CommandLine -match '(?i)(^|\s)-File(\s|$)' -and
            $_.CommandLine -match ('(?i)(?:^|\s)-File\s+"?' + [regex]::Escape($watchScript) + '"?(?:\s|$)') -and
            $_.CommandLine -match ('(?i)(?:^|\s)-Serial\s+"?' + [regex]::Escape($Serial) + '"?(?:\s|$)')
        }
    )
    $stopped = 0
    foreach ($watcher in $watchers) {
        # Kill any in-flight adb child first. Otherwise killing only the watcher
        # can orphan the shell probe that capture is trying to avoid racing.
        $children = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ParentProcessId -eq $watcher.ProcessId -and $_.Name -match '(?i)^adb(?:\.exe)?$'
        })
        foreach ($child in $children) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $watcher.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped++
    }
    if ($stopped -gt 0) { Start-Sleep -Milliseconds 500 }
    return $stopped
}

function Reset-FacebookAndroidWifi {
    param([Parameter(Mandatory = $true)][string]$Serial)
    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','svc','wifi','disable') -TimeoutSeconds 8
    Start-Sleep -Seconds 2
    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','svc','wifi','enable') -TimeoutSeconds 8
    Start-Sleep -Seconds 3
    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','cmd','wifi','connect-network','AndroidWifi','open','-r','none') -TimeoutSeconds 10
    return (Ensure-FacebookAndroidNetwork -Serial $Serial -Attempts 10 -DelaySeconds 2)
}

function Invoke-AdbCommandBounded {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds = 20
    )
    $process = $null
    try {
        $quotedArgs = foreach ($argument in $Arguments) {
            $value = [string]$argument
            if ($value -notmatch '[\s"]') { $value }
            else { '"' + $value.Replace('"', '\"') + '"' }
        }
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = 'adb'
        $startInfo.Arguments = ($quotedArgs -join ' ')
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            return [pscustomobject]@{ Success=$false; TimedOut=$false; ExitCode=$null; Stdout=''; Stderr='start-failed' }
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $completed = $process.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)
        if (-not $completed) {
            try { $process.Kill() } catch {}
            try { $null = $process.WaitForExit(2000) } catch {}
            return [pscustomobject]@{ Success=$false; TimedOut=$true; ExitCode=$null; Stdout=''; Stderr='timeout' }
        }
        $exitCode = $process.ExitCode
        $stdout = ''
        $stderr = ''
        try { if ($stdoutTask.Wait(1000)) { $stdout = [string]$stdoutTask.Result } } catch {}
        try { if ($stderrTask.Wait(1000)) { $stderr = [string]$stderrTask.Result } } catch {}
        return [pscustomobject]@{
            Success = ($exitCode -eq 0)
            TimedOut = $false
            ExitCode = $exitCode
            Stdout = $stdout
            Stderr = $stderr
        }
    }
    catch {
        return [pscustomobject]@{ Success=$false; TimedOut=$false; ExitCode=$null; Stdout=''; Stderr=$_.Exception.Message }
    }
    finally {
        if ($process) { $process.Dispose() }
    }
}

function Invoke-FacebookAdbCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds = 20
    )
    return Invoke-AdbCommandBounded -Arguments (@('-s', $Serial) + $Arguments) -TimeoutSeconds $TimeoutSeconds
}

function Invoke-FacebookAdbQuiet {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds = 20
    )
    $result = Invoke-FacebookAdbCommand -Serial $Serial -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds
    return [bool]$result.Success
}

function Invoke-FacebookAdbStdoutToFile {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$LocalPath,
        [int]$TimeoutSeconds = 12
    )
    $process = $null
    $file = $null
    try {
        $parent = Split-Path -Parent $LocalPath
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        $allArgs = @('-s',$Serial) + $Arguments
        $quotedArgs = foreach ($argument in $allArgs) {
            $value = [string]$argument
            if ($value -notmatch '[\s"]') { $value }
            else { '"' + $value.Replace('"','\"') + '"' }
        }
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = 'adb'
        $startInfo.Arguments = ($quotedArgs -join ' ')
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            return [pscustomobject]@{ Success=$false; TimedOut=$false; ExitCode=$null; Stderr='start-failed' }
        }
        $file = [IO.File]::Open($LocalPath,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None)
        $copyTask = $process.StandardOutput.BaseStream.CopyToAsync($file)
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $completed = $process.WaitForExit([Math]::Max(1,$TimeoutSeconds) * 1000)
        if (-not $completed) {
            try { $process.Kill() } catch {}
            try { $null = $process.WaitForExit(2000) } catch {}
            return [pscustomobject]@{ Success=$false; TimedOut=$true; ExitCode=$null; Stderr='timeout' }
        }
        $exitCode = $process.ExitCode
        $copyOk = $false
        try { $copyOk = $copyTask.Wait(3000) } catch {}
        try { $file.Flush() } catch {}
        $stderr = ''
        try { if ($stderrTask.Wait(1000)) { $stderr=[string]$stderrTask.Result } } catch {}
        return [pscustomobject]@{
            Success=($exitCode -eq 0 -and $copyOk)
            TimedOut=$false
            ExitCode=$exitCode
            Stderr=$stderr
        }
    }
    catch {
        return [pscustomobject]@{ Success=$false; TimedOut=$false; ExitCode=$null; Stderr=$_.Exception.Message }
    }
    finally {
        if ($file) { try { $file.Dispose() } catch {} }
        if ($process) { try { $process.Dispose() } catch {} }
    }
}

function Test-FacebookFrameworkServicesReady {
    param([Parameter(Mandatory = $true)][string]$Serial)
    # Probe only the four framework services capture automation depends on.
    # A full service-list probe can time out during emulator boot even when these are ready.
    foreach ($serviceName in @("activity","package","window","accessibility")) {
        $result = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @("shell","service","check",$serviceName) -TimeoutSeconds 4
        if (-not $result.Success) { return $false }
        $raw = ([string]$result.Stdout).Trim()
        $expected = "Service ${serviceName}: found"
        if ($raw -ne $expected) { return $false }
    }
    return $true
}
function Ensure-FacebookFrameworkServicesReady {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$TimeoutSeconds = 45
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(5, $TimeoutSeconds))
    do {
        if (Test-FacebookFrameworkServicesReady -Serial $Serial) { return $true }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Test-FacebookUiDumpHelperInstalled {
    param([Parameter(Mandatory = $true)][string]$Serial)
    $result = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','pm','path','com.openai.facebookuidump') -TimeoutSeconds 6
    return [bool]($result.Success -and ([string]$result.Stdout -match 'package:'))
}

function Get-FacebookUiDumpHelperVersionCode {
    param([Parameter(Mandatory = $true)][string]$Serial)
    $result = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','dumpsys','package','com.openai.facebookuidump') -TimeoutSeconds 8
    if (-not $result.Success) { return 0 }
    $match = [regex]::Match([string]$result.Stdout, '(?m)\bversionCode=([0-9]+)\b')
    if (-not $match.Success) { return 0 }
    return [int]$match.Groups[1].Value
}

function Ensure-FacebookUiDumpHelper {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [string]$BasePath = $PSScriptRoot
    )
    $requiredVersionCode = 5
    if ((Test-FacebookUiDumpHelperInstalled -Serial $Serial) -and (Get-FacebookUiDumpHelperVersionCode -Serial $Serial) -ge $requiredVersionCode) { return $true }
    $apk = Join-Path $BasePath 'android-ui-dump-helper\facebook-ui-dump-helper.apk'
    if (-not (Test-Path -LiteralPath $apk)) {
        Write-Warning "[ui] instrumentation fallback APK is missing: $apk"
        return $false
    }
    if (-not (Ensure-FacebookFrameworkServicesReady -Serial $Serial -TimeoutSeconds 30)) {
        Write-Warning '[ui] Android framework services are not ready; helper install deferred.'
        return $false
    }
    $remote = '/data/local/tmp/facebook-ui-dump-helper.apk'
    $push = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('push',$apk,$remote) -TimeoutSeconds 15
    if (-not $push.Success) {
        Write-Warning '[ui] instrumentation fallback APK push failed.'
        return $false
    }
    $install = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','pm','install','-r','-t',$remote) -TimeoutSeconds 35
    if (-not $install.Success) {
        Write-Warning "[ui] instrumentation fallback APK install failed: $($install.Stderr)"
        return $false
    }
    return (Test-FacebookUiDumpHelperInstalled -Serial $Serial)
}

function Invoke-FacebookFastUiDump {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string]$LocalPath,
        [int]$TimeoutSeconds = 12,
        [switch]$RequireFreshResult
    )
    if (-not (Test-FacebookUiDumpHelperInstalled -Serial $Serial)) {
        # Keep the normal fast path unchanged. Only when the helper is missing
        # do one bounded self-heal attempt before reporting failure.
        if (-not (Ensure-FacebookUiDumpHelper -Serial $Serial -BasePath $PSScriptRoot)) {
            return [pscustomobject]@{ Success=$false; RootPackage=''; NodeCount=0; Error='helper-not-installed' }
        }
    }
    $instrument = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @(
        'shell','am','instrument','-w','-r','com.openai.facebookuidump/.DumpInstrumentation'
    ) -TimeoutSeconds ([Math]::Max(6, $TimeoutSeconds))
    if (-not $instrument.Success) {
        return [pscustomobject]@{
            Success=$false; RootPackage=''; NodeCount=0;
            Error= if ($instrument.TimedOut) { 'instrument-timeout' } else { 'instrument-failed' }
        }
    }
    if ($RequireFreshResult) {
        $output = [string]$instrument.Stdout
        $status = [regex]::Matches($output, '(?m)^[ \t]*INSTRUMENTATION_RESULT:[ \t]*status=([^\r\n]*)\r?$')
        $nodes = [regex]::Matches($output, '(?m)^[ \t]*INSTRUMENTATION_RESULT:[ \t]*nodes=([0-9]+)[ \t]*\r?$')
        $code = [regex]::Matches($output, '(?m)^[ \t]*INSTRUMENTATION_CODE:[ \t]*(-?[0-9]+)[ \t]*\r?$')
        $nodeCount = 0
        $positiveNodes = [bool]($nodes.Count -eq 1 -and [int]::TryParse($nodes[0].Groups[1].Value, [ref]$nodeCount) -and $nodeCount -gt 0)
        if ($status.Count -ne 1 -or $status[0].Groups[1].Value.Trim() -cne 'ok' -or
            -not $positiveNodes -or $code.Count -ne 1 -or $code[0].Groups[1].Value -ne '-1') {
            # Instrumentation can exit 0 after RESULT_CANCELED, leaving its
            # previous XML intact. Never pull that file for a strict Share dump.
            return [pscustomobject]@{ Success=$false; RootPackage=''; NodeCount=0; Error='instrument-result-unverified' }
        }
    }
    $copy = Invoke-FacebookAdbStdoutToFile -Serial $Serial -Arguments @(
        'exec-out','run-as','com.openai.facebookuidump','cat','files/facebook-fast-ui.xml'
    ) -LocalPath $LocalPath -TimeoutSeconds 8
    if (-not $copy.Success -or -not (Test-Path -LiteralPath $LocalPath)) {
        return [pscustomobject]@{ Success=$false; RootPackage=''; NodeCount=0; Error='helper-output-read-failed' }
    }
    try { $raw = [IO.File]::ReadAllText($LocalPath,[Text.Encoding]::UTF8) }
    catch { return [pscustomobject]@{ Success=$false; RootPackage=''; NodeCount=0; Error='helper-output-decode-failed' } }
    if ($raw -notmatch '<hierarchy\b' -or $raw -notmatch '<node\b') {
        return [pscustomobject]@{ Success=$false; RootPackage=''; NodeCount=0; Error='helper-output-invalid' }
    }
    $rootPackage = ''
    $packageMatch = [regex]::Match($raw, 'package="([^"]+)"')
    if ($packageMatch.Success) { $rootPackage = $packageMatch.Groups[1].Value }
    return [pscustomobject]@{
        Success=$true
        RootPackage=$rootPackage
        NodeCount=[regex]::Matches($raw, '<node\b').Count
        Error=''
    }
}

function Get-FacebookNativeScrollPort {
    return 18766
}

function Invoke-FacebookNativeScrollCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [int]$Port = 18766,
        [int]$TimeoutMs = 1500
    )
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $connectTask = $client.ConnectAsync('127.0.0.1',$Port)
        if (-not $connectTask.Wait([Math]::Max(100,$TimeoutMs))) {
            return [pscustomobject]@{ Success=$false; Response=''; Error='connect-timeout' }
        }
        $client.ReceiveTimeout = [Math]::Max(100,$TimeoutMs)
        $client.SendTimeout = [Math]::Max(100,$TimeoutMs)
        $stream = $client.GetStream()
        $writer = New-Object IO.StreamWriter($stream,[Text.UTF8Encoding]::new($false),1024,$true)
        $reader = New-Object IO.StreamReader($stream,[Text.Encoding]::UTF8,$false,1024,$true)
        $writer.NewLine = "`n"
        $writer.WriteLine($Command)
        $writer.Flush()
        $response = $reader.ReadLine()
        return [pscustomobject]@{ Success=[bool]($response -and $response.StartsWith('OK ')); Response=[string]$response; Error='' }
    }
    catch {
        return [pscustomobject]@{ Success=$false; Response=''; Error=$_.Exception.Message }
    }
    finally {
        if ($client) { try { $client.Dispose() } catch {} }
    }
}

function Start-FacebookNativeScrollController {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$Port = 18766,
        [int]$TimeoutSeconds = 8
    )
    if (-not (Ensure-FacebookUiDumpHelper -Serial $Serial -BasePath $PSScriptRoot)) { return $false }
    $forward = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('forward',("tcp:{0}" -f $Port),("tcp:{0}" -f $Port)) -TimeoutSeconds 6
    if (-not $forward.Success) { return $false }
    $ping = Invoke-FacebookNativeScrollCommand -Command 'PING' -Port $Port -TimeoutMs 400
    if ($ping.Success -and $ping.Response -eq 'OK PONG') { return $true }
    $args = @('-s',$Serial,'shell','am','instrument','-w','-r','-e','mode','server','-e','port',[string]$Port,'com.openai.facebookuidump/.DumpInstrumentation')
    $null = Start-Process -FilePath 'adb' -ArgumentList $args -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds([Math]::Max(2,$TimeoutSeconds))
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 100
        $ping = Invoke-FacebookNativeScrollCommand -Command 'PING' -Port $Port -TimeoutMs 400
        if ($ping.Success -and $ping.Response -eq 'OK PONG') { return $true }
    }
    return $false
}

function Invoke-FacebookNativeReactionScroll {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$Count = 1,
        [int]$GapMs = 200,
        [int]$DurationMs = 300,
        [int]$StartY = 1700,
        [int]$EndY = 250,
        [int]$Port = 18766,
        [int]$TimeoutMs = 2500
    )
    if (-not (Start-FacebookNativeScrollController -Serial $Serial -Port $Port -TimeoutSeconds 8)) {
        return [pscustomobject]@{ Success=$false; Scrolled=0; Response=''; Error='controller-unavailable' }
    }
    $boundedCount = [Math]::Max(1,[Math]::Min(8,$Count))
    $boundedGap = [Math]::Max(0,[Math]::Min(1000,$GapMs))
    $boundedDuration = [Math]::Max(80,[Math]::Min(1200,$DurationMs))
    $boundedStartY = [Math]::Max(1,[Math]::Min(4000,$StartY))
    $boundedEndY = [Math]::Max(1,[Math]::Min(4000,$EndY))
    # UiAutomation injectInputEvent(..., true) can spend roughly one second
    # waiting for each gesture to settle. A fixed timeout that works for one
    # swipe therefore truncates a healthy four-swipe pipeline burst.
    $requiredCommandTimeoutMs = 2000 + `
        ($boundedCount * ($boundedDuration + 1000)) + `
        ([Math]::Max(0, $boundedCount - 1) * $boundedGap)
    $nativeCommandTimeoutFloorMs = 30000
    $effectiveTimeoutMs = [Math]::Max(
        $nativeCommandTimeoutFloorMs,
        [Math]::Max([Math]::Max(500,$TimeoutMs), $requiredCommandTimeoutMs)
    )
    $command = "SCROLL {0} {1}" -f $boundedCount,$boundedGap
    $result = Invoke-FacebookNativeScrollCommand -Command $command -Port $Port -TimeoutMs $effectiveTimeoutMs
    if ($result.Response -eq 'ERR root-null') {
        # A freshly attached UiAutomation can briefly have no active root even
        # though its server and Facebook are both healthy.
        Start-Sleep -Milliseconds 700
        $result = Invoke-FacebookNativeScrollCommand -Command $command -Port $Port -TimeoutMs $effectiveTimeoutMs
    }
    $scrolled = 0
    if ($result.Response -match '^OK SCROLL ([0-9]+)$') { $scrolled = [int]$Matches[1] }
    return [pscustomobject]@{ Success=[bool]($result.Success -and $scrolled -gt 0); Scrolled=$scrolled; Response=[string]$result.Response; Error=[string]$result.Error }
}

function Invoke-FacebookNativeReactionCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$Port = 18766,
        [int]$TimeoutMs = 2500
    )
    if (-not (Start-FacebookNativeScrollController -Serial $Serial -Port $Port -TimeoutSeconds 8)) {
        return [pscustomobject]@{ Success=$false; Response=''; Error='controller-unavailable' }
    }
    $effectiveTimeoutMs = [Math]::Max(1500, $TimeoutMs)
    $result = Invoke-FacebookNativeScrollCommand -Command 'CHECK_REACTION' -Port $Port -TimeoutMs $effectiveTimeoutMs
    if ($result.Response -eq 'ERR root-null') {
        Start-Sleep -Milliseconds 700
        $result = Invoke-FacebookNativeScrollCommand -Command 'CHECK_REACTION' -Port $Port -TimeoutMs $effectiveTimeoutMs
    }
    return $result
}

function Stop-FacebookNativeScrollController {
    param([string]$Serial = '', [int]$Port = 18766)
    $null = Invoke-FacebookNativeScrollCommand -Command 'STOP' -Port $Port -TimeoutMs 800
    if ($Serial) { $null = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('forward','--remove',("tcp:{0}" -f $Port)) -TimeoutSeconds 5 }
}

function Invoke-FacebookUiSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string]$LocalPath,
        [string]$RemotePath = '/sdcard/facebook-ui-snapshot.xml',
        [int]$LegacyTimeoutSeconds = 10,
        [int]$LegacyRetryCount = 1,
        [int]$FastTimeoutSeconds = 12
    )
    $legacyError = ''
    for ($attempt = 0; $attempt -le [Math]::Max(0, $LegacyRetryCount); $attempt++) {
        $dump = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','uiautomator','dump',$RemotePath) -TimeoutSeconds ([Math]::Max(3,$LegacyTimeoutSeconds))
        if ($dump.Success) {
            $pull = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('pull',$RemotePath,$LocalPath) -TimeoutSeconds ([Math]::Max(5,$LegacyTimeoutSeconds))
            if ($pull.Success -and (Test-Path -LiteralPath $LocalPath)) {
                try {
                    $raw = [IO.File]::ReadAllText($LocalPath)
                    if ($raw -match '<hierarchy\b' -and $raw -match '<node\b') {
                        $legacyNodeCount = [regex]::Matches($raw,'<node\b').Count
                        $legacyLabelCount = [regex]::Matches($raw,'(?:text|content-desc)="[^"]+"').Count
                        if ($legacyNodeCount -ge 3 -or $legacyLabelCount -ge 1) {
                            return [pscustomobject]@{
                                Success=$true; Source='legacy-uiautomator'; RootPackage=(Get-FacebookUiSurfacePackage -Raw $raw)
                                NodeCount=$legacyNodeCount; Error=''; LegacyAttempts=($attempt+1)
                            }
                        }
                        # uiautomator can exit 0 while returning only a blank root.
                        # Treat this as a semantic failure so the independent
                        # instrumentation path can inspect the real active window.
                        $legacyError = 'legacy-placeholder'
                        break
                    }
                    $legacyError = 'legacy-output-invalid'
                }
                catch { $legacyError = 'legacy-output-read-failed' }
            }
            else { $legacyError = 'legacy-pull-failed' }
        }
        else {
            $legacyError = if ($dump.TimedOut) { 'legacy-timeout' } else { 'legacy-dump-failed' }
            # A timed-out uiautomator is the known failure mode. Retrying the same
            # idle-waiting CLI wastes another full timeout; jump directly to the
            # independent instrumentation path.
            if ($dump.TimedOut) { break }
        }
        if ($attempt -lt $LegacyRetryCount) { Start-Sleep -Milliseconds 500 }
    }

    $fast = Invoke-FacebookFastUiDump -Serial $Serial -LocalPath $LocalPath -TimeoutSeconds ([Math]::Max(6,$FastTimeoutSeconds))
    if ($fast.Success) {
        return [pscustomobject]@{
            Success=$true; Source='instrumentation'; RootPackage=$fast.RootPackage
            NodeCount=$fast.NodeCount; Error=''; LegacyAttempts=0
        }
    }
    return [pscustomobject]@{
        Success=$false; Source=''; RootPackage=''; NodeCount=0
        Error=($legacyError + ';' + $fast.Error).Trim(';'); LegacyAttempts=0
    }
}

function Get-FacebookUiNodeCenterByResourceId {
    param(
        [Parameter(Mandatory = $true)][string]$Raw,
        [Parameter(Mandatory = $true)][string]$ResourceId
    )
    foreach ($tagMatch in [regex]::Matches($Raw, '<node\b[^>]*>')) {
        $tag = $tagMatch.Value
        $rid = [regex]::Match($tag, 'resource-id="([^"]*)"')
        if (-not $rid.Success -or $rid.Groups[1].Value -ne $ResourceId) { continue }
        $bounds = [regex]::Match($tag, 'bounds="\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]"')
        if (-not $bounds.Success) { continue }
        $x1=[int]$bounds.Groups[1].Value; $y1=[int]$bounds.Groups[2].Value
        $x2=[int]$bounds.Groups[3].Value; $y2=[int]$bounds.Groups[4].Value
        return [pscustomobject]@{ X=[int](($x1+$x2)/2); Y=[int](($y1+$y2)/2) }
    }
    return $null
}

function Dismiss-FacebookAnrWait {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string]$Raw
    )
    $wait = Get-FacebookUiNodeCenterByResourceId -Raw $Raw -ResourceId 'android:id/aerr_wait'
    if (-not $wait) { return $false }
    return [bool](Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','input','tap',[string]$wait.X,[string]$wait.Y) -TimeoutSeconds 5)
}

function Dismiss-FacebookSystemUiAnr {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string]$Raw
    )
    return (Dismiss-FacebookAnrWait -Serial $Serial -Raw $Raw)
}

function Start-FacebookAppBounded {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$TimeoutSeconds = 15,
        [switch]$ForceStop
    )
    if ($ForceStop) {
        if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'am', 'force-stop', 'com.facebook.katana') -TimeoutSeconds 10)) {
            Write-Warning '[transport] Facebook force-stop failed/timed out.'
            return $false
        }
        Start-Sleep -Milliseconds 500
    }
    $started = [bool](Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @(
        'shell', 'am', 'start',
        '-a', 'android.intent.action.MAIN',
        '-c', 'android.intent.category.LAUNCHER',
        '-n', 'com.facebook.katana/.LoginActivity'
    ) -TimeoutSeconds ([Math]::Max(5, $TimeoutSeconds)))
    if (-not $started) { Write-Warning '[transport] Facebook am start failed/timed out.' }
    return $started
}

function Start-FacebookUrlBounded {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 15
    )
    if ([string]::IsNullOrWhiteSpace($Url)) {
        Write-Warning '[transport] Facebook URL launch was requested without a URL.'
        return $false
    }

    # Facebook currently exposes more than one VIEW activity for the same
    # permalink.  Package-only resolution therefore opens Android's chooser;
    # an explicit component keeps batch URL switches inside Facebook.
    $component = 'com.facebook.katana/.IntentUriHandler'
    $flags = '0x34000000' # NEW_TASK | CLEAR_TOP | SINGLE_TOP
    # adb shell rejoins argv on Android. Host-side quoting alone does not
    # preserve URL query separators or literal shell metacharacters there.
    $shellUrl = $Url
    if ($Url -match '[\s&|;<>()$`"]') { $shellUrl = "'" + $Url.Replace("'", "'\''") + "'" }
    $result = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @(
        'shell', 'am', 'start', '-W',
        '-a', 'android.intent.action.VIEW',
        '-d', $shellUrl,
        '-n', $component,
        '-f', $flags
    ) -TimeoutSeconds ([Math]::Max(5, $TimeoutSeconds))
    if (-not $result.Success) {
        $detail = ([string]$result.Stderr).Trim()
        if (-not $detail) { $detail = "exit=$($result.ExitCode)" }
        Write-Warning "[transport] Facebook explicit URL launch failed ($component): $detail"
        return $false
    }
    return $true
}

function Get-FacebookUiSurfacePackage {
    param([Parameter(Mandatory = $true)][string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw)) { return '' }
    $match = [regex]::Match($Raw, 'package="([^"]+)"')
    if (-not $match.Success) { return '' }
    return $match.Groups[1].Value
}

function Get-FacebookUiBlockingState {
    param([Parameter(Mandatory = $true)][string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw)) { return '' }
    # Android ANR dialogs expose stable framework resource IDs regardless of
    # locale. Prefer structural detection over localized title text.
    if (
        $Raw -match 'android:id/aerr_wait' -and
        $Raw -match 'android:id/alertTitle' -and
        $Raw -match '(?i)text="[^"]*system[^"]*"'
    ) {
        return 'system-ui-not-responding'
    }
    if ($Raw -match 'android:id/aerr_wait' -and $Raw -match 'android:id/aerr_close') { return 'app-not-responding' }
    if (
        $Raw -match "(?i)system\s+ui.*(?:isn(?:'|&apos;)t|is\s+not|not)\s+responding" -or
        $Raw -match '(?i)(?:process|进程)[^<\r\n]*system[^<\r\n]*(?:not responding|没有响应|无响应|沒有回應)' -or
        $Raw -match '系统界面没有响应|系統介面沒有回應'
    ) {
        return 'system-ui-not-responding'
    }
    if (
        $Raw -match "(?i)isn(?:'|&apos;)t responding|not responding" -or
        $Raw -match '没有响应|无响应|沒有回應'
    ) {
        return 'app-not-responding'
    }
    return ''
}

function Test-FacebookUiOffline {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw)) { return $false }
    $noNetwork = -join @([char]0x65E0,[char]0x7F51,[char]0x7EDC,[char]0x8FDE,[char]0x63A5)
    $checkNetwork = -join @([char]0x8BF7,[char]0x68C0,[char]0x67E5,[char]0x7F51,[char]0x7EDC,[char]0x8FDE,[char]0x63A5)
    $englishNoInternet = 'No internet connection'
    return [bool](
        $Raw.Contains($noNetwork) -or
        $Raw.Contains($checkNetwork) -or
        $Raw.IndexOf($englishNoInternet, [StringComparison]::OrdinalIgnoreCase) -ge 0
    )
}

function Confirm-FacebookUiOnline {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$CapturePort = 8081,
        [int]$Attempts = 2,
        [int]$WaitSeconds = 8
    )
    $remote = '/sdcard/facebook-network-health.xml'
    $forceRestartNext = $true
    for ($attempt = 1; $attempt -le [Math]::Max(1, $Attempts); $attempt++) {
        $networkFailure = $false
        $null = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $CapturePort
        # Successful capture teardown normally leaves Facebook on a usable
        # surface. Probe that surface in place before taking the recovery path;
        # a healthy package alone is insufficient because an offline or blocked
        # window can still expose a Facebook process.
        $healthLocal = Join-Path $env:TEMP ("facebook-network-health-in-place-{0}-{1}" -f $PID, $attempt)
        try {
            $healthSnapshot = Invoke-FacebookUiSnapshot -Serial $Serial -LocalPath $healthLocal -RemotePath $remote -LegacyTimeoutSeconds 10 -LegacyRetryCount 0 -FastTimeoutSeconds 12
            if ($healthSnapshot.Success) {
                $healthRaw = Get-Content -Raw -Encoding UTF8 $healthLocal
                $healthSurfacePackage = Get-FacebookUiSurfacePackage -Raw $healthRaw
                $healthBlockingState = Get-FacebookUiBlockingState -Raw $healthRaw
                $healthLabelCount = [regex]::Matches($healthRaw, '(?:text|content-desc)="[^"]+"').Count
                $healthOffline = Test-FacebookUiOffline -Raw $healthRaw
                if (
                    $healthSurfacePackage -eq 'com.facebook.katana' -and
                    -not $healthBlockingState -and
                    -not $healthOffline -and
                    $healthLabelCount -ge 3
                ) {
                    Write-Host "[transport] Facebook UI is healthy in place after capture (labels=$healthLabelCount); preserving the existing process."
                    return $true
                }
            }
        }
        catch {
            Write-Warning "[transport] in-place Facebook UI health probe failed; continuing automatic recovery: $($_.Exception.Message)"
        }
        finally {
            Remove-Item -Force -ErrorAction SilentlyContinue $healthLocal
        }
        if ($forceRestartNext) {
            $launchOk = Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15 -ForceStop
        }
        else {
            $launchOk = Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15
        }
        $forceRestartNext = $false
        if (-not $launchOk) {
            Write-Warning "[transport] bounded Facebook launch timed out/failed on health attempt $attempt."
            if ($attempt -lt $Attempts) { Start-Sleep -Seconds 2 }
            continue
        }
        Start-Sleep -Seconds ([Math]::Max(4, $WaitSeconds))
        $local = Join-Path $env:TEMP ("facebook-network-health-{0}-{1}.xml" -f $PID, $attempt)
        try {
            $snapshot = Invoke-FacebookUiSnapshot -Serial $Serial -LocalPath $local -RemotePath $remote -LegacyTimeoutSeconds 10 -LegacyRetryCount 0 -FastTimeoutSeconds 12
            if (-not $snapshot.Success) { throw "UI snapshot failed: $($snapshot.Error)" }
            if ($snapshot.Source -eq 'instrumentation') {
                Write-Warning "[transport] UI health used instrumentation fallback (package=$($snapshot.RootPackage) nodes=$($snapshot.NodeCount))."
            }
            $raw = Get-Content -Raw -Encoding UTF8 $local
            $surfacePackage = Get-FacebookUiSurfacePackage -Raw $raw
            if ($surfacePackage -eq 'com.google.android.permissioncontroller') {
                Write-Warning "[transport] Android Permission Controller is covering Facebook; dismissing the overlay with Back and relaunching Facebook."
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4') -TimeoutSeconds 5
                Start-Sleep -Seconds 1
                $overlayLaunchOk = Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15
                if (-not $overlayLaunchOk) { Write-Warning '[transport] bounded Facebook relaunch after Permission Controller timed out/failed.' }
                Start-Sleep -Seconds 3
                $overlaySnapshot = Invoke-FacebookUiSnapshot -Serial $Serial -LocalPath $local -RemotePath $remote -LegacyTimeoutSeconds 10 -LegacyRetryCount 0 -FastTimeoutSeconds 12
                if ($overlaySnapshot.Success) {
                    $raw = Get-Content -Raw -Encoding UTF8 $local
                    $surfacePackage = Get-FacebookUiSurfacePackage -Raw $raw
                }
            }
            $blockingState = Get-FacebookUiBlockingState -Raw $raw
            if ($blockingState -in @('system-ui-not-responding','app-not-responding')) {
                Write-Warning "[transport] Android ANR ($blockingState) is covering Facebook on attempt $attempt; choosing Wait and refreshing the window."
                if (Dismiss-FacebookAnrWait -Serial $Serial -Raw $raw) {
                    Start-Sleep -Seconds 3
                    $anrSnapshot = Invoke-FacebookUiSnapshot -Serial $Serial -LocalPath $local -RemotePath $remote -LegacyTimeoutSeconds 10 -LegacyRetryCount 0 -FastTimeoutSeconds 12
                    if ($anrSnapshot.Success) {
                        $raw = Get-Content -Raw -Encoding UTF8 $local
                        $surfacePackage = Get-FacebookUiSurfacePackage -Raw $raw
                        $blockingState = Get-FacebookUiBlockingState -Raw $raw
                    }
                }
            }
            $labelCount = [regex]::Matches($raw, '(?:text|content-desc)="[^"]+"').Count
            if ($blockingState -in @('system-ui-not-responding','app-not-responding')) {
                Write-Warning "[transport] Android ANR ($blockingState) remains visible on attempt $attempt."
            }
            elseif ($surfacePackage -ne 'com.facebook.katana') {
                Write-Warning "[transport] Facebook is not the accessible foreground surface on attempt $attempt (package=$surfacePackage)."
            }
            elseif ($blockingState) {
                Write-Warning "[transport] Facebook UI is blocked by $blockingState on attempt $attempt; next attempt will use a fresh app process."
                $forceRestartNext = $true
            }
            elseif (Test-FacebookUiOffline -Raw $raw) {
                $networkFailure = $true
                Write-Warning "[transport] Facebook UI still reports offline on attempt $attempt."
            }
            elseif ($labelCount -lt 3) {
                Write-Warning "[transport] Facebook package is foreground but UI is still blank/placeholder on attempt $attempt (labels=$labelCount)."
            }
            else {
                return $true
            }
        }
        finally {
            Remove-Item -Force -ErrorAction SilentlyContinue $local
        }
        if ($attempt -lt $Attempts) {
            if ($networkFailure) {
                $null = Reset-FacebookAndroidWifi -Serial $Serial
            }
            $null = Ensure-FacebookReverseMappings -Serial $Serial -Ports @(7897, $CapturePort)
            $null = Set-FacebookAndroidProxy -Serial $Serial -Port 7897
        }
    }
    throw 'Facebook UI did not become healthy after automatic recovery.'
}
