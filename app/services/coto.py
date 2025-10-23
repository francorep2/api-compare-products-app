# app/services/coto.py
from typing import List, Dict, Optional
import asyncio, logging
from scrapers.coto_scraper_async import search_coto_requests as coto_search_sync
from scrapers.coto_scraper_async import search_coto_by_barcode as coto_search_barcode_sync


log = logging.getLogger("coto")

async def search_coto(q: Optional[str] = None, limit: int = 3) -> List[Dict]:
    try:
        return await asyncio.to_thread(coto_search_sync, q, limit)
    except Exception as e:
        log.exception(f"[COTO] error: {e}")
        return []