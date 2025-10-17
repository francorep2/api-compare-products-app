from typing import List, Optional
from fastapi import Query
from app.models.search_item import SearchItem, SearchResponse
from app.services.coto import search_coto
from app.services.dia import search  
from app.services.carrefour import search
from app.services.jumbo import search  
from fastapi import APIRouter, Query
from app.services.all import search_all_grouped
from app.dependencies.auth import get_current_user, User
from fastapi import APIRouter, Depends, Query
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_db
from typing import Optional, Dict, List, Any
from app.dependencies.auth import get_current_user, User
from app.auth.models import ScrapePrefs
from app.services.all import search_all_grouped
from playwright.async_api import BrowserContext
from app.core.browser_async import browser_manager_async


router = APIRouter(prefix="/search", tags=["search"],dependencies=[Depends(get_current_user)],)

VALID_STORES = {"coto","carrefour","dia","jumbo"}


def as_list(v: Any) -> List[Dict]:
    if v is None: return []
    if isinstance(v, list): return v
    if isinstance(v, tuple): return list(v)
    if isinstance(v, dict): return [v]
    return []

def normalize_results(raw: Dict[str, Any], enabled: list[str]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    raw = raw or {}
    for s in enabled:
        out[s] = as_list(raw.get(s))
    return out

def _enabled_stores_from_prefs(prefs) -> List[str]:
    cand = []
    if getattr(prefs, "include_carrefour", True): cand.append("carrefour")
    if getattr(prefs, "include_coto", True):       cand.append("coto")
    if getattr(prefs, "include_jumbo", True):      cand.append("jumbo")
    if getattr(prefs, "include_dia", True):        cand.append("dia")
    return cand

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
    items = await search(q=q, barcode=barcode, limit=limit)
    return {"results": items}

@router.get("/dia")
async def search_dia_endpoint(
    q: str | None = Query(None),
    barcode: str | None = Query(None),
    limit: int = Query(3, ge=1, le=10),
):
    items = await search(q=q, barcode=barcode, limit=limit)
    return {"results": items}


@router.get("/jumbo")
async def search_jumbo_endpoint(
    q: str | None = Query(None),
    barcode: str | None = Query(None),
    limit: int = Query(3, ge=1, le=10),
):
    items = await search(q=q, barcode=barcode, limit=limit)
    return {"results": items}


@router.get("/all")
async def search_all_endpoint(
    q: Optional[str] = Query(None),
    barcode: Optional[str] = Query(None),
    limit: int = Query(3, ge=1, le=10),
    enabled: Optional[List[str]] = Query(None, description="Filtrar tiendas: carrefour,coto,jumbo,dia"),
):

    return await search_all_grouped(q=q, barcode=barcode, limit=limit, enabled=enabled)