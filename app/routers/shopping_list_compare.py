# app/routers/shopping_lists_compare.py
from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body, Path, Query
from pydantic import BaseModel, Field
from app.models.shopping_list import CompareItemIn, ComparePayloadIn, CompareResponse

from app.services.compare_list import compare_list_prices, ListItem

# Si tenés tu Session/Repo para leer la lista por ID:
try:
    from app.db import get_db  # noqa
    from app.models import ShoppingItem  # noqa
    HAS_DB = True
except Exception:
    HAS_DB = False
    get_db = None
    ShoppingItem = None

router = APIRouter(prefix="/shopping-lists", tags=["shopping-lists"])


# --------- Endpoints ---------

@router.get("/{list_id}/compare", response_model=CompareResponse)
async def compare_by_list_id(
    list_id: int = Path(..., ge=1),
    use_barcode_first: bool = Query(True),
    db=Depends(get_db) if HAS_DB else None,
):
    """
    Lee la lista {list_id} desde DB, busca cada ítem en todos los comercios
    y devuelve el total por tienda + cuánto te ahorrarías eligiendo la más barata.
    """
    if not HAS_DB:
        raise HTTPException(status_code=501, detail="DB no disponible en este entorno. Usá POST /shopping-lists/compare con items.")

    # Adaptá a tu ORM: ejemplo con SQLAlchemy
    rows: List[ShoppingItem] = (
        db.query(ShoppingItem)
        .filter(ShoppingItem.list_id == list_id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Lista vacía o no encontrada.")

    items = []
    for r in rows:
        # asumimos columnas: name, quantity, barcode
        items.append(ListItem(name=r.name, quantity=float(getattr(r, "quantity", 1) or 1), barcode=getattr(r, "barcode", None)))

    data = await compare_list_prices(items)
    return data

@router.post("/compare", response_model=CompareResponse)
async def compare_by_payload(payload: ComparePayloadIn = Body(...)):
    """
    Alternativa sin DB: mandás los items en el body:
    {
      "items": [
        {"name":"Coca Cola 500 ml","quantity":2,"barcode":"7790895000961"},
        {"name":"Papel Higiénico Elite 4u","quantity":1}
      ]
    }
    """
    items = [ListItem(name=i.name, quantity=i.quantity, barcode=i.barcode) for i in payload.items]
    data = await compare_list_prices(items)
    return data
