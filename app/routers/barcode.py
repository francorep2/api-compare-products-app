# app/routers/barcode.py
from fastapi import APIRouter, Query, Depends
from typing import Dict, List, Optional
from playwright.async_api import BrowserContext

from app.core.browser_async import browser_manager_async
from app.models.search_item import SearchItem
from app.services.coto import search_coto
from app.services.jumbo import search as search_jumbo
from app.services.carrefour import search as search_carrefour
from app.services.dia import search as search_dia
from scrapers.barcode_resolver import resolve_name_carrefour, resolve_name_dia, resolve_name_jumbo
from app.dependencies.auth import get_current_user, User

import asyncio, inspect, logging, re, unicodedata

router = APIRouter(prefix="/search/barcode", tags=["barcode"], dependencies=[Depends(get_current_user)])
log = logging.getLogger(__name__)

ALLOWED = {"carrefour", "coto", "jumbo", "dia"}

async def get_ctx() -> BrowserContext:
    # crea y cierra un contexto por request
    async with browser_manager_async.new_context() as ctx:
        yield ctx

def _task(fn, *args, **kwargs):
    # Wrapper para que gather siempre reciba awaitables (soporta sync/async)
    if inspect.iscoroutinefunction(fn):
        return fn(*args, **kwargs)
    try:
        res = fn(*args, **kwargs)
        if inspect.isawaitable(res):
            return res
        return asyncio.to_thread(lambda: res)
    except TypeError:
        return asyncio.to_thread(fn, *args, **kwargs)

def _split_csv(values: Optional[List[str]]) -> List[str]:
    if not values: return []
    out: List[str] = []
    for v in values:
        if not v: continue
        v = re.sub(r"\[\]$", "", v)
        out.extend([p.strip().lower() for p in v.split(",") if p.strip()])
    seen=set(); norm=[]
    for x in out:
        if x in ALLOWED and x not in seen:
            seen.add(x); norm.append(x)
    return norm

@router.get("/all")
async def search_barcode_all(
    barcode: str = Query(..., min_length=5, max_length=32),
    limit: int = Query(3, ge=1, le=10),
    enabled: Optional[List[str]] = Query(None, description="repetir clave o CSV"),
    ctx: BrowserContext = Depends(get_ctx),   # 👈 te inyecta el contexto async
):
    enabled_set = set(_split_csv(enabled) or ALLOWED)

    initial_tasks = {
        "coto_direct": _task(search_coto, ctx, barcode=barcode, limit=limit) if "coto" in enabled_set else asyncio.sleep(0, result=[]),
        "name_carrefour": _task(resolve_name_carrefour, barcode) if "carrefour" in enabled_set else asyncio.sleep(0, result=None),
        "name_dia": _task(resolve_name_dia, barcode) if "dia" in enabled_set else asyncio.sleep(0, result=None),
        "name_jumbo": _task(resolve_name_jumbo, barcode) if "jumbo" in enabled_set else asyncio.sleep(0, result=None),
    }
    initial_results = await asyncio.gather(*initial_tasks.values(), return_exceptions=True)
    initial_map = dict(zip(initial_tasks.keys(), initial_results))

    def _safe(r): return [] if isinstance(r, Exception) else (r or [])

    coto_items = _safe(initial_map.get("coto_direct"))
    name_carrefour = initial_map.get("name_carrefour") if not isinstance(initial_map.get("name_carrefour"), Exception) else None
    name_dia = initial_map.get("name_dia") if not isinstance(initial_map.get("name_dia"), Exception) else None
    name_jumbo = initial_map.get("name_jumbo") if not isinstance(initial_map.get("name_jumbo"), Exception) else None

    text_tasks = {
        "carrefour": _task(search_carrefour, q=name_carrefour, limit=limit) if name_carrefour and "carrefour" in enabled_set else asyncio.sleep(0, result=[]),
        "dia": _task(search_dia, q=name_dia, limit=limit) if name_dia and "dia" in enabled_set else asyncio.sleep(0, result=[]),
        "jumbo": _task(search_jumbo, q=name_jumbo, limit=limit) if name_jumbo and "jumbo" in enabled_set else asyncio.sleep(0, result=[]),
    }
    text_results = await asyncio.gather(*text_tasks.values(), return_exceptions=True)
    text_map = dict(zip(text_tasks.keys(), text_results))

    buckets: Dict[str, List[SearchItem]] = {k: [] for k in ALLOWED}
    if "coto" in enabled_set: buckets["coto"] = coto_items
    if "carrefour" in enabled_set: buckets["carrefour"] = _safe(text_map.get("carrefour"))
    if "dia" in enabled_set: buckets["dia"] = _safe(text_map.get("dia"))
    if "jumbo" in enabled_set: buckets["jumbo"] = _safe(text_map.get("jumbo"))

    results_enabled = {k: buckets.get(k, []) for k in enabled_set}
    total = sum(len(v or []) for v in results_enabled.values())

    return {"results": results_enabled, "count": total, "enabled": list(enabled_set), "barcode": barcode, "limit": limit}
