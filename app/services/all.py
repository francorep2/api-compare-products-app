from typing import Dict, List, Optional, Callable
import asyncio
import logging
import inspect

# Importa el manager del navegador
from app.core.browser_async import browser_manager_async

# Importa las funciones de búsqueda específicas
from app.services.coto import search_coto
from app.services.carrefour import search as search_carrefour
from app.services.dia import search as search_dia
from app.services.jumbo import search as search_jumbo

log = logging.getLogger(__name__)

# Define qué scrapers necesitan un navegador
BROWSER_PROVIDERS = {"coto"}

# Mapeo de nombres a funciones
PROVIDERS: Dict[str, Callable] = {
    "carrefour": search_carrefour,
    "coto": search_coto,
    "jumbo": search_jumbo,
    "dia": search_dia,
}

PER_PROVIDER_TIMEOUT = 25  # Aumentado para dar margen a Playwright
OVERALL_TIMEOUT = 40

async def _invoke_provider(
    name: str,
    fn: Callable,
    q: Optional[str],
    barcode: Optional[str],
    limit: int
) -> List[Dict]:
    """Llama al scraper, pasándole el contexto del navegador si lo necesita."""
    
    # Preparamos los argumentos base
    kwargs = {"q": q, "barcode": barcode, "limit": limit}

    if name in BROWSER_PROVIDERS:
        # Si es un scraper de navegador, creamos un contexto y lo pasamos
        async with browser_manager_async.new_context() as ctx:
            return await fn(ctx=ctx, **kwargs)
    else:
        # Si es un scraper de API, lo llamamos directamente
        if inspect.iscoroutinefunction(fn):
            return await fn(**kwargs)
        else:
            return await asyncio.to_thread(fn, **kwargs)

async def _run_provider(
    name: str,
    fn: Callable,
    q: Optional[str],
    barcode: Optional[str],
    limit: int
) -> List[Dict]:
    """Ejecuta un scraper con timeout y manejo de errores."""
    try:
        # El timeout ahora envuelve directamente la invocación
        res = await asyncio.wait_for(
            _invoke_provider(name, fn, q, barcode, limit),
            timeout=PER_PROVIDER_TIMEOUT
        )
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
    """Agrega resultados por tienda, corriendo todo en paralelo."""
    if not q and not barcode:
        return {"results": {k: [] for k in PROVIDERS}, "count": 0, "enabled": list(PROVIDERS.keys())}

    enabled_set = set(enabled or PROVIDERS.keys())
    active_providers = {k: v for k, v in PROVIDERS.items() if k in enabled_set}

    tasks = [
        asyncio.create_task(_run_provider(name, fn, q, barcode, limit))
        for name, fn in active_providers.items()
    ]

    try:
        results_list = await asyncio.wait_for(
            asyncio.gather(*tasks),
            timeout=OVERALL_TIMEOUT
        )
    except asyncio.TimeoutError:
        log.warning(f"[all] Timeout general de {OVERALL_TIMEOUT}s")
        results_list = [[] for _ in active_providers]

    # Mapea los resultados de vuelta a cada tienda
    results_per_store = dict(zip(active_providers.keys(), results_list))

    total_count = sum(len(v) for v in results_per_store.values())

    return {
        "results": results_per_store,
        "count": total_count,
        "enabled": list(active_providers.keys()),
    }