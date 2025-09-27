from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app import models
from app.schemas.support import SubmitSupportTiket
from app import db
from app.dependencies.auth import get_current_user 

router = APIRouter(prefix="/support", tags=["Support"])

@router.post("/tickets", status_code=status.HTTP_201_CREATED)
def submit_ticket(
    ticket_in: SubmitSupportTiket, 
    db: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Permite a un usuario autenticado enviar un nuevo ticket de soporte.
    """
    new_ticket = models.SupportTicket(
        user_id=current_user.id, 
        message=ticket_in.message
    )
    db.add(new_ticket)
    db.commit()
    db.refresh(new_ticket)
    return {"detail": "Tu reporte ha sido enviado con éxito."}