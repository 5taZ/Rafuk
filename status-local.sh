#!/usr/bin/env bash
# Shows the state of the stack started by start-local.sh (PID files in .run/ and the
# compose containers of this project only).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"
API_PORT="${LOCAL_API_PORT:-8010}"

cd "$ROOT_DIR"

print_status() {
    local name="$1"
    local value="$2"
    printf '%-12s %s\n' "$name" "$value"
}

is_pid_running() {
    local pid_file="$1"
    if [[ ! -f "$pid_file" ]]; then
        return 1
    fi

    local pid
    pid="$(tr -d '[:space:]' <"$pid_file")"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

running_services="$(docker compose ps --services --status running 2>/dev/null || true)"

container_status() {
    local service="$1"
    local running_text="$2"
    if grep -qx "$service" <<<"$running_services"; then
        print_status "$service" "$running_text"
    else
        print_status "$service" "stopped"
    fi
}

container_status db "running on 127.0.0.1:5433"
container_status redis "running on 127.0.0.1:6380"
container_status frontend "running on http://127.0.0.1:8081"

api_host="127.0.0.1"
if [[ -f "$RUN_DIR/api.host" ]]; then
    api_host="$(tr -d '[:space:]' <"$RUN_DIR/api.host")"
fi

if is_pid_running "$RUN_DIR/api.pid"; then
    status="$(curl -s -o /dev/null -w '%{http_code}' "http://$api_host:$API_PORT/healthz" \
        || true)"
    if [[ "$status" == "200" ]]; then
        print_status "api" "running on http://$api_host:$API_PORT"
    elif [[ -n "$status" && "$status" != "000" ]]; then
        print_status "api" "process running, /healthz returned HTTP $status"
    else
        print_status "api" "process running, not answering on $api_host:$API_PORT"
    fi
else
    print_status "api" "stopped"
fi

for name in bot scheduler; do
    if is_pid_running "$RUN_DIR/$name.pid"; then
        print_status "$name" "running"
    else
        print_status "$name" "stopped"
    fi
done

if is_pid_running "$RUN_DIR/cloudflared.pid"; then
    print_status "tunnel" "running"
else
    print_status "tunnel" "stopped"
fi

if [[ -f .env ]]; then
    mini_app_url="$(grep '^MINI_APP_URL=' .env | tail -n 1 | cut -d= -f2- || true)"
    if [[ -n "$mini_app_url" ]]; then
        print_status "mini_app_url" "$mini_app_url"
    fi
fi

if [[ -d "$RUN_DIR" ]]; then
    print_status "logs" "$RUN_DIR"
fi
