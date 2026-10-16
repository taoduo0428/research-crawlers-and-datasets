Set-StrictMode -Version 2.0

function Resolve-FacebookOpenSsl {
    param([string]$OpenSslPath = '')
    if (-not [string]::IsNullOrWhiteSpace($OpenSslPath)) {
        if (-not (Test-Path -LiteralPath $OpenSslPath)) { throw "OpenSSL not found: $OpenSslPath" }
        return (Resolve-Path -LiteralPath $OpenSslPath).Path
    }
    $command = Get-Command openssl.exe -ErrorAction SilentlyContinue
    if ($command) { return [string]$command.Source }
    foreach ($candidate in @(
        'C:\Program Files\Git\usr\bin\openssl.exe',
        'C:\Program Files\Git\mingw64\bin\openssl.exe'
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw 'OpenSSL is required to calculate the Android legacy CA subject hash.'
}

function Get-FacebookMitmCertificateHash {
    param(
        [Parameter(Mandatory = $true)][string]$CertificatePath,
        [string]$OpenSslPath = ''
    )
    if (-not (Test-Path -LiteralPath $CertificatePath)) { throw "mitmproxy CA not found: $CertificatePath" }
    $openssl = Resolve-FacebookOpenSsl -OpenSslPath $OpenSslPath
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $openssl x509 -inform PEM -subject_hash_old -in $CertificatePath -noout 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($exitCode -ne 0) { throw "OpenSSL could not read the mitmproxy CA: $($output -join ' ')" }
    $hash = [string](@($output | Where-Object { [string]$_ -match '^[0-9a-fA-F]{8}$' }) | Select-Object -First 1)
    if ($hash -notmatch '^[0-9a-fA-F]{8}$') { throw 'OpenSSL did not return an Android legacy CA subject hash.' }
    return $hash.ToLowerInvariant()
}

function Wait-FacebookCertificateBoot {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$TimeoutSeconds = 180
    )
    if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds $TimeoutSeconds -RecoverOffline -AllowServerRestart)) {
        throw "ADB did not return after certificate setup reboot: $Serial"
    }
    $deadline = (Get-Date).AddSeconds([Math]::Max(30, $TimeoutSeconds))
    while ((Get-Date) -lt $deadline) {
        $boot = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','getprop','sys.boot_completed') -TimeoutSeconds 5
        if ($boot.Success -and ([string]$boot.Stdout).Trim() -eq '1') { return }
        Start-Sleep -Seconds 2
    }
    throw "Android did not finish booting after certificate setup reboot: $Serial"
}

function Enable-FacebookAdbRoot {
    param([Parameter(Mandatory = $true)][string]$Serial)
    $root = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('root') -TimeoutSeconds 20
    if (-not $root.Success) { throw "adb root failed on ${Serial}: $($root.Stderr)" }
    if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 30 -RecoverOffline)) {
        throw "ADB did not return after adb root: $Serial"
    }
    $identity = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','id','-u') -TimeoutSeconds 6
    if (-not $identity.Success -or ([string]$identity.Stdout).Trim() -ne '0') {
        throw "The Android image does not provide root adbd: $Serial"
    }
}

function Invoke-FacebookCertificateRemount {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [int]$BootTimeoutSeconds = 180
    )
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Enable-FacebookAdbRoot -Serial $Serial
        $remount = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('remount') -TimeoutSeconds 60
        $details = (([string]$remount.Stdout) + "`n" + ([string]$remount.Stderr)).Trim()
        if ($remount.Success -and $details -notmatch '(?i)reboot.*take effect|reboot your device') { return }
        if ($details -match '(?i)disable-verity') {
            $verity = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('disable-verity') -TimeoutSeconds 30
            if (-not $verity.Success) { throw "adb disable-verity failed on ${Serial}: $($verity.Stderr)" }
        }
        if ($attempt -eq 3) { throw "adb remount did not become writable on ${Serial}: $details" }
        $reboot = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('reboot') -TimeoutSeconds 10
        if (-not $reboot.Success) { throw "Android reboot failed during certificate setup: $Serial" }
        Wait-FacebookCertificateBoot -Serial $Serial -TimeoutSeconds $BootTimeoutSeconds
    }
}

