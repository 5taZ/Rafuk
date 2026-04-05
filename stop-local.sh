#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"

cd "$ROOT_DIR"

pkill -f 'uv run uvicorn api.main:app --host 0.0.0.0 --port 8010' || true
pkill -f 'uv run uvicorn api.main:app --host 127.0.0.1 --port 8010' || true
pkill -f 'python -m bot.main' || true

if [[ -f "$RUN_DIR/cloudflared.pid" ]]; then
    kill "$(cat "$RUN_DIR/cloudflared.pid")" 2>/dev/null || true
fi

if [[ -f "$RUN_DIR/api.pid" ]]; then
    kill "$(cat "$RUN_DIR/api.pid")" 2>/dev/null || true
fi

if [[ -f "$RUN_DIR/bot.pid" ]]; then
    kill "$(cat "$RUN_DIR/bot.pid")" 2>/dev/null || true
fi

rm -f "$RUN_DIR"/*.pid
docker compose stop frontend >/dev/null 2>&1 || true

echo "Project stopped."
