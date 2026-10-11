-- Monthly range partitioning for the query_snapshots table (PostgreSQL 12+).
--
-- Manual maintenance script: no migration or service runs it. Consider it once
-- query_snapshots grows past ~1M rows, and run it in a maintenance window: it takes an
-- ACCESS EXCLUSIVE lock on query_snapshots for the whole copy.
--
--   psql -v ON_ERROR_STOP=1 -d kufar -f scripts/partition_snapshots.sql
--
-- Schema it expects (alembic head, see api/models.py and migration 20261011_0010):
--   query_snapshots(id SERIAL PRIMARY KEY, query, snapshot_at, ..., created_at)
--   with UNIQUE uq_query_snapshot_bucket (query, snapshot_at).
-- What the partitioned table keeps compatible with the application:
--   - id still gets its value from a sequence, so ORM inserts without id keep working;
--   - uq_query_snapshot_bucket stays (it includes the partition key), so the hourly
--     snapshot upsert cannot create duplicate buckets;
--   - the primary key becomes (id, snapshot_at), because every unique constraint of a
--     partitioned table must include the partition key. The ORM still treats id as the
--     identity; alembic autogenerate on Postgres will report this PK difference, so keep
--     it out of generated migrations.
-- Partition bounds are computed in UTC. A DEFAULT partition catches rows outside the
-- created months; keep it empty by creating partitions ahead of time (step 7), because
-- a new partition cannot be attached for a range the DEFAULT partition already holds.

BEGIN;

SET LOCAL TimeZone = 'UTC';
LOCK TABLE query_snapshots IN ACCESS EXCLUSIVE MODE;

-- Step 1: move the old table and every named object on it out of the way.
-- ALTER TABLE ... RENAME does not rename indexes, constraints or sequences.
ALTER TABLE query_snapshots RENAME TO query_snapshots_old;
ALTER TABLE query_snapshots_old
    RENAME CONSTRAINT uq_query_snapshot_bucket TO uq_query_snapshot_bucket_old;
ALTER TABLE query_snapshots_old
    RENAME CONSTRAINT query_snapshots_pkey TO query_snapshots_old_pkey;
-- Indexes dropped by migration 20261011_0010; present only on older schemas.
ALTER INDEX IF EXISTS idx_query_snapshots_query RENAME TO idx_query_snapshots_query_old;
ALTER INDEX IF EXISTS idx_query_snapshots_query_time
    RENAME TO idx_query_snapshots_query_time_old;
-- The SERIAL sequence is owned by the old table and would be dropped with it.
ALTER SEQUENCE IF EXISTS query_snapshots_id_seq RENAME TO query_snapshots_old_id_seq;

-- Step 2: the partitioned table. SERIAL works on partitioned tables (identity columns
-- need PostgreSQL 17) and creates a new query_snapshots_id_seq owned by this table.
CREATE TABLE query_snapshots (
    id SERIAL NOT NULL,
    query VARCHAR(255) NOT NULL,
    snapshot_at TIMESTAMP WITH TIME ZONE NOT NULL,
    total_results INTEGER NOT NULL DEFAULT 0,
    analyzed_count INTEGER NOT NULL DEFAULT 0,
    mean_byn DOUBLE PRECISION NOT NULL DEFAULT 0,
    median_byn DOUBLE PRECISION NOT NULL DEFAULT 0,
    min_byn DOUBLE PRECISION NOT NULL DEFAULT 0,
    max_byn DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT query_snapshots_pkey PRIMARY KEY (id, snapshot_at),
    -- Also serves lookups by query and by (query, snapshot_at); no extra indexes needed.
    CONSTRAINT uq_query_snapshot_bucket UNIQUE (query, snapshot_at)
) PARTITION BY RANGE (snapshot_at);

-- Step 3: helper that creates one monthly partition (idempotent). Bounds are UTC
-- month starts, whatever the caller's TimeZone setting is.
CREATE OR REPLACE FUNCTION create_monthly_partition(
    parent_table TEXT,
    month_start DATE
) RETURNS VOID AS $func$
DECLARE
    partition_name TEXT;
    range_start TIMESTAMP WITH TIME ZONE;
    range_end TIMESTAMP WITH TIME ZONE;
BEGIN
    month_start := date_trunc('month', month_start)::DATE;
    partition_name := parent_table || '_y' || to_char(month_start, 'YYYY')
        || 'm' || to_char(month_start, 'MM');
    range_start := month_start::TIMESTAMP AT TIME ZONE 'UTC';
    range_end := (month_start + INTERVAL '1 month')::TIMESTAMP AT TIME ZONE 'UTC';

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        parent_table,
        range_start,
        range_end
    );
END;
$func$ LANGUAGE plpgsql;

-- Step 4: partitions for every month that has data, up to three months ahead,
-- plus the DEFAULT partition.
DO $partitions$
DECLARE
    first_month DATE;
    last_month DATE;
    current_month DATE;
BEGIN
    SELECT date_trunc('month', COALESCE(min(snapshot_at), now()))::DATE
      INTO first_month
      FROM query_snapshots_old;
    last_month := (date_trunc('month', now()) + INTERVAL '3 months')::DATE;
    current_month := first_month;
    WHILE current_month <= last_month LOOP
        PERFORM create_monthly_partition('query_snapshots', current_month);
        current_month := (current_month + INTERVAL '1 month')::DATE;
    END LOOP;
END;
$partitions$;

CREATE TABLE IF NOT EXISTS query_snapshots_default PARTITION OF query_snapshots DEFAULT;

-- Step 5: copy the data with an explicit column list (column order may differ).
INSERT INTO query_snapshots (
    id, query, snapshot_at, total_results, analyzed_count,
    mean_byn, median_byn, min_byn, max_byn, created_at
)
SELECT
    id, query, snapshot_at, total_results, analyzed_count,
    mean_byn, median_byn, min_byn, max_byn, created_at
FROM query_snapshots_old;

-- Continue the id sequence after the copied rows.
SELECT setval(
    pg_get_serial_sequence('query_snapshots', 'id'),
    COALESCE((SELECT max(id) FROM query_snapshots), 0) + 1,
    false
);

-- Step 6: drop the old table (and query_snapshots_old_id_seq, which it owns).
DROP TABLE query_snapshots_old;

COMMIT;

-- Step 7: create future partitions monthly. With the pg_cron extension
-- (CREATE EXTENSION IF NOT EXISTS pg_cron; it must be in shared_preload_libraries and
-- the job must run in this database, see cron.database_name / cron.schedule_in_database):
--
-- SELECT cron.schedule(
--     'query-snapshots-partitions',
--     '0 0 1 * *',
--     $$SELECT create_monthly_partition('query_snapshots', (now() + INTERVAL '2 months')::DATE)$$
-- );
--
-- Without pg_cron, run the same SELECT from any monthly job (or by hand) at least one
-- month ahead.

-- Retention: detach and drop old partitions.
-- ALTER TABLE query_snapshots DETACH PARTITION query_snapshots_y2026m04;
-- DROP TABLE query_snapshots_y2026m04;
