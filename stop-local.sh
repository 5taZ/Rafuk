#!/usr/bin/env bash
# Stops the processes started by start-local.sh. Only PIDs recorded in .run/ are
# signalled: no global pkill patterns, so other checkouts and projects are left alone.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"

usage() {
    echo "Usage: ./stop-local.sh [--keep-containers | --all]"
    echo "  --keep-containers  stop only the host processes (API, bot, scheduler, tunnel)"
    echo "  --all              also stop the Postgres and Redis containers"
}

STOP_FRONTEND=1
STOP_INFRA=0
for arg in "$@"; do
    case "$arg" in
        --keep-containers)
            STOP_FRONTEND=0
            ;;
        --all)
            STOP_INFRA=1
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

cd "$ROOT_DIR"

# Signals the process group when the process leads one (started through setsid),
# otherwise the process and its direct children (the uv wrapper and the Python process).
signal_tree() {
    local signal="$1"
    local pid="$2"
    local pgid=""

    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    if [[ "$pgid" == "$pid" ]]; then
        kill "-$signal" -- "-$pid" 2>/dev/null || true
        return
    fi

    if command -v pkill >/dev/null 2>&1; then
        pkill "-$signal" -P "$pid" 2>/dev/null || true
    fi
    kill "-$signal" "$pid" 2>/dev/null || true
}

stop_service() {
    local name="$1"
    local pid_file="$RUN_DIR/$name.pid"

    if [[ ! -f "$pid_file" ]]; then
        return 0
    fi

    local pid
    pid="$(tr -d '[:space:]' <"$pid_file")"
    rm -f "$pid_file"

    if [[ ! "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    signal_tree TERM "$pid"
    for _ in $(seq 1 20); do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "Stopped $name (pid $pid)."
            return 0
        fi
        sleep 0.5
    done

    signal_tree KILL "$pid"
    echo "Killed $name (pid $pid) after timeout."
}

stop_service cloudflared
stop_service api
stop_service bot
stop_service scheduler
rm -f "$RUN_DIR/api.host"

if [[ "$STOP_FRONTEND" -eq 1 ]]; then
    docker compose stop frontend >/dev/null 2>&1 || true
fi

if [[ "$STOP_INFRA" -eq 1 ]]; then
    docker compose stop db redis >/dev/null 2>&1 || true
fi

echo "Project stopped."
