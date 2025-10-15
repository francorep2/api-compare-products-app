from typing import Dict, List, Optional, Callable, Any
import asyncio
import logging
import inspect

# 1. Importa el manager del navegador y las funciones de scraper específicas
from app.core.browser_async import browser_manager_async
from app.services.coto import search as search_coto
from app.services.carrefour import search as search_carrefour
from app.services.dia import search as search_dia
from app.services.jumbo import search as search_jumbo

log = logging.getLogger(__name__)

# 2. Define qué scrapers usan navegador y el mapeo de funciones
BROWSER_PROVIDERS = {"coto"}

PROVIDERS: Dict[str, Callable] = {
    "carrefour": search_carrefour,
    "coto": search_coto,
    "jumbo": search_jumbo,
    "dia": search_dia,
}

PER_PROVIDER_TIMEOUT = 25
OVERALL_TIMEOUT = 45

async def _run_provider(
    name: str,
    fn: Callable,
    q: Optional[str],
    barcode: Optional[str],
    limit: int,
    ctx: Optional[Any] = None
) -> List[Dict]:
    """Ejecuta un scraper individual, pasándole el contexto si lo necesita."""
    try:
        kwargs = {"q": q, "barcode": barcode, "limit": limit}
        # Solo añade 'ctx' si la función lo espera
        if "ctx" in inspect.signature(fn).parameters:
            if ctx is None:
                log.error(f"[{name}] es un scraper de navegador pero no recibió un contexto.")
                return []
            kwargs["ctx"] = ctx

        res = await asyncio.wait_for(fn(**kwargs), timeout=PER_PROVIDER_TIMEOUT)
        
        if not isinstance(res, list):
            log.warning(f"[{name}] El scraper devolvió un tipo inesperado: {type(res)}")
            return []
        return res
    except asyncio.TimeoutError:
        log.warning(f"[{name}] Timeout: excedió los {PER_PROVIDER_TIMEOUT}s")
        return []
    except Exception as e:
        log.exception(f"[{name}] Falló con una excepción: {e}")
        return []

async def search_all_grouped(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3,
    enabled: Optional[List[str]] = None,
) -> Dict[str, object]:
    """
    Agrega resultados por tienda. Corre en paralelo y gestiona el navegador
    solo cuando es necesario.
    """
    if not q and not barcode:
        return {"results": {k: [] for k in PROVIDERS}, "count": 0, "enabled": list(PROVIDERS.keys())}

    enabled_set = set(enabled or PROVIDERS.keys())
    active_providers = {k: v for k, v in PROVIDERS.items() if k in enabled_set}

    tasks = []
    
    # Separa las tareas: las que usan navegador y las que no
    api_providers = {name: fn for name, fn in active_providers.items() if name not in BROWSER_PROVIDERS}
    browser_providers = {name: fn for name, fn in active_providers.items() if name in BROWSER_PROVIDERS}

    # Lanza las tareas de API (rápidas)
    for name, fn in api_providers.items():
        tasks.append(_run_provider(name, fn, q, barcode, limit))

    # Si hay tareas de navegador, abre un contexto y las lanza
    if browser_providers:
        try:
            async with browser_manager_async.new_context() as ctx:
                for name, fn in browser_providers.items():
                    tasks.append(_run_provider(name, fn, q, barcode, limit, ctx=ctx))
                
                # Ejecuta todo junto
                results_list = await asyncio.gather(*tasks)
        except Exception as e:
            log.error(f"No se pudo iniciar el contexto del navegador: {e}")
            # Si el navegador falla, corremos solo las de API
            api_only_tasks = [t for name, t in zip(active_providers.keys(), tasks) if name not in BROWSER_PROVIDERS]
            api_only_results = await asyncio.gather(*api_only_tasks)
            results_list = api_only_results + [[] for _ in browser_providers] # Rellena con vacíos
    else:
        # Si no hay tareas de navegador, solo ejecuta las de API
        results_list = await asyncio.gather(*tasks)

    # Mapea los resultados
    results_per_store = dict(zip(active_providers.keys(), results_list))
    total_count = sum(len(v or []) for v in results_per_store.values())

    return {
        "results": results_per_store,
        "count": total_count,
        "enabled": list(active_providers.keys()),
    }