# app/routers/scrap.py
from typing import Optional
from fastapi import Query, APIRouter, HTTPException

# Se importan los servicios individuales para las rutas específicas
from app.services.coto import search_coto
from app.services.dia import search_dia
from app.services.carrefour import search_carrefour
from app.services.jumbo import search_jumbo

# IMPORTANTE: Se usan únicamente las funciones del servicio 'search.py' para las búsquedas "all"
from app.services.search import search_all_grouped, search_all_grouped_barcode

router = APIRouter(prefix="/search", tags=["search"])

# --- RUTAS INDIVIDUALES (no necesitan cambios) ---

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


# --- RUTAS "ALL" (CORREGIDAS Y UNIFICADAS) ---

@router.get("/all", summary="Buscar por texto en todas las tiendas")
async def search_all_endpoint(
    q: str = Query(..., description="El texto a buscar"),
    limit: int = Query(3, ge=1, le=10),
    debug: int = Query(0, ge=0, le=2, description="Nivel de debug: 1 para logs, 2 para logs y datos crudos")
):
    """
    Busca un producto por texto en todas las tiendas utilizando el motor de búsqueda unificado.
    """
    # Se asegura de llamar a la función correcta de 'app/services/search.py'
    return await search_all_grouped(q=q, limit=limit, debug=debug)


@router.get("/all/barcode", summary="Buscar por código de barras en todas las tiendas")
async def search_all_barcode_endpoint(
    barcode: str = Query(..., description="El código de barras para buscar"),
    limit: int = Query(3, ge=1, le=10),
    debug: int = Query(0, ge=0, le=2, description="Nivel de debug: 1 para logs, 2 para logs y datos crudos")
):
    """
    Busca un producto por código de barras en todas las tiendas utilizando el motor de búsqueda unificado.
    """
    if not barcode:
        raise HTTPException(status_code=400, detail="El código de barras es requerido")
    # Se asegura de llamar a la función correcta de 'app/services/search.py'
    return await search_all_grouped_barcode(barcode=barcode, limit=limit, debug=debug)