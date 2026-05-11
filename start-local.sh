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

# Verify a stored PID is alive AND owns a process whose command line
# starts with $ROOT_DIR — this prevents us from killing a stranger that
# happened to recycle the PID after our previous run died. Returns 0
# (true) if the PID belongs to us, 1 otherwise.
_pid_belongs_to_project() {
    local pid="$1"
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    # /proc/<pid>/cwd is a symlink to the process's working directory.
    # If it points anywhere inside $ROOT_DIR (or equals it), this PID
    # is one of ours.
    local cwd
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    if [[ "$cwd" == "$ROOT_DIR" || "$cwd" == "$ROOT_DIR"/* ]]; then
        return 0
    fi
    return 1
}

# Kill the process recorded in a PID file IF it still belongs to us.
# Never falls back to pkill -f: that's been known to wipe out unrelated
# uvicorn / python -m processes from other projects on the same host.
_kill_pid_file() {
    local pid_file="$1"
    if [[ ! -f "$pid_file" ]]; then
        return 0
    fi
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if _pid_belongs_to_project "$pid"; then
        kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
}

stop_existing() {
    # Stop by recorded PID first — never `pkill -f`, which would happily
    # nuke a stranger's `python -m something` on the same host.
    _kill_pid_file "$RUN_DIR/cloudflared.pid"
    _kill_pid_file "$RUN_DIR/api.pid"
    _kill_pid_file "$RUN_DIR/bot.pid"
    _kill_pid_file "$RUN_DIR/scheduler.pid"
    docker compose stop api bot scheduler migrate >/dev/null 2>&1 || true

    # Last-resort cleanup: if the API port is still bound (PID file was
    # missing or stale and a real listener is leftover), free :8010 by
    # PID. lsof gives us the actual owner — no name-pattern guessing.
    local pids
    pids="$(lsof -ti :8010 2>/dev/null)" || true
    for pid in $pids; do
        if _pid_belongs_to_project "$pid"; then
            kill "$pid" 2>/dev/null || true
        fi
    done

    sleep 1
}

wait_for_http() {
    local url="$1"
    local attempts="${2:-30}"

    for _ in $(seq 1 "$attempts"); do
        if curl -fsS --noproxy '*' "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done

    return 1
}

wait_for_tcp() {
    local host="$1" port="$2" attempts="${3:-30}"
    for _ in $(seq 1 "$attempts"); do
        if (echo > "/dev/tcp/${host}/${port}") >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

start_infra() {
    # Bring up Redis (defined in docker-compose.yml) and verify Postgres
    # is reachable. Postgres lives in a separate container in this repo
    # (e.g. marketplace_postgres on :5433), so we only probe it instead
    # of trying to start a non-existent compose service.
    echo "Starting Redis container on :6380 ..."
    docker compose up -d redis

    if ! wait_for_tcp 127.0.0.1 6380 30; then
        echo "Warning: Redis on :6380 did not become reachable within 30s." >&2
        echo "Check: docker compose ps redis; docker compose logs redis" >&2
    else
        echo "Redis is ready on :6380."
    fi

    if ! wait_for_tcp 127.0.0.1 5433 5; then
        echo "Warning: PostgreSQL on :5433 is not reachable." >&2
        echo "Migrations and the API will fail. Start it manually:" >&2
        echo "  docker start marketplace_postgres   # if container already exists" >&2
        echo "  # or follow the docs to provision Postgres on :5433" >&2
    else
        echo "PostgreSQL is ready on :5433."
    fi
}

start_frontend() {
    echo "Starting frontend container on http://127.0.0.1:8081 ..."
    docker compose up -d --build frontend --no-deps --force-recreate
}

start_tunnel() {
    if ! command -v cloudflared >/dev/null 2>&1; then
        echo "cloudflared is not installed, cannot start tunnel."
        exit 1
    fi

    : >"$CLOUDFLARED_LOG"
    echo "Starting Cloudflare Tunnel ..."
    local tunnel_url=""
    local max_attempts=3
    local pid

    for attempt in $(seq 1 "$max_attempts"); do
        printf '\n=== cloudflared attempt %s/%s at %s ===\n' "$attempt" "$max_attempts" "$(date -Is)" >>"$CLOUDFLARED_LOG"
        nohup cloudflared tunnel --url http://127.0.0.1:8081 >>"$CLOUDFLARED_LOG" 2>&1 &
        pid="$!"
        echo "$pid" >"$RUN_DIR/cloudflared.pid"

        for _ in $(seq 1 30); do
            tunnel_url="$(grep -Eo 'https://[[:alnum:]-]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" | head -n 1 || true)"
            if [[ -n "$tunnel_url" ]]; then
                break
            fi
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done

        if [[ -n "$tunnel_url" ]]; then
            break
        fi

        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi

        if [[ "$attempt" -lt "$max_attempts" ]]; then
            if grep -q 'status_code="500 Internal Server Error"' "$CLOUDFLARED_LOG"; then
                echo "Cloudflare Quick Tunnel returned 500; retrying ..."
            else
                echo "Cloudflare Tunnel did not publish a URL; retrying ..."
            fi
            sleep "$((attempt * 2))"
        fi
    done

    if [[ -z "$tunnel_url" ]]; then
        echo "Failed to detect Cloudflare Tunnel URL."
        if grep -q 'status_code="500 Internal Server Error"' "$CLOUDFLARED_LOG"; then
            echo "Cloudflare Quick Tunnel API is returning 500 right now; this is outside the app."
            echo "Try again later, or configure a named Cloudflare tunnel instead of an account-less quick tunnel."
        fi
        echo "Check log: $CLOUDFLARED_LOG"
        rm -f "$RUN_DIR/cloudflared.pid"
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

    if ! wait_for_http "http://127.0.0.1:8010/api/v1/health" 30; then
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
    start_infra
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
