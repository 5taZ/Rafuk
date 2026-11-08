# Autotracker capacity and verification

The scheduler's main capacity unit is a distinct normalized search plus
Kufar category, rather than a subscriber. Strict/broad title matching and
personal price, region, seller and condition filters run locally.

## Processing

* Due trackers use ordered keyset pages of at most 2000 rows. Processing
  continues across pages within the existing 240-second budget. Failed
  rows are visited once in a tick; deferred rows remain due for the next.
* Two HTTP prefetches overlap with serial database work. The existing
  Kufar client still enforces request spacing, concurrency, retries and
  circuit breaking. Database read transactions end before awaiting HTTP.
* A tick retains at most 64 raw search responses. These can serve both
  strictness modes and subsequent subscriber pages without another HTTP
  request. Categories remain separate. No responses survive the tick.
* Listing sync reads only the current page's IDs, inserts new states in
  batches of 50 with conflict handling, and deactivates missing states in
  SQL. Historical rows are not materialized to compare a 50-ad page.
* Deduplication and threshold-floor lookups read only the current ads,
  in batches of tracker IDs. Existing cursors, price history, consent
  handling and per-query atomic event/outbox commits are preserved.
* Listing descriptions share in-flight downloads and use a 256-entry,
  five-minute cache in the existing client. Missing descriptions have a
  30-second cache; HTTP errors are not cached. A cancelled reader does
  not cancel a shared download; client shutdown cancels remaining work.
* The Mini App polls the visible tracking pane. Unchanged data backs off
  from 30 seconds to 60, then 120; changes and foreground requests reset
  the delay. Hidden views do not poll. An idle event pane goes from four
  HTTP requests per minute to one per two minutes after settling.

Migration `20261108_0033` adds partial due-tracker pagination and
tracker/ad/time dedup indexes. Both build concurrently on PostgreSQL;
no columns, records or volumes are removed.

## Reproducible synthetic benchmark

Run from the repository root:

```bash
uv run --locked python -m scripts.benchmark_trackers --users 10000
uv run --locked python -m scripts.benchmark_trackers --users 1000 --queries 100 --latency 0.05
```

This uses a separate SQLite database in memory, generated users and a
fake Kufar client. It checks every subscriber's cursor and event count
and queues notifications without sending to Telegram. It does not open
the application's database or call external services. The benchmark
also reports SQL counts for fresh listing-state batches.

On 2026-11-08, compared with commit `1ffa573`, fresh listing sync used:

| New ads | Previous SQL statements | Optimized SQL statements |
| ---: | ---: | ---: |
| 50 | 152 | 3 |
| 200 | 602 | 6 |
| 1500 | 4502 | 34 |

The local 10,000-subscriber, one-query run with one new ad and 10 ms fake
HTTP latency processed all subscribers in 11.379 seconds, made one fake
search, and persisted 10,000 events plus 10,000 outbox entries across
five database pages. These are isolated SQLite measurements, not a VPS
or Telegram throughput guarantee.

A second run with 1,000 subscribers, 100 different queries and 50 ms fake
HTTP latency completed in 2.995 seconds, with 100 searches, at most two
concurrent HTTP tasks, and all 1,000 events and outbox entries present.
The PostgreSQL path was also exercised on the VPS in an isolated schema
inside a rolled-back transaction: 200 inserts, refresh/deactivation to
50 active rows, Decimal-exact price drops and savepoint rollback passed.
The scratch schema was verified absent afterward.

An isolated scheduler run on the VPS PostgreSQL instance processed 1,000
synthetic users across five shared searches in 1.687 seconds. All 1,000
cursors, events and outbox entries were verified. This used 10 ms fake
Kufar calls and no Telegram sends; the transaction, including its entire
scratch schema, was rolled back and its removal verified.

## Remaining external capacity limits

Different query/category combinations still require different searches.
With the default one-second request spacing, upstream request starts
are bounded at one per second in the scheduler; slow responses, retries,
the per-tick budget and delivery work lower effective throughput. A
large shared audience is much cheaper than the same number of entirely
different searches. Increasing concurrency does not remove that limit.

Telegram delivery retains its durable outbox, bounded user concurrency,
retry-after handling and per-user event caps. A burst to many users can
take time to deliver even after collection finishes. Watch scheduler
budget-deferral logs, pending outbox age and container CPU/memory when
sizing real capacity. Multiple scheduler replicas require a separate
distributed-ownership design; the current deployment runs one.
