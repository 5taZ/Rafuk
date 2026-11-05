@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Manage-RafukVps.ps1" %*
set "RAFUK_VPS_EXIT=%ERRORLEVEL%"
if not "%RAFUK_VPS_EXIT%"=="0" pause
exit /b %RAFUK_VPS_EXIT%
