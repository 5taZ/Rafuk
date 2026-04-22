#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"
API_LOG="$RUN_DIR/api.log"
BOT_LOG="$RUN_DIR/bot.log"
SCHEDULER_LOG="$RUN_DIR/scheduler.log"
CLOUDFLARED_LOG="$RUN_DIR/cloudflared.log"

WITH_TUNNEL=0
for arg in "$@"; do
    case "$arg" in
        --with-tunnel)
            WITH_TUNNEL=1
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: ./start-local.sh [--with-tunnel]"
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

stop_existing() {
    # Kill by port (most reliable — catches both uv run and direct python)
    for port in 8010; do
        local pids
        pids="$(lsof -ti :"$port" 2>/dev/null)" || true
        if [[ -n "$pids" ]]; then
            kill $pids 2>/dev/null || true
        fi
    done

    # Kill by process pattern (catches bot/scheduler regardless of how launched)
    pkill -f 'uvicorn api.main:app' || true
    pkill -f 'python -m bot.main' || true
    pkill -f 'python -m scheduler.collector' || true

    sleep 1

    if [[ -f "$RUN_DIR/cloudflared.pid" ]]; then
        kill "$(cat "$RUN_DIR/cloudflared.pid")" 2>/dev/null || true
        rm -f "$RUN_DIR/cloudflared.pid"
    fi

    rm -f "$RUN_DIR/api.pid" "$RUN_DIR/bot.pid" "$RUN_DIR/scheduler.pid"
}

wait_for_http() {
    local url="$1"
    local attempts="${2:-30}"

    for _ in $(seq 1 "$attempts"); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done

    return 1
}

start_frontend() {
    echo "Starting frontend container on http://127.0.0.1:8081 ..."
    docker compose up -d frontend --no-deps --force-recreate
}

start_tunnel() {
    if ! command -v cloudflared >/dev/null 2>&1; then
        echo "cloudflared is not installed, cannot start tunnel."
        exit 1
    fi

    : >"$CLOUDFLARED_LOG"
    echo "Starting Cloudflare Tunnel ..."
    nohup cloudflared tunnel --url http://127.0.0.1:8081 >"$CLOUDFLARED_LOG" 2>&1 &
    echo $! >"$RUN_DIR/cloudflared.pid"

    local tunnel_url=""
    for _ in $(seq 1 30); do
        tunnel_url="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" | head -n 1 || true)"
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
    : >"$API_LOG"
    echo "Starting API on 0.0.0.0:8010 ..."
    nohup uv run uvicorn api.main:app --host 0.0.0.0 --port 8010 >"$API_LOG" 2>&1 &
    echo $! >"$RUN_DIR/api.pid"

    if ! wait_for_http "http://127.0.0.1:8010/api/v1/currency-rates" 30; then
        echo "API did not become healthy."
        echo "Check log: $API_LOG"
        exit 1
    fi
}

start_bot() {
    : >"$BOT_LOG"
    echo "Starting bot ..."
    nohup uv run python -m bot.main >"$BOT_LOG" 2>&1 &
    echo $! >"$RUN_DIR/bot.pid"
}

start_scheduler() {
    : >"$SCHEDULER_LOG"
    echo "Starting scheduler ..."
    nohup uv run python -m scheduler.collector >"$SCHEDULER_LOG" 2>&1 &
    echo $! >"$RUN_DIR/scheduler.pid"
}

main() {
    cd "$ROOT_DIR"
    stop_existing
    start_frontend

    if [[ "$WITH_TUNNEL" -eq 1 ]]; then
        start_tunnel
    fi

    migrate_db
    start_api
    start_bot
    start_scheduler

    echo
    echo "Project is up."
    echo "Frontend: http://127.0.0.1:8081"
    echo "API:      http://127.0.0.1:8010"
    if [[ "$WITH_TUNNEL" -eq 1 ]]; then
        echo "Mini App:  $(grep '^MINI_APP_URL=' .env | cut -d= -f2-)"
    fi
    echo
    echo "Logs:"
    echo "  API:  $API_LOG"
    echo "  Bot:  $BOT_LOG"
    echo "  Scheduler: $SCHEDULER_LOG"
    if [[ "$WITH_TUNNEL" -eq 1 ]]; then
        echo "  Tunnel: $CLOUDFLARED_LOG"
    fi
}

main "$@"
