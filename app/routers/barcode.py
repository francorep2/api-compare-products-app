# app/routes/barcode.py
import asyncio
from fastapi import APIRouter, Query
from typing import Dict, List
from app.models.search_item import SearchItem
from app.services.barcode.jumbo_barcode import search_jumbo_barcode
from app.services.barcode.carrefour_barcode import search_carrefour_barcode
from app.services.barcode.dia_barcode import search_dia_barcode
from app.services.barcode.coto_barcode import search_coto_barcode

router = APIRouter(prefix="/search/barcode", tags=["barcode"])
@router.get("/all", response_model=Dict[str, List[SearchItem]])
async def search_barcode_all(
    barcode: str = Query(..., min_length=5, max_length=32),
    limit: int = Query(3, ge=1, le=10),
):
    tasks = [
        search_jumbo_barcode(barcode, limit),
        search_carrefour_barcode(barcode, limit),
        search_dia_barcode(barcode, limit),
        search_coto_barcode(barcode, limit),
    ]
    # Que ninguna excepción rompa todo el endpoint
    results = await asyncio.gather(*tasks, return_exceptions=True)

    def _safe(i):
        return [] if isinstance(i, Exception) else (i or [])

    jumbo, carrefour, dia, coto = map(_safe, results)
    return {"jumbo": jumbo, "carrefour": carrefour, "dia": dia, "coto": coto}

# Ruta dinámica con regex (evita capturar 'all')
@router.get("/{store:jumbo|carrefour|dia|coto}", response_model=Dict[str, List[SearchItem]])
async def search_barcode_store(
    store: str,
    barcode: str = Query(..., min_length=5, max_length=32),
    limit: int = Query(3, ge=1, le=10),
):
    fn = {
        "jumbo": search_jumbo_by_barcode,
        "carrefour": search_carrefour_by_barcode,
        "dia": search_dia_by_barcode,
        "coto": search_coto_by_barcode,
    }[store]
    items = await fn(barcode, limit)
    return {store: items or []}