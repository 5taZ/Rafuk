param(
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs', 'deploy', 'backup', 'backup-sync')]
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
    $sshOptions = @('-i', $keyPath, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes', '-o', "UserKnownHostsFile=$knownHosts",
        '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3')
    $target = "root@$($config.host)"
    if ($Action -ne 'backup-sync') {
        & ssh.exe @sshOptions $target "/usr/local/sbin/rafuk-vps $Action"
        if ($LASTEXITCODE -ne 0) { throw "VPS command failed with exit code $LASTEXITCODE." }
    }
    if ($Action -in 'backup', 'backup-sync') {
        # INF-H1: the off-host copy of the VPS dumps. The folder is outside the
        # repository so dumps never reach a release or git.
        $backupDir = if ($config.backup_dir) { [Environment]::ExpandEnvironmentVariables($config.backup_dir) }
                     else { Join-Path $env:USERPROFILE 'RafukBackups' }
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        $remote = @(& ssh.exe @sshOptions $target '/usr/local/sbin/rafuk-vps backups')
        if ($LASTEXITCODE -ne 0) { throw "Listing VPS backups failed with exit code $LASTEXITCODE." }
        $copied = 0
        foreach ($name in $remote) {
            if ($name -notmatch '^rafuk-\d{8}-\d{6}\.dump$') { continue }
            $local = Join-Path $backupDir $name
            if (Test-Path -LiteralPath $local) { continue }
            & scp.exe -q -p @sshOptions "${target}:/opt/rafuk/backups/daily/$name" "$local.partial"
            if ($LASTEXITCODE -ne 0) { throw "Copying $name failed with exit code $LASTEXITCODE." }
            Move-Item -LiteralPath "$local.partial" -Destination $local
            $copied++
        }
        $cutoff = (Get-Date).ToUniversalTime().AddDays(-30).ToString('yyyyMMdd')
        Get-ChildItem -LiteralPath $backupDir -Filter 'rafuk-*.dump' |
            Where-Object { $_.Name -match '^rafuk-(\d{8})-\d{6}\.dump$' -and $Matches[1] -lt $cutoff } |
            Remove-Item
        $kept = @(Get-ChildItem -LiteralPath $backupDir -Filter 'rafuk-*.dump')
        $size = ($kept | Measure-Object -Property Length -Sum).Sum / 1MB
        Write-Host ('Backups in {0}: {1} copied, {2} kept, {3:N1} MB' -f $backupDir, $copied, $kept.Count, $size)
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
