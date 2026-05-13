"""Partition ai_audit_log by created_at month

Revision ID: 20260514_0016
Revises: 20260513_0015

REST-DB-01: ai_audit_log can grow indefinitely between retention runs. Convert
it to a monthly RANGE-partitioned table so retention and date-filtered audits
can prune whole child tables instead of scanning one monolith.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260514_0016"
down_revision: str | Sequence[str] | None = "20260513_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _create_partition_maintenance_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ensure_ai_audit_log_monthly_partitions(
            months_back integer DEFAULT 1,
            months_ahead integer DEFAULT 13
        )
        RETURNS void AS $$
        DECLARE
            table_oid oid;
            table_kind "char";
            current_month date := date_trunc('month', now())::date;
            month_start date;
            last_month date;
            partition_name text;
        BEGIN
            IF months_back < 0 OR months_ahead < 0 THEN
                RAISE EXCEPTION 'months_back/months_ahead must be non-negative';
            END IF;

            table_oid := to_regclass('ai_audit_log');
            IF table_oid IS NULL THEN
                RETURN;
            END IF;
            SELECT relkind INTO table_kind FROM pg_class WHERE oid = table_oid;
            IF table_kind <> 'p' THEN
                RETURN;
            END IF;

            month_start := (
                current_month - make_interval(months => months_back)
            )::date;
            last_month := (
                current_month + make_interval(months => months_ahead)
            )::date;

            WHILE month_start <= last_month LOOP
                partition_name := format(
                    'ai_audit_log_%s',
                    to_char(month_start, 'YYYY_MM')
                );
                IF to_regclass(partition_name) IS NULL THEN
                    EXECUTE format(
                        'CREATE TABLE %I PARTITION OF ai_audit_log '
                        'FOR VALUES FROM (%L) TO (%L)',
                        partition_name,
                        month_start,
                        (month_start + interval '1 month')::date
                    );
                END IF;
                month_start := (month_start + interval '1 month')::date;
            END LOOP;

            IF to_regclass('ai_audit_log_default') IS NULL THEN
                EXECUTE
                    'CREATE TABLE ai_audit_log_default '
                    'PARTITION OF ai_audit_log DEFAULT';
            END IF;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def _create_cleanup_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION clean_ai_audit_log(
            retention_days integer DEFAULT 365
        )
        RETURNS bigint AS $$
        DECLARE
            deleted_count bigint;
        BEGIN
            PERFORM ensure_ai_audit_log_monthly_partitions(1, 13);
            DELETE FROM ai_audit_log
            WHERE created_at < now() - (retention_days || ' days')::interval;
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def _create_legacy_cleanup_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION clean_ai_audit_log(
            retention_days integer DEFAULT 365
        )
        RETURNS bigint AS $$
        DECLARE
            deleted_count bigint;
        BEGIN
            DELETE FROM ai_audit_log
            WHERE created_at < now() - (retention_days || ' days')::interval;
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def upgrade() -> None:
    if not _is_postgresql():
        return

    _create_partition_maintenance_function()
    op.execute(
        """
        DO $$
        DECLARE
            legacy_count bigint;
            copied_count bigint;
            max_id bigint;
            month_start date;
            month_stop date;
            partition_name text;
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.oid = to_regclass('ai_audit_log')
                  AND c.relkind = 'p'
            ) THEN
                PERFORM ensure_ai_audit_log_monthly_partitions(1, 13);
                RETURN;
            END IF;

            LOCK TABLE ai_audit_log IN ACCESS EXCLUSIVE MODE;
            ALTER SEQUENCE IF EXISTS ai_audit_log_id_seq OWNED BY NONE;
            CREATE SEQUENCE IF NOT EXISTS ai_audit_log_id_seq AS bigint;
            ALTER TABLE ai_audit_log RENAME TO ai_audit_log_unpartitioned;
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'ai_audit_log_unpartitioned'::regclass
                  AND conname = 'ai_audit_log_pkey'
            ) THEN
                ALTER TABLE ai_audit_log_unpartitioned
                    RENAME CONSTRAINT ai_audit_log_pkey
                    TO ai_audit_log_unpartitioned_pkey;
            END IF;
            DROP INDEX IF EXISTS idx_ai_audit_user;
            DROP INDEX IF EXISTS idx_ai_audit_created;
            DROP INDEX IF EXISTS idx_ai_audit_log_created_at;

            CREATE TABLE ai_audit_log (
                id bigint NOT NULL DEFAULT nextval('ai_audit_log_id_seq'::regclass),
                user_id bigint NOT NULL,
                endpoint varchar(64) NOT NULL,
                ad_id varchar(32),
                query varchar(256),
                result_summary varchar(512),
                model varchar(128) NOT NULL,
                latency_ms integer,
                ip_address varchar(45),
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT ai_audit_log_pkey PRIMARY KEY (id, created_at),
                CONSTRAINT ai_audit_log_user_id_fkey
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) PARTITION BY RANGE (created_at);

            SELECT COALESCE(
                date_trunc('month', min(created_at)),
                date_trunc('month', now()) - interval '1 month'
            )::date
            INTO month_start
            FROM ai_audit_log_unpartitioned;

            month_stop := (date_trunc('month', now()) + interval '13 months')::date;
            WHILE month_start <= month_stop LOOP
                partition_name := format(
                    'ai_audit_log_%s',
                    to_char(month_start, 'YYYY_MM')
                );
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF ai_audit_log '
                    'FOR VALUES FROM (%L) TO (%L)',
                    partition_name,
                    month_start,
                    (month_start + interval '1 month')::date
                );
                month_start := (month_start + interval '1 month')::date;
            END LOOP;

            CREATE TABLE ai_audit_log_default PARTITION OF ai_audit_log DEFAULT;

            SELECT count(*) INTO legacy_count FROM ai_audit_log_unpartitioned;
            INSERT INTO ai_audit_log (
                id,
                user_id,
                endpoint,
                ad_id,
                query,
                result_summary,
                model,
                latency_ms,
                created_at,
                ip_address
            )
            SELECT
                id,
                user_id,
                endpoint,
                ad_id,
                query,
                result_summary,
                model,
                latency_ms,
                created_at,
                ip_address
            FROM ai_audit_log_unpartitioned
            ORDER BY created_at, id;
            GET DIAGNOSTICS copied_count = ROW_COUNT;
            IF copied_count <> legacy_count THEN
                RAISE EXCEPTION
                    'ai_audit_log partition copy mismatch: copied %, expected %',
                    copied_count,
                    legacy_count;
            END IF;

            SELECT COALESCE(max(id), 0) INTO max_id FROM ai_audit_log;
            IF max_id > 0 THEN
                PERFORM setval('ai_audit_log_id_seq', max_id, true);
            ELSE
                PERFORM setval('ai_audit_log_id_seq', 1, false);
            END IF;
            ALTER SEQUENCE ai_audit_log_id_seq OWNED BY ai_audit_log.id;

            CREATE INDEX idx_ai_audit_user ON ai_audit_log (user_id);
            CREATE INDEX idx_ai_audit_created ON ai_audit_log (created_at);

            DROP TABLE ai_audit_log_unpartitioned;
        END;
        $$
        """
    )
    _create_cleanup_function()


def downgrade() -> None:
    if not _is_postgresql():
        return

    op.execute(
        """
        DO $$
        DECLARE
            partitioned_count bigint;
            copied_count bigint;
            max_id bigint;
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_class c
                WHERE c.oid = to_regclass('ai_audit_log')
                  AND c.relkind = 'p'
            ) THEN
                RETURN;
            END IF;

            LOCK TABLE ai_audit_log IN ACCESS EXCLUSIVE MODE;
            ALTER SEQUENCE IF EXISTS ai_audit_log_id_seq OWNED BY NONE;
            ALTER TABLE ai_audit_log RENAME TO ai_audit_log_partitioned;
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'ai_audit_log_partitioned'::regclass
                  AND conname = 'ai_audit_log_pkey'
            ) THEN
                ALTER TABLE ai_audit_log_partitioned
                    RENAME CONSTRAINT ai_audit_log_pkey
                    TO ai_audit_log_partitioned_pkey;
            END IF;
            DROP INDEX IF EXISTS idx_ai_audit_user;
            DROP INDEX IF EXISTS idx_ai_audit_created;

            CREATE TABLE ai_audit_log (
                id bigint NOT NULL DEFAULT nextval('ai_audit_log_id_seq'::regclass),
                user_id bigint NOT NULL,
                endpoint varchar(64) NOT NULL,
                ad_id varchar(32),
                query varchar(256),
                result_summary varchar(512),
                model varchar(128) NOT NULL,
                latency_ms integer,
                created_at timestamptz NOT NULL DEFAULT now(),
                ip_address varchar(45),
                CONSTRAINT ai_audit_log_pkey PRIMARY KEY (id),
                CONSTRAINT ai_audit_log_user_id_fkey
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            SELECT count(*) INTO partitioned_count FROM ai_audit_log_partitioned;
            INSERT INTO ai_audit_log (
                id,
                user_id,
                endpoint,
                ad_id,
                query,
                result_summary,
                model,
                latency_ms,
                created_at,
                ip_address
            )
            SELECT
                id,
                user_id,
                endpoint,
                ad_id,
                query,
                result_summary,
                model,
                latency_ms,
                created_at,
                ip_address
            FROM ai_audit_log_partitioned
            ORDER BY created_at, id;
            GET DIAGNOSTICS copied_count = ROW_COUNT;
            IF copied_count <> partitioned_count THEN
                RAISE EXCEPTION
                    'ai_audit_log downgrade copy mismatch: copied %, expected %',
                    copied_count,
                    partitioned_count;
            END IF;

            SELECT COALESCE(max(id), 0) INTO max_id FROM ai_audit_log;
            IF max_id > 0 THEN
                PERFORM setval('ai_audit_log_id_seq', max_id, true);
            ELSE
                PERFORM setval('ai_audit_log_id_seq', 1, false);
            END IF;
            ALTER SEQUENCE ai_audit_log_id_seq OWNED BY ai_audit_log.id;

            CREATE INDEX idx_ai_audit_user ON ai_audit_log (user_id);
            CREATE INDEX idx_ai_audit_created ON ai_audit_log (created_at);
            CREATE INDEX idx_ai_audit_log_created_at ON ai_audit_log (created_at);

            DROP TABLE ai_audit_log_partitioned CASCADE;
        END;
        $$
        """
    )
    op.execute("DROP FUNCTION IF EXISTS ensure_ai_audit_log_monthly_partitions(integer, integer)")
    _create_legacy_cleanup_function()
