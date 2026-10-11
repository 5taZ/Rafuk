#!/usr/bin/env bash
# Hybrid local stack: Postgres, Redis and the nginx frontend run in Docker (compose),
# the API, bot and scheduler run on the host through uv. Logs and PID files go to .run/.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"
API_LOG="$RUN_DIR/api.log"
BOT_LOG="$RUN_DIR/bot.log"
SCHEDULER_LOG="$RUN_DIR/scheduler.log"
CLOUDFLARED_LOG="$RUN_DIR/cloudflared.log"

API_PORT="${LOCAL_API_PORT:-8010}"
FRONTEND_URL="http://127.0.0.1:8081"
# Proxies whose X-Forwarded-For uvicorn trusts: loopback and Docker networks (nginx).
FORWARDED_IPS="127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

usage() {
    echo "Usage: ./start-local.sh [--with-tunnel] [--no-infra]"
    echo "  --with-tunnel  expose the frontend through a Cloudflare quick tunnel"
    echo "  --no-infra     do not start Postgres/Redis containers (SQLite or external services)"
}

WITH_TUNNEL=0
START_INFRA=1
for arg in "$@"; do
    case "$arg" in
        --with-tunnel)
            WITH_TUNNEL=1
            ;;
        --no-infra)
            START_INFRA=0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            usage
            exit 1
            ;;
    esac
done

mkdir -p "$RUN_DIR"

