# app/auth/router.py
from fastapi import APIRouter, Depends, HTTPException, status, Header, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from app.core.db import get_db
from app.auth import schemas as S
from app.auth.models import User, OTPCode, PendingRegistration
from app.auth.security import (
    hash_password, verify_password, create_access_token,
    generate_otp_code, new_challenge_id, minutes_from_now, decode_token
)
from app.auth.emailer import send_otp_email

router = APIRouter(prefix="/auth", tags=["auth"])

# Config OTP
OTP_TTL_MIN = 10
OTP_MAX_ATTEMPTS = 5


# ------------------------ Helpers ------------------------

def _mask(email: str) -> str:
    try:
        name, dom = email.split("@", 1)
        if len(name) <= 2:
            n = name[0] + "*"
        else:
            n = name[0] + "*" * (len(name) - 2) + name[-1]
        return f"{n}@{dom}"
    except Exception:
        return email


def _create_pending_and_queue_email(
    bg: BackgroundTasks, db: Session, email: str, password_hash: str
) -> str:
    """
    Crea/renueva un registro pendiente de alta y envía OTP por mail (en background).
    No crea el User todavía.
    """
    # Invalidar pendientes anteriores no consumidos para ese email (opcional)
    db.query(PendingRegistration).filter(
        PendingRegistration.email == email.lower(),
        PendingRegistration.consumed == False
    ).delete()

    code = generate_otp_code()
    challenge = new_challenge_id()

    pending = PendingRegistration(
        email=email.lower(),
        password_hash=password_hash,
        challenge_id=challenge,
        code=code,
        expires_at=minutes_from_now(OTP_TTL_MIN),
    )
    db.add(pending)
    db.commit()

    # Enviar OTP sin bloquear la request
    bg.add_task(send_otp_email, email, code, "verify")
    return challenge


def _create_login_otp_and_queue(
    bg: BackgroundTasks, db: Session, user: User, purpose: str = "login"
) -> str:
    """
    Crea un OTP para login en 2 pasos y envía el correo en background.
    """
    code = generate_otp_code()
    challenge = new_challenge_id()
    otp = OTPCode(
        user_id=user.id,
        purpose=purpose,
        challenge_id=challenge,
        code=code,
        expires_at=minutes_from_now(OTP_TTL_MIN),
    )
    db.add(otp)
    db.commit()

    bg.add_task(send_otp_email, user.email, code, purpose)
    return challenge


def _get_login_challenge(db: Session, challenge_id: str, expected_purpose: str) -> OTPCode:
    otp = db.query(OTPCode).filter(OTPCode.challenge_id == challenge_id).first()
    if not otp:
        raise HTTPException(400, "Challenge inválido o expirado")
    if otp.purpose != expected_purpose:
        raise HTTPException(400, "Challenge inválido (propósito incorrecto)")
    if otp.consumed:
        raise HTTPException(400, "OTP ya consumido")
    if otp.expires_at < datetime.utcnow():
        raise HTTPException(400, "OTP expirado")
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(429, "Demasiados intentos; solicitá un nuevo código")
    return otp


# ------------------------ Registro ------------------------

@router.post("/register/start", response_model=S.RegisterStartOut)
def register_start(
    data: S.RegisterStartIn,
    bg: BackgroundTasks,
    db: Session = Depends(get_db)
):
    # Si ya existe usuario activo con ese email, no revelamos demasiado
    existing = db.query(User).filter(User.email == data.email.lower()).first()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No se pudo iniciar el registro")

    challenge = _create_pending_and_queue_email(
        bg=bg,
        db=db,
        email=data.email,
        password_hash=hash_password(data.password),
    )
    return S.RegisterStartOut(
        challenge_id=challenge,
        email=data.email.lower(),
        message=f"Te enviamos un código a {_mask(data.email)}"
    )


