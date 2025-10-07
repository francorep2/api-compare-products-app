# en tu servicio
import asyncio
from scrapers.coto_scraper_async import search as coto_search_sync

async def coto_search(q=None, barcode=None, limit=3):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: coto_search_sync(q=q, barcode=barcode, limit=limit))
