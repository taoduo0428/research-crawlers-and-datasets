@echo off
setlocal
set "FACEBOOK_ROOT=%~dp0"
cd /d "%FACEBOOK_ROOT%"
call "%FACEBOOK_ROOT%START_API_AND_EMULATOR.cmd" %*
set "exit_code=%ERRORLEVEL%"
if "%exit_code%"=="0" (
  start "Facebook Capture Console" "http://127.0.0.1:8766/"
)
exit /b %exit_code%