function Restore-FacebookMitmCertificateRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [Parameter(Mandatory = $true)][string]$CertificateHash
    )
    if ($CertificateHash -notmatch '^[0-9a-f]{8}$') { throw "Invalid Android CA hash: $CertificateHash" }
    $systemPath = "/system/etc/security/cacerts/$CertificateHash.0"
    $present = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','test','-f',$systemPath) -TimeoutSeconds 6
    if (-not $present.Success) { return $false }

    Enable-FacebookAdbRoot -Serial $Serial
    $bindMain = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @(
        'shell','mount','--bind','/system/etc/security/cacerts','/apex/com.android.conscrypt/cacerts'
    ) -TimeoutSeconds 15
    if (-not $bindMain.Success) {
        throw "Could not bind the system CA store into Conscrypt on ${Serial}: $($bindMain.Stderr)"
    }
    $zygoteBind = 'for pid in $(pidof zygote zygote64); do nsenter -t $pid -m -- mount --bind /system/etc/security/cacerts /apex/com.android.conscrypt/cacerts || exit 1; done'
    $bindZygotes = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell',$zygoteBind) -TimeoutSeconds 20
    if (-not $bindZygotes.Success) {
        throw "Could not bind the CA store into Android app mount namespaces on ${Serial}: $($bindZygotes.Stderr)"
    }
    $verify = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @(
        'shell','test','-r',"/apex/com.android.conscrypt/cacerts/$CertificateHash.0"
    ) -TimeoutSeconds 6
    if (-not $verify.Success) { throw "Conscrypt CA verification failed on $Serial" }
    return $true
}

function Ensure-FacebookMitmCertificate {
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [string]$CertificatePath = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem",
        [string]$OpenSslPath = '',
        [int]$BootTimeoutSeconds = 180
    )
    $resolvedCertificate = (Resolve-Path -LiteralPath $CertificatePath).Path
    $certificateHash = Get-FacebookMitmCertificateHash -CertificatePath $resolvedCertificate -OpenSslPath $OpenSslPath
    $systemPath = "/system/etc/security/cacerts/$certificateHash.0"
    $existing = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','test','-r',$systemPath) -TimeoutSeconds 6
    if (-not $existing.Success) {
        Invoke-FacebookCertificateRemount -Serial $Serial -BootTimeoutSeconds $BootTimeoutSeconds
        $stagePath = "/data/local/tmp/$certificateHash.0"
        $push = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('push',$resolvedCertificate,$stagePath) -TimeoutSeconds 20
        if (-not $push.Success) { throw "Could not stage the mitmproxy CA on ${Serial}: $($push.Stderr)" }
        $installCommand = "cp $stagePath $systemPath && chown root:root $systemPath && chmod 0644 $systemPath && restorecon $systemPath"
        $install = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell',$installCommand) -TimeoutSeconds 15
        if (-not $install.Success) { throw "Could not install the mitmproxy CA on ${Serial}: $($install.Stderr)" }
    }
    if (-not (Restore-FacebookMitmCertificateRuntime -Serial $Serial -CertificateHash $certificateHash)) {
        throw "The mitmproxy CA was not present after installation on $Serial"
    }
    $systemVerify = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','test','-r',$systemPath) -TimeoutSeconds 6
    if (-not $systemVerify.Success) { throw "System CA verification failed on $Serial" }
    return [pscustomobject]@{
        certificate_hash = $certificateHash
        system_path = $systemPath
        conscrypt_path = "/apex/com.android.conscrypt/cacerts/$certificateHash.0"
        system_installed = $true
        conscrypt_runtime_installed = $true
    }
}
