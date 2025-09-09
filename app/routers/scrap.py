from typing import Optional
from fastapi import Query, APIRouter, HTTPException

from app.models.search_item import SearchItem, SearchResponse
from app.services.coto import search_coto
from app.services.dia import search_dia  
from app.services.carrefour import search_carrefour
from app.services.jumbo import search_jumbo  
from app.services.all import search_all_grouped, search_all_by_barcode
from app.services.search import search_all_grouped_barcode 

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/coto")
async def search_coto_endpoint(
    q: str | None = Query(None),
    barcode: str | None = Query(None),
    limit: int = Query(3, ge=1, le=10),
):
    items = await search_coto(q=q, barcode=barcode, limit=limit)
    return {"results": items}


@router.get("/carrefour")
async def search_carrefour_endpoint(
    q: str | None = Query(None),
    barcode: str | None = Query(None),
    limit: int = Query(3, ge=1, le=10),
):
    items = await search_carrefour(q=q, barcode=barcode, limit=limit)
    return {"results": items}


@router.get("/dia")
async def search_dia_endpoint(
    q: str | None = Query(None),
    barcode: str | None = Query(None),
    limit: int = Query(3, ge=1, le=10),
):
    items = await search_dia(q=q, barcode=barcode, limit=limit)
    return {"results": items}


@router.get("/jumbo")
async def search_jumbo_endpoint(
    q: str | None = Query(None),
    barcode: str | None = Query(None),
    limit: int = Query(3, ge=1, le=10),
):
    items = await search_jumbo(q=q, barcode=barcode, limit=limit)
    return {"results": items}


@router.get("/all")
async def search_all_endpoint(
    q: str | None = Query(None),
    barcode: str | None = Query(None),
    limit: int = Query(3, ge=1, le=10),
):
    return await search_all_grouped(q=q, barcode=barcode, limit=limit)


@router.get("/all/barcode")
async def search_all_barcode_endpoint(
    barcode: str = Query(..., min_length=8, description="Código EAN/UPC"),
    limit: int = Query(3, ge=1, le=10),
    debug: int = Query(0, ge=0, le=2, description="0=off, 1=diag, 2=diag+raw"),
):
    if not barcode.isdigit():
        raise HTTPException(status_code=400, detail="El barcode debe ser numérico")
    return await search_all_grouped_barcode(barcode=barcode, limit=limit, debug=debug)