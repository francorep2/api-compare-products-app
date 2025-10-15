from typing import Dict, List, Optional, Callable, Any
import asyncio
import logging

from app.core.browser_async import browser_manager_async
from app.services.coto import search_coto        # <-- ojo: usar el nombre real exportado
from app.services.carrefour import search as search_carrefour
from app.services.dia import search as search_dia
from app.services.jumbo import search as search_jumbo

log = logging.getLogger(__name__)

# Scrapers que requieren Playwright (ctx)
BROWSER_PROVIDERS = {"coto"}

# Orden de proveedores (mantener orden determinístico)
PROVIDERS: Dict[str, Callable] = {
    "carrefour": search_carrefour,
    "coto": search_coto,
    "jumbo": search_jumbo,
    "dia": search_dia,
}

PER_PROVIDER_TIMEOUT = 25   # segundos
OVERALL_TIMEOUT = 45        # segundos (timeout global de la búsqueda)

async def _run_provider(
    name: str,
    fn: Callable,
    q: Optional[str],
    barcode: Optional[str],
    limit: int,
    ctx: Optional[Any] = None,
) -> List[Dict]:
    """
    Ejecuta un scraper individual con timeout por proveedor.
    Pasa ctx solo si el proveedor lo necesita.
    """
    try:
        kwargs = {"q": q, "barcode": barcode, "limit": limit}
        if name in BROWSER_PROVIDERS:
            if ctx is None:
                log.error(f"[{name}] requiere navegador pero no recibió contexto.")
                return []
            kwargs["ctx"] = ctx

        # TODOS los scrapers deben ser async. Si alguno es sync, envolver con to_thread.
        res = await asyncio.wait_for(fn(**kwargs), timeout=PER_PROVIDER_TIMEOUT)

        if not isinstance(res, list):
            log.warning(f"[{name}] devolvió tipo inesperado: {type(res)}")
            return []
        return res
    except asyncio.TimeoutError:
        log.warning(f"[{name}] Timeout: excedió {PER_PROVIDER_TIMEOUT}s")
        return []
    except Exception as e:
        log.exception(f"[{name}] Excepción: {e}")
        return []

async def search_all_grouped(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3,
    enabled: Optional[List[str]] = None,
) -> Dict[str, object]:
    """
    Ejecuta scrapers en paralelo y agrupa respuestas por tienda.
    No mezcla resultados: usa un mapa nombre->task y luego nombre->resultado.
    """
    if not q and not barcode:
        return {
            "results": {k: [] for k in PROVIDERS.keys()},
            "count": 0,
            "enabled": list(PROVIDERS.keys()),
        }

    # Filtrar proveedores habilitados respetando el orden de PROVIDERS
    enabled_set = set(enabled or PROVIDERS.keys())
    active_providers: Dict[str, Callable] = {
        k: v for k, v in PROVIDERS.items() if k in enabled_set
    }

    # Separar API vs Browser
    api_providers = {name: fn for name, fn in active_providers.items() if name not in BROWSER_PROVIDERS}
    browser_providers = {name: fn for name, fn in active_providers.items() if name in BROWSER_PROVIDERS}

    # Mapa nombre -> task
    task_map: Dict[str, asyncio.Task] = {}

    async def _run_all_with_ctx(ctx=None):
        # Crear tasks de API
        for name, fn in api_providers.items():
            task_map[name] = asyncio.create_task(_run_provider(name, fn, q, barcode, limit, ctx=None))
        # Crear tasks de Browser (si hay ctx)
        if browser_providers:
            if ctx is None:
                # No hay contexto; devolveremos [] a los de browser luego
                return
            for name, fn in browser_providers.items():
                task_map[name] = asyncio.create_task(_run_provider(name, fn, q, barcode, limit, ctx=ctx))

    try:
        if browser_providers:
            # Abrir un contexto SOLO si hay scrapers que lo necesitan
            async with browser_manager_async.new_context() as ctx:
                await _run_all_with_ctx(ctx)
                # Timeout global para la suma de todas las tareas
                await asyncio.wait_for(asyncio.gather(*task_map.values()), timeout=OVERALL_TIMEOUT)
        else:
            await _run_all_with_ctx(ctx=None)
            await asyncio.wait_for(asyncio.gather(*task_map.values()), timeout=OVERALL_TIMEOUT)

    except asyncio.TimeoutError:
        log.warning(f"[ALL] Timeout global: excedió {OVERALL_TIMEOUT}s")
        # dejá que se recolecten lo que haya terminado
    except Exception as e:
        log.error(f"[ALL] Error al ejecutar scrapers: {e}")

        # Si el error fue al crear el contexto del navegador,
        # ejecutamos solo API y marcamos browser como vacío.
        if not task_map and api_providers:
            # reintento: solo API
            for name, fn in api_providers.items():
                task_map[name] = asyncio.create_task(_run_provider(name, fn, q, barcode, limit, ctx=None))
            try:
                await asyncio.gather(*task_map.values())
            except Exception:
                pass

    # Construir resultados por tienda sin perder correspondencia
    results_per_store: Dict[str, List[Dict]] = {}
    for name in active_providers.keys():
        task = task_map.get(name)
        if task and task.done() and not task.cancelled():
            try:
                results_per_store[name] = task.result() or []
            except Exception:
                results_per_store[name] = []
        else:
            # No hubo task (p.ej. falló el ctx de browser) → vacío
            results_per_store[name] = []

    total_count = sum(len(v) for v in results_per_store.values())

    return {
        "results": results_per_store,            # {carrefour: [...], coto: [...], ...}
        "count": total_count,
        "enabled": list(active_providers.keys()),# preserva orden de PROVIDERS filtrado
    }
