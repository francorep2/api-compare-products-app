import asyncio
from fastapi import APIRouter
from scrapers.coto_scraper_async import search_coto
from scrapers.carrefour_scraper_async import search_carrefour
from scrapers.dia_scraper_async import search as search_dia
from scrapers.jumbo_scraper_async import search as search_jumbo

router = APIRouter()

async def safe_call(coro, timeout=12.0):
    """Llama un scraper pero lo corta si tarda más de X segundos."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except Exception:
        return []  # si falla o se pasa de tiempo, devolvemos lista vacía

@router.get("/search/all")
async def search_all(q: str, limit: int = 3):
    # Lanzamos los 4 scrapers en paralelo con timeout individual
    coto_task = safe_call(search_coto(q, limit))
    carrefour_task = safe_call(search_carrefour(q, limit))
    dia_task = safe_call(search_dia(q, limit))
    jumbo_task = safe_call(search_jumbo(q, limit))

    coto, carrefour, dia, jumbo = await asyncio.gather(
        coto_task, carrefour_task, dia_task, jumbo_task
    )

    # Mismo formato de respuesta que ya usás
    return {
        "results": {
            "coto": coto,
            "carrefour": carrefour,
            "dia": dia,
            "jumbo": jumbo,
        }
    }
