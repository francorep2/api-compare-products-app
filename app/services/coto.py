# app/services/coto.py
from __future__ import annotations
from typing import List, Dict, Optional
import asyncio
import logging
from scrapers.coto_scraper_async import search_coto_requests as coto_search_sync
from scrapers.coto_scraper_async import search_coto_by_barcode as coto_search_barcode_sync

log = logging.getLogger("coto")

async def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    if not q and not barcode:
        return []

    try:
        if barcode:
            log.info(f"[COTO] barcode={barcode} limit={limit}")
            # ⬇️ corre la función SINC en un hilo
            return await asyncio.to_thread(coto_search_barcode_sync, str(barcode), limit)
        else:
            log.info(f"[COTO] q={q} limit={limit}")
            # ⬇️ corre la función SINC en un hilo
            return await asyncio.to_thread(coto_search_sync, q, limit)
    except Exception as e:
        log.exception(f"[COTO][ERROR] q={q} barcode={barcode} limit={limit} -> {type(e).__name__}: {e}")
        return []
