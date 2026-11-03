param([switch]$ChangeToken, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$compose = @('compose', '-p', 'rafuk-local', '-f', 'docker-compose.yml', '--profile', 'local-db')
$url = 'http://127.0.0.1:8081'
$tunnelName = 'rafuk-local-quick-tunnel'

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Install Docker Desktop for Windows, then run this launcher again.'
    }

    $settings = @()
    if (Test-Path -LiteralPath .env) {
        $settings = @(Get-Content -LiteralPath .env)
    } else {
        $settings = @(
            'ENV=development'
            'POSTGRES_DB=kufar'
            'POSTGRES_USER=kufar'
            ('POSTGRES_PASSWORD=' + [guid]::NewGuid().ToString('N'))
            ('REDIS_PASSWORD=' + [guid]::NewGuid().ToString('N'))
            'API_BASE_URL=http://127.0.0.1:8010'
            "MINI_APP_URL=$url"
            'WORKERS=1'
        )
    }

    $tokenLine = $settings | Where-Object { $_ -match '^\s*BOT_TOKEN\s*=' } | Select-Object -Last 1
    if ($ChangeToken -or $tokenLine -notmatch '^BOT_TOKEN=\d+:[A-Za-z0-9_-]{30,}$') {
        $secret = Read-Host 'Paste the Telegram bot token' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        try {
            $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim()
            if ($token -notmatch '^\d+:[A-Za-z0-9_-]{30,}$') {
                throw 'Invalid Telegram bot token format.'
            }
            $settings = @($settings | Where-Object { $_ -notmatch '^\s*BOT_TOKEN\s*=' })
            $settings += "BOT_TOKEN=$token"
            [IO.File]::WriteAllLines((Join-Path $PSScriptRoot '.env'), [string[]]$settings)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            $secret.Dispose()
            Remove-Variable token -ErrorAction SilentlyContinue
        }
    }

    Write-Host 'Starting Docker Desktop if needed...'
    & docker desktop start --timeout 180
    if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop did not start.' }

    # The public endpoint must use Telegram authentication.
    $env:AUTH_BYPASS = 'false'
    $network = 'rafuk-local_kufar-net'
    $networks = & docker network ls --format '{{.Name}}'
    if ($LASTEXITCODE -ne 0) { throw 'Could not list Docker networks.' }
    if ($networks -notcontains $network) {
        & docker @compose up -d --build frontend
        if ($LASTEXITCODE -ne 0) { throw 'Could not start the web server.' }
    }

    Write-Host 'Starting the HTTPS tunnel for Telegram...'
    $containers = & docker ps -a --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0) { throw 'Could not list Docker containers.' }
    if ($containers -contains $tunnelName) {
        & docker start $tunnelName | Out-Null
    } else {
        & docker run -d --name $tunnelName --restart unless-stopped --network $network `
            cloudflare/cloudflared:2024.12.1 tunnel --no-autoupdate --protocol http2 --url http://frontend:8080 | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the HTTPS tunnel.' }

    $miniAppUrl = $null
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        $startedAt = & docker inspect --format '{{.State.StartedAt}}' $tunnelName
        $tunnelLogs = & cmd.exe /d /c "docker logs --since $startedAt $tunnelName 2>&1" | Out-String
        $match = [regex]::Match($tunnelLogs, 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($match.Success -and $tunnelLogs -match 'Registered tunnel connection') {
            $miniAppUrl = $match.Value
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $miniAppUrl) { throw 'Cloudflare did not provide a working HTTPS tunnel.' }

    $settings = @($settings | Where-Object { $_ -notmatch '^\s*MINI_APP_URL\s*=' })
    $settings += "MINI_APP_URL=$miniAppUrl"
    [IO.File]::WriteAllLines((Join-Path $PSScriptRoot '.env'), [string[]]$settings)
    $env:MINI_APP_URL = $miniAppUrl

    Write-Host 'Building and starting Rafuk...'
    & docker @compose up -d --build --wait --wait-timeout 180
    if ($LASTEXITCODE -ne 0) { throw 'Rafuk did not start. See the Docker output above.' }

    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$url/api/v1/health/ready" | Out-Null
    } catch {
        & docker @compose restart frontend
        if ($LASTEXITCODE -ne 0) { throw 'Could not restart the web server.' }
        Start-Sleep -Seconds 3
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 "$url/api/v1/health/ready" | Out-Null
    }

    Write-Host 'Checking public HTTPS...'
    & curl.exe --fail --silent --show-error --output NUL --connect-timeout 5 --max-time 10 `
        --retry 3 --retry-all-errors --retry-delay 2 "$miniAppUrl/api/v1/health/ready"
    if ($LASTEXITCODE -ne 0) {
        throw "Rafuk is running locally at $url, but this PC could not verify the public HTTPS address $miniAppUrl. Try the address from your phone or run Start-Rafuk.cmd again."
    }
    Write-Host "Telegram Mini App is ready at $miniAppUrl. Send /app in the bot's private chat."
    if (-not $NoBrowser) { Start-Process $url }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
