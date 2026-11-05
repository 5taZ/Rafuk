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

Local `Start-Rafuk.cmd` and `Stop-Rafuk.cmd` control the Windows Docker
stack. Keep its bot and scheduler stopped while the VPS runs the same
bot token to avoid duplicate polling and notifications.
