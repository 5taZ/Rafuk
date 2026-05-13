#!/usr/bin/env bash
# Gracefully stop everything that start-local.sh launched.
#
# Uses PID files written by start-local.sh — NOT `pkill -f`, which is
# happy to match unrelated processes on the same host. Each kill also
# verifies that the PID's working directory is somewhere inside this
# project before sending the signal, so a recycled PID belonging to
# another user/process is left alone.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-myprojetctkufar}"
export COMPOSE_PROJECT_NAME

cd "$ROOT_DIR"

_pid_belongs_to_project() {
    local pid="$1"
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    local cwd
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    if [[ "$cwd" == "$ROOT_DIR" || "$cwd" == "$ROOT_DIR"/* ]]; then
        return 0
    fi
    return 1
}

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

for service in api bot scheduler cloudflared; do
    _kill_pid_file "$RUN_DIR/$service.pid"
done

# Compose services use named volumes (myprojetctkufar_pgdata,
# myprojetctkufar_redis-data). Stop containers only; do not remove
# volumes, so local DB/cache data survives normal shutdown/restart cycles.
docker compose --profile local-db stop frontend redis postgres >/dev/null 2>&1 || true

echo "Project stopped."
echo "Local database data is preserved in Docker volume: ${COMPOSE_PROJECT_NAME}_pgdata"
