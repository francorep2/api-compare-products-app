from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String
from sqlalchemy.sql import func

from app.db import Base


class ComparisonTask(Base):
    """Resultado (o estado) de una comparación de lista. Persistido en Postgres para
    sobrevivir reinicios y funcionar con varios workers."""

    __tablename__ = "comparison_tasks"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="processing")
    data = Column(JSON, nullable=True)
    list_id = Column(Integer, nullable=True, index=True)  # lista de compras desde la que se comparó (opcional)
    title = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


Index("ix_comparison_tasks_created_at", ComparisonTask.created_at)
