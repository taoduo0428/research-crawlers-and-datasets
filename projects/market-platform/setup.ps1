[CmdletBinding()]
param(
    [string]$PythonLauncher = "py",
    [string]$PythonVersion = "",
    [string]$VenvDir = ".venv",
    [switch]$InstallChromium,
    [switch]$SkipDoctor
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Push-Location -LiteralPath $PSScriptRoot
try {
    $launcherArgs = @()
    if ($PythonLauncher -eq "py" -and $PythonVersion) {
        $launcherArgs += "-$PythonVersion"
    }

    & $PythonLauncher @launcherArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.11 or newer is required."
    }

    $venvPath = Join-Path $PSScriptRoot $VenvDir
    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "Creating virtual environment: $venvPath"
        & $PythonLauncher @launcherArgs -m venv $venvPath
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create the virtual environment."
        }
    }

    & $venvPython -m pip install --disable-pip-version-check --no-input --timeout 30 --retries 2 --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to prepare pip."
    }

    & $venvPython -m pip install --disable-pip-version-check --no-input --timeout 30 --retries 2 --editable .
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install crawler dependencies."
    }

    if ($InstallChromium) {
        & $venvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install Playwright Chromium."
        }
    }

    if (-not $SkipDoctor) {
        & $venvPython run.py doctor
        if ($LASTEXITCODE -ne 0) {
            throw "Crawler doctor check failed."
        }
    }

    Write-Host "Installation completed. Python: $venvPython"
}
finally {
    Pop-Location
}
