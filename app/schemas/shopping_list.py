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

# app/schemas/shopping_list.py
from pydantic import BaseModel, Field, field_serializer
from pydantic import ConfigDict
from datetime import date, datetime

# ----------------------
# Input schemas
# ----------------------
class ShoppingListCreate(BaseModel):
    nombre: str = Field(min_length=1)

class ShoppingListRename(BaseModel):
    nombre: str = Field(min_length=1)

class ShoppingItemBase(BaseModel):
    nombre: str = Field(min_length=1)
    cantidad: str = Field(min_length=1)

class ShoppingItemCreate(ShoppingItemBase):
    pass

class ShoppingItemUpdate(BaseModel):
    nombre: str | None = None
    cantidad: str | None = None

# ----------------------
# Output schemas
# ----------------------
class ShoppingListOut(BaseModel):
    id: int
    nombre: str
    created_at: date  # viene como DATE desde la DB

    # Permitir instanciar desde objetos ORM (SQLAlchemy)
    model_config = ConfigDict(from_attributes=True)

    # Formatear la fecha como dd-mm-YYYY
    @field_serializer("created_at")
    def _serialize_created_at(self, value: date, _info):
        return value.strftime("%d-%m-%Y")

class ShoppingItemOut(ShoppingItemBase):
    id: int
    list_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ShoppingListDetail(ShoppingListOut):
    items: list[ShoppingItemOut]