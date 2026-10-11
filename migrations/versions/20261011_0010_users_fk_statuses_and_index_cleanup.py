"""fix user FKs, add saved search discount column, canonical statuses, drop duplicate indexes

- user_id FKs reference users.telegram_user_id; users is backfilled first.
- saved_searches.min_discount_percent, declared by the model but never migrated.
- legacy lead/watchlist statuses are normalized and the Postgres status CHECKs created.
- indexes covered by a unique constraint or a composite index are dropped.

Revision ID: 20261011_0010
Revises: 20260407_0009
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261011_0010"
down_revision = "20260407_0009"
branch_labels = None
depends_on = None

# Tables whose user_id column stores the raw Telegram user ID.
USER_SCOPED_TABLES = (
    "trackers",
    "saved_searches",
    "tracker_events",
    "lead_items",
    "watchlist_items",
)

# Canonical vocabularies (hard-coded on purpose: migrations must not import app code).
LEAD_STATUSES = (
    "new",
    "reviewing",
    "in_progress",
    "negotiating",
    "deferred",
    "bought",
    "reselling",
    "sold",
    "skipped",
)
WATCHLIST_STATUSES = ("watching", "reviewing", "in_progress", "deferred", "skipped")
MARKET_STATUSES = ("active", "missing", "price_drop", "duplicate")

# Value of the CHECK lists 0008 used to add -> canonical value; anything else outside the
# canonical list gets the default.
LEAD_STATUS_MAP = {"closed": "sold", "abandoned": "skipped"}
WATCHLIST_STATUS_MAP = {"lead": "in_progress", "archived": "skipped"}

LEAD_STATUS_CHECK = "chk_lead_items_status"
WATCHLIST_STATUS_CHECK = "chk_watchlist_items_workflow_status"
MARKET_STATUS_CHECK = "chk_watchlist_items_market_status"

# Indexes made redundant by a unique constraint or a composite index on the same leading
# columns: (table, index name, columns, how downgrade() recreates it).
# downgrade() modes: "plain" = a regular index, "active" = partial on active = true
# (Postgres; plain elsewhere, as 0008 did), "skip" = leave it dropped (indexes that only
# Base.metadata.create_all ever produced).
REDUNDANT_INDEXES = (
    ("users", "idx_users_telegram_id", ("telegram_user_id",), "plain"),
    ("tracker_events", "idx_tracker_events_user", ("user_id",), "plain"),
    ("lead_items", "idx_lead_items_user", ("user_id",), "plain"),
    ("watchlist_items", "idx_watchlist_items_user", ("user_id",), "plain"),
    ("query_snapshots", "idx_query_snapshots_query", ("query",), "plain"),
    ("query_snapshots", "idx_query_snapshots_query_time", ("query", "snapshot_at"), "plain"),
    ("query_listing_states", "idx_query_listing_states_query", ("query",), "plain"),
    # Listing states are always loaded by query without an active filter, so the partial
    # index is never used; uq_query_listing_state (query, ad_id) serves those lookups.
    ("query_listing_states", "idx_query_listing_states_query_active", ("query",), "active"),
    # Duplicates of named indexes, created by Base.metadata.create_all on old local DBs.
    ("trackers", "ix_trackers_user_id", ("user_id",), "skip"),
    ("saved_searches", "ix_saved_searches_user_id", ("user_id",), "skip"),
    ("tracker_events", "ix_tracker_events_user_id", ("user_id",), "skip"),
    ("tracker_events", "ix_tracker_events_tracker_id", ("tracker_id",), "skip"),
    ("lead_items", "ix_lead_items_user_id", ("user_id",), "skip"),
    ("watchlist_items", "ix_watchlist_items_user_id", ("user_id",), "skip"),
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _index_names(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table) if index["name"]}


def _check_names(bind, table: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints(table)
        if constraint["name"]
    }


def _is_covered(bind, table: str, index_name: str, columns: tuple[str, ...]) -> bool:
    """True when another full index or unique constraint starts with ``columns``."""
    inspector = sa.inspect(bind)
    candidates: list[list[str]] = []
    for index in inspector.get_indexes(table):
        if index["name"] == index_name:
            continue
        options = index.get("dialect_options") or {}
        if any(options.get(key) is not None for key in ("postgresql_where", "sqlite_where")):
            continue  # a partial index does not cover every row
        candidates.append(list(index["column_names"]))
    for constraint in inspector.get_unique_constraints(table):
        candidates.append(list(constraint["column_names"]))
    primary_key = inspector.get_pk_constraint(table).get("constrained_columns") or []
    candidates.append(list(primary_key))
    prefix = list(columns)
    return any(candidate[: len(prefix)] == prefix for candidate in candidates)


def _backfill_users(bind, tables: set[str]) -> None:
    sources = [table for table in USER_SCOPED_TABLES if table in tables]
    if "users" not in tables or not sources:
        return
    union = " UNION ".join(f"SELECT user_id FROM {table}" for table in sources)
    # Literals rather than bind parameters: Postgres would type an untyped parameter in a
    # SELECT list as text, which does not cast to boolean on assignment.
    select_sql = (
        "SELECT ids.user_id, '', FALSE, CURRENT_TIMESTAMP "
        f"FROM ({union}) AS ids WHERE ids.user_id IS NOT NULL"
    )
    columns = "users (telegram_user_id, first_name, is_bot, created_at)"
    dialect = bind.dialect.name
    if dialect == "postgresql":
        sql = f"INSERT INTO {columns} {select_sql} ON CONFLICT (telegram_user_id) DO NOTHING"
    elif dialect == "sqlite":
        sql = f"INSERT OR IGNORE INTO {columns} {select_sql}"
    else:
        sql = (
            f"INSERT INTO {columns} {select_sql} AND NOT EXISTS "
            "(SELECT 1 FROM users WHERE users.telegram_user_id = ids.user_id)"
        )
    bind.execute(sa.text(sql))


def _normalize(bind, table: str, column: str, mapping: dict[str, str], allowed, default) -> None:
    for legacy, canonical in mapping.items():
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = :canonical WHERE {column} = :legacy"),
            {"canonical": canonical, "legacy": legacy},
        )
    placeholders = ", ".join(f":v{position}" for position in range(len(allowed)))
    params = {f"v{position}": value for position, value in enumerate(allowed)}
    params["default"] = default
    bind.execute(
        sa.text(
            f"UPDATE {table} SET {column} = :default "
            f"WHERE {column} IS NULL OR {column} NOT IN ({placeholders})"
        ),
        params,
    )


def _drop_checks(bind, table: str, names) -> set[str]:
    """Drop the named CHECK constraints that exist on ``table``; return the dropped names."""
    present = [name for name in names if name in _check_names(bind, table)]
    if not present:
        return set()
    if bind.dialect.name == "sqlite":
        # SQLite cannot drop a constraint in place; batch mode rebuilds the table.
        with op.batch_alter_table(table, recreate="always") as batch:
            for name in present:
                batch.drop_constraint(name, type_="check")
    else:
        for name in present:
            op.drop_constraint(name, table, type_="check")
    return set(present)


def _create_checks(bind, table: str, checks: dict[str, str]) -> None:
    if not checks:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch:
            for name, condition in checks.items():
                batch.create_check_constraint(name, condition)
    else:
        for name, condition in checks.items():
            op.create_check_constraint(name, table, condition)


def _replace_status_checks(bind, table: str, checks: dict[str, str], normalize) -> None:
    """Swap the status CHECKs of ``table``: drop the old ones, rewrite the rows, add the new.

    The order matters: the old CHECK rejects the new vocabulary, so rewriting the rows while
    it is still in place fails with a check violation. The new CHECKs are validated, which
    holds because ``normalize`` leaves only allowed values. Migrations add CHECKs on Postgres
    only; on SQLite only the CHECKs that were already there are put back.
    """
    dropped = _drop_checks(bind, table, tuple(checks))
    normalize()
    if bind.dialect.name == "sqlite":
        checks = {name: condition for name, condition in checks.items() if name in dropped}
    _create_checks(bind, table, checks)


def _user_fks(bind, table: str) -> list[dict]:
    return [
        fk
        for fk in sa.inspect(bind).get_foreign_keys(table)
        if fk["constrained_columns"] == ["user_id"] and fk["referred_table"] == "users"
    ]


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    is_sqlite = bind.dialect.name == "sqlite"

    # Step 1: user_id stores the Telegram ID, so the FKs must reference
    # users.telegram_user_id (BIGINT, unique) instead of users.id.
    if not is_sqlite:
        for table in USER_SCOPED_TABLES:
            if table not in tables:
                continue
            for fk in _user_fks(bind, table):
                if fk["referred_columns"] != ["telegram_user_id"] and fk["name"]:
                    op.drop_constraint(fk["name"], table, type_="foreignkey")

    # Step 2: every referenced Telegram ID needs a users row before the FKs come back.
    _backfill_users(bind, tables)

    if not is_sqlite and "users" in tables:
        for table in USER_SCOPED_TABLES:
            if table not in tables:
                continue
            referred = [fk["referred_columns"] for fk in _user_fks(bind, table)]
            if ["telegram_user_id"] in referred:
                continue
            op.create_foreign_key(
                f"fk_{table}_user_id",
                table,
                "users",
                ["user_id"],
                ["telegram_user_id"],
                ondelete="CASCADE",
            )

    # Step 3: the column exists in the model (TrackerFiltersMixin) but 0005 never created it.
    if "saved_searches" in tables:
        columns = {column["name"] for column in sa.inspect(bind).get_columns("saved_searches")}
        if "min_discount_percent" not in columns:
            op.add_column(
                "saved_searches",
                sa.Column("min_discount_percent", sa.Float(), nullable=True),
            )

    # Step 4: one status vocabulary for the DB, the API, the bot and the Mini App.
    if "lead_items" in tables:
        _replace_status_checks(
            bind,
            "lead_items",
            {LEAD_STATUS_CHECK: _in_list("status", LEAD_STATUSES)},
            lambda: _normalize(
                bind, "lead_items", "status", LEAD_STATUS_MAP, LEAD_STATUSES, "new"
            ),
        )
    if "watchlist_items" in tables:

        def normalize_watchlist() -> None:
            _normalize(
                bind,
                "watchlist_items",
                "workflow_status",
                WATCHLIST_STATUS_MAP,
                WATCHLIST_STATUSES,
                "watching",
            )
            _normalize(bind, "watchlist_items", "market_status", {}, MARKET_STATUSES, "active")

        _replace_status_checks(
            bind,
            "watchlist_items",
            {
                WATCHLIST_STATUS_CHECK: _in_list("workflow_status", WATCHLIST_STATUSES),
                MARKET_STATUS_CHECK: _in_list("market_status", MARKET_STATUSES),
            },
            normalize_watchlist,
        )

    # Step 5: drop indexes duplicated by a unique constraint or a composite index.
    for table, index_name, columns, _ in REDUNDANT_INDEXES:
        if table not in tables or index_name not in _index_names(bind, table):
            continue
        if _is_covered(bind, table, index_name, columns):
            op.drop_index(index_name, table_name=table)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    is_sqlite = bind.dialect.name == "sqlite"

    # Recreate the indexes of 0002/0004/0006/0008/0009 that upgrade() dropped.
    for table, index_name, columns, mode in REDUNDANT_INDEXES:
        if mode == "skip" or table not in tables:
            continue
        if index_name in _index_names(bind, table):
            continue
        if mode == "active" and not is_sqlite:
            op.create_index(
                index_name,
                table,
                list(columns),
                postgresql_where=sa.text("active = true"),
            )
        else:
            op.create_index(index_name, table, list(columns))

    # Drop the status CHECKs and keep the rows: 0008 no longer adds status CHECKs, and the
    # code before this revision already wrote the canonical statuses (bought, sold, skipped...).
    # The market status CHECK stays, 0008 adds the same list.
    if "lead_items" in tables:
        _drop_checks(bind, "lead_items", (LEAD_STATUS_CHECK,))
    if "watchlist_items" in tables:
        _drop_checks(bind, "watchlist_items", (WATCHLIST_STATUS_CHECK,))

    if "saved_searches" in tables:
        columns = {column["name"] for column in sa.inspect(bind).get_columns("saved_searches")}
        if "min_discount_percent" in columns:
            op.drop_column("saved_searches", "min_discount_percent")

    # Drop the FKs to users.telegram_user_id. The 0009 FKs to users.id are not restored:
    # user_id holds Telegram IDs, so they would reject every existing row. Backfilled
    # users rows are kept.
    if not is_sqlite:
        for table in USER_SCOPED_TABLES:
            if table not in tables:
                continue
            for fk in _user_fks(bind, table):
                if fk["referred_columns"] == ["telegram_user_id"] and fk["name"]:
                    op.drop_constraint(fk["name"], table, type_="foreignkey")
