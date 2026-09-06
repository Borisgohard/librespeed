@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0new\interactive_vps_pair.ps1"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
