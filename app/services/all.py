# app/services/all.py
from typing import Dict, List, Optional, Callable, Any
import asyncio
import logging
import inspect

# ===== Importar cada provider con alias (evita sobrescribir 'search') =====
from app.services.coto import coto_search as coto_svc
from app.services import carrefour as carrefour_svc
from app.services import dia as dia_svc
from app.services import jumbo as jumbo_svc

log = logging.getLogger(__name__)

HEAVY_PROVIDERS = {"carrefour", "dia", "jumbo", "coto"} 

# Mapa de providers -> función
PROVIDERS: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "carrefour": carrefour_svc.search,
    "coto": coto_svc.search_coto,        #
    "jumbo": jumbo_svc.search,
    "dia": dia_svc.search,
}

PER_PROVIDER_TIMEOUT = 25   
OVERALL_TIMEOUT = 40        


async def _invoke_provider(
    fn: Callable[..., List[Dict]],
    q: Optional[str],
    barcode: Optional[str],
    limit: int,
    ctx: Any = None,
):
    """Llama al provider, pasando ctx solo si lo acepta."""
    kwargs = {"q": q, "barcode": barcode, "limit": limit}
    try:
        params = inspect.signature(fn).parameters
        if "ctx" in params:
            kwargs["ctx"] = ctx
    except Exception:
        # si falla la introspección, no pasamos ctx
        pass

    if inspect.iscoroutinefunction(fn):
        return await fn(**kwargs)
    else:
        # Ejecutar sync sin bloquear el loop
        return await asyncio.to_thread(fn, **kwargs)


async def _run_provider(
    name: str,
    fn: Callable[..., List[Dict]],
    q: Optional[str],
    barcode: Optional[str],
    limit: int,
    sem: asyncio.Semaphore,
    ctx: Any = None,
) -> List[Dict]:
    """Ejecuta un provider con semáforo, timeout y manejo de errores."""
    try:
        async def _job():
            if name in HEAVY_PROVIDERS:
                async with sem:
                    return await _invoke_provider(fn, q, barcode, limit, ctx)
            return await _invoke_provider(fn, q, barcode, limit, ctx)

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
    ctx: Any = None,
) -> Dict[str, object]:
    """
    Agrega resultados por tienda. Corre todo en paralelo con control de tiempo.
    Si algunos providers necesitan 'ctx', pasalo en este parámetro.
    """
    if not q and not barcode:
        empty = {k: [] for k in PROVIDERS.keys()}
        return {"results": empty, "count": 0, "enabled": list(PROVIDERS.keys())}

    enabled_set = set(enabled or PROVIDERS.keys())
    active = {k: v for k, v in PROVIDERS.items() if k in enabled_set}

    # Limitar paralelismo de scrapers pesados (navegador)
    browser_semaphore = asyncio.Semaphore(1)

    tasks = [
        asyncio.create_task(
            _run_provider(name, fn, q, barcode, limit, browser_semaphore, ctx)
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
