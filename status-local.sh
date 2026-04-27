#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"

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
    pid="$(cat "$pid_file")"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

frontend_status="$(docker compose ps --status running frontend 2>/dev/null | awk 'NR>1 {print $1}' | head -n 1 || true)"
if [[ -n "$frontend_status" ]]; then
    print_status "frontend" "running on http://127.0.0.1:8081"
else
    print_status "frontend" "stopped"
fi

if curl -fsS --noproxy '*' http://127.0.0.1:8010/api/v1/health >/dev/null 2>&1; then
    print_status "api" "running on http://127.0.0.1:8010"
else
    print_status "api" "stopped"
fi

if is_pid_running "$RUN_DIR/bot.pid" || pgrep -f 'python -m bot.main' >/dev/null 2>&1; then
    print_status "bot" "running"
else
    print_status "bot" "stopped"
fi

if is_pid_running "$RUN_DIR/scheduler.pid" || pgrep -f 'python -m scheduler.collector' >/dev/null 2>&1; then
    print_status "scheduler" "running"
else
    print_status "scheduler" "stopped"
fi

if is_pid_running "$RUN_DIR/cloudflared.pid"; then
    print_status "tunnel" "running"
else
    print_status "tunnel" "stopped"
fi

if [[ -f .env ]]; then
    mini_app_url="$(grep '^MINI_APP_URL=' .env | cut -d= -f2- || true)"
    if [[ -n "$mini_app_url" ]]; then
        print_status "mini_app_url" "$mini_app_url"
    fi
fi

if [[ -d "$RUN_DIR" ]]; then
    print_status "logs" "$RUN_DIR"
fi
