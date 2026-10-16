param(
    [int]$Port = 8765,
    [string]$Serial = "emulator-5554"
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $env:SystemRoot 'py.exe'
if (-not (Test-Path $PythonExe)) {
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue }
    if (-not $pythonCommand) { throw 'Python executable could not be resolved.' }
    $PythonExe = [string]$pythonCommand.Source
}
Set-Location $Base
. (Join-Path $Base 'facebook_runtime.ps1')
$PythonExe = Resolve-FacebookPythonExe -BasePath $Base
& $PythonExe "$Base\facebook_capture_api.py" --host 127.0.0.1 --port $Port --serial $Serial
