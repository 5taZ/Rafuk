# Remaining project issues

Date: 2026-05-14  
Scope: post-audit backlog after Waves 76-82. This file is the live tracker for
remaining reliability, ops, and maintainability work. The earlier `audit.md`
remediation waves are closed; this document captures what is still useful to do
next.

## Current baseline

- No known P0/blocking code issues after the latest audit remediation waves.
- Latest full verification passed before this backlog was created:
  - `uv run ruff check .`
  - `uv run pytest --tb=short -q`
  - `uv run alembic -c migrations/alembic.ini current`
  - `node --check frontend/js/app_bundle.js`
- Existing unrelated local deletions are intentionally not part of this backlog
  unless the owner explicitly chooses to restore or remove them:
  - `gpt.md`
  - `mybad.md`
  - `problems.md`

## Classification rules

- **Auto-fixable**: clear code/config/docs change, no external vendor choice or
  secret-management decision required, testable locally.
- **Manual-gated**: requires an owner decision, credentials, deployment target,
  external service, or a potentially destructive migration/backfill.
- **Opportunistic**: valuable only when touching the same area for a real feature
  or bug; should not be done as cosmetic churn.

## P1 — production reliability / ops

| ID | Item | Status | Classification | Next action |
|----|------|--------|----------------|-------------|
| REST-OPS-01 | Off-host PostgreSQL backups | Open; script parser hardened in wave87 and runbook added in wave88 | Manual-gated | Pick backup target such as S3/R2/Backblaze/remote host; then wire `scripts/backup.sh` to it and complete a restore drill. |
| REST-OPS-02 | Monitoring / uptime alerting | Open | Manual-gated | Pick monitoring provider or self-hosted stack; then add checks for frontend, API readiness, bot, scheduler, and backup freshness. |
| REST-OPS-03 | Docker image registry publishing | Completed in wave86 | Auto-fixable | CI now pushes SHA and `latest` tags to GHCR after green lint/test/security on `main`. |
| REST-OPS-04 | Deployment workflow | Open | Manual-gated | Needs deployment target and credentials before adding `deploy.yml`. |
| REST-SEC-01 | Docker/secret-store hardening | Open | Manual-gated | Choose Docker Secrets, Vault, Doppler, SOPS, or another store; then migrate runtime secrets out of plain Compose `environment:` where practical. |

## P2 — database and maintainability

| ID | Item | Status | Classification | Next action |
|----|------|--------|----------------|-------------|
| REST-DB-01 | `ai_audit_log` range partitioning | Open | Manual-gated | Only worth doing when table growth proves cleanup is insufficient; requires Postgres migration/backfill planning. |
| REST-MAINT-01 | `scheduler/collector.py` split | Open | Opportunistic | Extract a focused helper only while changing scheduler behavior; avoid cosmetic splits. |
| REST-MAINT-02 | AI service / marketplace splits | Open | Opportunistic | Continue module extraction only around active AI changes; preserve backward-compatible imports. |
| REST-FE-01 | Frontend TypeScript/JSDoc/build-system | Open | Manual-gated | Large frontend architecture decision; not required for current vanilla-JS flow. |
| REST-FE-02 | Critical/deferred CSS split | Open | Opportunistic | Evaluate only with browser/Telegram WebView testing to avoid FOUC/regressions. |

## P3 — docs and polish

| ID | Item | Status | Classification | Next action |
|----|------|--------|----------------|-------------|
| REST-DOC-01 | Refresh stale UI/UX report status | Completed in wave84 | Auto-fixable | Marked the report as historical so already-fixed items are not mistaken for current issues. |
| REST-DOC-02 | Refresh stale handoff counts in `claude.md` | Completed in wave85 | Auto-fixable | Added a status update pointing to `AGENTS.md`/`restissues.md` and refreshed test/Alembic counts. |
| REST-HOUSE-01 | Decide fate of deleted local notes | Open | Manual-gated | Owner decides whether `gpt.md`, `mybad.md`, and `problems.md` should be restored or permanently removed. |

## Wave order

1. `REST-DOC-00`: create this backlog file.
2. `REST-DOC-01`: make stale UI/UX recommendations explicitly historical — completed in wave84.
3. `REST-OPS-03`: if GHCR is acceptable, add a safe registry publish path in CI — completed in wave86.
4. Manual-gated items proceed only after the owner chooses providers/targets.
