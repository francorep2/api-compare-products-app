# app/services/all_async.py
from typing import Dict, List, Optional
import asyncio

from app.services.coto import search_coto
from app.services.carrefour import search_carrefour
from app.services.dia import search_dia
from app.services.jumbo import search_jumbo

async def search_all_grouped(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3
) -> Dict[str, List[Dict]]:
    providers = {
        "coto": search_coto,
        "carrefour": search_carrefour,
        "dia": search_dia,
        "jumbo": search_jumbo,
    }

    # correr todo en paralelo
    tasks = {name: asyncio.create_task(fn(q=q, barcode=barcode, limit=limit))
             for name, fn in providers.items()}

    results: Dict[str, List[Dict]] = {}
    for name, task in tasks.items():
        try:
            items = await task
            # nos quedamos solo con los campos originales
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
