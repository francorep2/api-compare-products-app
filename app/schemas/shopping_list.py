# app/schemas/shopping_list.py
from typing import Optional, List, Dict
from pydantic import BaseModel, Field, field_serializer, ConfigDict
from datetime import date, datetime

# ----------------------
# Input schemas (listas & items)
# ----------------------
class ShoppingListCreate(BaseModel):
    nombre: str = Field(min_length=1)

class ShoppingListRename(BaseModel):
    nombre: str = Field(min_length=1)

class ShoppingItemBase(BaseModel):
    nombre: str = Field(min_length=1)
    cantidad: str = Field(min_length=1)
    done: Optional[bool] = False

class ShoppingItemCreate(ShoppingItemBase):
    pass

class ShoppingItemUpdate(BaseModel):
    nombre: Optional[str] = None
    cantidad: Optional[str] = None
    done: Optional[bool] = None

# ----------------------
# Output schemas (listas & items)
# ----------------------
class ShoppingListOut(BaseModel):
    id: int
    nombre: str
    created_at: date  # viene como DATE desde la DB

    # Pydantic v2: permite instanciar desde objetos ORM (SQLAlchemy)
    model_config = ConfigDict(from_attributes=True)

    # Formatear la fecha como dd-mm-YYYY
    @field_serializer("created_at")
    def _serialize_created_at(self, value: date, _info):
        return value.strftime("%d-%m-%Y")

class ShoppingItemOut(ShoppingItemBase):
    id: int
    list_id: int
    created_at: datetime
    done: bool

    model_config = ConfigDict(from_attributes=True)

class ShoppingListDetail(ShoppingListOut):
    items: List[ShoppingItemOut]

# ----------------------
# Compare schemas (NO heredan de ShoppingItemBase)
# ----------------------
class CompareItemIn(BaseModel):
    name: str
    quantity: float = 1.0
    barcode: Optional[str] = None

class ComparePayloadIn(BaseModel):
    items: List[CompareItemIn]

class StoreItemOut(BaseModel):
    name: str
    barcode: Optional[str] = None
    qty: float
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    title: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    missing: Optional[bool] = None

class StoreQuoteOut(BaseModel):
    total: float
    save: float
    items: List[StoreItemOut]

class CompareResponse(BaseModel):
    currency: str = "ARS"
    best_store: Optional[str]
    best_total: float
    stores: Dict[str, StoreQuoteOut]  # { "carrefour": StoreQuoteOut, ... }
