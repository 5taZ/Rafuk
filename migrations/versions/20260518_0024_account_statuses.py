"""Wave A: account statuses and admin audit data model.

Revision ID: 20260518_0024
Revises: 20260517_0023
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260518_0024"
down_revision: str | Sequence[str] | None = "20260517_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT_STATUS_SEED_ROWS: tuple[dict[str, object], ...] = (
    {
        "code": "bare_search",
        "display_name": "Голый Поиск",
        "tagline": "базовый режим: ищешь руками, без магии",
        "accent": "gray",
        "sort_order": 10,
        "ai_daily_limit": 0,
        "assistant_daily_limit": 0,
    },
    {
        "code": "scout",
        "display_name": "Скаут Барахолки",
        "tagline": "попробовать AI, понять ценность",
        "accent": "blue",
        "sort_order": 20,
        "ai_daily_limit": 10,
        "assistant_daily_limit": 3,
    },
    {
        "code": "flipper",
        "display_name": "Флиппер",
        "tagline": "регулярный поиск выгодных лотов",
        "accent": "violet",
        "sort_order": 30,
        "ai_daily_limit": 40,
        "assistant_daily_limit": 12,
    },
    {
        "code": "shark",
        "display_name": "Куфарная Акула",
        "tagline": "активный ресейл / постоянные сделки",
        "accent": "amber",
        "sort_order": 40,
        "ai_daily_limit": 120,
        "assistant_daily_limit": 35,
    },
    {
        "code": "market_maker",
        "display_name": "Имба Маркетмейкер",
        "tagline": "почти “god mode”, высокий лимит",
        "accent": "red",
        "sort_order": 50,
        "ai_daily_limit": 300,
        "assistant_daily_limit": 100,
    },
)


def _account_statuses_table() -> sa.Table:
    return sa.table(
        "account_statuses",
        sa.column("code", sa.String(length=32)),
        sa.column("display_name", sa.String(length=64)),
        sa.column("tagline", sa.String(length=255)),
        sa.column("accent", sa.String(length=32)),
        sa.column("sort_order", sa.Integer()),
        sa.column("ai_daily_limit", sa.Integer()),
        sa.column("assistant_daily_limit", sa.Integer()),
    )


def upgrade() -> None:
    op.create_table(
        "account_statuses",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("tagline", sa.String(length=255), nullable=False),
        sa.Column("accent", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("ai_daily_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assistant_daily_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "ai_daily_limit >= 0 AND ai_daily_limit <= 10000",
            name="chk_account_statuses_ai_daily_limit_bounds",
        ),
        sa.CheckConstraint(
            "assistant_daily_limit >= 0 AND assistant_daily_limit <= 10000",
            name="chk_account_statuses_assistant_daily_limit_bounds",
        ),
    )
    op.bulk_insert(_account_statuses_table(), [dict(row) for row in ACCOUNT_STATUS_SEED_ROWS])
    op.create_index(
        "idx_account_statuses_sort_order",
        "account_statuses",
        ["sort_order"],
    )

    with op.batch_alter_table("users", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "account_status_code",
                sa.String(length=32),
                nullable=False,
                server_default="bare_search",
            )
        )
        batch_op.add_column(
            sa.Column("status_granted_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("status_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("status_note", sa.String(length=512), nullable=True))
        batch_op.create_foreign_key(
            "fk_users_account_status_code",
            "account_statuses",
            ["account_status_code"],
            ["code"],
        )
        batch_op.create_index("idx_users_account_status_code", ["account_status_code"])

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_admin_audit_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            name="fk_admin_audit_target_user_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("idx_admin_audit_actor", "admin_audit_log", ["actor_user_id"])
    op.create_index("idx_admin_audit_target", "admin_audit_log", ["target_user_id"])
    op.create_index("idx_admin_audit_created", "admin_audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_admin_audit_created", table_name="admin_audit_log")
    op.drop_index("idx_admin_audit_target", table_name="admin_audit_log")
    op.drop_index("idx_admin_audit_actor", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")

    with op.batch_alter_table("users", recreate="auto") as batch_op:
        batch_op.drop_index("idx_users_account_status_code")
        batch_op.drop_constraint("fk_users_account_status_code", type_="foreignkey")
        batch_op.drop_column("status_note")
        batch_op.drop_column("status_expires_at")
        batch_op.drop_column("status_granted_at")
        batch_op.drop_column("account_status_code")

    op.drop_index("idx_account_statuses_sort_order", table_name="account_statuses")
    op.drop_table("account_statuses")
