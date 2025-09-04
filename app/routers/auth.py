# app/auth/router.py
from fastapi import APIRouter, Depends, HTTPException, status, Header, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
from app.db import get_db
from app.schemas import auth as S
from app.auth.models import User, OTPCode, PendingRegistration
from app.auth.security import (
    hash_password, verify_password, create_access_token,
    generate_otp_code, new_challenge_id, minutes_from_now, decode_token
)
from app.auth.emailer import send_otp_email
from app.auth.security import create_reset_token

router = APIRouter(prefix="/auth", tags=["auth"])

# Config OTP
OTP_TTL_MIN = 10
OTP_MAX_ATTEMPTS = 5

def _create_pwd_otp(bg: BackgroundTasks, db: Session, user: User) -> str:
    code = generate_otp_code()
    challenge = new_challenge_id()
    otp = OTPCode(
        user_id=user.id,
        purpose="pwd_reset",
        challenge_id=challenge,
        code=code,
        expires_at=minutes_from_now(OTP_TTL_MIN),
    )
    db.add(otp); db.commit()
    bg.add_task(send_otp_email, user.email, code, "verify")
    return challenge

@router.post("/password/reset/start")
def password_reset_start(
    data: S.PasswordResetStartIn,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == data.email.lower()).first()
    # Siempre responder 200 para no filtrar existencia; pero sólo enviar si existe
    if user and user.is_active:
        _create_pwd_otp(bg, db, user)
    return {"message": "Si el email existe, enviamos un código para verificar"}

@router.post("/password/reset/resend")
def password_reset_resend(
    challenge_id: str,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
):
    otp = db.query(OTPCode).filter(
        OTPCode.challenge_id == challenge_id, OTPCode.purpose == "pwd_reset"
    ).first()
    if not otp or otp.consumed:
        raise HTTPException(400, "Challenge inválido")
    user = db.query(User).filter(User.id == otp.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(400, "Usuario inválido")

    if otp.expires_at < datetime.utcnow():
        # regenerar
        otp.code = generate_otp_code()
        otp.challenge_id = new_challenge_id()
        otp.expires_at = minutes_from_now(OTP_TTL_MIN)
        db.commit()

    bg.add_task(send_otp_email, user.email, otp.code, "verify")
    return {"challenge_id": otp.challenge_id, "message": "Reenviamos el código"}

@router.post("/password/reset/verify", response_model=S.ResetTokenOut)
def password_reset_verify(
    data: S.PasswordResetVerifyIn,
    db: Session = Depends(get_db),
):
    otp = db.query(OTPCode).filter(
        OTPCode.challenge_id == data.challenge_id, OTPCode.purpose == "pwd_reset"
    ).first()
    if not otp:
        raise HTTPException(400, "Challenge inválido o expirado")
    if otp.consumed:
        raise HTTPException(400, "OTP ya consumido")
    if otp.expires_at < datetime.utcnow():
        raise HTTPException(400, "OTP expirado")

    otp.attempts += 1
    if otp.attempts > OTP_MAX_ATTEMPTS:
        db.commit()
        raise HTTPException(429, "Demasiados intentos; solicitá un nuevo código")

    if data.code != otp.code:
        db.commit()
        raise HTTPException(400, "Código incorrecto")

    # OK: consumir OTP y emitir reset_token corto (scope=pwd_reset)
    otp.consumed = True
    db.commit()

    user = db.query(User).filter(User.id == otp.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(400, "Usuario inválido")

    reset_token = create_reset_token(user.id, user.email, minutes=15)
    return S.ResetTokenOut(reset_token=reset_token)

@router.post("/password/reset/confirm")
def password_reset_confirm(
    data: S.PasswordResetConfirmIn,
    db: Session = Depends(get_db),
):
    try:
        payload = decode_token(data.reset_token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido/expirado")
    if payload.get("scope") != "pwd_reset":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido")

    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Usuario inválido")

    user.password_hash = hash_password(data.new_password)
    db.commit()
    return {"message": "Contraseña actualizada"}

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


# ------------------------ Registro (con OTP) ------------------------

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


# ------------------------ Login SIN OTP (token directo) ------------------------

@router.post("/login", response_model=S.TokenOut)
def login_direct(
    data: S.LoginStartIn,   # reutilizamos el esquema: {email, password}
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")

    # Generar token directo
    token = create_access_token(user.id, user.email)
    return S.TokenOut(access_token=token)


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