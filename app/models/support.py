# app/models.py
from __future__ import annotations
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, ForeignKey, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship, Mapped, mapped_column
import enum

from app.db import Base

# Enum de estado: usa Postgres ENUM nativo con nombre estable para migraciones
class TicketStatus(str, enum.Enum):
    OPEN = "abierto"
    IN_PROGRESS = "en_proceso"
    RESOLVED = "resuelto"
    CLOSED = "cerrado"

class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # ENUM nativo de Postgres. El parámetro "name" es CLAVE para Alembic.
    status = Column(
        Enum(TicketStatus, name="ticket_status_enum", create_type=True),
        nullable=False,
        default=TicketStatus.OPEN,
        server_default=TicketStatus.OPEN.value,
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user = relationship("User", back_populates="tickets")

    # Timestamps: created en servidor; updated se setea en cada UPDATE desde SQLAlchemy
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

# Índices útiles (status para filtros)
Index("ix_support_tickets_status", SupportTicket.status)
