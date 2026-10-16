Set-StrictMode -Version 2.0

function Resolve-FacebookPythonExe {
    param([string]$BasePath = $PSScriptRoot)

    $candidates = New-Object System.Collections.Generic.List[object]
    $seen = @{}

    function Add-PythonCandidate([string]$Path, [double]$Priority) {
        if ([string]::IsNullOrWhiteSpace($Path)) { return }
        try { $full = [IO.Path]::GetFullPath($Path) } catch { return }
        if ($seen.ContainsKey($full) -or -not (Test-Path $full)) { return }
        $seen[$full] = $true
        $candidates.Add([pscustomobject]@{ path = $full; priority = $Priority })
    }

    if (-not [string]::IsNullOrWhiteSpace($env:FACEBOOK_PYTHON_EXE)) {
        Add-PythonCandidate $env:FACEBOOK_PYTHON_EXE 10000
        if (Test-Path -LiteralPath $env:FACEBOOK_PYTHON_EXE) { return [IO.Path]::GetFullPath($env:FACEBOOK_PYTHON_EXE) }
    }

    if (-not [string]::IsNullOrWhiteSpace($BasePath)) {
        Add-PythonCandidate (Join-Path $BasePath '.venv\Scripts\python.exe') 9000
        Add-PythonCandidate (Join-Path $BasePath 'venv\Scripts\python.exe') 8500
    }

    $localPythonRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path $localPythonRoot) {
        foreach ($dir in @(Get-ChildItem -Path $localPythonRoot -Directory -Filter 'Python*' -ErrorAction SilentlyContinue)) {
            $priority = 5000.0
            if ($dir.Name -match '^Python(?<major>\d)(?<minor>\d+)$') {
                $priority += ([double]$Matches.major * 100.0) + [double]$Matches.minor
            }
            Add-PythonCandidate (Join-Path $dir.FullName 'python.exe') $priority
        }
    }

    $pathPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pathPython) { Add-PythonCandidate ([string]$pathPython.Source) 1000 }

    $ordered = @($candidates | Sort-Object -Property priority -Descending)
    if ($ordered.Count -eq 0) {
        throw 'Python interpreter could not be resolved from project venv, LOCALAPPDATA, FACEBOOK_PYTHON_EXE, or PATH.'
    }

    # Prefer a candidate that also owns mitmdump, so capture and analysis use the
    # same Python environment. Fall back to the highest-priority interpreter for
    # non-capture entrypoints such as API/report helpers.
    foreach ($candidate in $ordered) {
        $sibling = Join-Path (Split-Path -Parent $candidate.path) 'mitmdump.exe'
        if (Test-Path $sibling) { return [string]$candidate.path }
        $scripts = Join-Path (Split-Path -Parent $candidate.path) 'Scripts\mitmdump.exe'
        if (Test-Path $scripts) { return [string]$candidate.path }
    }
    return [string]$ordered[0].path
}

function Resolve-FacebookMitmdumpExe {
    param([Parameter(Mandatory = $true)][string]$PythonExe)

    $sibling = Join-Path (Split-Path -Parent $PythonExe) 'mitmdump.exe'
    if (Test-Path $sibling) { return [IO.Path]::GetFullPath($sibling) }
    $candidate = Join-Path (Split-Path -Parent $PythonExe) 'Scripts\mitmdump.exe'
    if (Test-Path $candidate) { return [IO.Path]::GetFullPath($candidate) }

    $command = Get-Command mitmdump.exe -ErrorAction SilentlyContinue
    if ($command) { return [string]$command.Source }
    throw 'mitmdump executable could not be resolved from the selected Python environment or PATH.'
}

