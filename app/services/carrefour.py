# app/services/carrefour_async.py
from typing import List, Dict, Optional
from scrapers.carrefour_scraper_async import search_carrefour as _search_carrefour

async def search(
    q: Optional[str] = None,
    barcode: Optional[str] = None,
    limit: int = 3,
    include_promotions: bool = False,  # rápido por defecto
) -> List[Dict]:
    return await _search_carrefour(
        q=q,
        barcode=barcode,
        limit=limit,
        include_promotions=include_promotions,
    )
