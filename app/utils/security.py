# app/utils/security.py
import os
from typing import Optional, Dict, Any
from jose import jwt, JWTError

from app.core.settings import JWT_SECRET, JWT_ALGORITHM
JWT_SUB_CLAIM = os.getenv("JWT_SUB_CLAIM", "sub")  # por si tu login usa 'user_id' u otro

class TokenError(Exception):
    pass

def decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise TokenError(str(e))

def get_user_id_from_payload(payload: Dict[str, Any]) -> str:
    user_id = payload.get(JWT_SUB_CLAIM)
    if not user_id:
        raise TokenError(f"El claim '{JWT_SUB_CLAIM}' no está presente en el token")
    return str(user_id)
