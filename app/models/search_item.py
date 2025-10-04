# app/models.py
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator

class SearchItem(BaseModel):
    title: Optional[str] = None
    price: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    barcode: Optional[str] = None
    promotion: Optional[str] = None

class SearchResponse(BaseModel):
    source: str
    items: List[SearchItem]

class ScrapePrefsOut(BaseModel):
    include_coto: bool
    include_carrefour: bool
    include_dia: bool
    include_jumbo: bool

class ScrapePrefsIn(BaseModel):
    include_coto: bool = True
    include_carrefour: bool = True
    include_dia: bool = True
    include_jumbo: bool = True

    @model_validator(mode="after")
    def at_least_one_store(self):
        if not any([self.include_coto, self.include_carrefour, self.include_dia, self.include_jumbo]):
            raise ValueError("Debés seleccionar al menos una tienda")
        return self
