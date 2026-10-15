# app/auth/security.py
import os, time, uuid, secrets, datetime as dt
from passlib.context import CryptContext
import jwt 
from datetime import datetime, timezone, timedelta
import secrets, hashlib


pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

from app.core.settings import JWT_SECRET, JWT_ALGORITHM as JWT_ALG
ACCESS_MINUTES = 60
REFRESH_TTL_DAYS = 30


def create_reset_token(user_id: int, email: str, minutes: int = 15) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "email": email,
        "scope": "pwd_reset",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def hash_password(password: str) -> str:
    return pwd_ctx.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    return pwd_ctx.verify(password, password_hash)

def create_access_token(user_id: int, email: str) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + ACCESS_MINUTES * 60,
        "typ": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str):
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])

def generate_otp_code() -> str:
    # 6 dígitos, con ceros a la izquierda
    return f"{secrets.randbelow(1000000):06d}"

def new_challenge_id() -> str:
    return str(uuid.uuid4())

def minutes_from_now(m: int) -> dt.datetime:
    return dt.datetime.utcnow() + dt.timedelta(minutes=m)

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def new_refresh_token_value() -> str:
    return secrets.token_urlsafe(64)

def hash_refresh_token(token_plain: str) -> str:
    return _sha256(token_plain)

def refresh_expires_at(days: int = REFRESH_TTL_DAYS) -> datetime:
    return datetime.utcnow() + timedelta(days=days)