# app/services/all_async.py
import logging
from typing import Dict, List
import asyncio

from app.services.coto import search_coto
from app.services.carrefour import search_carrefour
from app.services.dia import search_dia
from app.services.jumbo import search_jumbo

logger = logging.getLogger(__name__)

# Marca qué proveedores realmente soportan EAN (ajustá según implementes)
SUPPORTS_BARCODE: Dict[str, bool] = {
    "coto": True,
    "carrefour": True,
    "dia": True,
    "jumbo": True,
}

PROVIDERS = {
    "coto": search_coto,
    "carrefour": search_carrefour,
    "dia": search_dia,
    "jumbo": search_jumbo,
}

def _clean_items(items: List[dict]) -> List[dict]:
    return [
        {
            "title": it.get("title"),
            "price": it.get("price"),
            "url": it.get("url"),
            "image_url": it.get("image_url"),
            "barcode": it.get("barcode"),
        }
        for it in (items or [])
    ]

async def search_all_by_barcode(barcode: str, limit: int = 3) -> Dict[str, List[dict]]:
    """
    Si el scraper no soporta barcode, se hace fallback a búsqueda por texto usando el `barcode` como `q`.
    """
    tasks: Dict[str, asyncio.Task] = {}

    for name, fn in PROVIDERS.items():
        if SUPPORTS_BARCODE.get(name, False):
            # Soporta EAN: prioriza parámetro barcode
            tasks[name] = asyncio.create_task(fn(q=None, barcode=barcode, limit=limit))
        else:
            # Fallback: busca por texto usando el código como query
            tasks[name] = asyncio.create_task(fn(q=barcode, barcode=None, limit=limit))

    results: Dict[str, List[dict]] = {}
    for name, task in tasks.items():
        try:
            items = await task
            clean = _clean_items(items)
            results[name] = clean
            logger.info("EAN %s -> %s: %d items", barcode, name, len(clean))
        except Exception as e:
            logger.exception("Error buscando %s con EAN %s: %s", name, barcode, e)
            results[name] = []


async def search_all_grouped(
    q: str | None = None,
    barcode: str | None = None,
    limit: int = 3
) -> Dict[str, List[Dict]]:
    providers = {
        "coto": search_coto,
        "carrefour": search_carrefour,
        "dia": search_dia,
        "jumbo": search_jumbo,
    }

    tasks = {name: asyncio.create_task(fn(q=q, barcode=barcode, limit=limit))
             for name, fn in providers.items()}

    results: Dict[str, List[Dict]] = {}
    for name, task in tasks.items():
        try:
            items = await task
            clean_items = [
                {
                    "title": it.get("title"),
                    "price": it.get("price"),
                    "url": it.get("url"),
                    "image_url": it.get("image_url"),
                    "barcode": it.get("barcode"),
                }
                for it in items
            ]
            results[name] = clean_items
        except Exception:
            results[name] = []
    return results

