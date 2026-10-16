[CmdletBinding()]
param([switch]$CheckOnly, [switch]$NoBrowser, [switch]$InstallOnly)
$ErrorActionPreference = 'Stop'
$Base = $PSScriptRoot
try {
    $python = Join-Path $Base '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        $bootstrap = Get-Command py.exe -ErrorAction SilentlyContinue
        $prefix = @('-3')
        if (-not $bootstrap) { $bootstrap = Get-Command python.exe -ErrorAction SilentlyContinue; $prefix = @() }
        if (-not $bootstrap) { throw 'Install Python 3.12 or 3.13 from https://www.python.org/downloads/ and enable Add python.exe to PATH.' }
        & $bootstrap.Source @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
        if ($CheckOnly) {
            & $bootstrap.Source @prefix (Join-Path $Base 'facebook_local.py') doctor
            exit $LASTEXITCODE
        }
        & $bootstrap.Source @prefix -m venv (Join-Path $Base '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create .venv. Install Python 3.12 and retry.' }
    }
    if ($CheckOnly) { & $python (Join-Path $Base 'facebook_local.py') doctor; exit $LASTEXITCODE }
    $requirements = Join-Path $Base 'requirements.txt'
    $stamp = Join-Path $Base '.venv\facebook-requirements.sha256'
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($requirements))).Replace('-', '') } finally { $sha.Dispose() }
    if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw).Trim() -ne $hash) {
        & $python -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network / pip configuration, then retry START.cmd.' }
        & $python -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Installed dependency versions conflict.' }
        [IO.File]::WriteAllText($stamp, $hash)
    }
    & $python (Join-Path $Base 'facebook_local.py') init
    if ($LASTEXITCODE -ne 0) { throw 'Local configuration initialization failed.' }
    if ($InstallOnly) { exit 0 }
    $arguments = @((Join-Path $Base 'facebook_local.py'), 'start')
    if ($NoBrowser) { $arguments += '--no-browser' }
    & $python @arguments
    exit $LASTEXITCODE
} catch {
    Write-Error $_
    exit 1
}
