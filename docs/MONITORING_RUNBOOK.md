# Monitoring runbook

This runbook documents what to monitor with the health and metrics surfaces that
already exist. The monitoring provider is still an owner choice; this file is the
provider-agnostic checklist for `REST-OPS-02`.

## Public checks

Monitor these through the same public route real users use:

| Check | Endpoint | Expected |
|-------|----------|----------|
| Frontend availability | `/` | HTTP 200 and non-empty HTML |
| API readiness | `/api/v1/health/ready` | HTTP 200 |
| API detailed health | `/api/v1/health` | JSON `status` is `healthy` |

Recommended alert shape:

- Page if frontend or API readiness fails for 2 consecutive checks.
- Ticket if detailed health reports `degraded` but readiness still returns 200.
- Track latency separately from availability; alert on sustained p95 regressions
  only after a baseline is known for the deployment.

## Internal checks

The bot and scheduler expose internal-only aiohttp health servers:

| Service | Endpoint inside container/network | Expected |
|---------|-----------------------------------|----------|
| Bot | `http://bot:8001/health/ready` | HTTP 200 |
| Scheduler | `http://scheduler:8002/health/ready` | HTTP 200 |

These are already wired into Docker Compose healthchecks. If the chosen
monitoring system runs outside the Docker network, use a host-local probe or
blackbox exporter rather than exposing these ports publicly.

## Metrics

The API exposes Prometheus text at `/metrics`.

- In production-like deployments, set `METRICS_BEARER_TOKEN`.
- Scrape with `Authorization: Bearer <token>`.
- Do not expose `/metrics` unauthenticated on the public internet.

Useful series to watch first:

- `kufar_http_requests_total{status=~"5.."}` — backend error rate.
- `kufar_http_request_duration_seconds_*` — API latency.
- `kufar_metrics_backend_info{backend="redis"}` — cross-worker metrics path is active.
- `kufar_query_dataset_events_total{event="distributed_singleflight_timeout"}` —
  distributed cache/lock contention.
- `kufar_query_dataset_upstream_fetch_duration_seconds_count` — upstream Kufar fetch volume.

## Backup freshness

Pair monitoring with `docs/BACKUP_RUNBOOK.md`:

```bash
test "$(find /mnt/kufar-backups -name 'kufar_*.dump' -mtime -1 | wc -l)" -gt 0
```

Alert if no fresh dump exists for the expected backup window.

## Close-out criteria for REST-OPS-02

`REST-OPS-02` can be marked completed only when all of these are true:

1. A monitoring provider or self-hosted stack is selected.
2. Public frontend and API readiness checks are active.
3. Bot and scheduler readiness are monitored from inside the host/network.
4. `/metrics` is scraped with `METRICS_BEARER_TOKEN`.
5. Backup freshness is monitored.
6. Alert recipients and escalation rules are documented for the deployment.
