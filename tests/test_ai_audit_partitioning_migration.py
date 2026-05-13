from __future__ import annotations

from pathlib import Path

MIGRATION = Path("migrations/versions/20260514_0016_ai_audit_partitioning.py")


def test_ai_audit_partitioning_migration_swaps_and_validates_copy() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260514_0016"' in sql
    assert 'down_revision: str | Sequence[str] | None = "20260513_0015"' in sql
    assert "PARTITION BY RANGE (created_at)" in sql
    assert "PRIMARY KEY (id, created_at)" in sql
    assert "CREATE TABLE ai_audit_log_default PARTITION OF ai_audit_log DEFAULT" in sql
    assert "copied_count <> legacy_count" in sql
    assert "DROP TABLE ai_audit_log_unpartitioned" in sql


def test_ai_audit_partitioning_migration_has_future_partition_maintenance() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ensure_ai_audit_log_monthly_partitions" in sql
    assert "months_ahead integer DEFAULT 13" in sql
    assert "to_char(month_start, 'YYYY_MM')" in sql
    assert "PERFORM ensure_ai_audit_log_monthly_partitions(1, 13)" in sql
