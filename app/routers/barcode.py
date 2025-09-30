import asyncio
import logging
import re
import unicodedata
import inspect  # <-- ya lo importaste
from app.core import browser
from fastapi import APIRouter, Query, HTTPException, Depends
from typing import Dict, List, Optional

from app.models.search_item import SearchItem
from app.services.coto import search_coto
from app.services.peer_name_resolver import resolve_product_name_from_peers
from app.services.jumbo import search as search_jumbo
from app.services.carrefour import search as search_carrefour
from app.services.dia import search as search_dia
from scrapers.barcode_resolver import (
    resolve_name_carrefour,
    resolve_name_dia,
    resolve_name_jumbo,
)
from app.dependencies.auth import get_current_user, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/search/barcode", tags=["barcode"], dependencies=[Depends(get_current_user)])

ALLOWED = {"carrefour", "coto", "jumbo", "dia"}

# ---- NUEVO: wrapper para tener awaitables siempre ----
def _task(fn, *args, **kwargs):
    # Si es una función async
    if inspect.iscoroutinefunction(fn):
        return fn(*args, **kwargs)
    # Si devuelve una corutina aunque la función no esté marcada como async
    try:
        res = fn(*args, **kwargs)
        if inspect.isawaitable(res):
            return res
        # Es síncrona → correr en thread
        return asyncio.to_thread(lambda: res)
    except TypeError:
        # Algunas funciones síncronas usan sólo posicionales: reintentar directo en thread
        return asyncio.to_thread(fn, *args, **kwargs)

def _split_csv(values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    out: List[str] = []
    for v in values:
        if not v:
            continue
        v = re.sub(r"\[\]$", "", v)
        parts = [p.strip().lower() for p in v.split(",") if p.strip()]
        out.extend(parts)
    seen = set()
    norm: List[str] = []
    for x in out:
        if x in ALLOWED and x not in seen:
            seen.add(x); norm.append(x)
    return norm

# ... (resto de helpers de normalización que ya tenés) ...

# Si usás Playwright síncrono, esto está bien; si fuera async, deberías crear el context async.
ctx = browser.new_context(
    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
)

@router.get("/all")
async def search_barcode_all(
    barcode: str = Query(..., min_length=5, max_length=32),
    limit: int = Query(3, ge=1, le=10),
    enabled: Optional[List[str]] = Query(None, description="repetir clave o CSV"),
):
    enabled_set = set(_split_csv(enabled) or ALLOWED)

    # --- FASE 1: DIRECTO + RESOLVER NOMBRES EN PARALELO ---
    initial_tasks = {
        # Coto necesita ctx
        "coto_direct": _task(search_coto, ctx, barcode=barcode, limit=limit) if "coto" in enabled_set else asyncio.sleep(0, result=[]),
        "name_carrefour": _task(resolve_name_carrefour, barcode) if "carrefour" in enabled_set else asyncio.sleep(0, result=None),
        "name_dia": _task(resolve_name_dia, barcode) if "dia" in enabled_set else asyncio.sleep(0, result=None),
        "name_jumbo": _task(resolve_name_jumbo, barcode) if "jumbo" in enabled_set else asyncio.sleep(0, result=None),
    }

    initial_results = await asyncio.gather(*initial_tasks.values(), return_exceptions=True)
    initial_results_map = dict(zip(initial_tasks.keys(), initial_results))

    def _safe(r):
        return [] if isinstance(r, Exception) else (r or [])

    coto_items = _safe(initial_results_map.get("coto_direct"))
    name_carrefour = initial_results_map.get("name_carrefour") if not isinstance(initial_results_map.get("name_carrefour"), Exception) else None
    name_dia = initial_results_map.get("name_dia") if not isinstance(initial_results_map.get("name_dia"), Exception) else None
    name_jumbo = initial_results_map.get("name_jumbo") if not isinstance(initial_results_map.get("name_jumbo"), Exception) else None

    # --- Fallback opcional para Coto si no hubo hits directos ---
    if not coto_items and "coto" in enabled_set:
        best_name = name_carrefour or name_dia or name_jumbo
        if best_name:
            # Por ejemplo, podrías intentar una búsqueda de texto en Coto si tenés esa función
            pass

    # --- FASE 2: BÚSQUEDA POR NOMBRE EN PARALELO ---
    text_search_tasks = {
        "carrefour": _task(search_carrefour, q=name_carrefour, limit=limit) if name_carrefour and "carrefour" in enabled_set else asyncio.sleep(0, result=[]),
        "dia": _task(search_dia, q=name_dia, limit=limit) if name_dia and "dia" in enabled_set else asyncio.sleep(0, result=[]),
        "jumbo": _task(search_jumbo, q=name_jumbo, limit=limit) if name_jumbo and "jumbo" in enabled_set else asyncio.sleep(0, result=[]),
    }

    text_search_results = await asyncio.gather(*text_search_tasks.values(), return_exceptions=True)
    text_search_results_map = dict(zip(text_search_tasks.keys(), text_search_results))

    # --- FASE 3: UNIFICAR RESULTADOS ---
    buckets: Dict[str, List[SearchItem]] = {k: [] for k in ALLOWED}
    if "coto" in enabled_set: buckets["coto"] = coto_items
    if "carrefour" in enabled_set: buckets["carrefour"] = _safe(text_search_results_map.get("carrefour"))
    if "dia" in enabled_set: buckets["dia"] = _safe(text_search_results_map.get("dia"))
    if "jumbo" in enabled_set: buckets["jumbo"] = _safe(text_search_results_map.get("jumbo"))

    results_enabled = {k: buckets.get(k, []) for k in enabled_set}
    total = sum(len(v or []) for v in results_enabled.values())

    return {
        "results": results_enabled,
        "count": total,
        "enabled": list(enabled_set),
        "barcode": barcode,
        "limit": limit,
    }
