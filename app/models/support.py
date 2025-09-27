# En tu archivo de modelos (ej: app/models.py)
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum

# Un Enum para manejar los estados del ticket, muy útil para el futuro
class TicketStatus(str, enum.Enum):
    OPEN = "abierto"
    IN_PROGRESS = "en_proceso"
    RESOLVED = "resuelto"
    CLOSED = "cerrado"

class SupportTicket(Base): # Asegúrate de que herede de tu Base declarativa
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, index=True)
    message = Column(Text, nullable=False)
    status = Column(Enum(TicketStatus), default=TicketStatus.OPEN, nullable=False)
    
    # Se vincula al usuario que está logueado, es más seguro que usar el email
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())