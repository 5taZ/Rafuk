@echo off
setlocal
pushd "%~dp0" || exit /b 1

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Rafuk.ps1" -StopLocalhostRunTunnel
if errorlevel 1 goto failed

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker is unavailable. If Docker Desktop is closed, Rafuk is already offline.
    pause
    exit /b 1
)

docker container inspect rafuk-local-quick-tunnel >nul 2>&1
if not errorlevel 1 (
    docker stop rafuk-local-quick-tunnel
    if errorlevel 1 goto failed
)

docker compose -p rafuk-local -f docker-compose.yml --profile local-db stop
if errorlevel 1 goto failed

echo Rafuk is stopped. Your data is saved.
pause
exit /b 0

:failed
echo Rafuk could not be fully stopped. See the Docker error above.
pause
exit /b 1
