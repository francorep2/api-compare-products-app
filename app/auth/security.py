# app/auth/security.py
import os, time, uuid, secrets, datetime as dt
from passlib.context import CryptContext
import jwt 

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
ACCESS_MINUTES = 60

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
