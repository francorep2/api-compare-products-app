# app/auth/router.py
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional

from app.core.db import get_db, Base, engine
from app.auth import schemas as S
from app.auth.models import User, OTPCode
from app.auth.security import (
    hash_password, verify_password, create_access_token,
    generate_otp_code, new_challenge_id, minutes_from_now, decode_token
)
from app.auth.emailer import send_otp_email

# Crear tablas en import (o hacelo en startup)
Base.metadata.create_all(bind=engine)

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_TTL_MIN = 10
OTP_MAX_ATTEMPTS = 5

# ---------- Helpers ----------
def _mask(email: str) -> str:
    try:
        name, dom = email.split("@", 1)
        if len(name) <= 2:
            n = name[0] + "*"
        else:
            n = name[0] + "*" * (len(name)-2) + name[-1]
        return f"{n}@{dom}"
    except:
        return email

def _create_and_send_otp(db: Session, user: User, purpose: str) -> str:
    code = generate_otp_code()
    challenge = new_challenge_id()
    otp = OTPCode(
        user_id=user.id,
        purpose=purpose,
        challenge_id=challenge,
        code=code,
        expires_at=minutes_from_now(OTP_TTL_MIN),
    )
    db.add(otp); db.commit()
    send_otp_email(user.email, code, purpose)
    return challenge

def _get_challenge(db: Session, challenge_id: str) -> OTPCode:
    otp = db.query(OTPCode).filter(OTPCode.challenge_id == challenge_id).first()
    if not otp:
        raise HTTPException(400, "Challenge inválido o expirado")
    if otp.consumed:
        raise HTTPException(400, "OTP ya consumido")
    if otp.expires_at < datetime.utcnow():
        raise HTTPException(400, "OTP expirado")
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(429, "Demasiados intentos; solicitá un nuevo código")
    return otp

# ---------- Registro ----------
@router.post("/register/start", response_model=S.RegisterStartOut)
def register_start(data: S.RegisterStartIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if user:
        raise HTTPException(400, "Ese email ya está registrado")

    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        is_active=True,
        is_verified=False
    )
    db.add(user); db.commit(); db.refresh(user)

    challenge = _create_and_send_otp(db, user, purpose="verify")
    return S.RegisterStartOut(
        challenge_id=challenge,
        email=user.email,
        message=f"Te enviamos un código a {_mask(user.email)}"
    )

@router.post("/register/verify", response_model=S.TokenOut)
def register_verify(data: S.RegisterVerifyIn, db: Session = Depends(get_db)):
    otp = _get_challenge(db, data.challenge_id)
    if otp.purpose != "verify":
        raise HTTPException(400, "Challenge inválido (propósito incorrecto)")
    otp.attempts += 1
    if data.code != otp.code:
        db.commit()
        raise HTTPException(400, "Código incorrecto")
    otp.consumed = True
    user = db.query(User).filter(User.id == otp.user_id).first()
    user.is_verified = True
    db.commit()
    token = create_access_token(user_id=user.id, email=user.email)
    return S.TokenOut(access_token=token)

# ---------- Login 2 pasos ----------
@router.post("/login/start", response_model=S.LoginStartOut)
def login_start(data: S.LoginStartIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")

    challenge = _create_and_send_otp(db, user, purpose="login")
    return S.LoginStartOut(
        challenge_id=challenge,
        email=user.email,
        message=f"Te enviamos un código a {_mask(user.email)}"
    )

@router.post("/login/verify", response_model=S.TokenOut)
def login_verify(data: S.LoginVerifyIn, db: Session = Depends(get_db)):
    otp = _get_challenge(db, data.challenge_id)
    if otp.purpose != "login":
        raise HTTPException(400, "Challenge inválido (propósito incorrecto)")
    otp.attempts += 1
    if data.code != otp.code:
        db.commit()
        raise HTTPException(400, "Código incorrecto")
    otp.consumed = True
    db.commit()
    user = db.query(User).filter(User.id == otp.user_id).first()
    token = create_access_token(user_id=user.id, email=user.email)
    return S.TokenOut(access_token=token)

# ---------- Reenviar OTP ----------
@router.post("/otp/resend")
def otp_resend(challenge_id: str, db: Session = Depends(get_db)):
    otp = db.query(OTPCode).filter(OTPCode.challenge_id == challenge_id).first()
    if not otp:
        raise HTTPException(400, "Challenge inválido")
    if otp.consumed:
        raise HTTPException(400, "OTP ya consumido")
    if otp.expires_at < datetime.utcnow():
        # crear uno nuevo
        user = db.query(User).filter(User.id == otp.user_id).first()
        new_ch = _create_and_send_otp(db, user, otp.purpose)
        return {"challenge_id": new_ch, "message": "Generamos un nuevo código"}
    else:
        # reenviar el mismo
        user = db.query(User).filter(User.id == otp.user_id).first()
        send_otp_email(user.email, otp.code, otp.purpose)
        return {"challenge_id": otp.challenge_id, "message": "Reenviamos el código"}

# ---------- /me (protegido por JWT) ----------
@router.get("/me", response_model=S.MeOut)
def me(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido/expirado")

    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario inválido")
    return {"id": user.id, "email": user.email, "is_verified": user.is_verified}
