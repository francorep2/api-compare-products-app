# app/services/all.py
from typing import Dict, List, Optional, Callable
import asyncio
import logging
import inspect

from app.services.coto import search_coto
from app.services.carrefour import search_carrefour
from app.services.dia import search_dia
from app.services.jumbo import search_jumbo

log = logging.getLogger(__name__)

HEAVY_PROVIDERS = {"carrefour", "dia"}

# nombre -> función (puede ser sync o async)
PROVIDERS: Dict[str, Callable[..., List[Dict]]] = {
    "carrefour": search_carrefour,
    "coto": search_coto,
    "jumbo": search_jumbo,
    "dia": search_dia,
}

async def _invoke_provider(fn: Callable[..., List[Dict]],
                           q: Optional[str],
                           barcode: Optional[str],
                           limit: int):
    """
    Llama al provider sea sync o async.
    - Si es coroutinefunction -> await directo
    - Si es sync -> ejecuta en thread (to_thread)
    """
    if inspect.iscoroutinefunction(fn):
        return await fn(q=q, barcode=barcode, limit=limit)
    else:
        # Nota: to_thread pasa args posicionales
        return await asyncio.to_thread(fn, q, barcode, limit)

async def _run_provider(name: str, fn: Callable[..., List[Dict]],
                        q: Optional[str], barcode: Optional[str], limit: int,
                        sem: asyncio.Semaphore) -> List[Dict]:
    """
    Envuelve la ejecución con semáforo para pesados, manejo de errores
    y normaliza el resultado a list.
    """
    try:
        if name in HEAVY_PROVIDERS:
            async with sem:
                res = await _invoke_provider(fn, q, barcode, limit)
        else:
            res = await _invoke_provider(fn, q, barcode, limit)

        # Normalización defensiva
        if res is None:
            return []
        if inspect.isawaitable(res):
            # Por si el provider sync devolvió un awaitable (caso raro)
            res = await res
        if not isinstance(res, list):
            log.warning(f"[all] Provider {name} devolvió {type(res)}; se esperaba list. Lo fuerzo a [].")
            return []
        return res
    except Exception as e:
        log.exception(f"[all] Provider {name} falló: {e}")
        return []

async def search_all_grouped(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3,
    enabled: Optional[List[str]] = None,
) -> Dict[str, object]:
    """
    Agrega resultados por tienda. Acepta scrapers sync y async.
    """
    if not q and not barcode:
        empty = {k: [] for k in PROVIDERS.keys()}
        return {"results": empty, "count": 0, "enabled": list(PROVIDERS.keys())}

    enabled_set = set(enabled or PROVIDERS.keys())
    active = {k: v for k, v in PROVIDERS.items() if k in enabled_set}

    # Un pesado a la vez
    browser_semaphore = asyncio.Semaphore(1)

    tasks = {
        name: asyncio.create_task(
            _run_provider(name, fn, q=q, barcode=barcode, limit=limit, sem=browser_semaphore)
        )
        for name, fn in active.items()
    }

    results_per_store: Dict[str, List[Dict]] = {}
    for name, task in tasks.items():
        items = await task  # <— AQUI nos aseguramos de obtener la lista, no una coroutine
        results_per_store[name] = (items or [])[:limit]

    # Asegura claves también para deshabilitadas
    for name in PROVIDERS.keys():
        results_per_store.setdefault(name, [])

    total_count = sum(len(v) for v in results_per_store.values())

    return {
        "results": results_per_store,
        "count": total_count,
        "enabled": list(active.keys()),
    }
