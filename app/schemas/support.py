# app/schemas/support.py
from pydantic import BaseModel, Field
from typing import Literal, Optional

class SupportTicketCreate(BaseModel):
    message: str = Field(..., min_length=3, max_length=4000)

class SupportTicketOut(BaseModel):
    id: int
    message: str
    status: Literal["abierto", "en_proceso", "resuelto", "cerrado"]
    user_id: int

    class Config:
        from_attributes = True  # pydantic v2
