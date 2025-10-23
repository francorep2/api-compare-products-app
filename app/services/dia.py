from typing import List, Dict, Optional
from scrapers.dia_scraper_async import search

async def search_dia(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    return await search(q=q, barcode=barcode, limit=limit)