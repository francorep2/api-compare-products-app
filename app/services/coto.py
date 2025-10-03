# app/services/coto.py
from typing import List, Dict, Optional
from app.core.browser_async import browser_manager_async, DEFAULT_CTX
from scrapers.coto_scraper_async import search as coto_core_search  # ESTE SÍ PIDE ctx

async def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    async with browser_manager_async.new_context(**DEFAULT_CTX) as ctx:
        return await coto_core_search(ctx, q=q, barcode=barcode, limit=limit)
