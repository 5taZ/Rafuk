# Atomic audit-fix waves

Status document linked from `README.md`. Tracks the wave-per-commit
remediation plan for the audit findings recorded in `issues.md`.

## Convention

Each wave is one git commit, scoped to a single coherent change set,
with the test suite green at HEAD before the commit lands. Commit
messages reference the audit IDs (`SEC-Cn`, `BE-Hn`, `LOGIC-Mn`, …)
that map back to the issue tracker so any wave can be traced to its
originating finding.

## Current wave series (issues.md remediation, May 2026)

| Wave  | Theme                                                                  | Commit  |
|-------|------------------------------------------------------------------------|---------|
| 159   | Refresh static cache version + frontend bundle + tests                 | 8d9337c |
| 160   | AI critical — auth_bypass-gated consent skip + NFKC homoglyph guard    | ca01319 |
| 161   | Scheduler critical — drop abs() in discount alert + Kufar outage guard | 1451cd5 |
| 162   | SQLite-safe ensure_user + content-derived AI cache version             | d396540 |
| 163   | Consent migration squash + SQLite dialect guards                       | cf5e206 |
| 164   | Lead status state-machine + double-sale lock                           | f01a269 |
| 165   | Cloudflare IPv6 + nginx hardening + Telegram Web Z                     | 6327cbd |
| 166   | AI privacy — consent-revocation cache eviction + image off-loop        | 066897b |
| 167   | LeadItemPriceSnapshot retention + CI SHA pins + Codecov token          | 55ab7d7 |
| 168   | Cache-key digests + Query bounds + currency fallback                   | 5155ff9 |
| 169   | Remove internal user_id from read schemas + DB constraints             | f385292 |
| 170+  | Frontend medium fixes, scheduler/bot medium, low-priority cleanup      | (active)|
| 181   | Admin audit endpoint + admin rate-limits                               | 691e64e |
| 182   | Schema freeze: SavedSearch + exclude_duplicates marked reserved        | 8c27b70 |
| 183   | FE-NEW-1: SRI on lazy-loaded JS modules                                | 68ae62b |

## Source-of-truth files

* `issues.md` — full audit log produced 2026-05-16 (220 findings).
* `AGENTS.md` — repo-level conventions and recurring-ops policy.
* `docs/BACKUP_RUNBOOK.md` — operational restore drills, expected
  Alembic head sync.

This document supersedes the historical `claude.md` / `mybad.md`
session-handoff snapshots referenced from older AGENTS.md revisions.
