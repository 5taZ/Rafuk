param(
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs', 'deploy')]
    [string]$Action = 'start'
)

$ErrorActionPreference = 'Stop'
try {
    if ($Action -eq 'deploy') {
        $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) {
            throw 'Project Python is missing. Run uv sync --locked --extra dev first.'
        }
        & $python (Join-Path $PSScriptRoot 'scripts\vps\deploy.py')
        if ($LASTEXITCODE -ne 0) { throw "VPS deployment failed with exit code $LASTEXITCODE." }
        exit 0
    }
    $configPath = Join-Path $PSScriptRoot '.run\rafuk-vps.json'
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw 'VPS connection is not configured. See docs/VPS.md.'
    }
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $keyPath = [Environment]::ExpandEnvironmentVariables($config.key)
    $knownHosts = Join-Path $PSScriptRoot '.run\vps-known_hosts'
    if (-not (Test-Path -LiteralPath $keyPath)) {
        throw "SSH key was not found at $keyPath."
    }
    if (-not (Test-Path -LiteralPath $knownHosts)) {
        throw 'The verified VPS host key is missing from .run/vps-known_hosts.'
    }
    if ($config.host -notmatch '^[a-zA-Z0-9.-]+$') {
        throw 'The VPS host in .run/rafuk-vps.json is invalid.'
    }
    & ssh.exe -i $keyPath -o BatchMode=yes -o IdentitiesOnly=yes `
        -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$knownHosts" `
        -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 `
        "root@$($config.host)" "/usr/local/sbin/rafuk-vps $Action"
    if ($LASTEXITCODE -ne 0) { throw "VPS command failed with exit code $LASTEXITCODE." }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
