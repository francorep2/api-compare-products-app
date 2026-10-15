"""comparison_tasks: list_id y title (historial de comparaciones)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("comparison_tasks")}
    if "list_id" not in cols:
        op.add_column("comparison_tasks", sa.Column("list_id", sa.Integer(), nullable=True))
        op.create_index("ix_comparison_tasks_list_id", "comparison_tasks", ["list_id"])
    if "title" not in cols:
        op.add_column("comparison_tasks", sa.Column("title", sa.String(length=120), nullable=True))


def downgrade() -> None:
    pass
