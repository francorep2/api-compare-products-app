# app/auth/schemas.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from pydantic import BaseModel, EmailStr, Field

class PasswordResetStartIn(BaseModel):
    email: EmailStr

class PasswordResetVerifyIn(BaseModel):
    challenge_id: str
    code: str = Field(min_length=4, max_length=8)

class ResetTokenOut(BaseModel):
    reset_token: str

class PasswordResetConfirmIn(BaseModel):
    reset_token: str
    new_password: str = Field(min_length=6, max_length=128)

class RegisterStartIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)

class RegisterStartOut(BaseModel):
    challenge_id: str
    email: EmailStr
    message: str

class RegisterVerifyIn(BaseModel):
    challenge_id: str
    code: str

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