function ConvertTo-FacebookNativeArgument {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }

    # Windows CreateProcess quoting. PowerShell does not use backslash as an
    # escape character, so the literals below intentionally contain one slash.
    $builder = New-Object System.Text.StringBuilder
    $null = $builder.Append('"')
    $slashes = 0
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq '\') {
            $slashes++
            continue
        }
        if ($ch -eq '"') {
            if ($slashes -gt 0) { $null = $builder.Append((('\' * ($slashes * 2)) -join '')) }
            $null = $builder.Append('\"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) {
            $null = $builder.Append((('\' * $slashes) -join ''))
            $slashes = 0
        }
        $null = $builder.Append($ch)
    }
    if ($slashes -gt 0) { $null = $builder.Append((('\' * ($slashes * 2)) -join '')) }
    $null = $builder.Append('"')
    return $builder.ToString()
}

function Invoke-FacebookPythonCaptured {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = ''
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = (@($Arguments | ForEach-Object { ConvertTo-FacebookNativeArgument ([string]$_) }) -join ' ')
    if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) { $psi.WorkingDirectory = $WorkingDirectory }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    try {
        if (-not $process.Start()) { throw "Failed to start Python process: $PythonExe" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        return [pscustomobject]@{
            ExitCode = [int]$process.ExitCode
            Stdout = [string]$stdoutTask.Result
            Stderr = [string]$stderrTask.Result
        }
    }
    finally {
        $process.Dispose()
    }
}

function Invoke-FacebookPowerShellFileCaptured {
    param(
        [Parameter(Mandatory = $true)][string]$PowerShellExe,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string[]]$ScriptArguments = @(),
        [string]$WorkingDirectory = ''
    )

    $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$ScriptPath) + @($ScriptArguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PowerShellExe
    $psi.Arguments = (@($arguments | ForEach-Object { ConvertTo-FacebookNativeArgument ([string]$_) }) -join ' ')
    if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) { $psi.WorkingDirectory = $WorkingDirectory }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    try {
        if (-not $process.Start()) { throw "Failed to start PowerShell process: $PowerShellExe" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        return [pscustomobject]@{
            ExitCode = [int]$process.ExitCode
            Stdout = [string]$stdoutTask.Result
            Stderr = [string]$stderrTask.Result
        }
    }
    finally {
        $process.Dispose()
    }
}

function Write-FacebookChildProcessOutput {
    param($Result)
    if ($Result -and -not [string]::IsNullOrWhiteSpace([string]$Result.Stdout)) {
        Write-Host ([string]$Result.Stdout).TrimEnd()
    }
    if ($Result -and -not [string]::IsNullOrWhiteSpace([string]$Result.Stderr)) {
        Write-Warning ([string]$Result.Stderr).TrimEnd()
    }
}

function Acquire-FacebookLiveCaptureLock {
    param(
        [string]$BasePath = $PSScriptRoot,
        [string]$Owner = 'facebook-live-work',
        [string]$OwnerId = ''
    )

    $path = Join-Path $BasePath 'facebook-live-capture.lock'
    if (Test-Path $path) {
        try {
            $staleHandle = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            $staleHandle.Dispose()
            Remove-Item -Force $path
        }
        catch {
            return [pscustomobject]@{ acquired = $false; path = $path; handle = $null }
        }
    }

    try {
        $handle = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $payload = "owner=$Owner`nowner_id=$OwnerId`npid=$PID`nstarted_at=$((Get-Date).ToString('o'))`n"
        $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
        $handle.Write($bytes, 0, $bytes.Length)
        $handle.Flush()
        return [pscustomobject]@{ acquired = $true; path = $path; handle = $handle }
    }
    catch {
        return [pscustomobject]@{ acquired = $false; path = $path; handle = $null }
    }
}

function Release-FacebookLiveCaptureLock {
    param($Lock)
    if (-not $Lock) { return }
    if ($Lock.handle) {
        try { $Lock.handle.Dispose() } catch {}
    }
    if ($Lock.path -and (Test-Path $Lock.path)) {
        try { Remove-Item -Force $Lock.path } catch {}
    }
}
