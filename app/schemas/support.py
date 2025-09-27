

from pydantic import BaseModel, EmailStr


class SubmitSupportTiket(BaseModel):
    email: EmailStr
    message: str
