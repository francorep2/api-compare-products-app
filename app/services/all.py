# app/services/all.py
from typing import Dict, List, Optional
import asyncio
from app.models.search_item import SearchHit
from app.services.coto import search_coto
from app.services.carrefour import search_carrefour
from app.services.dia import search_dia
from app.services.jumbo import search_jumbo

STORES = {
    "coto": search_coto,
    "carrefour": search_carrefour,
    "dia": search_dia,
    "jumbo": search_jumbo,
}

async def search_stores(
    stores: list[str],
    q: str | None = None,
    barcode: str | None = None,
    limit: int = 3,
) -> list[SearchHit]:
    tasks = []
    for store_name in stores:
        if store_name in STORES:
            tasks.append(STORES[store_name](q=q, barcode=barcode, limit=limit))

    search_results = await asyncio.gather(*tasks)

    results: list[SearchHit] = []
    for store_name, items in zip(stores, search_results):
        for item in items:
            # FIX: Se elimina la clave 'store' si ya existe antes de crear el objeto
            item.pop('store', None)
            results.append(
                SearchHit(
                    **item,
                    store=store_name,
                )
            )
    return results

async def search_all_grouped(
    q: str | None = None, barcode: str | None = None, limit: int = 3
) -> Dict:
    stores = list(STORES.keys())
    results = await search_stores(stores, q=q, barcode=barcode, limit=limit)
    
    grouped_results: Dict[str, list] = {store: [] for store in stores}
    for result in results:
        grouped_results[result.store].append(result.dict())

    return {
        "count": len(results),
        "results": grouped_results,
    }

async def search_all_by_barcode(barcode: str, limit: int = 3) -> list[SearchHit]:
    stores = list(STORES.keys())
    results = await search_stores(stores, barcode=barcode, limit=limit)
    return results