-- Partitioning strategy for query_snapshots table
-- Run this when query_snapshots exceeds ~1M rows
--
-- This script creates monthly partitions for the query_snapshots table.
-- It should be run during a maintenance window as it requires an ACCESS EXCLUSIVE lock.
--
-- Usage: psql -d kufar -f scripts/partition_snapshots.sql

BEGIN;

-- Step 1: Rename existing table
ALTER TABLE query_snapshots RENAME TO query_snapshots_old;

-- Step 2: Create new partitioned table
CREATE TABLE query_snapshots (
    id INTEGER NOT NULL,
    query VARCHAR(255) NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL,
    total_results INTEGER NOT NULL DEFAULT 0,
    analyzed_count INTEGER NOT NULL DEFAULT 0,
    mean_byn FLOAT NOT NULL DEFAULT 0.0,
    median_byn FLOAT NOT NULL DEFAULT 0.0,
    min_byn FLOAT NOT NULL DEFAULT 0.0,
    max_byn FLOAT NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (snapshot_at);

-- Step 3: Create indexes on the partitioned table
CREATE INDEX idx_query_snapshots_query ON query_snapshots(query);
CREATE INDEX idx_query_snapshots_query_time ON query_snapshots(query, snapshot_at ASC);

-- Step 4: Create partitions for the next 12 months
-- Adjust dates as needed
CREATE TABLE query_snapshots_y2026m04 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE query_snapshots_y2026m05 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE query_snapshots_y2026m06 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE query_snapshots_y2026m07 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE query_snapshots_y2026m08 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE query_snapshots_y2026m09 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE query_snapshots_y2026m10 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE query_snapshots_y2026m11 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE query_snapshots_y2026m12 PARTITION OF query_snapshots
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE query_snapshots_y2027m01 PARTITION OF query_snapshots
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE query_snapshots_y2027m02 PARTITION OF query_snapshots
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE query_snapshots_y2027m03 PARTITION OF query_snapshots
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');

-- Step 5: Migrate data from old table to new partitioned table
-- This may take a while for large tables
INSERT INTO query_snapshots
    SELECT * FROM query_snapshots_old;

-- Step 6: Drop old table
DROP TABLE query_snapshots_old;

-- Step 7: Create function to auto-create future partitions
CREATE OR REPLACE FUNCTION create_monthly_partition(
    table_name TEXT,
    start_date DATE
) RETURNS VOID AS $$
DECLARE
    partition_name TEXT;
    end_date DATE;
BEGIN
    partition_name := table_name || '_y' || TO_CHAR(start_date, 'YYYY') || 'm' || TO_CHAR(start_date, 'MM');
    end_date := start_date + INTERVAL '1 month';

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        table_name,
        start_date,
        end_date
    );
END;
$$ LANGUAGE plpgsql;

-- Step 8: Create scheduled job to auto-create partitions (PostgreSQL 10+)
-- Requires pg_cron extension: CREATE EXTENSION IF NOT EXISTS pg_cron;
-- SELECT cron.job('create-partitions', '0 0 1 * *', $$
--     SELECT create_monthly_partition('query_snapshots', (NOW() + INTERVAL '2 months')::DATE)
-- $$);

COMMIT;

-- Future maintenance: Drop old partitions
-- ALTER TABLE query_snapshots DETACH PARTITION query_snapshots_y2026m04;
-- DROP TABLE query_snapshots_y2026m04;
