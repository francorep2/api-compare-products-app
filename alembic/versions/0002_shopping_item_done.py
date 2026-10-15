"""shopping_item.done (bases creadas con docker/init/01_shopping.sql no la tienen)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("shopping_item")}
    if "done" not in cols:
        op.add_column(
            "shopping_item",
            sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    pass
