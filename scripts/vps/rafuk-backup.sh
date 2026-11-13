#!/usr/bin/env bash
# INF-H1: daily dump of the Rafuk database on the VPS. Installed as
# /usr/local/sbin/rafuk-backup and run by rafuk-backup.timer; `host
# backup-sync` copies the dumps off the server (docs/VPS.md).
set -euo pipefail
umask 077
dir=/opt/rafuk/backups/daily
days=${RAFUK_BACKUP_DAYS:-30}
install -d -m 0700 "$dir"
exec 9>/opt/rafuk/backups/.lock
flock -n 9
name=rafuk-$(date -u +%Y%m%d-%H%M%S).dump
tmp=$dir/.$name.partial
trap 'rm -f "$tmp"' EXIT
docker exec rafuk-vps-postgres-1 sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$tmp"
# A dump pg_restore cannot read is no backup; keep the previous ones instead.
docker exec -i rafuk-vps-postgres-1 pg_restore --list > /dev/null < "$tmp"
mv "$tmp" "$dir/$name"
find "$dir" -maxdepth 1 -type f -name 'rafuk-*.dump' -mtime +"$((days - 1))" -delete
printf '%s\n' "$dir/$name"
