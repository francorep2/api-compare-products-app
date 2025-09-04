# app/schemas/shopping.py
from pydantic import BaseModel, Field
from datetime import datetime

class ShoppingListCreate(BaseModel):
    nombre: str = Field(min_length=1)

class ShoppingListRename(BaseModel):
    nombre: str = Field(min_length=1)

class ShoppingListOut(BaseModel):
    id: int
    nombre: str
    created_at: str

class ShoppingItemBase(BaseModel):
    nombre: str = Field(min_length=1)
    cantidad: str = Field(min_length=1)

class ShoppingItemCreate(ShoppingItemBase):
    pass

class ShoppingItemUpdate(BaseModel):
    nombre: str | None = None
    cantidad: str | None = None

class ShoppingItemOut(ShoppingItemBase):
    id: int
    list_id: int
    created_at: datetime

class ShoppingListDetail(ShoppingListOut):
    items: list[ShoppingItemOut]
