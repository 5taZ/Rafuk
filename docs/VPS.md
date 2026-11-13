# Rafuk on the LC-1 VPS

The Mini App runs at <https://2.27.206.15>. Docker, Rafuk and certificate
renewal start automatically after a VPS reboot. The Windows PC can be off.

From CMD in the project directory, run one of these commands.

```cmd
host
host status
host deploy
host restart
host stop
host backup
host backup-sync
```

`stop-host.cmd` also stops Rafuk. Stopping preserves PostgreSQL data.
`host logs` shows recent application logs.

The VPS runs a deployed snapshot of the local `bad-app` working tree.
`host deploy` uploads completed changes, rebuilds the Python and frontend
images, updates the services, and checks readiness and the public files.
It preserves PostgreSQL, Redis, production secrets and the previous release.
Failed activation restores the previous release and image tags.

Editing files on the PC alone does not update the VPS. `host restart`
restarts the deployed version without uploading files. Agents must run
`host deploy` after each completed, tested application change, as required
by `AGENTS.md`; no additional permission is needed for that routine step.

The release packs tracked files plus new, not yet added files under `api/`,
`bot/`, `scheduler/`, `migrations/`, `frontend/`, `assets/` and `nginx/`
(ignored files never ship), so deploying needs no write to `.git`.
Uncommitted edits and new files ship with a warning; `DEPLOYMENT.json` lists
them under `uncommitted_files` (new ones also under `untracked_files`) next
to `git_revision`.

Before deploying runtime or test changes, run the full test suite and Ruff.
After frontend edits, bump static versions and rebuild the JS/CSS bundles
as applicable. Deployment packages tracked working files plus the VPS
launchers; it excludes local secrets, caches and databases. Each release
contains `DEPLOYMENT.json` with source hashes and `REVISION` with its ID.
The command prints the deployed ID only after verification succeeds.
Changes to system nginx or systemd configuration require their own
validation/reload in addition to an application deployment.

## Connection

The local, gitignored `.run/rafuk-vps.json` contains the server address and
SSH private-key path. The private key stays in the Windows user's `.ssh`
directory. `.run/vps-known_hosts` pins the server's SSH host key. The
server password is not stored in the launchers.

## Server paths

| Path | Purpose |
|------|---------|
| `/opt/rafuk/current` | Active source release |
| `/opt/rafuk/releases/` | Deployed source releases |
| `/opt/rafuk/shared/.env` | Private production settings |
| `/opt/rafuk/shared/compose.vps.yml` | VPS image and memory overrides |
| `/opt/rafuk/backups/` | Database snapshot taken during migration |
| `/opt/rafuk/backups/daily/` | Daily database dumps, kept 30 days |
| `/usr/local/sbin/rafuk-backup` | Database dump command (`scripts/vps/rafuk-backup.sh`) |
| `/usr/local/sbin/rafuk-vps` | Server control command |
| `/etc/nginx/sites-enabled/rafuk-*` | Public HTTP/HTTPS ingress |
| `/etc/letsencrypt/live/rafuk-ip/` | Public IP certificate |

Production Compose uses the base file and `compose.vps.yml` explicitly.
The local development override is not loaded. Only HTTP, HTTPS and SSH
are public. PostgreSQL and the API bind to loopback; Redis stays internal.

The Let's Encrypt certificate covers the public IP and uses the
`shortlived` profile. `rafuk-cert-renew.timer` checks renewal twice a day
and reloads nginx after a successful check.

```bash
systemctl status rafuk.service
systemctl list-timers rafuk-cert-renew.timer
systemctl start rafuk-cert-renew.service
journalctl -u rafuk-cert-renew.service --no-pager -n 30
```

## Database backups

`rafuk-backup.timer` dumps the database every day at 01:30 UTC into
`/opt/rafuk/backups/daily/rafuk-YYYYMMDD-HHMMSS.dump` (`pg_dump -Fc`,
checked with `pg_restore --list` before it replaces anything) and deletes
dumps older than 30 days. A dump of the current database is about 0.2 MB.

`host backup` makes a fresh dump now and copies every dump the PC does
not have yet; `host backup-sync` only copies. Copies go to
`%USERPROFILE%\RafukBackups` (or `backup_dir` in `.run/rafuk-vps.json`),
outside the repository, and are kept for 30 days. On the PC that holds the
VPS key, the scheduled task "Rafuk backup sync" runs `backup-sync` daily
and catches up after the PC was off. Another developer's deploys do not
touch the timer or the dumps; a PC without the VPS key has no copy.

Install or update the server side after a deploy that changed these files:

```bash
install -m 0755 /opt/rafuk/current/scripts/vps/rafuk-backup.sh /usr/local/sbin/rafuk-backup
install -m 0755 /opt/rafuk/current/scripts/vps/rafuk-vps /usr/local/sbin/rafuk-vps
install -m 0644 /opt/rafuk/current/scripts/vps/rafuk-backup.service /opt/rafuk/current/scripts/vps/rafuk-backup.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now rafuk-backup.timer
```

Restore follows `docs/BACKUP_RUNBOOK.md` (`pg_restore` into a stopped stack).

Local `Start-Rafuk.cmd` and `Stop-Rafuk.cmd` control the Windows Docker
stack. Keep its bot and scheduler stopped while the VPS runs the same
bot token to avoid duplicate polling and notifications.
