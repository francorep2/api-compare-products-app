# app/models.py
from pydantic import BaseModel, HttpUrl
from typing import Optional, List

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
