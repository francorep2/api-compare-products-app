# app/services/carrefour_async.py
from typing import List, Dict, Optional
from app.core.browser_async import browser_manager_async
from scrapers.carrefour_scraper_async import search as carrefour_search

DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
}

async def search_carrefour(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    async with browser_manager_async.new_context(**DEFAULT_CTX) as ctx:
        return await carrefour_search(ctx, q=q, barcode=barcode, limit=limit)
