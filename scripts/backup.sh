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

# Parse postgresql[+driver]://user:pass@host[:port]/dbname[?params]
# NOTE: percent-encoded chars in user/password are NOT decoded; if your
# password contains '@', ':', or '/', escape it before setting DATABASE_URL,
# or rotate it to a safe value.
if [[ ! "$DATABASE_URL" =~ ^postgresql(\+[^:]+)?://([^:]+):([^@]+)@([^:/]+):?([0-9]*)/(.+)$ ]]; then
    echo "Cannot parse DATABASE_URL" >&2
    exit 1
fi

PGUSER_VAL="${BASH_REMATCH[2]}"
PGPASSWORD_VAL="${BASH_REMATCH[3]}"
PGHOST_VAL="${BASH_REMATCH[4]}"
PGPORT_VAL="${BASH_REMATCH[5]:-5432}"
PGDATABASE_VAL="${BASH_REMATCH[6]%%\?*}"  # strip optional ?query params

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
