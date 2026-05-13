#!/usr/bin/env bash
# PostgreSQL backup script.
#
# Reads connection params from DATABASE_URL but does NOT pass it on the
# command line (which would expose the password in `ps` output). Instead
# it parses the URL and feeds the password to pg_dump via PGPASSWORD env.
#
# Required env: DATABASE_URL (format: postgresql[+driver]://user:pass@host:port/dbname)
# Optional env: BACKUP_DIR (default /backups), RETENTION_DAYS (default 30)

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is not set" >&2
    exit 1
fi

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

eval "$(
    python3 - <<'PY'
import os
import shlex
import sys
from urllib.parse import unquote, urlsplit

url = os.environ["DATABASE_URL"]
try:
    parsed = urlsplit(url)
    if parsed.scheme != "postgresql" and not parsed.scheme.startswith("postgresql+"):
        raise ValueError("scheme must be postgresql or postgresql+driver")
    if not parsed.username or parsed.password is None or not parsed.hostname or parsed.path in ("", "/"):
        raise ValueError("user, password, host, and database are required")
    values = {
        "PGUSER_VAL": unquote(parsed.username),
        "PGPASSWORD_VAL": unquote(parsed.password),
        "PGHOST_VAL": parsed.hostname,
        "PGPORT_VAL": str(parsed.port or 5432),
        "PGDATABASE_VAL": unquote(parsed.path.lstrip("/")),
    }
except Exception as exc:
    print(f"Cannot parse DATABASE_URL: {exc}", file=sys.stderr)
    raise SystemExit(1)

for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"

mkdir -p "$BACKUP_DIR"
FILENAME="$BACKUP_DIR/kufar_$(date +%Y%m%d_%H%M%S).dump"

PGPASSWORD="$PGPASSWORD_VAL" pg_dump \
    -Fc \
    -h "$PGHOST_VAL" \
    -p "$PGPORT_VAL" \
    -U "$PGUSER_VAL" \
    -d "$PGDATABASE_VAL" \
    -f "$FILENAME"

find "$BACKUP_DIR" -name "kufar_*.dump" -mtime +"$RETENTION_DAYS" -delete
echo "Backup created: $FILENAME"
