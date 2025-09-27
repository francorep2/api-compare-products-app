from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.dependencies.auth import get_current_user, User
from app.models.support import SupportTicket, TicketStatus
from app.schemas.support import SupportTicketCreate, SupportTicketOut

router = APIRouter(prefix="/support", tags=["Support"])

@router.post("/tickets", status_code=status.HTTP_201_CREATED, response_model=SupportTicketOut)
def submit_ticket(
    ticket_in: SupportTicketCreate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    t = SupportTicket(
            user_id=int(current_user.id),            
            message=ticket_in.message,
            status=TicketStatus.OPEN,  
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t