# app/auth/schemas.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from pydantic import BaseModel, EmailStr, Field

class PasswordResetStartIn(BaseModel):
    email: EmailStr

class PasswordResetVerifyIn(BaseModel):
    challenge_id: str
    code: str = Field(min_length=4, max_length=6)

class ResetTokenOut(BaseModel):
    reset_token: str

class PasswordResetConfirmIn(BaseModel):
    reset_token: str
    new_password: str = Field(min_length=6, max_length=128)

class RegisterStartIn(BaseModel):
    first_name: str = Field(min_length=2)
    last_name: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=6)

class RegisterStartOut(BaseModel):
    challenge_id: str
    email: EmailStr
    message: str

class RegisterVerifyIn(BaseModel):
    challenge_id: str
    code: str  = Field(min_length=4, max_length=6)

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LoginStartIn(BaseModel):
    email: EmailStr
    password: str

class LoginStartOut(BaseModel):
    challenge_id: str
    email: EmailStr
    message: str

class LoginVerifyIn(BaseModel):
    challenge_id: str
    code: str

class MeOut(BaseModel):
    id: int
    email: EmailStr
    is_verified: bool
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class UserUpdateIn(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    first_name: Optional[str] = Field(default=None, min_length=2)
    last_name: Optional[str] = Field(default=None, min_length=2)

class UserOut(BaseModel):
    id: int
    email: EmailStr
    first_name: str
    last_name: str
    is_verified: bool

    class Config:
        orm_mode = True
        
        
class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: Optional[str] = Field(
        default=None,
        description="En mobile se manda en body; en web puede venir por cookie HttpOnly"
    )
    device_id: Optional[str] = None


class LogoutIn(BaseModel):
    refresh_token: Optional[str] = None  # opcional si usás cookie HttpOnly