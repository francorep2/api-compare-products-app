# app/auth/models.py
from sqlalchemy import Index, String, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlmodel import Column
from app.db import Base
import uuid
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base
import uuid
import datetime as dt
from sqlalchemy import Column, Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db import Base


class PendingRegistration(Base):
    __tablename__ = "pending_registrations"
    __table_args__ = (    
        UniqueConstraint("email", name="uq_pending_reg_email"),
        UniqueConstraint("challenge_id", name="uq_pending_reg_challenge"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    challenge_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    expires_at: Mapped["DateTime"] = mapped_column(DateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now())
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now())
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    otps: Mapped[list["OTPCode"]] = relationship(back_populates="user")

class OTPCode(Base):
    __tablename__ = "otp_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))  # 'verify' o 'login'
    challenge_id: Mapped[str] = mapped_column(String(36), index=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(6))
    expires_at: Mapped["DateTime"] = mapped_column(DateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="otps")



class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    token_hash = Column(String(255), unique=True, nullable=False)
    user_agent = Column(String(255), nullable=True)
    device_id = Column(String(255), nullable=True)
    ip = Column(String(64), nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_used_at = Column(DateTime, nullable=True)

    revoked = Column(Boolean, default=False, nullable=False)
    replaced_by_id = Column(Integer, ForeignKey("refresh_tokens.id"), nullable=True)

    user = relationship("User", backref="refresh_tokens", foreign_keys=[user_id])
    replaced_by = relationship("RefreshToken", remote_side=[id], uselist=False)

Index("ix_refresh_tokens_hash", RefreshToken.token_hash)

class ScrapePrefs(Base):
    __tablename__ = "scrape_prefs"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    include_coto = Column(Boolean, default=True, nullable=False)
    include_carrefour = Column(Boolean, default=True, nullable=False)
    include_dia = Column(Boolean, default=True, nullable=False)
    include_jumbo = Column(Boolean, default=True, nullable=False)

    user = relationship("User", backref="scrape_prefs", uselist=False)
