# app/services/all.py
from typing import Dict, List, Optional, Callable
import asyncio
import logging
import inspect

from app.core.browser_async import browser_manager_async, DEFAULT_CTX

# Usá alias para no pisar nombres:
from app.services.carrefour import search as carrefour_search
from app.services.dia import search as dia_search
from app.services.jumbo import search as jumbo_search

# Preferimos tu wrapper; si no existe, cargamos el search del scraper (que pide ctx)
try:
    from app.services.coto import search_coto as coto_search  # wrapper SIN ctx
except Exception:
    from scrapers.coto_scraper_async import search as coto_search  # core CON ctx

log = logging.getLogger(__name__)

HEAVY_PROVIDERS = {"carrefour", "dia"}

PROVIDERS: Dict[str, Callable[..., List[Dict]]] = {
    "carrefour": carrefour_search,
    "coto": coto_search,      # puede ser wrapper o core; ambos funcionarán
    "jumbo": jumbo_search,
    "dia": dia_search,
}

PER_PROVIDER_TIMEOUT = 15   # segundos máx por tienda
OVERALL_TIMEOUT = 40        # máx para toda la búsqueda


async def _invoke_provider(fn: Callable[..., List[Dict]],
                           q: Optional[str],
                           barcode: Optional[str],
                           limit: int):
    """
    Llama al provider detectando si requiere 'ctx'.
    Si el provider tiene un parámetro 'ctx' en su firma, abrimos un context Playwright acá.
    """
    sig = inspect.signature(fn)
    params = sig.parameters

    kwargs = {}
    if "q" in params: kwargs["q"] = q
    if "barcode" in params: kwargs["barcode"] = barcode
    if "limit" in params: kwargs["limit"] = limit

    # Debug opcional
    try:
        log.debug("[providers] calling %s.%s sig=%s kwargs=%s",
                  getattr(fn, "__module__", "?"),
                  getattr(fn, "__name__", "?"),
                  str(sig), kwargs)
    except Exception:
        pass

    if "ctx" in params:
        async with browser_manager_async.new_context(**DEFAULT_CTX) as ctx:
            kwargs["ctx"] = ctx
            return await fn(**kwargs)

    if inspect.iscoroutinefunction(fn):
        return await fn(**kwargs)
    else:
        return await asyncio.to_thread(lambda: fn(**kwargs))


async def _run_provider(name: str,
                        fn: Callable[..., List[Dict]],
                        q: Optional[str],
                        barcode: Optional[str],
                        limit: int,
                        sem: asyncio.Semaphore) -> List[Dict]:
    """Ejecuta un provider con semáforo, timeout y manejo de errores."""
    try:
        async def _job():
            if name in HEAVY_PROVIDERS:
                async with sem:
                    return await _invoke_provider(fn, q, barcode, limit)
            return await _invoke_provider(fn, q, barcode, limit)

        res = await asyncio.wait_for(_job(), timeout=PER_PROVIDER_TIMEOUT)

        if res is None:
            return []
        if inspect.isawaitable(res):
            res = await res
        if not isinstance(res, list):
            log.warning(f"[all] Provider {name} devolvió {type(res)}; se esperaba list.")
            return []
        return res
    except asyncio.TimeoutError:
        log.warning(f"[all] Provider {name} excedió {PER_PROVIDER_TIMEOUT}s")
        return []
    except Exception as e:
        log.exception(f"[all] Provider {name} falló: {e}")
        return []


async def search_all_grouped(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3,
    enabled: Optional[List[str]] = None,
) -> Dict[str, object]:
    """Agrega resultados por tienda. Corre todo en paralelo con control de tiempo."""
    if not q and not barcode:
        empty = {k: [] for k in PROVIDERS.keys()}
        return {"results": empty, "count": 0, "enabled": list(PROVIDERS.keys())}

    enabled_set = set(enabled or PROVIDERS.keys())
    active = {k: v for k, v in PROVIDERS.items() if k in enabled_set}

    browser_semaphore = asyncio.Semaphore(1)

    tasks = [
        asyncio.create_task(
            _run_provider(name, fn, q, barcode, limit, browser_semaphore)
        )
        for name, fn in active.items()
    ]

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=False),
            timeout=OVERALL_TIMEOUT
        )
    except asyncio.TimeoutError:
        log.warning(f"[all] Timeout general de {OVERALL_TIMEOUT}s")
        results = [[] for _ in active]

    results_per_store: Dict[str, List[Dict]] = {
        name: (res or [])[:limit] for name, res in zip(active.keys(), results)
    }

    # asegurar claves vacías para deshabilitadas
    for name in PROVIDERS.keys():
        results_per_store.setdefault(name, [])

    total_count = sum(len(v) for v in results_per_store.values())

    return {
        "results": results_per_store,
        "count": total_count,
        "enabled": list(active.keys()),
    }