set_env_var() {
    local key="$1"
    local value="$2"
    local env_file="$ROOT_DIR/.env"

    if [[ ! -f "$env_file" ]]; then
        touch "$env_file"
    fi

    if grep -q "^${key}=" "$env_file"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

env_value() {
    grep "^$1=" "$ROOT_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r' || true
}

check_env_file() {
    if [[ ! -f "$ROOT_DIR/.env" ]]; then
        echo "No .env found. Copy .env.example to .env and fill in BOT_TOKEN first."
        exit 1
    fi

    # Host processes cannot resolve compose service names.
    local database_url redis_url
    database_url="$(env_value DATABASE_URL)"
    redis_url="$(env_value REDIS_URL)"
    if [[ "$database_url" == *"@db:"* || "$redis_url" == *"//redis:"* \
        || "$redis_url" == *"@redis:"* ]]; then
        echo "DATABASE_URL/REDIS_URL in .env point at compose hosts (db, redis)."
        echo "Host mode needs localhost:5433 and localhost:6380, see .env.example."
        exit 1
    fi
}

port_in_use() {
    local host="$1"
    local port="$2"
    (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null
}

# Address the API binds to. It must be reachable from the frontend container through
# host.docker.internal, but should not be exposed to the LAN.
detect_api_host() {
    if [[ -n "${LOCAL_API_HOST:-}" ]]; then
        echo "$LOCAL_API_HOST"
        return
    fi

    # Docker Desktop (macOS, Windows, WSL) forwards host.docker.internal to the host loopback.
    if docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep -qi 'docker desktop'; then
        echo "127.0.0.1"
        return
    fi

    # Native Docker on Linux: host-gateway is the docker0 bridge address.
    local gateway
    gateway="$(docker network inspect bridge \
        --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || true)"
    if [[ -n "$gateway" ]]; then
        echo "$gateway"
        return
    fi

    echo "Could not detect the Docker bridge address; binding the API to 0.0.0.0." >&2
    echo "Set LOCAL_API_HOST to restrict it." >&2
    echo "0.0.0.0"
}

# Starts a command in the background with its own process group when setsid exists,
# so stop-local.sh can stop the uv wrapper together with its children.
start_background() {
    local name="$1"
    local log="$2"
    shift 2

    : >"$log"
    if command -v setsid >/dev/null 2>&1; then
        nohup setsid "$@" >"$log" 2>&1 </dev/null &
    else
        nohup "$@" >"$log" 2>&1 </dev/null &
    fi
    echo $! >"$RUN_DIR/$name.pid"
}

pid_alive() {
    local pid_file="$RUN_DIR/$1.pid"
    [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

stop_existing() {
    "$ROOT_DIR/stop-local.sh" --keep-containers
}

ensure_port_free() {
    local host="$1"
    if port_in_use 127.0.0.1 "$API_PORT" || port_in_use "$host" "$API_PORT"; then
        echo "Port $API_PORT is already in use by a process this script did not start."
        echo "Stop it (or set LOCAL_API_PORT) and run ./start-local.sh again."
        exit 1
    fi
}

start_infra() {
    echo "Starting Postgres and Redis containers (localhost:5433, localhost:6380) ..."
    docker compose up -d --wait db redis
}

start_frontend() {
    echo "Starting frontend container on $FRONTEND_URL (API upstream: host:$API_PORT) ..."
    NGINX_API_UPSTREAM="host.docker.internal:$API_PORT" \
        docker compose up -d --no-deps --force-recreate frontend
}

start_tunnel() {
    if ! command -v cloudflared >/dev/null 2>&1; then
        echo "cloudflared is not installed, cannot start tunnel."
        exit 1
    fi

    echo "Starting Cloudflare Tunnel ..."
    start_background cloudflared "$CLOUDFLARED_LOG" cloudflared tunnel --url "$FRONTEND_URL"

    local tunnel_url=""
    for _ in $(seq 1 30); do
        tunnel_url="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" \
            | head -n 1 || true)"
        if [[ -n "$tunnel_url" ]]; then
            break
        fi
        sleep 1
    done

    if [[ -z "$tunnel_url" ]]; then
        echo "Failed to detect Cloudflare Tunnel URL."
        echo "Check log: $CLOUDFLARED_LOG"
        exit 1
    fi

    set_env_var "MINI_APP_URL" "$tunnel_url"
    echo "Tunnel URL: $tunnel_url"
}

migrate_db() {
    echo "Running database migrations ..."
    uv run alembic -c migrations/alembic.ini upgrade head
}

start_api() {
    local host="$1"
    echo "Starting API on $host:$API_PORT ..."
    start_background api "$API_LOG" \
        uv run uvicorn api.main:app --host "$host" --port "$API_PORT" \
        --proxy-headers --forwarded-allow-ips "$FORWARDED_IPS"
    echo "$host" >"$RUN_DIR/api.host"

    # /healthz answers 200 once startup has finished; the PID check makes sure it is
    # the process started here and not something else answering on the port.
    local status
    for _ in $(seq 1 30); do
        if ! pid_alive api; then
            echo "API process exited during startup."
            echo "Check log: $API_LOG"
            exit 1
        fi
        status="$(curl -s -o /dev/null -w '%{http_code}' "http://$host:$API_PORT/healthz" \
            || true)"
        if [[ "$status" == "200" ]]; then
            return 0
        fi
        sleep 1
    done

    echo "API did not become healthy."
    echo "Check log: $API_LOG"
    exit 1
}

start_bot() {
    echo "Starting bot ..."
    start_background bot "$BOT_LOG" uv run python -m bot.main
}

start_scheduler() {
    echo "Starting scheduler ..."
    start_background scheduler "$SCHEDULER_LOG" uv run python -m scheduler.collector
}

check_workers() {
    sleep 2
    local name
    for name in bot scheduler; do
        if ! pid_alive "$name"; then
            echo "Warning: $name exited right after start, check $RUN_DIR/$name.log"
        fi
    done
}

main() {
    cd "$ROOT_DIR"
    check_env_file
    stop_existing

    local api_host
    api_host="$(detect_api_host)"
    ensure_port_free "$api_host"

    if [[ "$START_INFRA" -eq 1 ]]; then
        start_infra
    fi
    start_frontend

    if [[ "$WITH_TUNNEL" -eq 1 ]]; then
        start_tunnel
    fi

    migrate_db
    start_api "$api_host"
    start_bot
    start_scheduler
    check_workers

    echo
    echo "Project is up."
    echo "Frontend: $FRONTEND_URL"
    echo "API:      http://$api_host:$API_PORT"
    if [[ "$WITH_TUNNEL" -eq 1 ]]; then
        echo "Mini App: $(env_value MINI_APP_URL)"
    fi
    echo
    echo "Logs:"
    echo "  API:       $API_LOG"
    echo "  Bot:       $BOT_LOG"
    echo "  Scheduler: $SCHEDULER_LOG"
    if [[ "$WITH_TUNNEL" -eq 1 ]]; then
        echo "  Tunnel:    $CLOUDFLARED_LOG"
    fi
}

main "$@"
