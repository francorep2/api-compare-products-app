# app/auth/schemas.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

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
