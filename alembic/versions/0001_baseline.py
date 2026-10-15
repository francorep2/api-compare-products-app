"""baseline: crea las tablas que falten

Idempotente a propósito: en bases ya existentes (creadas antes con create_all o con
docker/init/01_shopping.sql) no toca nada; en bases nuevas crea todo el esquema.

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""
from alembic import op

import app.auth.models  # noqa: F401
import app.models  # noqa: F401
import app.models.comparison  # noqa: F401
import app.models.support  # noqa: F401
from app.db import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    pass  # el baseline no se revierte