@router.post("/register/verify", response_model=S.TokenOut)
def register_verify(
    data: S.RegisterVerifyIn,
    db: Session = Depends(get_db)
):
    pending = db.query(PendingRegistration).filter(
        PendingRegistration.challenge_id == data.challenge_id
    ).first()

    if not pending:
        raise HTTPException(400, "Challenge inválido o expirado")
    if pending.consumed:
        raise HTTPException(400, "OTP ya consumido")
    if pending.expires_at < datetime.utcnow():
        raise HTTPException(400, "OTP expirado")

    pending.attempts += 1
    if pending.attempts > OTP_MAX_ATTEMPTS:
        db.commit()
        raise HTTPException(429, "Demasiados intentos; iniciá nuevamente")

    if data.code != pending.code:
        db.commit()
        raise HTTPException(400, "Código incorrecto")

    # OK → marcar consumido y crear el usuario real si no existe
    pending.consumed = True

    existing = db.query(User).filter(User.email == pending.email).first()
    if existing:
        if not existing.is_active:
            db.commit()
            raise HTTPException(400, "Usuario inactivo")
        token = create_access_token(existing.id, existing.email)
        db.commit()
        return S.TokenOut(access_token=token)

    user = User(
        email=pending.email,
        password_hash=pending.password_hash,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, user.email)
    db.commit()
    return S.TokenOut(access_token=token)


@router.post("/register/resend")
def register_resend(
    challenge_id: str,
    bg: BackgroundTasks,
    db: Session = Depends(get_db)
):
    pending = db.query(PendingRegistration).filter(
        PendingRegistration.challenge_id == challenge_id
    ).first()

    if not pending or pending.consumed:
        raise HTTPException(400, "Challenge inválido")

    # Si expiró, regenerar
    if pending.expires_at < datetime.utcnow():
        pending.code = generate_otp_code()
        pending.expires_at = minutes_from_now(OTP_TTL_MIN)

    # Reenviar el mismo (o el regenerado)
    bg.add_task(send_otp_email, pending.email, pending.code, "verify")
    db.commit()
    return {"challenge_id": pending.challenge_id, "message": "Reenviamos el código"}


# ------------------------ Login 2 pasos ------------------------

@router.post("/login/start", response_model=S.LoginStartOut)
def login_start(
    data: S.LoginStartIn,
    bg: BackgroundTasks,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")

    challenge = _create_login_otp_and_queue(bg, db, user, purpose="login")
    return S.LoginStartOut(
        challenge_id=challenge,
        email=user.email,
        message=f"Te enviamos un código a {_mask(user.email)}"
    )


@router.post("/login/verify", response_model=S.TokenOut)
def login_verify(
    data: S.LoginVerifyIn,
    db: Session = Depends(get_db)
):
    otp = _get_login_challenge(db, data.challenge_id, expected_purpose="login")
    otp.attempts += 1
    if data.code != otp.code:
        db.commit()
        raise HTTPException(400, "Código incorrecto")

    otp.consumed = True
    db.commit()

    user = db.query(User).filter(User.id == otp.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario inválido")

    token = create_access_token(user.id, user.email)
    return S.TokenOut(access_token=token)


@router.post("/otp/resend")
def otp_resend(
    challenge_id: str,
    bg: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Reenvía el OTP de un challenge de login (no de registro).
    Para registro usar /auth/register/resend.
    """
    otp = db.query(OTPCode).filter(OTPCode.challenge_id == challenge_id).first()
    if not otp:
        raise HTTPException(400, "Challenge inválido")
    if otp.consumed:
        raise HTTPException(400, "OTP ya consumido")

    user = db.query(User).filter(User.id == otp.user_id).first()
    if not user:
        raise HTTPException(400, "Usuario inválido")

    # Si expiró, generar uno nuevo
    if otp.expires_at < datetime.utcnow():
        new_code = generate_otp_code()
        new_challenge = new_challenge_id()
        otp.code = new_code
        otp.challenge_id = new_challenge
        otp.expires_at = minutes_from_now(OTP_TTL_MIN)

    # Reenviar
    bg.add_task(send_otp_email, user.email, otp.code, otp.purpose)
    db.commit()
    return {"challenge_id": otp.challenge_id, "message": "Reenviamos el código"}


# ------------------------ Perfil ------------------------

@router.get("/me", response_model=S.MeOut)
def me(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db)
):
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

    return S.MeOut(id=user.id, email=user.email, is_verified=user.is_verified)