"""Drop the unused sellers' contacts table

LEGAL-07: ``contacts`` held Kufar sellers' phones and names, but no code
path ever wrote it — a store of third parties' personal data with no
purpose and no mention in the processing policy. The table is dropped only
when empty: rows mean someone filled it by hand, and erasing them is the
owner's decision, so the migration stops instead.

Revision ID: 20261112_0036
Revises: 20261112_0035

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0036"
down_revision: str | Sequence[str] | None = "20261112_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("contacts"):
        return
    rows = bind.execute(sa.text("SELECT count(*) FROM contacts")).scalar_one()
    if rows:
        raise RuntimeError(
            f"contacts holds {rows} row(s); export or erase them deliberately "
            "before this migration drops the table"
        )
    op.drop_table("contacts")


def downgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("seller_name", sa.String(length=128), nullable=True),
        sa.Column("kufar_profile", sa.String(length=512), nullable=True),
        sa.Column(
            "saved_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "phone", name="uq_contacts_user_phone"),
    )
    op.create_index("idx_contacts_user", "contacts", ["user_id"])
