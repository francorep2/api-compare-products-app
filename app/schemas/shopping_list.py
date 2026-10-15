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

# --- SCHEMAS PARA LA PETICIÓN DE COMPARACIÓN ---

class ComparisonItemIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=200)
    cantidad: int = Field(default=1, ge=1, le=99)
    barcode: Optional[str] = Field(default=None, max_length=32)  # EAN: compara el MISMO producto en todas las tiendas

class ComparisonRequestIn(BaseModel):
    items: List[ComparisonItemIn] = Field(min_length=1, max_length=60)
    enabled: Optional[List[str]] = None
    list_id: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=120)


# --- SCHEMAS PARA LA RESPUESTA DE COMPARACIÓN ---

class StoreSummary(BaseModel):
    total: float
    missing_count: int
    missing_items: List[str]

class ComparisonSummaryOut(BaseModel):
    totals_by_store: Dict[str, StoreSummary]
    winner: Optional[str] = None
    updated_at: str
