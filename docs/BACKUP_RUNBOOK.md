# PostgreSQL backup and restore runbook

This runbook covers the existing `scripts/backup.sh` path. A backup is not
production-grade until `BACKUP_DIR` points to storage that survives host loss
or is synced off-host immediately after each run.

## Create a backup

```bash
DATABASE_URL='postgresql+asyncpg://user:password@host:5432/kufar' \
BACKUP_DIR=/mnt/kufar-backups \
RETENTION_DAYS=30 \
scripts/backup.sh
```

Expected result:

- `scripts/backup.sh` prints `Backup created: ...`.
- A `kufar_YYYYmmdd_HHMMSS.dump` file exists in `BACKUP_DIR`.
- The dump is PostgreSQL custom format (`pg_dump -Fc`).

## Schedule it

Use a scheduler outside the app containers. Example cron shape:

```cron
17 2 * * * cd /srv/kufar-analytics && DATABASE_URL='postgresql+asyncpg://user:password@host:5432/kufar' BACKUP_DIR=/mnt/kufar-backups RETENTION_DAYS=30 scripts/backup.sh >> /var/log/kufar-backup.log 2>&1
```

The concrete path, environment source, and off-host sync mechanism are
deployment-specific. Do not treat local disk alone as an off-host backup.

## Freshness check

Run this from the host that owns the backup directory:

```bash
test "$(find /mnt/kufar-backups -name 'kufar_*.dump' -mtime -1 | wc -l)" -gt 0
```

Wire that check into the chosen monitoring system so stale backups page before a
restore is needed.

## Restore drill

Restore drills should target a scratch database first. Restoring with
`--clean --if-exists` can drop objects in the target database.

```bash
LATEST_DUMP="$(ls -t /mnt/kufar-backups/kufar_*.dump | head -1)"
pg_restore --list "$LATEST_DUMP" >/tmp/kufar-restore-list.txt
pg_restore --clean --if-exists --no-owner --no-privileges \
  -d 'postgresql://restore_user:restore_password@restore_host:5432/kufar_restore' \
  "$LATEST_DUMP"
```

After restore:

```bash
DATABASE_URL='postgresql+asyncpg://restore_user:restore_password@restore_host:5432/kufar_restore' \
uv run alembic -c migrations/alembic.ini current
```

Expected Alembic head: `20260514_0017`.

## Close-out criteria for REST-OPS-01

`REST-OPS-01` can be marked completed only when all of these are true:

1. Backups run on a schedule.
2. Backup files are stored off-host or synced off-host immediately.
3. Backup freshness is monitored.
4. A restore drill to a scratch database succeeds.
5. The selected storage target and restore procedure are documented here.
