# Working on this repo

This file is the canonical reference for anyone — human or AI — working
on the Kufar Analytics codebase. It supersedes `claude.md` (which is a
session handoff snapshot) for everything except the latest in-flight
context.

## TL;DR

```bash
# install
uv sync --extra dev

# tests must stay green before any commit
uv run pytest --tb=short

# lint (CI matches this — `ruff format` is NOT enforced; see Code style)
uv run ruff check .

# bump frontend cache-busting tags after touching frontend/js or frontend/css
scripts/bump_static_version.sh

# alembic head revision
uv run alembic -c migrations/alembic.ini current
```

If the test suite goes red, fix the code or roll back the change.
Do **not** commit a red suite.

## Project shape

| Folder | Role |
|--------|------|
| `api/` | FastAPI backend (PostgreSQL + Redis) |
| `bot/` | Aiogram 3 Telegram bot, long-polling |
| `scheduler/` | APScheduler — periodic Kufar scrape + alerts |
| `frontend/` | Vanilla JS Mini-App (no bundler, no JSX) |
| `migrations/` | Alembic migrations |
| `tests/` | pytest, ~494 tests against aiosqlite locally / Postgres in CI |
| `nginx/` | Reverse-proxy config, CSP/headers |
| `docker-compose.yml` | api / bot / scheduler / frontend / redis / cloudflared |

A more detailed file map lives at the bottom of `claude.md`. Update it
there when you reshape the layout.

## Conventions

### Atomic waves

Every meaningful change set is a **single atomic commit** named a
"wave". The body of the commit message names every audit ID it closes
(e.g. `BE-M11: Decimal vs float in history_service`). The trailer is:

```
Generated with [Devin](https://cli.devin.ai/docs)

Co-Authored-By: Devin <158243242+devin-ai-integration[bot]@users.noreply.github.com>
```

This lets `git log --grep="BE-M11"` find the wave that closed a given
audit item, in either direction.

### Audit ID comments

When a fix is non-obvious, anchor it inline:

```python
# BE-M11: Numeric(12,2) gives Decimal on Postgres but float on SQLite —
# coerce both sides so the production path doesn't TypeError.
```

The IDs come from `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` (gitignored).

### Backward-compat re-exports

When you split a module (e.g. Wave 10 split `ai_analysis.py` into four
services), keep the old import surface working via re-exports rather
than rewriting every caller. Document the re-exports inline with the
list of importers so removing them is a deliberate decision.

### Code style

* Compact, dense, idiomatic. Avoid excessive `try`/`except`.
* **Comments explain *why*, not *what*.** Don't add comments unless
  asked or unless the code is genuinely subtle.
* Don't add or remove comments accidentally during refactors.
* Follow the existing patterns — read neighbouring files before
  reaching for a new abstraction or library.
* **`ruff format` is intentionally NOT enforced.** The project
  uses single-line trailing-comma argument lists (`f(a, b, c,)`)
  which the ruff formatter would explode into multi-line form
  (`f(\n    a,\n    b,\n    c,\n)`). CI runs `ruff check` (real
  correctness issues) but skips the formatter. If you find yourself
  wanting to bulk-reformat, don't — make the change you actually
  came for instead.

### Tests

* Async tests use the `_CSRFTestClient` shim in `tests/conftest.py`
  which auto-injects `Origin` and `X-Requested-With` headers (CSRF
  middleware needs both — see Wave 6/FE-H7).
* Async tests using `httpx.AsyncClient` directly (e.g.
  `tests/test_consent.py`) must set both headers manually.
* Pytest is async-mode auto (`asyncio_mode = "auto"`).
* Coverage runs in CI via `pytest-cov`; local runs don't need it.

## Hard constraints from the user

1. **Do not rotate secrets.** The user has accepted the risk that the
   bot token, AI API key, DB credentials and proxy credentials in
   `.env` are unrotated. `.env` is gitignored — that's the only line
   of defence and that's intentional.
2. **Local Redis on `:6380` has no password.** `REDIS_URL` stays
   plaintext for the host; `DOCKER_REDIS_URL` carries the
   `REDIS_PASSWORD` env var.
3. **494 tests must stay green.** If a refactor breaks tests, either
   update the test (with audit-ID comments) or roll back. Never commit
   a red suite.
4. **Never commit `.env`, `DEEP_DIVE_REVIEW_COMPREHENSIVE.md`, or
   `notmyfault.md`.** They're gitignored — keep them that way.

## Operational items deferred by user policy

| ID    | Item | Disposition |
|-------|------|-------------|
| SEC-C1 | Secret rotation | Skip — user policy |
| INF-C2 | HTTPS/TLS | Cloudflared already proxies, deeper TLS is deployment-side |
| INF-C3 | `pkill -f` in scripts | Local dev scripts only |
| SEC-H2 | Docker Secrets | Needs Compose Spec migration + secret-store decision |
| INF-H1 | `pg_dump` cron | Needs off-host backup target |
| INF-H2 | Monitoring | Needs Prometheus / UptimeRobot decision |
| INF-H3 | Push image to registry | Needs CD pipeline first |
| INF-H4 | `deploy.yml` | CD pipeline — needs deployment target |
| DB-H3  | `ai_audit_log` partitioning | Cleanup function works; true range-partitioning postponed |

If you cross one of these "ops decision" lines (introducing a new
external service, secret rotation, destructive migration), **ping the
user first**.

## Where to look first

* **Adding an endpoint** → `api/routers/` mirrors HTTP shapes,
  `api/services/` holds the business logic. Look at how
  `routers/workflow.py` calls into `services/workflow_store.py` for
  the canonical pattern.
* **Adding a migration** → `uv run alembic -c migrations/alembic.ini
  revision -m "<name>" --autogenerate`, then verify the diff and
  remove anything you didn't intend.
* **Adding a frontend feature** → vanilla JS, no bundler. Read the
  `frontend/js/api_*.js` and `frontend/js/render_*.js` files and
  follow the existing module split. CSS uses `frontend/css/parts/`.
* **Touching the AI pipeline** → it lives across
  `api/services/ai_analysis_pipeline.py`,
  `api/services/ai_service.py`,
  `api/services/ai_marketplace.py`,
  `api/services/ai_guards.py`,
  `api/services/ai_shadow_store.py`,
  `api/services/ai_privacy.py`,
  `api/services/ai_audit.py`. Wave 10 split the original god-file —
  the seams are documented in `claude.md`.

## Current open work

The remaining audit items are tracked in
`DEEP_DIVE_REVIEW_COMPREHENSIVE.md` (gitignored). The execution plan
lives in `claude.md` section 8 ("Suggested next moves") and
`CHANGELOG.md` ("Audit progress"). Keep waves themed and atomic.

## License

Private. Do not redistribute without permission.
