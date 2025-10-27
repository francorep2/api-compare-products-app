from typing import List, Dict, Optional

# 1. Importa la función 'search' del scraper que te di
from scrapers.coto_scraper_async import search
from app.core.browser_async import browser_manager_async

async def search_coto(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    items = await search(q=q, barcode=barcode, limit=limit)
    return items