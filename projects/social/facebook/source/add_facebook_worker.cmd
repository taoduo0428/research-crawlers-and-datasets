@echo off
setlocal
title Facebook Worker Bootstrap
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0add_facebook_worker.ps1" %*
set "BOOTSTRAP_EXIT=%ERRORLEVEL%"
echo.
if not "%BOOTSTRAP_EXIT%"=="0" (
  echo Worker bootstrap failed. Review worker-bootstrap\logs and rerun with -Action Resume -WorkerId worker-NN.
) else (
  echo Worker bootstrap command completed.
)
pause
exit /b %BOOTSTRAP_EXIT%
