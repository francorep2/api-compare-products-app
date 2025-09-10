# app/models.py
from pydantic import BaseModel, HttpUrl
from typing import Optional, List

class SearchItem(BaseModel):
    title: Optional[str] = None
    price: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    barcode: Optional[str] = None

class SearchResponse(BaseModel):
    source: str
    items: List[SearchItem]

class SearchHit(BaseModel):
    title: Optional[str]
    price: Optional[str]
    url: Optional[HttpUrl] | str
    image_url: Optional[HttpUrl] | str = None
    barcode: Optional[str] = None
    store: str