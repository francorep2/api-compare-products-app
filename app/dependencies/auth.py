# app/deps/auth.py
from dataclasses import dataclass
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.utils.security import decode_token, get_user_id_from_payload, TokenError

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

@dataclass
class User:
    id: str

def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    try:
        payload = decode_token(token)
        user_id = get_user_id_from_payload(payload)
        return User(id=user_id)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
