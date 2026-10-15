from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.services.users import update_user_profile
from app.db import get_db
from app.schemas.auth import UserUpdateIn, UserOut  # Debes crear estos esquemas
from app.dependencies.auth import get_current_user  # Debes tener esta dependencia

router = APIRouter(prefix="/user", tags=["user"])

@router.put("/me", response_model=UserOut)
def edit_my_profile(
    data: UserUpdateIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):

    user = update_user_profile(
        db=db,
        user_id=current_user.id,
        email=data.email,
        password=data.password,
        first_name=data.first_name,
        last_name=data.last_name,
    )
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user