# app/services/dia_async.py
from typing import List, Dict, Optional
from app.core.browser_async import browser_manager_async
from scrapers.dia_scraper_async import search as dia_search
from scrapers.vtex_utils_async import dedupe_and_trim


DEFAULT_CTX = {
    "locale": "es-AR",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "viewport": {"width": 1366, "height": 800},
}

async def search_dia(q: Optional[str] = None, barcode: Optional[str] = None, limit: int = 3) -> List[Dict]:
    async with browser_manager_async.new_context(**DEFAULT_CTX) as ctx:
        return await dia_search(ctx, q=q, barcode=barcode, limit=limit)
