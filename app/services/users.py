# ------------------------ Funciones para Editar usuario ------------------------

from sqlalchemy.orm import Session
from app.auth.models import User
from app.auth.security import hash_password

def update_user_profile(
    db: Session,
    user_id: int,
    email: str = None,
    password: str = None,
    first_name: str = None,
    last_name: str = None,
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None

    if email:
        user.email = email.lower()
    if password:
        user.password_hash = hash_password(password)
    if first_name:
        user.first_name = first_name
    if last_name:
        user.last_name = last_name

    db.commit()
    db.refresh(user)
    return user