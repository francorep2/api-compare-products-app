from typing import Dict, List, Optional, Callable, Any
import asyncio
import logging
import inspect

# 1. Importa el manager del navegador
from app.core.browser_async import browser_manager_async

# 2. Importa cada función de scraper con un alias claro
from app.services.coto import search as search_coto
from app.services.carrefour import search as search_carrefour
from app.services.dia import search as search_dia
from app.services.jumbo import search as search_jumbo

log = logging.getLogger(__name__)

# 3. Define qué scrapers necesitan un navegador
BROWSER_PROVIDERS = {"coto"}

PROVIDERS: Dict[str, Callable] = {
    "carrefour": search_carrefour,
    "coto": search_coto,
    "jumbo": search_jumbo,
    "dia": search_dia,
}

PER_PROVIDER_TIMEOUT = 25  # Aumentado para dar margen a Playwright
OVERALL_TIMEOUT = 40

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
    active_names = []
    browser_providers_to_run = {k: v for k, v in active_providers.items() if k in BROWSER_PROVIDERS}
    
    # 4. Crea tareas para scrapers que NO usan navegador
    for name, fn in active_providers.items():
        if name not in BROWSER_PROVIDERS:
            active_names.append(name)
            tasks.append(_run_provider(name, fn, q, barcode, limit))

    # 5. Si hay scrapers de navegador, crea un contexto y sus tareas
    if browser_providers_to_run:
        try:
            async with browser_manager_async.new_context() as ctx:
                for name, fn in browser_providers_to_run.items():
                    active_names.append(name)
                    tasks.append(_run_provider(name, fn, q, barcode, limit, ctx=ctx))
                
                # Ejecuta todas las tareas juntas
                results_list = await asyncio.gather(*tasks)
        except Exception as e:
            log.error(f"No se pudo iniciar el contexto del navegador: {e}")
            # Si el navegador falla, las tareas de Coto no se ejecutan, pero las otras sí.
            # Necesitamos manejar los resultados parciales.
            api_only_results = await asyncio.gather(*[t for t in tasks if name not in browser_providers_to_run])
            results_list = api_only_results + [[] for _ in browser_providers_to_run] # Rellena con vacíos
    else:
        # Si no hay scrapers de navegador, simplemente ejecuta las tareas de API
        results_list = await asyncio.gather(*tasks)

    # 6. Mapea los resultados de vuelta
    results_per_store = dict(zip(active_names, results_list))
    total_count = sum(len(v) for v in results_per_store.values())

    return {
        "results": results_per_store,
        "count": total_count,
        "enabled": list(active_providers.keys()),
    }