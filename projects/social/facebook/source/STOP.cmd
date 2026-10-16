@echo off
setlocal
cd /d "%~dp0"

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_all_facebook.ps1"
set "exit_code=%ERRORLEVEL%"

if not "%exit_code%"=="0" (
  echo.
  echo [STOP] Facebook runtime stop encountered an error. Exit code: %exit_code%
  echo [STOP] No unrelated process is intentionally terminated by this script.
  pause
)

exit /b %exit_code%
