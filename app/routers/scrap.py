from fastapi import Query
from app.models.search_item import SearchItem, SearchResponse
from app.services.coto import search_coto
from app.services.dia import search_dia  
from app.services.carrefour import search_carrefour
from app.services.jumbo import search_jumbo  
from app.routers.auth import router as auth_router
from app.routers.shopping_list import router as shopping_list
from app.db import Base, engine
from fastapi import APIRouter, Depends, HTTPException, status, Header, BackgroundTasks

router = APIRouter(prefix="/search", tags=["search"])

@router.get("/search/coto", response_model=SearchResponse)
def search_coto_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    items = [SearchItem(**it) for it in search_coto(q, limit=limit)]
    return SearchResponse(source="coto", items=items)

@router.get("/search/carrefour", response_model=SearchResponse)
def search_carrefour_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    items = [SearchItem(**it) for it in search_carrefour(q, limit=limit)]
    return SearchResponse(source="carrefour", items=items)

@router.get("/search/dia", response_model=SearchResponse)
def search_dia_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    items = [SearchItem(**it) for it in search_dia(q, limit=limit)]
    return SearchResponse(source="dia", items=items)

@router.get("/search/jumbo", response_model=SearchResponse)
def search_jumbo_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    items = [SearchItem(**it) for it in search_jumbo(q, limit=limit)]
    return SearchResponse(source="jumbo", items=items)

@router.get("/search/all")
def search_all_endpoint(q: str = Query(..., min_length=1), limit: int = Query(3, ge=1, le=10)):
    coto_items = search_coto(q, limit=limit)
    carrefour_items = search_carrefour(q, limit=limit)
    dia_items = search_dia(q, limit=limit)
    jumbo_items = search_jumbo(q, limit=limit)   
    return {
        "coto": coto_items,
        "carrefour": carrefour_items,
        "dia": dia_items,
        "jumbo": jumbo_items
    }
